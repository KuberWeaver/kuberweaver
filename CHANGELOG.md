# Changelog

All notable changes to this project are recorded in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

No version has been tagged yet, so `Unreleased` collects everything that is on
`develop` and not yet published.

## [Unreleased]

### Added

- Community governance: `CONTRIBUTING.md` rewritten for external contributors,
  `SECURITY.md`, `SUPPORT.md`, `CODEOWNERS`, GitHub issue templates for bugs,
  feature requests and plugin submissions, and a pull request template.
- Repository licence: GPL-3.0-or-later, with `COMMERCIAL-LICENSE.md` describing
  when a commercial agreement is required.
- Static showcase site under `site/`, served by any static host and buildable
  offline. It reproduces the README capability boundaries verbatim.
- `docs/DISCORD.md`: the intended community server structure, to be created by
  an account holder (see SUPPORT.md for the current invite status).
- `docs/AGENT_GUIDE.md`: the contract for a coding agent working in this
  repository, with the checks, the commit rules and the refusal paths. A root
  `AGENTS.md` would be the canonical location; it is not committed here.
- `docs/README.md`: index of every documentation page, separating instructions
  from internal notes and from school artifacts kept for provenance.
- `docs/AUTHORING.md`: the shortest path to add a brick or an extension, with the
  exact fields the workload provider checks before touching a guest.
- `docs/kw-cli-usage.md` rewritten in English, so the README rule that public
  documentation is in English holds on the most-read CLI page.
- `docs/README.md`, `docs/AUTHORING.md` and one `README.md` per extension close the
  "where do I put this" question; every brick and every extension now documents its
  own contract.
- `scripts/release.py` cuts releases from `main`, not from the stale `develop`
  branch. `CONTRIBUTING.md`, `ops/autopilot/RULES.md` and the release-script tests
  follow: pull requests target `main`.
- `pyproject.toml` declares `license = GPL-3.0-or-later` and
  `requires-python = ">=3.14"`, matching the pinned requirements and CI.
- `pyrightconfig.json` removed: it referenced a type checker that is neither
  installed nor run, so it advertised a check that does not exist.
- `.gitignore` drops the rules for the removed React portal (`frontend/dist`,
  `node_modules`, `*.tsbuildinfo`).
- `docs/EXECUTION.md` separates the current `ansible-workload-v1` contract from the
  older `ansible-container-v1` prototype path used by two demonstration entries.
- `docs/CLEANUP.md` marked as an internal note: its list of entries to remove
  contradicts the public capability table.
- Labels for contribution triage: `plugin`, `brick`, `extension`, `kernel`,
  `cli`, `ci`, `security`, `needs-review`, `triage`.

- Kernel with identity, admission, approvals, durable state, audit and serialized orchestration, exposed over HTTP and MCP.

