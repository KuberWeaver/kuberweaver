# KuberWeaver

Self-hosted infrastructure management with a service catalogue, governed requests, explicit approvals and verifiable execution. KuberWeaver exposes one API (plus an MCP interface) to replaceable inventory, execution, secrets, notification and diagnostic providers; the `kw` CLI is the reference client. A modular GUI can be built by anyone on the same API; none is shipped here, and this version focuses on the core, the CLI and real services. It does not require an embedded AI model or Kubernetes.

This file is the project documentation. Detailed reference material that used to live under `docs/` is archived outside Git in `archive/`, which is not committed and is not part of a clone.

## What is inside

- **Connections and hypervisors:** discovery, credentials, TLS trust and provisioning boundaries. The VMware Ubuntu example performs a create-only OVF import through an approved OpenTofu plan.
- **SSH keys:** create, select, disable and explicitly export managed keys. Private keys never appear in list responses or in blueprint configuration.
- **Durable state:** SQLite for the documented local installation, PostgreSQL for a durable deployment, plus an optional local encrypted vault. Backup and restore requirements are stated below.
- **Execution:** provider contracts for Ansible workload bricks, rootless OCI/QEMU on the controller, and replaceable inventory, notification and diagnostic providers. Environment settings and operating limits are policy-driven.
- **Agent access and MCP:** scoped, revocable machine tokens with explicit scopes and expiry; MCP exposes typed domain tools, not shell commands.
- **Observability:** a registry for Grafana, Uptime Kuma, Prometheus and Netdata links with probe paths, and a periodic read-only health and drift column.
- **Environment and stack state:** one encrypted, generation-numbered record per environment and stack, the exclusive apply claim, adoption of an existing VM by UUID and ownership marker, destruction behind the same proof with a cancellation window, and administrator lease release.
- **Drift reports:** the nightly read-only comparison of each recorded declaration against the live hypervisor, field by field, with no remediation.
- **Git-backed declarations:** a CI bundle pins one commit, one stack declaration and one saved OpenTofu plan by SHA-256, verified from the Git object database and executed as-is.
- **Workload bricks:** one pinned Ansible role on one recorded guest, prepared from the deployment evidence a succeeded provisioning job produced, approved as an exact saved plan.
- **Bundled blueprints:** a saved stack graph of workload bricks, validated into dependency-ordered one-brick jobs. The Zabbix 7 blueprint composes PostgreSQL, the server, the web frontend and the agent on one guest.
- **Users, sessions and single sign-on:** Argon2id password storage, a strength policy, account rotation, cookie flags, absolute and idle timeouts, CSRF binding, login throttling, and an optional external OpenID Connect sign-in that binds to an existing local account.

## Community

The project is open source under GPL-3.0-or-later, with a commercial license
available for organizations that cannot accept copyleft terms; see
[LICENSE](LICENSE) and [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md). The
project shows its work as it is: this repository is an early-stage prototype
with one maintainer, not a finished product.

Start here:

- [Contributing](CONTRIBUTING.md): setup, checks, branches, commits, how to add
  a brick or an extension.
- [Support](SUPPORT.md): where to ask, what is supported and what response to
  expect. [Security policy](SECURITY.md): report a vulnerability privately,
  never in a public issue.
- [Static showcase](site/): the marketing-free page in `site/`, servable by any
  static host, buildable offline.
- [Changelog](CHANGELOG.md) and release script: what changed and how a version
  is tagged.

Pull requests target `main`, the default branch. There is no service level
agreement: issues are triaged when the maintainer has time, and there is no
merge guarantee.

## Access and roles

The API listens on `http://127.0.0.1:8088` for the documented local installation; drive it with the `kw` CLI. The server binds to workstation loopback; it is not a public multi-tenant SaaS deployment.

