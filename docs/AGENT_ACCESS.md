# External agent access

KuberWeaver is controlled by external API/MCP clients; service tokens do not embed a model and none are automatically created. Bind to loopback; remote agents use a protected local tunnel. Host, Origin, cross-site and 1 MiB request-body checks remain enabled.

## Human administrator workflow / exact API contract

Only a logged-in **human admin browser session**, with its normal `kw_session` cookie and `X-CSRF-Token`, may manage credentials. Agent tokens, including admin tokens, cannot issue, list or revoke credentials. Never send cookie and Authorization together.

`POST /api/agent-tokens` (201):

```json
{"label":"deploy-controller","role":"operator","scopes":["read","plan","submit"],"expires_in_seconds":3600}
```

Returns `{"token":"<shown once>","metadata":{"id":"<32 hex>","label":"deploy-controller","role":"operator","scopes":["read","plan","submit"],"expires_at":1234567890.0,"revoked_at":null}}`.

`GET /api/agent-tokens` returns `{"tokens":[<metadata>],"available_scopes":[...],"role_scopes":{"viewer":[...],"operator":[...],"admin":[...]}}`.

`DELETE /api/agent-tokens/{id}` returns the safe metadata directly, with `revoked_at` set to an epoch timestamp. Revocation is idempotent; unknown IDs return 404. Revoked entries remain visible in GET.

Role and integer lifetime are required. Lifetime is 60–2592000 seconds (30 days). Scopes default to `["read"]`, **never approve**, even for admin. Empty, unknown, duplicated or role-incompatible scopes are rejected (422). Labels are 1–80 printable ASCII characters, start alphanumeric, and may subsequently contain spaces, `. _ : @ -`. No unknown JSON fields are accepted.

## Machine HTTP contract

Use the `Authorization` header with a Bearer token alone, without session cookies or a CSRF header. `GET /api/auth/me` returns `{"user":{"id":"agent:<id>","username":"agent:<id>","role":"operator","label":"deploy-controller","principal_type":"agent","token_id":"<id>","scopes":[...]},"csrf_token":null}`. Stable `agent:<id>` actors distinguish machine audit records from human accounts; the label is descriptive and cannot spoof actor identity.

Invalid, expired, revoked, malformed, duplicate Authorization or mixed session/Bearer credentials return 401 and never fall back to browser authentication. Missing scope or unrecognized route/method returns 403. Normal endpoint role dependencies and approval/admission/provenance checks still run after scope validation. No agent gets automatic approval.

### Closed scope map

| Scope | Routes | Eligible roles |
|---|---|---|
| read | All business reads allowed by the role; deliberately broad access | all |
| read:finops | GET /api/finops only | all |
| read:jobs | GET /api/jobs and job details/events | all |
| read:catalog | GET /api/plugins and /openapi.json | all |
| read:settings | GET /api/settings, policies/pricing and integration metadata | all |
| read:infrastructure | GET /api/infrastructure | all |
| read:integrations | GET /api/integrations, /api/providers, /api/extensions | all |
| read:diagnostics | GET /api/diagnostics | all |
| read:blueprints | GET /api/blueprints and blueprint details | all |
| read:audit | GET /api/audit | all |
| read:overview | GET /api/overview, aggregate view including costs and recent jobs | all |
| read:operations | GET /api/operations, /api/operations/notifications and /metrics; operations require admin role | all, subject to route role restrictions |
| plan | POST /api/plans/validate | all |
| submit | POST /api/jobs, /api/blueprints | operator/admin |
| approve | POST /api/jobs/{id}/approve or /reject | admin only |
| rollback | POST /api/jobs/{id}/rollback | admin only |
| configure | PATCH /api/settings, /api/providers | admin only |
| diagnose | POST /api/diagnostics/run | operator/admin |
| notify | POST /api/operations/notifications/retry | admin only |
| extensions | POST /api/extensions/{id}/enable or /disable | admin only |
| chatops | POST /api/chatops/actions | admin only |

Strict FinOps example: `{"label":"finops-reader","role":"viewer","scopes":["read:finops"],"expires_in_seconds":3600}`. Do not add `read` or `read:overview`: they deliberately broaden access. This token reads costs but receives 403 for jobs, infrastructure, audit and all mutations. Agent access does not automatically select any scope.

`GET /api/auth/me`, `/healthz` and `/readyz` accept a valid token without an additional scope. SSE streams revalidate expiry, revocation and `read:jobs`/`read` on every iteration.

Existing endpoints may require stricter roles (e.g. operations is admin-only); scopes never elevate roles. Future endpoints must be added explicitly to `agent_auth.required_scope`, with HTTP tests. Query parameters do not change authority; unsupported methods and trailing aliases fail closed.

Account administration (`/api/users`, role changes and password rotation) is human-only in the same way: no agent scope maps to those routes, so even an admin token receives `403`. See `USERS.md`.

ChatOps actions still require their separately signed human-mapped gateway callback. The sole browser-CSRF exemption is **POST /api/chatops/callback**; its adapter performs signature/replay/actor validation. Bearer credentials do not authorize that callback and cannot bypass its authentication.

## Client credential handling

Store the one-time value in a user-owned 0600 file inside a 0700 directory, never localStorage, command arguments, logs, URLs, examples, or tool results. A CLI/MCP client reads that file and attaches the Bearer header to each request, maintaining no cookie jar. Do not expose token-management tools to agents. Prefer viewer/read for inspection and operator/read+plan+submit for deployment proposals. Only deliberately issued admin+approve tokens can approve through the API. Rotate by issuing replacement, validating `/api/auth/me`, switching clients, then revoking the old ID and reading back its metadata.

The dedicated `agent_tokens` table in the selected durable store contains only a SHA-256 digest of 256-bit random credentials. Metadata/list/audit contain no clear token or digest. Private state uses 0700/0600 permissions; in SQLite mode this also applies to DB/WAL/SHM files. Revocation/expiry are checked against the selected store on every new authenticated request, including after restart. See `POSTGRESQL.md` for the documented local backend selection.

## Background services

`app.state.background_services` is initialized before API installation. Registered synchronous `start()`/`close()` services start after the worker; shutdown closes the worker first, then successfully started services in reverse order. Partial startup failure cleans up only completed starts; a service whose start fails must clean up its own partial resources. Cleanup continues if another close raises.
