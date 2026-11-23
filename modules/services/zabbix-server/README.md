# zabbix-server

Zabbix server brick for an existing Ubuntu guest. It wraps the pinned
`community.zabbix.zabbix_server` role (collection 4.2.0, GPL-3.0) and consumes
the credential handoff published by `db-postgresql`.

## Contract

| Field | Value |
| --- | --- |
| kind | `workload` |
| requires | `compute.vm` |
| executor | `ansible-workload-v1` |
| data class | `config` |

Inputs: `server_port` (default 10051), `database_handoff` (default
`/etc/kuberweaver/postgresql/handoff.json`), `zabbix_version` (`7.0`, `7.2` or
`7.4`, default `7.4`), plus the two portal inputs `target_uuid` and
`ssh_key_id` (both required).

## Lifecycle

    snapshot   package, unit, server configuration and handoff presence
    apply      community.zabbix.zabbix_server installs the server, the Zabbix 7 repository and the schema
    verify     unit active, port open, Zabbix 7 reported, schema row present
    rollback   restore the previous configuration, remove what did not exist before
    remove     stop the server, remove its package and configuration, keep the database

The server owns its package and configuration. The PostgreSQL database, its
role and the credential handoff belong to `db-postgresql` and are not removed
here.

## Install the pinned collection

    ansible-galaxy collection install -r plugins/zabbix-server/requirements.yml \
        -p plugins/zabbix-server/collections

The collection must sit beside the lifecycle playbooks because the isolated
executor sets no global collection path. The brick refuses to run when the
installed role does not match the locked version, when a declared assertion is
not reported, or when the configuration names a field the schema does not
declare.

## Why this role

The community collection is the maintained upstream for Zabbix 7; the older
standalone `dj-wasabi` roles have no Zabbix 7 support. The collection also
installs the official repository and the SQL schema, which a hand-written
playbook would otherwise have to reproduce.
