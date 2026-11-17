# Implementation contracts

KuberWeaver is a self-hosted infrastructure portal with a FastAPI kernel, durable PostgreSQL or explicitly selected SQLite state, serialized jobs, a React/Vite/React Flow portal and independently registered providers. Ansible handles reviewed web-container lifecycles; specialized provisioning uses its own explicit contract. The control plane is local-only by default. An AI model or paid service is not mandatory.

This document records interfaces, not an implementation task assignment. `README.md`, `EXECUTION.md`, `EXTENSIONS.md` and the current OpenAPI schema define operational scope. Historical acceptance is not a fresh test result.

## Graph and catalogue

Graph fields are `name`, `environment` (`dev`, `staging`, `production`), `nodes` (`id`, `plugin_id`, `label`, `position`, `config`), `edges` (`id`, `source`, `target`), `strategy` (`canary`, `batch_size`) and `failure_mode` (`none`, `canary`, `rolling`). Preserve these identifiers and environment values.

Web-executor defaults include targets `app` and `branch-client`, port 18090, version `1.0.0`, CPU 1, memory 256 MiB, `disk_gb: 2` and `public_cidr: 127.0.0.1/32`. Ports are allowlisted to 18090–18109. Arbitrary hosts, users and commands are not graph fields. Intentional failure injection is restricted to administrators and owned lab resources. Ownership includes graph, environment and node identity.

Declarative discovery uses `plugins/*/manifest.yaml`. Manifests declare `id`, `name`, `version`, `description`, `category`, `icon`, `price_monthly`, `capability` (`live` or `planned`), `engine`, JSON `schema`, `defaults`, `resources`, `security`, `executor` and lifecycle assets as applicable. `GET /api/plugins` returns a flat array. Planned entries cannot execute. Local checks are not marketplace certification. New compatible blueprints need no kernel/plugin-ID branch or React rebuild; a new engine requires a provider, not just metadata.

## HTTP, authentication and governance

All normal portal routes share `/api` and the same origin. `POST /api/auth/login` takes `username`/`password`, returns user metadata, a CSRF token and the session lifetime, and sets an HttpOnly `SameSite=Strict` `kw_session` cookie, `Secure` over HTTPS. Sessions have an 8-hour absolute deadline derived from the token's issue time and a 30-minute idle deadline refreshed on use; both are enforced server-side, an expired session is deleted when presented, and login replaces any cookie it was handed. Browser writes send `X-CSRF-Token`, compared against the token bound to that session. Failed logins are limited per client and account. `GET /api/auth/me` returns the principal and current CSRF token; `POST /api/auth/logout` deletes the session. Accounts use Argon2id hashes, with existing PBKDF2 verifiers upgraded on successful login, and the fixed roles `admin`, `operator`, `viewer`; `USERS.md` defines the strength policy, account administration and bootstrap rotation, and `SESSIONS.md` the cookie, timeouts, CSRF and throttling contract. Bootstrap random passwords belong only in private `data/bootstrap-credentials.txt` (0600), and a rotated account's plaintext line is removed. Machine clients authenticate with a human-issued Bearer token whose closed scope map lives in `agent_auth.required_scope`; scopes never elevate a role, rotation replaces the secret in place without extending `expires_at`, and every authenticated agent request writes one `agent.request` audit row while scope denials write `agent.request_denied`.

