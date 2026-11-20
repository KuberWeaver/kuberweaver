"""Read-only SQL surface for external dashboards.

The platform does not draw charts. Grafana, Metabase or a Python script query
these views directly, so a dashboard can be replaced without touching the
product, and a visualisation can join the platform's facts with anything else in
the database.

Only PostgreSQL gets the schema: SQLite deployments keep the application tables
and the API. Every view is created independently, so one malformed legacy row
does not take the whole surface down.

``costs`` mirrors /api/finops: the latest successful graph per (environment,
graph name), last write wins by store rowid, expanded into per-node declared
cost. ``resources`` mirrors the live inventory index: one row per resource of
every stack that has not been destroyed. Both report declared estimates and
recorded facts, not metered billing or live hypervisor truth.
"""
from __future__ import annotations

import json

SCHEMA = 'reporting'

VIEWS: dict[str, str] = {
    'jobs': """
        CREATE OR REPLACE VIEW {schema}.jobs AS
        SELECT id,
               body::jsonb ->> 'name' AS name,
               body::jsonb ->> 'environment' AS environment,
               body::jsonb ->> 'operation' AS operation,
               body::jsonb ->> 'status' AS status,
               body::jsonb ->> 'created_by' AS created_by,
               NULLIF(body::jsonb ->> 'approved_by', '') AS approved_by,
               NULLIF(body::jsonb ->> 'created_at', '')::timestamptz AS created_at,
               NULLIF(body::jsonb ->> 'approved_at', '')::timestamptz AS approved_at,
               NULLIF(body::jsonb ->> 'finished_at', '')::timestamptz AS finished_at,
               COALESCE(jsonb_array_length(body::jsonb -> 'graph' -> 'nodes'), 0) AS nodes,
               NULLIF(body::jsonb -> 'cost' ->> 'monthly', '')::numeric AS cost_monthly
        FROM jobs
    """,
    'deployments': """
        CREATE OR REPLACE VIEW {schema}.deployments AS
        SELECT DISTINCT ON (environment, name)
               environment,
               name,
               plugin,
               job_id,
               deployed_at,
               finished_at,
               deployed_by
        FROM (
            SELECT body::jsonb ->> 'environment' AS environment,
                   node ->> 'label' AS name,
                   node ->> 'plugin_id' AS plugin,
                   jobs.id AS job_id,
                   NULLIF(body::jsonb ->> 'created_at', '')::timestamptz AS deployed_at,
                   NULLIF(body::jsonb ->> 'finished_at', '')::timestamptz AS finished_at,
                   body::jsonb ->> 'created_by' AS deployed_by
            FROM jobs
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(body::jsonb -> 'graph' -> 'nodes', '[]'::jsonb)) AS node
            WHERE body::jsonb ->> 'status' = 'succeeded'
        ) AS applied
        ORDER BY environment, name, deployed_at DESC
    """,
    'dashboards': """
        CREATE OR REPLACE VIEW {schema}.dashboards AS
        SELECT id,
               body::jsonb ->> 'label' AS label,
               body::jsonb ->> 'kind' AS kind,
               body::jsonb ->> 'url' AS url,
               COALESCE((body::jsonb ->> 'enabled')::boolean, true) AS enabled,
               COALESCE((body::jsonb ->> 'embed')::boolean, false) AS embed,
               NULLIF(body::jsonb ->> 'created_at', '')::timestamptz AS created_at
        FROM dashboards
    """,
    'drift': """
        CREATE OR REPLACE VIEW {schema}.drift AS
        SELECT id,
               body::jsonb ->> 'environment' AS environment,
               body::jsonb ->> 'status' AS status,
               NULLIF(body::jsonb ->> 'checked_at', '')::timestamptz AS checked_at,
               jsonb_array_length(COALESCE(body::jsonb -> 'differences', '[]'::jsonb)) AS differences
        FROM environment_state_drift
    """,
    'costs': """
        CREATE OR REPLACE VIEW {schema}.costs AS
        SELECT latest.environment,
               latest.graph_name,
               latest.job_id,
               latest.job_name,
               latest.currency,
               latest.finished_at,
               item ->> 'node_id' AS node_id,
               item ->> 'plugin_id' AS plugin_id,
               NULLIF(item ->> 'targets', '')::int AS targets,
               NULLIF(item ->> 'monthly', '')::numeric AS monthly,
               NULLIF(item -> 'resources' ->> 'cpu', '')::numeric AS cpu,
               NULLIF(item -> 'resources' ->> 'memory_mb', '')::numeric AS memory_mb,
               NULLIF(item -> 'resources' ->> 'disk_gb', '')::numeric AS disk_gb
        FROM (
            SELECT DISTINCT ON (body::jsonb ->> 'environment', body::jsonb -> 'graph' ->> 'name')
                   body::jsonb ->> 'environment' AS environment,
                   body::jsonb -> 'graph' ->> 'name' AS graph_name,
                   jobs.id AS job_id,
                   body::jsonb ->> 'name' AS job_name,
                   COALESCE(body::jsonb -> 'cost' ->> 'currency', 'EUR') AS currency,
                   NULLIF(body::jsonb ->> 'finished_at', '')::timestamptz AS finished_at,
                   COALESCE(body::jsonb -> 'cost' -> 'breakdown', '[]'::jsonb) AS breakdown
            FROM jobs
            WHERE body::jsonb ->> 'status' = 'succeeded'
            ORDER BY body::jsonb ->> 'environment', body::jsonb -> 'graph' ->> 'name', _kw_rowid DESC
        ) AS latest
        CROSS JOIN LATERAL jsonb_array_elements(latest.breakdown) AS item
    """,
    'resources': """
        CREATE OR REPLACE VIEW {schema}.resources AS
        SELECT body::jsonb ->> 'environment' AS environment,
               body::jsonb ->> 'stack' AS stack,
               body::jsonb ->> 'source' AS source,
               NULLIF(body::jsonb ->> 'updated_at', '')::timestamptz AS updated_at,
               resource ->> 'type' AS type,
               resource ->> 'name' AS name,
               resource ->> 'mode' AS mode
        FROM environment_state
        CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(body::jsonb -> 'resources', '[]'::jsonb)) AS resource
        WHERE body::jsonb ->> 'destroyed' IS NULL
    """,
}


