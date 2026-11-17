# Presets

A preset is a saved stack graph of workload bricks with sensible defaults. It is
data: a YAML file under `presets/` that names the bricks, their configuration
and their dependencies. The catalogue, the admission policy and the workload
provider are used unchanged.

`presets/zabbix-7.yaml` is the reference preset: PostgreSQL, the Zabbix server,
the web frontend and the agent on one guest.

## One brick per job

The workload execution contract is one brick per job: one prepared plan, one
approval and one `snapshot`/`apply`/`verify` lifecycle. A preset therefore
expands into one normal one-node job per step, in dependency order, instead of
adding a second execution path for multi-brick graphs.

    database   db-postgresql    no dependency
    server     zabbix-server    requires database
    web        zabbix-web       requires server
    agent      zabbix-agent     requires server

The order is a topological order of the declared dependencies. The validator
refuses a preset whose steps are not already listed in that order, so a human
reviewing the file sees the execution order without running a scheduler.

## Target inputs

Every brick requires `target_uuid` (the guest) and `ssh_key_id` (the managed key
used to reach it). Both are supplied per submission and are part of the prepared
plan digest. They are never stored in the preset:

- the preset must not carry `target_uuid` or `ssh_key_id` in a step config;
- the validator injects the values supplied on its command line into every step;
- all steps in the Zabbix preset receive the same UUID and key, which is what
  "on one guest" means;
- the portal still resolves the address and power state from the deployment
  evidence of a succeeded provisioning job, exactly as for a single brick.

## Validate a preset

    python scripts/validate_preset.py presets/zabbix-7.yaml \
        --uuid <vm-uuid> --ssh-key <32-hex-key-id>

The command prints a JSON plan with the dependency order, the stack graph and
one complete one-node graph per step. Each graph is already accepted by the
portal's admission policy, so it can be posted to `POST /api/jobs` as-is. The
command never contacts a guest, never resolves a secret and writes nothing.

Refusals:

| Condition | Result |
| --- | --- |
| preset is not valid YAML or carries an undeclared field | refused |
| step id is duplicated, a dependency is unknown, or the graph has a cycle | refused |
| steps are not listed in dependency order | refused, with the expected order |
| step config names `target_uuid` or `ssh_key_id` | refused; portal inputs come from the submission |
| brick is unknown, not `kind: workload`, or not `capability: live` | refused by the catalogue and the admission policy |
| brick has no `roles.lock.json`, or the lock names another version | refused |
| step config violates the brick schema, including an undeclared field | refused, with the field path |
| `--uuid` or `--ssh-key` is malformed | refused before any step is built |

The validator checks the role lock, not the installed role. Preparation still
runs the workload provider's own check: a missing or drifted role refuses the
job before a plan is saved.

## Run the Zabbix preset

1. Provision the guest and let the provisioning job record its evidence (UUID,
   address, `poweredOn` state).
2. Validate the preset with that UUID and the managed key id.
3. Submit each step in the printed order as a normal one-brick job, either from
   the canvas (load the step graph) or with `POST /api/jobs`.
4. Approve each saved plan after reviewing the brick, role version, target and
   key fingerprint.
5. Wait for the step to succeed before submitting the next one. A failed step
   rolls back its own brick and stops the sequence; later steps are not
   submitted.

The steps can also be submitted one at a time across a maintenance window. The
preset records intent, not a scheduler: nothing runs a step on its own.

## The Zabbix bricks

| Brick | Upstream | Data class | What it proves |
| --- | --- | --- | --- |
| `db-postgresql` | `geerlingguy.postgresql` 4.3.0 | `state` | cluster active, port open, database and login role exist |
| `zabbix-server` | `community.zabbix.zabbix_server` 4.2.0 | `config` | unit active, port 10051 open, Zabbix 7 reported, schema row present |
| `zabbix-web` | `community.zabbix.zabbix_web` 4.2.0 | `config` | Apache active, HTTP 200, login form served, configuration present |
| `zabbix-agent` | `community.zabbix.zabbix_agent` 4.2.0 | `config` | unit active, port 10050 open, `agent.ping` returns 1, server configured |

The database brick generates a random password on the guest, keeps it in a
`0600` file and publishes a handoff JSON beside it. The server and web bricks
read that handoff by path; the password is never a preset input, never returned
by the portal and never written to Git. The agent does not need it.

## Data is honest

PostgreSQL holds monitoring history. `db-postgresql` is `data_class: state`, its
removal playbook refuses while the database exists unless `drop_data` is true,
and its rollback keeps the data directory when PostgreSQL was already installed
before the brick.

`zabbix-server`, `zabbix-web` and `zabbix-agent` are `data_class: config`: their
packages and configuration files can be replaced by reinstalling the brick. The
database and its history stay with the database brick.

## Collections

The three Zabbix bricks use collection roles. Each brick has its own pinned
`requirements.yml` and a `roles.lock.json` that records the collection version
and the role checksum. Install the collection beside the lifecycle playbooks so
the isolated executor finds it without a global path:

    ansible-galaxy collection install -r plugins/zabbix-server/requirements.yml \
        -p plugins/zabbix-server/collections

`plugins/*/collections/` is ignored by Git, like `plugins/*/roles/`. The
`db-postgresql` brick additionally installs `community.postgresql`, which its
upstream role imports.
