# Cloudflare secrets and connector contract

`backend/kuberweaver/cloudflare.py` stores Cloudflare API credentials in the
replaceable vault and resolves them by reference at execution time. It makes no
Cloudflare network call: the API origin is the fixed constant
`https://api.cloudflare.com/client/v4`, never a client-supplied endpoint. Live
DNS/tunnel capabilities consume this contract; live use is gated on a real
token provided by the user and stored through the vault write path.

## SOTA choices

### Scoped API tokens only, never global keys

- Accepted shapes (validated before any vault write and again on resolution):
  - Scannable format (2026): `cfut_` (user token) or `cfat_` (account-owned
    token) + 40 alphanumerics + 8 hex checksum characters (53 total). The
    prefix/checksum format is what credential scanners (GitHub secret scanning,
    kingfisher, etc.) detect and Cloudflare auto-revokes on leak.
  - Legacy scoped tokens: exactly 40 alphanumerics.
- Rejected fail-closed: Global API Keys (`cfk_...` scannable, or 37-45 lowercase
  hex legacy), Origin CA keys (`v1.0-...`), Wrangler OAuth tokens (`cfoat_...`),
  and a pure lowercase-hex 40-char string, which is ambiguous with the legacy
  global-key shape. A legitimate legacy token that happens to be all lowercase
  hex is astronomically unlikely ((16/62)^40); the ambiguity resolves to refusal.
- Shape validation is a sanity gate, not proof of validity or of grants. The
  authoritative check is `GET /user/tokens/verify` against the live API, which
  belongs to the live DNS/tunnel capabilities, not to this contract. The
  checksum algorithm is not implemented here (scanner-side concern).
- Account-owned tokens (`cfat_`) are preferred for services: they are not tied
  to a user's lifecycle. Both scoped kinds are accepted.

### Least privilege per purpose

`SCOPE_PROFILES` records the exact permission groups an operator must grant,
scoped to the specific zone/account (never `*`):

| Purpose | Policy scope | Permission | Access |
| --- | --- | --- | --- |
| dns | Zone | Zone | Read |
| dns | Zone | DNS | Edit |
| tunnel | Account | Cloudflare Tunnel | Edit |
| tunnel | Zone | Zone | Read |
| tunnel | Zone | DNS | Edit |

The platform cannot inspect a token's grants offline; the connector's `purpose`
documents the intended contract and the operator creates the token with exactly
those permissions. One connector owns one `(secrets_provider, secret_ref)` pair:
references are unique across Cloudflare connectors, so a DNS token is never
silently reused for tunnel operations.

### Reference-only core, resolution at execution

- The connector record (`cloudflare_connectors` table, registered in the
  PostgreSQL COLUMNS/DDL/ORDERED allowlists) holds only `label`, `purpose`,
  `secret_ref`, `secrets_provider`, optional `account_id` (32-hex identifier,
  not a secret), `enabled`, `id`, `revision`. Closed strict Pydantic models
  reject any secret-bearing or endpoint field.
- The vault value is a closed JSON envelope `{"api_token": "..."}` with exactly
  one key (duplicate keys rejected). Legacy `username/password` or
  `auth_email/auth_key` material fails closed on resolution.
- `access()` resolves through the selected `SecretProvider` on every use: no
  persistent secret cache, `SecretValue` lifetime wipe on exit, sanitized
  static errors. Disabled connectors are refused before any resolution.
- `fingerprint()` pins the reference fields, revision and secrets-provider
  source provenance; it never resolves or hashes secret values. A credential
  rotation bumps the revision, invalidating any earlier approval digest.

### Encryption at rest (provider choice, same contract)

- `local-vault` (opt-in extension): Fernet (AES-128-CBC + HMAC-SHA256) in a
  0700 directory, 0600 key/ciphertext, descriptor-pinned IO, atomic durable
  writes with exact readback. The key sits beside the ciphertext: this does
  not protect against compromise of the owning OS account/root. Back up both
  privately. Suitable for the single-admin lab trust boundary.
- `openbao` (KV v2): TLS-verified transport (explicit numeric-loopback HTTP lab
  exception only), encrypted Raft backend, sealed after restart, least-privilege
  app token reloaded on every resolution. Writes require an explicitly writable
  provider configuration (`allow_write=True`); the default lab app policy is
  read-only, so lab writes use `local-vault` unless a write-scoped OpenBao token
  is deliberately provisioned.
- `restricted-file` remains read-only and is never a write target.

### Rotation

1. Roll/replace the token in the Cloudflare dashboard (or API) with the same
   least-privilege profile; prefer the scannable format.
2. `PUT /api/cloudflare/connectors/{id}/credentials` with the new token
   (human administrator session only). The provider write path validates shape
   before writing and owns the exact readback; the old ciphertext is replaced
   atomically.
3. The connector revision bumps; prior fingerprints/approvals are stale.
   Consumers re-resolve on next use, so no restart or cache flush is needed.
4. Revoke the old token in Cloudflare once the replacement is verified live.
   Vault-side deletion of the old material is inherent to the overwrite; there
   is no versioned secret history in the local provider (OpenBao KV v2 keeps
   versions under its own retention policy).

### No secrets in plans, logs, responses or Git

- Credential ingestion is a human-administrator workflow: agents are refused
  even with `configure` scope (no agent scope mapping exists for the PUT, and
  the handler re-checks the principal type). Operators/viewers are refused by
  the role gate; CSRF/Origin guards apply.
- Responses return only reference records and `{"stored": true, ...}`. Audit
  details are empty objects. Errors are static sanitized strings; upstream
  exception text never crosses the boundary.
- Tests scan persisted rows, audit tables, API responses, provider `describe()`
  and full tracebacks for the fixture token material.

## API surface

- `GET /api/cloudflare/connectors`, `GET /api/cloudflare/connectors/{id}` (any
  authenticated role; agents need `read:infrastructure`).
- `POST /api/cloudflare/connectors`, `PATCH /api/cloudflare/connectors/{id}`
  (admin; agents need `configure`; PATCH is revision-gated).
- `PUT /api/cloudflare/connectors/{id}/credentials` (human admin only), body
  `{"api_token": "<scoped token>"}`.

## Tests

```sh
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_cloudflare_secrets.py -q
```

Covers token/envelope shapes, closed reference-only specs, real local-vault and
loopback OpenBao write/readback/rotation, execution-time resolution lifecycle,
human-only API writes, leakage scans, PostgreSQL isolated-schema creation and
controller reopen. All token values are disposable fixtures; no test contacts
Cloudflare.