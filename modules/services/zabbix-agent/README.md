# zabbix-agent

Zabbix agent brick for an existing Ubuntu guest. It wraps the pinned
`community.zabbix.zabbix_agent` role (collection 4.2.0, GPL-3.0) and points the
agent at the Zabbix server, usually on the same guest.

## Contract

| Field | Value |
| --- | --- |
| kind | `workload` |
| requires | `compute.vm` |
| executor | `ansible-workload-v1` |
| data class | `config` |

Inputs: `server_host` (required), `agent_hostname` (required, the name the
server sees), `listen_port` (default 10050), `zabbix_version` (default `7.4`),
plus the two portal inputs `target_uuid` and `ssh_key_id` (both required).

## Lifecycle

    snapshot   package, unit and agent configuration
    apply      community.zabbix.zabbix_agent installs the agent, its repository and its configuration
    verify     unit active, port open, agent.ping returns 1 locally, Server= names the configured host
    rollback   restore the previous configuration, remove what did not exist before
    remove     stop the agent, remove its package and configuration

The agent needs no database credential. Its verification is local and does not
depend on the server being reachable, so the brick can be applied before the
server's own checks run.

## Install the pinned collection

    ansible-galaxy collection install -r modules/services/zabbix-agent/requirements.yml \
        -p modules/services/zabbix-agent/collections

The collection must sit beside the lifecycle playbooks because the isolated
executor sets no global collection path.