def ensure_reporting(store) -> dict:
    """Create the reporting schema and views. Returns what was created."""
    if store.backend != 'postgresql':
        return {'available': False, 'reason': 'reporting views require PostgreSQL', 'views': []}
    created, failed = [], []
    try:
        store.execute(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}')
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        return {'available': False, 'reason': str(error), 'views': []}
    for name, statement in VIEWS.items():
        try:
            store.execute(statement.format(schema=SCHEMA))
            created.append(name)
        except Exception:  # noqa: BLE001 - one legacy table must not stop the rest
            # CREATE OR REPLACE cannot change a column type or drop columns.
            # When a legacy shape is in place, rebuild the view so startup
            # converges without a manual migration step.
            try:
                store.execute(f'DROP VIEW IF EXISTS {SCHEMA}.{name}')
                store.execute(statement.format(schema=SCHEMA))
                created.append(name)
            except Exception as error:  # noqa: BLE001 - reported, never fatal
                failed.append({'view': name, 'error': str(error).splitlines()[0]})
    return {'available': True, 'views': created, 'failed': failed}


def describe(store) -> dict:
    """Report the surface without exposing credentials."""
    if store.backend != 'postgresql':
        return {'available': False, 'reason': 'reporting views require PostgreSQL'}
    try:
        rows = store.all(
            "SELECT table_name AS name FROM information_schema.views "
            f"WHERE table_schema = '{SCHEMA}' ORDER BY 1")
        names = [row['name'] if isinstance(row, dict) else row[0] for row in rows]
    except Exception:  # noqa: BLE001 - a diagnostic endpoint must answer
        names = []
    return {
        'available': True,
        'schema': SCHEMA,
        'views': names or sorted(VIEWS),
        'example': 'SELECT * FROM ' + SCHEMA + '.deployments ORDER BY deployed_at DESC LIMIT 50',
        'note': 'The platform exposes data, not charts. Point Grafana, Metabase or pandas at these views.',
    }