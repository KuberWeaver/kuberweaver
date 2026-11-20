# Reporting: expose data, not charts

KuberWeaver does not draw dashboards. It records what it did in PostgreSQL and
publishes a small read-only schema. Grafana, Metabase, Superset or a Python
script query that schema directly, so changing a visualisation never touches the
product, and a panel can join platform facts with anything else in the database.

For graphs the platform cannot know about, use the tools that already do it
well: Zabbix for host and service metrics, the log pipeline for events.

## The schema

Created at startup, idempotently, by `kuberweaver.reporting`. PostgreSQL only:
on SQLite the application tables remain, and `/api/reporting` reports the
surface as unavailable.

    reporting.jobs         one row per job ever prepared or executed
    reporting.deployments  one row per resource, latest successful apply
    reporting.dashboards   the registered external dashboards
    reporting.drift        the last drift comparison per environment
    reporting.costs        declared monthly cost per node of the latest
                           successful graph per environment (mirrors /api/finops)
    reporting.resources    one row per resource of every live (not destroyed)
                           stack in the inventory index

Columns:

| View | Columns |
| --- | --- |
| `jobs` | `id`, `name`, `environment`, `operation`, `status`, `created_by`, `approved_by`, `created_at`, `approved_at`, `finished_at`, `nodes`, `cost_monthly` |
| `deployments` | `environment`, `name`, `plugin`, `job_id`, `deployed_at`, `finished_at`, `deployed_by` |
| `dashboards` | `id`, `label`, `kind`, `url`, `enabled`, `embed`, `created_at` |
| `drift` | `id`, `environment`, `status`, `checked_at`, `differences` |
| `costs` | `environment`, `graph_name`, `job_id`, `job_name`, `currency`, `finished_at`, `node_id`, `plugin_id`, `targets`, `monthly`, `cpu`, `memory_mb`, `disk_gb` |
| `resources` | `environment`, `stack`, `source`, `updated_at`, `type`, `name`, `mode` |

`deployments` keeps one row per `(environment, name)`, the most recent successful
apply, so a chart never double counts a resource deployed five times. A failed
job never appears there: it exists in `jobs` with `status = 'failed'`.

`costs` keeps the latest successful job per `(environment, graph name)`, last
write wins by store rowid exactly like `/api/finops`, then expands its stored
cost breakdown into one row per node. A failed re-apply never displaces a
succeeded graph; a succeeded job without a breakdown contributes no rows.
`sum(monthly)` equals the FinOps monthly total. These are declared estimates,
not metered billing, and a since-destroyed stack keeps its last declared cost
until a newer successful graph replaces it (FinOps parity).

`resources` expands the inventory index of every environment state whose
`destroyed` marker is absent or null. It says what KuberWeaver recorded, not
whether the hypervisor still runs it.

    -- what is deployed, by environment
    SELECT environment, count(*) AS resources
    FROM reporting.deployments GROUP BY 1 ORDER BY 2 DESC;

    -- deployments over the last 30 days
    SELECT date_trunc('day', deployed_at) AS day, count(*)
    FROM reporting.deployments
    WHERE deployed_at > now() - interval '30 days'
    GROUP BY 1 ORDER BY 1;

    -- failure rate by environment
    SELECT environment,
           count(*) FILTER (WHERE status = 'failed')::float / count(*) AS failure_rate
    FROM reporting.jobs GROUP BY 1;

## Read-only access

Do not point a dashboard at the application role. `scripts/reporting_role.py`
provisions the `kw_dashboard` login idempotently (admin connection from
`data/postgresql/bootstrap.json`, password generated without `$`, stored in the
local vault as `grafana/kw-dashboard` and in `data/postgresql/dashboard-password`
0600 for the grafana brick). The statement set it runs as the database admin:

    CREATE ROLE kw_dashboard LOGIN PASSWORD '<generated, never printed>';
    GRANT USAGE ON SCHEMA reporting TO kw_dashboard;
    GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO kw_dashboard;
    ALTER DEFAULT PRIVILEGES FOR ROLE kuberweaver IN SCHEMA reporting
        GRANT SELECT ON TABLES TO kw_dashboard;

`FOR ROLE kuberweaver` matters: the views are (re)created by the application
role at startup, not by the admin running the script, so the default privileges
must attach to the application role. Views run with the privileges of their
owner, so this role reaches the application tables only through the published
columns. The script proves the boundary: SELECT on `reporting.jobs` works,
SELECT on `public.jobs` and CREATE TABLE are denied.

## Grafana

1. Add a data source of type PostgreSQL: host and port of the database, database
   name, user `kw_dashboard`, TLS as your deployment requires.
2. Create a panel, choose the data source, and paste a query from above.
3. Use a dashboard variable on `environment` to switch scope:

       SELECT DISTINCT environment FROM reporting.deployments ORDER BY 1;

## Metabase, Superset

Same shape: a PostgreSQL connection with `kw_dashboard`, then browse the
`reporting` schema as tables. No driver, no plugin, no SDK.

## Python

`pyecharts`, `matplotlib` or `pandas` are libraries, not dashboards: they read
the same views and render wherever you want.

    import pandas as pd
    import matplotlib.pyplot as plt
    import psycopg

    with psycopg.connect("postgresql://kw_dashboard:***@127.0.0.1:15433/kuberweaver") as db:
        frame = pd.read_sql(
            "SELECT date_trunc('day', deployed_at) AS day, count(*) AS resources "
            "FROM reporting.deployments GROUP BY 1 ORDER BY 1", db)

    frame.plot(x="day", y="resources", marker="o")
    plt.tight_layout()
    plt.savefig("deployments.png")

## What the views deliberately do not expose

- Encrypted environment state: the documents are sealed with the vault key. The
  views report the deployments and their outcome, not the ciphertext.
- Credentials, tokens and connection secrets: never in the reporting schema.
- Live hypervisor truth: whether a VM still exists is the hypervisor's answer.
  `reporting.deployments` says what KuberWeaver deployed and when.

## Checking it from the API

`GET /api/reporting` returns the available views, a sample query and the note
that the platform exposes data rather than charts. It never returns a DSN.
