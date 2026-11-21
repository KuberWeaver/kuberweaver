# kw, the KuberWeaver CLI

A thin client for the KuberWeaver API. One door: the same routes, roles and pins
as any other client. No business logic, no direct database writes, no way around
approval. The design notes are in `docs/cahier-des-charges-kw-cli.md`; that file
is a French school specification and is not part of the public documentation.

## Installation and invocation

```sh
uv pip install --python .venv/bin/python -e . --no-deps   # provides the `kw` command
kw --help
# or without installing:
.venv/bin/python -m kw --help
```

## Configuration

| Variable / file | Role |
| --- | --- |
| `KW_URL` | API URL (default `http://127.0.0.1:8088`). Otherwise `--url` or `config.json`. |
| `KW_CONFIG_DIR` | Config directory (default `~/.config/kuberweaver`, mode 0700). |
| `KW_PASSWORD` / `KW_PASSWORD_FILE` | Password, never as a command argument. Otherwise a prompt. |
| `KW_AGENT_TOKEN` | Scoped agent token for automation (machine mode). |

## Authentication

Human (cookie session plus CSRF, persisted in a 0600 file, cleared on logout):

```sh
kw auth login --username admin
kw auth whoami
kw auth logout
```

Automation (scoped agent token; it cannot approve, roll back or decommission):

```sh
export KW_AGENT_TOKEN=...
kw deploy list
```

## Journey

The order below is the order the server accepts. Each step depends on the one
before it: an SSH key before a connector, a connector before discovery, a pinned
artifact or blueprint before a deployment request.

```sh
# 1. SSH key -> vault (the private key is never returned in clear)
kw keys generate --name deploy-key
kw keys list
kw keys revoke <key-id>
kw keys export <key-id> --output ./deploy.key      # admin re-authentication, 0600 file

# 2-3. Connector: connection details, secrets in the vault
kw connectors drivers
kw connectors add --label "ESXi lab" --driver esxi --endpoint https://esxi.lab \
    --secret-ref hypervisors/lab --secrets-provider local-vault
kw connectors credentials <conn-id> --username root     # password through env or prompt
kw connectors test <conn-id>
kw connectors discover <conn-id>

# 5. Module: a pinned artifact (metadata), then an immutable offering
kw modules artifacts create --kind oci --reference registry.lab/web:1.0.0 --pin sha256:...
kw blueprints create --name "Web stack" --graph-file graph.json
kw modules offerings create --title "Web stack" --blueprint-id <bp-id>

# 6-7. Resource: import a pinned OpenTofu plan, or adopt a VM already owned by you
#        (--default has no API route; imported code is trusted like any plugin)
kw resources create --import --environment dev --stack web \
    --revision <git-commit> --bundle-path bundles/web --bundle-sha256 <sha256> --dry-run
kw resources adopt --environment dev --stack web --connection-id <conn-id> \
    --uuid <vm-uuid> --marker kuberweaver-job:<job-id>
kw resources status

# 8. Blueprint (edit, copy, duplicate)
kw blueprints list
kw blueprints copy <bp-id> --name "Web stack v2"

# 9-10. Deployment request, then human approval
kw deploy request --offering-id <off-id>        # or --blueprint-id / --graph-file
kw deploy validate --graph-file graph.json      # dry run, creates no job
kw deploy approve <job-id>                      # human administrator only
kw deploy watch <job-id>

# 11-12. Inventory and logs
kw inventory list
kw inventory get <state-id>
kw logs <job-id> --follow
kw audit

# 13. Rollback (governed human mutation)
kw rollback <job-id>

# 14. Decommissioning by deployment identifier, isolated
kw services decommission <job-id>               # verified service retirement
kw decommission request <state-id> --connection-id <c> --uuid <u> --marker <m>
kw decommission execute <state-id> --destroy-id <d>   # after the quarantine window
kw decommission status <state-id>

# 15. Retained data for analysis and FinOps
kw finops
kw finops overview
kw finops reporting
```

## Output and exit codes

- Table by default; `--json` for automation; `-v` for verbosity.
- Exit codes: 0 success, 1 API error, 2 authentication required, 3 permission
  refused (role or CSRF).
- Destructive mutations (approve, rollback, decommission, keys revoke/export)
  require a human administrator session; an agent token is refused.

## Accepted backend gaps (no client stub)

These capabilities have no API route yet; the CLI reports them as unavailable:

- `modules artifacts get` and `modules offerings get` (no GET by id), and the
  monthly cost per module (an offering stores an internal estimate at publication,
  but no route returns it).
- `resources create --default` (form) and `resources add` (add to an existing stack).

Injecting an SSH key at creation time is OpenTofu's job, not the CLI's.

## Governance invariants

One door (the API); human approval before execution; source and plan pins;
independent verification and verified retirement; a serialized worker that does
not replay; secrets never in plans, logs or responses; PostgreSQL required in
production; resource ownership and decommissioning isolated by identifier.

## Tests

```sh
.venv/bin/python -m pytest tests/test_cli.py tests/test_cli_journey.py -q -p no:cacheprovider
```

In-process integration: a real local uvicorn server on an ephemeral port, isolated
SQLite, the `sample-service` plugin, a real encrypted local vault. Only provider
boundaries are simulated (recording executors, read/write hypervisor). The end-to-end
test chains the whole journey.
