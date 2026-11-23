# db-postgresql

PostgreSQL server brick for an existing Ubuntu guest. It wraps the pinned
upstream `geerlingguy.postgresql` role (4.3.0, MIT) and creates one database and
one login role for a dependent brick.

## Contract

| Field | Value |
| --- | --- |
| kind | `workload` |
| requires | `compute.vm` |
| executor | `ansible-workload-v1` |
| data class | `state` |

Inputs: `db_name` (default `zabbix`), `db_user` (default `zabbix`),
`listen_port` (default 5432), `handoff_path` (default
`/etc/kuberweaver/postgresql/handoff.json`), `drop_data` (default false), plus
the two portal inputs `target_uuid` and `ssh_key_id` (both required).

## Data class

The database holds monitoring history. The brick can be removed; the data
cannot be rolled back by this tool. `remove.yml` refuses while the database
exists unless `drop_data` is true, and `rollback.yml` keeps the data directory
when PostgreSQL was already installed before the brick.

## Credential handoff

The playbook generates a random password on the guest, stores it in
`<handoff dir>/<db_user>.password` with mode `0600`, and writes a small JSON
handoff (`db_name`, `db_user`, `host`, `port`, `password_file`) to
`handoff_path`. Neither value is an input, neither is returned by the portal and
neither is written to Git. A dependent brick reads the handoff by path; the
preset in `blueprints/zabbix-7.yaml` is the only consumer in this repository.

## Lifecycle

    snapshot   package, cluster unit, data directory, generated files and cluster configuration
    apply      geerlingguy.postgresql installs the server, creates the database and role, publishes the handoff
    verify     unit active, pg_isready, database present, login role present
    rollback   restore the previous cluster configuration, remove what did not exist before
    remove     refuse while data exists, or drop the database and role with drop_data=true

## Install the pinned role and modules

    ansible-galaxy role install -r modules/services/db-postgresql/requirements.yml \
        -p modules/services/db-postgresql/roles
    ansible-galaxy collection install community.postgresql:==5.1.2 \
        -p modules/services/db-postgresql/collections

The collection must sit beside the lifecycle playbooks because the isolated
executor sets no global collection path. The brick refuses to run when the
installed role does not match the pinned version, when a declared assertion is
not reported, or when the configuration names a field the schema does not
declare.
