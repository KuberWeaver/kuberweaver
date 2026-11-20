# Cloudflare DNS capability

`backend/kuberweaver/cloudflare_dns.py` delivers DNS record CRUD on top of the
secrets/connector contract (`CLOUDFLARE_SECRETS.md`). The API origin stays the
fixed constant `https://api.cloudflare.com/client/v4`; no client-supplied
endpoint exists. Live use is gated on a real scoped token provided by the user
and stored through the vault write path (`PUT /api/cloudflare/connectors/{id}/credentials`,
human administrators only). No real token is used in tests: all Cloudflare
traffic in the suite goes to an in-process `httpx.MockTransport` fake.

## Governance: the same approval/audit model as deployments

- Reads (`GET /api/cloudflare/dns/zones`, `GET /api/cloudflare/dns/records`)
  need an authenticated session, are audited (`cloudflare_dns.zones_listed`,
  `cloudflare_dns.records_listed`) and never need approval, like every other
  platform read. Responses are paginated (bounded at 50 pages, `truncated`
  flag) and allowlisted to public record fields.
- Mutations are durable requests: `POST /api/cloudflare/dns/requests`
  (admin/operator, agent scope `submit`) lands in `pending_approval` and makes
  no Cloudflare call. The request pins the connector fingerprint (references +
  provider provenance + revision) and the canonical payload digest.
- `POST .../approve` (admin role; agents only with the explicit human-granted
  `approve` scope, exactly like job approval) re-verifies both pins, fails
  closed on any drift (connector edit, credential rotation, disable), checks
  credential resolvability, records a durable `executing` claim, then resolves
  the token from the vault at execution time and executes exactly the approved
  payload. An interrupted process leaves the claim visible, never a silent
  re-run. `POST .../reject` terminates a pending request.
- Every transition is audited: `cloudflare_dns.requested/approved/rejected/
  succeeded/failed`. Audit details carry only operation/zone/count facts.

## Live token gate

Before any mutation the token must verify as `active` against
`GET /user/tokens/verify` (user tokens) or
`GET /accounts/{account_id}/tokens/verify` when the connector carries an
`account_id` (account-owned tokens). Inactive, expired or unverifiable tokens
fail the request closed. Create sends `POST /zones/{zone_id}/dns_records`,
update sends `PATCH /zones/{zone_id}/dns_records/{record_id}`, delete sends
`DELETE` on the same path.

## Secret hygiene

- Closed pydantic schemas (`extra='forbid'`, strict): record type/name/content/
  ttl/proxied/priority with per-type rules; secret-shaped fields are refused.
- Upstream evidence is allowlisted, length-bounded, and redacted against the
  resolved token before persistence; `CloudflareApiError` messages are generic
  (details ride in a separate attribute, never in str/repr/tracebacks).
- The token travels only in the `Authorization` header of the fixed-origin
  client (`trust_env=False`, no redirects); it never appears in URLs, request
  rows, audit, logs or responses. Tests scan persisted rows, audit, responses,
  tracebacks and captured logs for the fixture token.

## CLI

`kw cloudflare zones|records|request|requests|get|approve|reject` — a thin
client over the same routes, roles and scopes. `request` takes `--operation
create|update|delete`, `--record-file`/`--record-json` and `--record-id`.

## Storage

`cloudflare_dns_requests(id, body)` is registered in the PostgreSQL contract
(`COLUMNS`/`DDL`/`ORDERED`); SQLite is used only by isolated tests. The
PostgreSQL lifecycle (create, execute, reopen) is covered by an isolated-schema
test against the dedicated test DSN.
