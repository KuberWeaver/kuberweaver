# Changelog

All notable changes to this project are recorded in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

No version has been tagged yet, so `Unreleased` collects everything that is on
`develop` and not yet published.

## [Unreleased]

### Added

- Kernel with identity, admission, approvals, durable state, audit and serialized orchestration, exposed over HTTP and MCP.
- React/TypeScript portal with a visual service catalogue, blueprint composition, approvals and deployment evidence.
- Provider contracts for inventory, execution, secrets, notifications and diagnostics, with ESXi discovery and a VMware OVF deployment path.
- Managed SSH keys: create, select, disable and explicit private-key export, with guest delivery through `guestinfo` and cloud-init.
- PostgreSQL store and a local encrypted vault, with SQLite retained for development and tests.
- Scoped, expiring and revocable agent tokens, plus an MCP server that exposes typed domain tools instead of arbitrary commands.
- Read-only observability snapshot for jobs, worker queue and storage health.
- Autopilot loop on `feat/production-track`: bounded iterations, backlog in `ops/autopilot/STATE.json`, contract in `ops/autopilot/RULES.md`.
- CI workflow covering the backend suite, frontend typecheck/test/build, a secret scan over tracked files and history, and repository hygiene checks.
- Contributor guide and release tooling: `CONTRIBUTING.md` and `scripts/release.py`.
- Environment-based configuration for the state, plugins, frontend and OpenSSH paths, with drift-free defaults, absolute-path validation and a refusal that names the missing variable.
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

### Security

- Passwords and tokens are never returned in list responses, and the secret scan blocks key material and provider tokens from entering the repository.
- Legacy PBKDF2 password verifiers are upgraded to Argon2id on the next successful login; self-service rotation revokes every existing session and reissues only the caller's.
- Expired or revoked sessions are deleted when presented and the stale cookie is cleared; tokens are stored as SHA-256 hashes, never in clear. Failed logins are limited to 10 per client and account and 30 per client per minute, answered with `429` and `Retry-After`, and unknown accounts are counted like wrong passwords.