- Provider contracts for inventory, execution, secrets, notifications and diagnostics, with ESXi discovery and a VMware OVF deployment path.
- Managed SSH keys: create, select, disable and explicit private-key export, with guest delivery through `guestinfo` and cloud-init.
- PostgreSQL store and a local encrypted vault, with SQLite retained for development and tests.
- Scoped, expiring and revocable agent tokens, plus an MCP server that exposes typed domain tools instead of arbitrary commands.
- Read-only observability snapshot for jobs, worker queue and storage health.
- Autopilot loop on `feat/production-track`: bounded iterations, backlog in `ops/autopilot/STATE.json`, contract in `ops/autopilot/RULES.md`.
- CI workflow covering the backend suite, a secret scan over tracked files and history, and repository hygiene checks.
- Contributor guide and release tooling: `CONTRIBUTING.md` and `scripts/release.py`.
- Environment-based configuration for the state, plugins and OpenSSH paths, with drift-free defaults, absolute-path validation and a refusal that names the missing variable.
- Structured readiness at `GET /readyz` for the database, the selected secret source and the plugin directory, with `/healthz` kept as liveness only.
- Argon2id password storage, a documented strength policy, administrator-managed accounts with fixed roles, and single-use bootstrap credentials that are retired when an account rotates its password; see `docs/USERS.md`.
- Session hardening with an HttpOnly `SameSite=Strict` `kw_session` cookie, an 8-hour absolute deadline derived from the token issue time, a 30-minute idle deadline refreshed on use, login that replaces a presented session, and a per-session CSRF token; see `docs/SESSIONS.md`.
- In-place rotation of agent tokens (`POST /api/agent-tokens/{id}/rotate`), which replaces the secret without changing the identity, role, scopes or expiry, and a per-request audit row for every authenticated machine call; see `docs/AGENT_ACCESS.md`.
- Optional OpenID Connect sign-in behind `KUBERWEAVER_OIDC_ISSUER`, disabled by default: discovery, S256 PKCE, single-use state and nonce, JWKS signature checks, and binding of an external subject to an existing local account whose role stays authoritative; see `docs/OIDC.md`.
- Black and light themes over one token layer: every colour is a CSS variable, the stored preference is applied by a blocking boot script before the first paint, and the toggle in the top bar and on the login card follows a switch made in another tab; see `docs/THEMING.md`.
- A single page registry drives the grouped sidebar, breadcrumbs, page headings and role gating, with named loading states, empty states and a `Page not found` fallback; see `docs/NAVIGATION.md`.
- External dashboard registry: Grafana, Uptime Kuma, Prometheus, Netdata and custom links with a kind, a default health probe path and an optional embed flag that is accepted only for an origin on `KUBERWEAVER_DASHBOARD_EMBED_ORIGINS`; see `docs/DASHBOARDS.md`.
- Embed page for a registered dashboard (`GET /api/dashboards/{id}/embed`): it frames the recorded URL only for an enabled record on the origin allowlist, with `frame-src` derived from that one origin, `frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN`; otherwise it serves the deep link with `frame-src 'none'` and states the reason. Every other response keeps `frame-ancestors 'none'`.
- Read-only dashboard health probe: every enabled record is observed once per interval on its own origin and probe path, with no credential, no redirect and a 64 KiB budget; the result is stored in `dashboard_health` and surfaced as a `health` status column (`healthy`, `unhealthy`, `unreachable`, `malformed`, `unknown`, `disabled`) with a `drift` flag, an administrator-only `POST /api/dashboards/{id}/probe`, and no automatic remediation; see `docs/DASHBOARDS.md`.
- Durable environment and stack state: one record per `(environment, stack)` with a monotonic generation, the applied resource list and an encrypted document; an exclusive claim refuses a concurrent apply, a stale generation cannot commit, and an interrupted apply stays claimed until an administrator releases the lease explicitly; see `docs/ENVIRONMENT_STATE.md`.
- Adoption of an existing VM into a stack that was never applied: an administrator submits the UUID and the `kuberweaver-job:` marker the portal wrote into the VM annotation, the ESXi extension reports that annotation only as a SHA-256 digest, and the store records generation 1 without creating or changing the VM. A marker mismatch, foreign UUID, held lease, applied stack or the same UUID under another stack is refused, and a repeated request keeps the generation; see `docs/ENVIRONMENT_STATE.md`.
- Destruction of an owned VM behind the same annotation and UUID proof: an administrator opens a 15-minute quarantine, can cancel it before execution, and execution re-reads the VM before calling a write-capable driver. Success commits an emptied generation with `source: destroy`; a refused proof, a missing driver capability or an unconfirmed state write is recorded as `failed` or `rollback_failed` with `requires_manual_review`, and only an administrator's observed `intact` or `destroyed` resolution closes the latter. An apply cannot claim a stack whose destruction is quarantined or executing; see `docs/ENVIRONMENT_STATE.md`.
- Drift reports: a nightly read-only comparison of every recorded declaration against the live hypervisor, one report per state record with a per-field diff (`uuid`, `name`, `power_state`, `cpu`, `memory_mb`), explicit `in_sync`, `drift`, `unverified`, `unsupported`, `empty` and `unavailable` statuses, an administrator-only `POST /api/environment-states/drift/run`, and no remediation: the only driver call is `read_resource`; see `docs/DRIFT.md`.
- Git-backed declarations: a bundle committed by CI pins one commit, one declaration file and one saved OpenTofu plan by SHA-256; `POST /api/declarations/verify` reads those blobs from the repository's object database at that commit and never from the working tree, and `POST /api/declarations/execute` applies the exact plan file as-is behind the stack's state claim, a `base_generation` check and one recorded generation, with no `init`, no new plan and no automatic retry; see `docs/DECLARATIONS.md`.
- Workload bricks in the portal: a one-node workload job is prepared from the brick contract and the deployment evidence a succeeded provisioning job recorded, approved as an exact saved plan, and executed with the `snapshot`, `apply` and `verify` lifecycle, with the declared assertions in the job result and private evidence in `0600` files; an unpinned role, a drifted role lock, a missing or ambiguous target or a disabled key refuses preparation; see `docs/WORKLOADS.md`.
- The Zabbix 7 preset and its four bricks: `db-postgresql`, `zabbix-server`, `zabbix-web` and `zabbix-agent` wrap pinned upstream roles, declare their assertions and data class, and compose on one guest as a dependency-ordered preset; `scripts/validate_preset.py` validates the preset against the catalogue and admission policy and prints one complete one-node job per step, so a multi-brick stack needs no second execution path; see `docs/PRESETS.md`.
- Service retirement, separate from rollback: an administrator's explicit `POST /api/studio/services/{job_id}/decommission` only queues a succeeded service request on the existing serialized worker, the job's pinned execution provider must itself declare `decommission(job, emit)` and verify `{'status': 'decommissioned'}`, and a refusal, unverified result or process interruption is persisted as `decommission_failed` with `requires_manual_review` and is never retried as a rollback; see `docs/SERVICE_STUDIO.md`.

