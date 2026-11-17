# KuberWeaver

Self-hosted infrastructure management with a visual service catalogue, governed requests, explicit approvals and verifiable execution. KuberWeaver connects a React/TypeScript portal and an API/MCP interface to replaceable inventory, execution, secrets, notification and diagnostic providers. It does not require an embedded AI model or Kubernetes.

## Documentation

- [Connections and hypervisors](docs/V2_HYPERVISORS.md): discovery, credentials, TLS trust and provisioning boundaries.
- [VMware Ubuntu deployment](examples/extensions/esxi-ovf/README.md): create-only OVF import through an approved OpenTofu plan.
- [SSH keys](docs/SSH_KEYS.md): create, select, disable and explicitly export managed keys.
- [PostgreSQL](docs/POSTGRESQL.md) and [local encrypted vault](examples/extensions/local-vault/README.md): durable state, backup requirements and threat model.
- [Execution](docs/EXECUTION.md), [configuration](docs/CONFIGURATION.md), [extensions](docs/EXTENSIONS.md) and [secrets](docs/SECRETS.md): provider contracts, environment settings and operating limits.
- [Agent access](docs/AGENT_ACCESS.md) and [MCP](docs/MCP.md): scoped, revocable machine access.
- [Navigation and page states](docs/NAVIGATION.md): the page registry, grouped sidebar, role gating, loading and empty states.
- [External dashboards](docs/DASHBOARDS.md): the registry for Grafana, Uptime Kuma, Prometheus and Netdata links, probe paths, the periodic read-only health and drift column, the embed allowlist and the embed page.
- [Environment and stack state](docs/ENVIRONMENT_STATE.md): one encrypted, generation-numbered record per environment and stack, the exclusive apply claim, the resource list and the administrator lease release.
- [Users and roles](docs/USERS.md): Argon2id password storage, the password policy and account rotation. [Sessions](docs/SESSIONS.md): cookie flags, absolute and idle timeouts, CSRF binding and login throttling. [Single sign-on](docs/OIDC.md): optional OpenID Connect provider, claim mapping and account linking.
- [Acceptance gates](docs/ACCEPTANCE.md), [verification history](docs/VERIFICATION.md) and [capability evidence](docs/COMPLETION_STATUS.md).
- [Contributing](CONTRIBUTING.md), [changelog](CHANGELOG.md) and release script: branch model, local checks and how a version is tagged.
- [Architecture direction](docs/ARCHITECTURE_CIBLE.md) and [requirements](docs/CAHIER_DES_CHARGES_KUBERWEAVER.md): distinguish design proposals from delivered capabilities. Historical framing documents and generated presentations are not current capability evidence.

The repository contains source, tests, manifests, scripts and public documentation. Credentials, local accounts, runtime state, snapshots, IaC state and installed dependencies are excluded. A clone does not recreate existing lab VMs, system images or SSH access. Adapt systemd paths and target access to your installation.

## Access and roles

Open `http://127.0.0.1:8088` for the documented local installation. The server binds to workstation loopback; it is not a public multi-tenant SaaS deployment.

Local `admin`, `operator` and `viewer` accounts have separate random passwords in the private `data/bootstrap-credentials.txt` file (0600). Never publish that file. No demonstration password is embedded in the frontend. Passwords are stored as Argon2id hashes; rotating an account through the users API removes its plaintext line, and the file disappears once every seeded account has been rotated. See [users, roles and password policy](docs/USERS.md).

- **Administrator:** policies, providers, explicit extension activation, approvals and eligible rollback operations.
- **Operator:** compose, save and submit requests, without approval authority.
- **Viewer:** read-only access.

## External agents

A human administrator issues a token with a maximum role, explicit scopes and an expiry in **Agent access**. The token is displayed once; store it in a private 0600 file. Tokens are not kept in browser storage. Revocation blocks new requests immediately, rotation replaces the secret in place without changing identity, scopes or expiry, and every authenticated call leaves an audit row.

For financial inspection alone, use `read:finops`, not broad `read` or `read:overview`. For deployment proposals, grant the required reads, `plan` and `submit`, without `approve`. Role and scopes are cumulative limits; MCP never replaces API enforcement.

```sh
.venv/bin/python scripts/mcp_server.py --base-url http://127.0.0.1:8088 --token-file /path/to/private/agent.token
```

MCP exposes typed domain tools, not arbitrary shell commands, URLs or direct secret access. No token or Hermes client configuration is created automatically. See [MCP](docs/MCP.md) for SDK installation and client setup.

## Web-service workflow