On a local installation the administrator starts as `admin`/`admin`, so a fresh install is reachable without opening a secret file. Set `KUBERWEAVER_ADMIN_PASSWORD` at first startup to choose another seed. The `operator` and `viewer` accounts get random passwords, and all three plaintext lines are written once to the private `data/bootstrap-credentials.txt` file (0600). Change the administrator password with `kw auth passwd`: self service asks for the current password, an administrator may set another account's password without it. The server logs a warning while the development default is still active, and `kw auth whoami` repeats it. Rotating an account removes its plaintext line, and the file disappears once every seeded account has been rotated. Never publish that file. Passwords are stored as Argon2id hashes.

- **Administrator:** policies, providers, explicit extension activation, approvals and eligible rollback operations.
- **Operator:** compose, save and submit requests, without approval authority.
- **Viewer:** read-only access.

## External agents

A human administrator issues a token with a maximum role, explicit scopes and an expiry in **Agent access**. The token is displayed once; store it in a private 0600 file and pass it to clients via the environment, never a command-line argument. Revocation blocks new requests immediately, rotation replaces the secret in place without changing identity, scopes or expiry, and every authenticated call leaves an audit row.

For financial inspection alone, use `read:finops`, not broad `read` or `read:overview`. For deployment proposals, grant the required reads, `plan` and `submit`, without `approve`. Role and scopes are cumulative limits; MCP never replaces API enforcement.

```sh
.venv/bin/python scripts/mcp_server.py --base-url http://127.0.0.1:8088 --token-file /path/to/private/agent.token
```

MCP exposes typed domain tools, not arbitrary shell commands, URLs or direct secret access. No token or Hermes client configuration is created automatically.

## CLI workflow

The full command reference lives in the `kw` help output and in `archive/docs/kw-cli-usage.md`. In short:

```sh
kw --help                      # every group, with the endpoint each command calls
kw auth login --username admin # password from KW_PASSWORD, KW_PASSWORD_FILE or a prompt
kw auth whoami
kw keys generate --name deploy-key
kw connectors drivers
kw connectors add --label "ESXi lab" --driver esxi --endpoint https://esxi.lab
kw blueprints create --name "Web stack" --graph-file graph.json
kw deploy validate --graph-file graph.json     # dry run, creates no job
kw deploy request --blueprint-id <bp-id>
kw deploy approve <job-id>                      # human administrator only
kw deploy watch <job-id>
kw inventory list
kw logs <job-id>
kw rollback <job-id>
```

In order:

1. Sign in and generate an SSH key into the vault.
2. Add and configure a connector; connection secrets go to the vault.
3. Compose a blueprint of resources and services and save it; forms and schemas come from plugins.
4. Validate governance and estimated cost, then submit a request.
5. An administrator reviews the exact plan and approves. The kernel commits the approved plan locally, then runs backup, canary, verification, rolling deployment and final verification.
6. Follow events, commit and HTTP evidence.
7. In an isolated lab, an administrator may inject an intentional rolling failure into an update of the same blueprint to exercise restoration. Verify both targets serve the previous version afterward.

Do not reuse a reserved port for a different blueprint; inspect actual ownership before running again. Graph/environment/node identities determine resource ownership. Blueprint edges order deployment; they do not create network links, firewall rules or application wiring.

Job endpoints on the management subnet may require a tunnel from the workstation. Successful guest probes do not imply automatic LAN publication. Set `KUBERWEAVER_GNS3_URL` and `KUBERWEAVER_GNS3_PROJECT` to your own lab server; no lab address is compiled into the software.

## Modular architecture

The kernel owns identity, admission, approvals, durable state, audit and serialized orchestration. Business capabilities use provider contracts; declarative manifests and blueprint schemas are separate from client code.

```text
backend/kuberweaver/
  app.py, api.py                HTTP, sessions, RBAC, CSRF
  agent_auth.py, mcp_server.py  revocable tokens, scopes, MCP tools
  models.py, policy.py          envelopes and admission
  storage.py, jobs.py           durable state, queue, audit, GitOps
  catalog.py                    declarative blueprint discovery
  extensions.py, runtime.py     SDK, activation and configuration
  providers.py                  built-in providers
  workload_execution.py         Ansible workload bricks on a recorded guest
  local_execution.py            administrator-pinned rootless OCI and QEMU
  secrets.py                    replaceable secret sources
kw/                             thin CLI client, the reference client over the API
modules/resources/              infrastructure manifests and provisioning assets
modules/services/               service manifests and execution assets
blueprints/                     reusable compositions shipped with the project
extensions/                     independently installable Python extensions
scripts/                        lifecycle and acceptance commands
ops/                            user systemd units
site/                           static showcase page
```