### Removed

- The React/TypeScript portal and its acceptance scripts (`frontend/`,
  `scripts/browser_acceptance.py`, `scripts/browser_final.py`,
  `scripts/modularity_acceptance.py`). The HTTP API, the `kw` CLI and the MCP
  interface remain; a modular GUI can be built on the same API. `docs/THEMING.md`
  and `docs/NAVIGATION.md` are kept as historical records of that interface.
- `pyrightconfig.json`, which referenced a type checker that is neither installed
  nor run.
- The browser acceptance leftovers: `tests/test_resource_workspace_browser.py`,
  `tests/test_v2_real_protocol.py`, `requirements-acceptance.txt` and
  `scripts/browser_{acceptance,final}.py`, `scripts/modularity_acceptance.py`.
  They drove the removed portal, and on a machine with Chromium they failed
  instead of skipping, because CI does not install `requirements-acceptance.txt`.

### Fixed

- Seventeen release-script tests were pinned to `develop` and failed once releases
  moved to `main`.
- The README and the showcase page contradicted the implemented OIDC support: both
  claimed there was no SSO at all. They now state that an optional external OpenID
  Connect sign-in exists and stays off unless configured.
- `LICENSE` carried the GPLv3 text without a notice, so it read as GPL-3.0-only while
  every other document announced `GPL-3.0-or-later`. It now opens with the copyright
  and the "version 3, or any later version" grant.

### Security

- The GNS3 inventory provider no longer carries a compiled-in lab address. It reads
  `KUBERWEAVER_GNS3_URL` and `KUBERWEAVER_GNS3_PROJECT`, and refuses with a named
  variable in the error while either is unset. The project UUID disappeared from
  `docs/ACCEPTANCE.md`, and the SSH fixture uses a documentation address
  (`192.0.2.10`) instead of a private one.
- `SECURITY.md` no longer sends reporters to a "Report a vulnerability" button that
  does not exist on a private repository, and the issue-template contact link stops
  pointing at an advisory form that only public repositories can use.
- Passwords and tokens are never returned in list responses, and the secret scan blocks key material and provider tokens from entering the repository.
- Legacy PBKDF2 password verifiers are upgraded to Argon2id on the next successful login; self-service rotation revokes every existing session and reissues only the caller's.
- Expired or revoked sessions are deleted when presented and the stale cookie is cleared; tokens are stored as SHA-256 hashes, never in clear. Failed logins are limited to 10 per client and account and 30 per client per minute, answered with `429` and `Retry-After`, and unknown accounts are counted like wrong passwords.