| Route | Contract |
| --- | --- |
| `GET /api/overview` | Counts, recent jobs, costs and integrations. |
| `GET /api/infrastructure` | Real GNS3 `project_id`, `server_url`, nodes, links, source and errors; read-only. |
| `GET /api/settings` | Policies, pricing and safe integration metadata. |
| `PATCH /api/settings` | Administrator-controlled policy/pricing changes. |
| `POST /api/plans/validate` | `allowed`, violations, warnings, monthly EUR cost/breakdown and phases. |
| `GET/POST /api/blueprints` | List or save `{name, graph}`; detail at `GET /api/blueprints/{id}`. |
| `POST /api/jobs` | Submit `{graph}`; approval required. |
| `GET /api/jobs` and `GET /api/jobs/{id}` | Persisted jobs, events, result, error, provenance and rollback eligibility. |
| `POST /api/jobs/{id}/approve` or `/reject` | Administrator decision on the exact request. |
| `POST /api/jobs/{id}/rollback` | Administrator-only serialized rollback for eligible successful changes or verified `rollback_failed` journals, never arbitrary failed/interrupted jobs. |
| `GET /api/jobs/{id}/events` | SSE with polling fallback. |
| `GET /api/audit`, `/api/finops`, `/api/diagnostics`, `/api/integrations` | Audit, internal estimates, source-labelled diagnostics and explicit availability. |
| `GET/POST /api/users`, `PATCH /api/users/{id}`, `POST /api/users/{id}/password` | Human-only account administration: list, create, role change and password rotation, with last-administrator protection. |
| `/metrics`, `/healthz`, `/readyz` | Local process/job metrics, liveness, and structured readiness for the database, the selected secret source and the plugin directory. |

Transient statuses include `queued`, `running`, `rolling_back`; terminal outcomes include `succeeded`, `failed`, `rolled_back`, `rollback_failed`, `rejected`. Interrupted work remains visible for review rather than silent retry. Current schemas may include additional states for provider-specific preparation and recovery; use OpenAPI rather than inferring unsupported transitions.

Validation covers schema, dependency DAG, roles, quotas, targets, unsafe public access and planned capabilities. Approved sanitized plans receive actual Git commits. Approval pins graph, provider provenance and executable dependencies, revalidated before mutation. Agent access uses the separate cumulative role/scope contract in `AGENT_ACCESS.md`; an agent never mints its own privileges.

## Execution provider

The Ansible `Executor(state_dir: Path, plugins_dir: Path | None = None)` exposes `execute(job, emit)` and `rollback(job, emit)`. `emit(phase, level, message)` records progress. Results include an honest status, target evidence and actual snapshots. Preflight may fail; restore failure must never be swallowed.

Snapshots distinguish prior absence and verify restored HTTP content, not only process state. Guest mutations are restricted to owned `kw-*` resources and `/opt/kuberweaver`; existing lab SSH configuration is an explicit restricted-file alternative, not proof of vault-based injection. Tests with injected runners are separate from authorized real-target acceptance. Create-only VM providers do not inherit web-service rollback support.

## Extensions and trust

The kernel owns identity/RBAC, admission, durable jobs/audit, approvals, provider selection, serialization and lifecycle dispatch. Inventory, execution, secrets, notifications, diagnostics and blueprints are replaceable capabilities. Python extensions are trusted host code with server privileges, not a sandbox.

`ExtensionRegistry` provides `register`, `get`, `list`, `emit`, `add_hook` and `load_directory`. Duplicate/partial registration fails safely; invalid packages must not break valid peers. A package has `plugin.yaml`, contained paths and a reviewed `register(ctx)` entrypoint. Disabled code is not imported. Installation and activation are distinct; activation requires explicit trust. User configuration and persistent plugin data live outside replaceable package sources.

Canonical hooks are `before_plan`, `after_approval`, `job_started`, `job_finished`. Hooks observe events and cannot remove safety gates. The `lab-notes` example demonstrates a diagnostic provider and event persistence. Static CLI `doctor` checks do not execute untrusted Python or certify packages.

## Operational verification

Use isolated test state, pinned dependencies, frontend tests/build and provider-specific tests. End-to-end acceptance requires a real browser/API and independent target readback where deployment is claimed. Record role/CSRF denial, blocked unsafe graphs, exact Git commits, recovery behavior, persistence and provider selection. Real deployment scripts can mutate targets and must not be run as ordinary unit tests.

Preserve source briefs, existing workloads and other GNS3 projects. Never print secrets or include them in plans, logs or public evidence. No public exposure, automatic destructive retry, universal rollback or production maturity is implied by a passing test suite.