Edit recipes in `modules/`. The checked-in `plugins/` directory is a generated runtime catalogue: keeping the original paths and files preserves existing saved-plan and rollback checks. After changing a recipe, stage its source and run `python3 scripts/sync_compat_catalog.py`, then stage the generated files as well. Install Ansible dependencies into `plugins/` as shown in each recipe. Bundled compositions previously referenced under `presets/` now live under `blueprints/`. A custom flat `KUBERWEAVER_PLUGINS_DIR` still works. Source packages in `extensions/` are separate from installed copies in `data/extensions/`; no reinstall is required for this layout change.

No browser interface ships. A modular GUI can be built on the same API.

A compatible service adds a manifest, JSON Schema and execution assets, not `if plugin_id` branches in the kernel. A new capability family supplies an extension registering a provider or hook through the SDK.

Example installation, disabled until explicitly trusted:

```sh
.venv/bin/python scripts/plugins.py install extensions/lab-notes
.venv/bin/python scripts/plugins.py doctor lab-notes
.venv/bin/python scripts/plugins.py enable lab-notes --trust
.venv/bin/python scripts/plugins.py disable lab-notes
```

Do not reinstall an existing ID. The CLI reports when restart is required. `doctor` is static by default; executing Python requires explicit trust. Extensions run with server-process privileges, not in a sandbox. Local blueprint checks are not a public marketplace security certification.

Integrations selects providers by capability. `GET /api/providers` reports active bindings and available implementations; administrators change them with `PATCH /api/providers`. Extensions must first be installed and explicitly enabled. Disabling a selected provider makes that function unavailable without silent fallback. Selection persists, is refused during execution, and execution-provider provenance is pinned to approval.

Canonical hooks are `before_plan`, `after_approval`, `job_started` and `job_finished`. They observe copies of events and cannot replace validation or approval.

## Capability boundaries

| Capability | Supported scope and limits |
| --- | --- |
| Catalogue, blueprint storage and governed deployment | Available through the API and the `kw` CLI. |
| Sessions, RBAC, CSRF, quotas and approval | Local accounts plus an optional, disabled-by-default OpenID Connect sign-in that binds to an existing local account; no per-object authorization. |
| NGINX | Real Ansible deployment of the reviewed `web-nginx` brick on an allowlisted guest, with HTTP verification. No Apache brick ships. |
| Canary, rolling and rollback | Exercised for managed web workloads; not a universal recovery guarantee. |
| VMware Ubuntu OVF import | Create-only workflow; no update, deletion, rollback or automatic cleanup after an ambiguous failure. Target OS/SSH acceptance requires an authorized real import. |
| ESXi discovery | Separate read-only provider; observed VMs are not proof of a running guest OS. |
| OpenTofu/libvirt and blank-disk vSphere blueprint | Planned/disabled with documented transport and powered-off-create blockers; not promoted by the separate OVF workflow. |
| Redis persistence | Not provided. The prototype container engine that carried it was removed; no manifest ships. |
| Frontend/backend stack, Cisco IOS, pfSense and cloud entries | Planned and disabled; no validated adapter/target path. |
| GitOps | Local approved-plan commits; optional remote publication verifies the exact SHA. Test-repository evidence is not a hosted customer delivery pipeline. |
| FinOps | Internal tariff estimates, not cloud billing or measured consumption. Prices are declared by the requester, not derived from a rate table. |
| Diagnostics | Deterministic, read-only rules. No model provider is wired in. |
| Notifications and ChatOps | Durable outbox, webhook delivery and signed callback exercised locally; no certified Slack/Teams integration or configured external SaaS recipient. |
| Secrets | Replaceable OpenBao and local Fernet providers; local custody and transport limits apply. |
| External API/MCP clients | Explicit role/scopes, expiry and revocation; no automatic approval. |
| Observability | Local metrics and authenticated snapshot; validated rules/dashboard files do not mean a deployed Prometheus/Grafana stack. |
| Public marketplace, payments and untrusted-code isolation | Not provided. |

