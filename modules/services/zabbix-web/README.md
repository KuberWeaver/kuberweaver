# zabbix-web

Zabbix web frontend brick for an existing Ubuntu guest. It wraps the pinned
`community.zabbix.zabbix_web` role (collection 4.2.0, GPL-3.0) over Apache and
PHP, and consumes the credential handoff published by `db-postgresql`.

## Contract

| Field | Value |
| --- | --- |
| kind | `workload` |
| requires | `compute.vm` |
| executor | `ansible-workload-v1` |
| data class | `config` |

Inputs: `server_host` (the Zabbix server address, required), `server_port`
(default 10051), `listen_port` (default 80), `database_handoff` (default
`/etc/kuberweaver/postgresql/handoff.json`), `zabbix_version` (default `7.4`),
plus the two portal inputs `target_uuid` and `ssh_key_id` (both required).

## Lifecycle

    snapshot   frontend package, Apache unit, vhost and frontend configuration
    apply      community.zabbix.zabbix_web installs Apache, PHP and the Zabbix frontend
    verify     Apache active, HTTP 200, login form served, configuration present
    rollback   restore the previous vhost and configuration, remove what did not exist before
    remove     remove the vhost and frontend package, keep Apache

The frontend reads the database connection from the handoff because it needs
the same credentials as the server. The password is never an input and never
appears in the plan or the job result.

## Install the pinned collection

    ansible-galaxy collection install -r modules/services/zabbix-web/requirements.yml \
        -p modules/services/zabbix-web/collections

The collection must sit beside the lifecycle playbooks because the isolated
executor sets no global collection path.