1. Sign in and open Canvas, initially empty for a new user.
2. Add NGINX or Apache from the catalogue; forms come from plugin schemas.
3. Select `app` and `branch-client` and an unused port from 18090 through 18109. Each resource owns its port on each target.
4. Validate governance and estimated cost, save the blueprint and submit a request.
5. An administrator reviews the exact plan and approves. The kernel commits the approved plan locally, then runs backup, canary, verification, rolling deployment and final verification.
6. Inspect events, commit and HTTP evidence in Deployments.
7. In an isolated lab, an administrator may inject an intentional rolling failure into an update of the same blueprint to exercise restoration. Verify both targets serve the previous version afterward.

Do not reuse a reserved port for a different blueprint. Historical acceptance left 18090 free and a portal example on 18091; inspect actual ownership before running again. Graph/environment/node identities determine resource ownership. Canvas edges order deployment; they do not create network links, firewall rules or application wiring.

Job endpoints on the management subnet `192.168.122.0/24` may require a tunnel from the workstation browser. Successful guest probes do not imply automatic LAN publication. The documented GNS3 deployment uses the lab server at `192.168.10.55`; these are installation-specific settings.

## Modular architecture

The kernel owns identity, admission, approvals, durable state, audit and serialized orchestration. Business capabilities use provider contracts; declarative manifests and blueprint schemas are separate from portal code.

```text
backend/kuberweaver/
  app.py, api.py                HTTP, sessions, RBAC, CSRF
  agent_auth.py, mcp_server.py  revocable tokens, scopes, MCP tools
  models.py, policy.py         envelopes and admission
  storage.py, jobs.py          durable state, queue, audit, GitOps
  catalog.py                   declarative blueprint discovery
  extensions.py, runtime.py    SDK, activation and configuration
  providers.py                 built-in providers
  execution.py                 lab Ansible executor
  secrets.py                   replaceable secret sources
frontend/                      React, TypeScript, React Flow
plugins/                       manifests, schemas and Ansible assets
examples/extensions/           independently installable Python examples
scripts/                       lifecycle and acceptance commands
ops/                           user systemd units
```

A compatible service adds a manifest, JSON Schema and execution assets, not `if plugin_id` branches in the kernel. A new capability family supplies an extension registering a provider or hook through the SDK.

Example installation, disabled until explicitly trusted:

```sh
.venv/bin/python scripts/plugins.py install examples/extensions/lab-notes
.venv/bin/python scripts/plugins.py doctor lab-notes
.venv/bin/python scripts/plugins.py enable lab-notes --trust
.venv/bin/python scripts/plugins.py disable lab-notes
```

Do not reinstall an existing ID. The CLI reports when restart is required. `doctor` is static by default; executing Python requires explicit trust. Extensions run with server-process privileges, not in a sandbox. Local blueprint checks are not a public marketplace security certification.

Integrations selects providers by capability. `GET /api/providers` reports active bindings and available implementations; administrators change them with `PATCH /api/providers`. Extensions must first be installed and explicitly enabled. Disabling a selected provider makes that function unavailable without silent fallback. Selection persists, is refused during execution, and execution-provider provenance is pinned to approval.

Canonical hooks are `before_plan`, `after_approval`, `job_started` and `job_finished`. They observe copies of events and cannot replace validation or approval. The separately installed `lab-notes` example has historical live evidence for event delivery and provenance; it is not required by the kernel.

## Capability boundaries

| Capability | Supported scope and limits |
| --- | --- |
| Canvas, catalogue, schema forms and blueprint storage | Available through the local portal and API. |
| Sessions, RBAC, CSRF, quotas and approval | Local accounts; no SSO or per-object authorization. |
| NGINX and Apache | Real Ansible deployment on two allowlisted GNS3 guests, with HTTP verification. |
| Canary, rolling and rollback | Exercised for managed web workloads; not a universal recovery guarantee. |
| VMware Ubuntu OVF import | Create-only workflow documented separately; no update, deletion, rollback or automatic cleanup after an ambiguous failure. Target OS/SSH acceptance requires an authorized real import. |
| ESXi discovery | Separate read-only provider; observed VMs are not proof of a running guest OS. |
| OpenTofu/libvirt and blank-disk vSphere blueprint | Planned/disabled with documented transport and powered-off-create blockers; not promoted by the separate OVF workflow. |
| Redis persistence | Service-specific lifecycle and restore tests; shipped manifest remains planned and runtime disabled pending real target acceptance. |
| Frontend/backend stack, Cisco IOS, pfSense and cloud entries | Planned and disabled; no validated adapter/target path. |
| GitOps | Local approved-plan commits; optional remote publication verifies the exact SHA. Test-repository evidence is not a hosted customer delivery pipeline. |
| FinOps | Internal tariff estimates, not cloud billing or measured consumption. |
| Diagnostics | Deterministic rules; optional LLM provider requires explicit configuration. |
| Notifications and ChatOps | Durable outbox, webhook delivery and signed callback exercised locally; no certified Slack/Teams integration or configured external SaaS recipient. |
| Secrets | Replaceable OpenBao and local Fernet providers; local custody and transport limits apply. |
| Execution credentials | Operation-scoped injection, isolation and cleanup; does not imply existing lab keys were replaced. |
| External API/MCP clients | Explicit role/scopes, expiry and revocation; no automatic approval. |
| Observability | Local metrics and authenticated snapshot; validated rules/dashboard files do not mean a deployed Prometheus/Grafana stack. |
| Public marketplace, payments and untrusted-code isolation | Not provided. |