For the web executor, `disk_gb` is a budgeting declaration, not an enforced disk quota. `public_cidr` is an admission guard, not an installed firewall rule. New cloud or network providers need their own contracts and acceptance evidence.

## Cost declaration

Every node carries a monthly price that the requester types in the graph (`price_monthly`), default `0`, which means free, plus an optional `currency` (three letters). A service can declare its own base `price_monthly` and `currency` in its manifest. There is no rate table in the software: CPU, memory and disk are quota dimensions, not pricing inputs. A node currency overrides the manifest one; a priced service without any currency is refused at admission with a named error, and a job that mixes currencies is refused.

## Data, secrets and recovery

`data/` is private and ignored by Git. It holds configuration, retained SQLite sources, snapshots, logs, GitOps plans, activations and extension state, including local vault keys and bootstrap files. Current application data uses the dedicated PostgreSQL volume `kw-postgresql-v2-data`: **backing up `data/` alone is insufficient**. Keep PostgreSQL backups and privately back up the vault key, authenticated marker and ciphertext together. Exercise restoration; a readable dump catalogue is not a restore test. Never publish the project directory wholesale without secret review.

Plugin data is separate from replaceable code. Disabling a plugin does not delete its state. Interrupted jobs require manual review and are not automatically replayed. Local vault encryption does not protect against compromise of the OS account or root.

## Start and stop

The documented installation uses user units from `ops/`; they are not enabled automatically at boot.

```sh
systemctl --user start kuberweaver.service
systemctl --user status kuberweaver.service kuberweaver-bao-tunnel.service
journalctl --user -u kuberweaver.service -n 80
systemctl --user stop kuberweaver.service kuberweaver-bao-tunnel.service
```

Graceful shutdown waits for the active operation before releasing its lock. Do not run multiple Uvicorn workers against the same state.

Foreground alternative from this directory, with the appropriate private database configuration:

```sh
PYTHONPATH=backend .venv/bin/uvicorn kuberweaver.app:app --host 127.0.0.1 --port 8088
```

Lab targets must be running for deployments. The API still exposes local capabilities and explicit unavailable states when targets cannot be reached. It does not start all GNS3 nodes automatically.

## Installation and verification

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-execution.txt -r requirements-mcp.txt -r requirements-infrastructure.txt -r requirements-postgresql.txt -r requirements-local-vault.txt
uv pip install --python .venv/bin/python -e . --no-deps   # installs the `kw` CLI
```

Run backend tests against disposable state, not the live database:

```sh
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL KUBERWEAVER_STATE_DIR="$state" .venv/bin/python -m pytest tests -q
python3 ops/ci/check.py --fast
ops/ci/cli_smoke.sh         # installed kw against a real server, about one second
```

FastAPI serves the API on one origin. `/healthz`, `/readyz`, `/metrics` and `/docs` are local endpoints. `/healthz` is liveness only; `/readyz` reports the database, the selected secret source and the plugin directory. Metrics describe the process and jobs, not a proven remote monitoring deployment.

## Security limits

Do not expose this installation directly to the Internet. It has no multi-tenancy, per-object permissions, HA or sandboxed marketplace, and its optional OpenID Connect sign-in is disabled unless configured. Protect remote access with an appropriate authenticated HTTPS boundary and a reviewed threat model. The existing unauthenticated GNS3 LAN API is not secured for other clients by this installation. Guest root and workstation administrators remain trusted. Deployment must stay within authorized targets and owned resources; do not change host routes, other GNS3 projects or production resources for acceptance tests.