For the web executor, `disk_gb` is a budgeting declaration, not an enforced disk quota. `public_cidr` is an admission guard, not an installed firewall rule. New cloud or network providers need their own contracts and acceptance evidence.

## Data, secrets and recovery

`data/` is private and ignored by Git. It holds configuration, retained SQLite sources, snapshots, logs, GitOps plans, activations and extension state, including local vault keys and bootstrap files. Current application data uses the dedicated PostgreSQL volume `kw-postgresql-v2-data`: **backing up `data/` alone is insufficient**. Keep PostgreSQL backups and privately back up the vault key, authenticated marker and ciphertext together. Exercise restoration; a readable dump catalogue is not a restore test. Never publish the project directory wholesale without secret review.

The historical remote `kw-openbao` service on `infra-operations` uses persistent encrypted Raft and a local SSH tunnel to guest HTTP `127.0.0.1:18200`. This is not end-to-end TLS. Restart requires unseal; the application token expires. Lab recovery shares and root credentials under one operator account are not production separation of duties. The separate local OpenBao attempt remains blocked with initialized state preserved; see [connections](docs/V2_HYPERVISORS.md). Do not reset or delete that state to bypass recovery.

Plugin data is separate from replaceable code. Disabling a plugin does not delete its state. Interrupted jobs require manual review and are not automatically replayed. Local vault encryption does not protect against compromise of the OS account or root.

## Start and stop

The documented installation uses user units from `ops/`; they are not enabled automatically at boot.

```sh
systemctl --user start kuberweaver.service
systemctl --user status kuberweaver.service kuberweaver-bao-tunnel.service
journalctl --user -u kuberweaver.service -n 80
systemctl --user stop kuberweaver.service kuberweaver-bao-tunnel.service
```

Graceful shutdown waits for the active operation before releasing its lock. Do not run multiple Uvicorn workers against the same state. The PostgreSQL service configuration selects its private URL file explicitly; the historical vault tunnel is a separate operational concern.

Foreground alternative from this directory, with the appropriate private database configuration:

```sh
PYTHONPATH=backend .venv/bin/uvicorn kuberweaver.app:app --host 127.0.0.1 --port 8088
```

Lab targets must be running for deployments and remote OpenBao access. The portal still exposes local capabilities and explicit unavailable states when targets cannot be reached. It does not start all GNS3 nodes automatically.

## Installation and verification

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-execution.txt -r requirements-mcp.txt -r requirements-infrastructure.txt -r requirements-postgresql.txt -r requirements-local-vault.txt
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
```

Run backend tests against disposable state, not the live database:

```sh
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL KUBERWEAVER_STATE_DIR="$state" .venv/bin/python -m pytest tests -q
```

FastAPI serves the frontend build on the same origin. `/healthz`, `/readyz`, `/metrics` and `/docs` are local endpoints. `/healthz` is liveness only; `/readyz` reports the database, the selected secret source and the plugin directory, as described in [health and readiness](docs/HEALTH.md). Metrics describe the process and jobs, not a proven remote monitoring deployment.

`scripts/browser_acceptance.py` and `scripts/api_acceptance.py` have real effects on example resources. Prepare target state and ports before running them; they are not unit tests. Playwright is an optional, separately installed acceptance dependency. Historical evidence is indexed by [verification](docs/VERIFICATION.md); old test counts are not fresh results and passing tests do not establish production maturity.

## Security limits

Do not expose this installation directly to the Internet. It has no SSO, multi-tenancy, per-object permissions, HA or sandboxed marketplace. Protect remote access with an appropriate authenticated HTTPS boundary and a reviewed threat model. The existing unauthenticated GNS3 LAN API is not secured for other clients by this portal. Guest root and workstation administrators remain trusted. Deployment must stay within authorized targets and owned resources; do not change host routes, other GNS3 projects or production resources for acceptance tests.
