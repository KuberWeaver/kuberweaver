# Generic signed-webhook ChatOps adapter

This is **not Slack/Teams platform authentication or a compliance claim**. It is
an optional local signed adapter for an explicitly configured, trusted gateway.
No outbound message is sent by these routes. A platform-specific gateway must
verify the platform signature, tenant/workspace, interaction context and actual
user identity before constructing a callback. Never trust an actor supplied by
an arbitrary browser or forward an unverified platform payload.

## Installation and trust boundary

Call `install_chatops_api(app)` after the jobs service and role dependencies are
installed. The application security middleware must exempt **only POST
`/api/chatops/callback`** from session/CSRF checks. Do not exempt a prefix, the
issue route, GET requests, trailing-slash variants, or any normal jobs route.
Keep local-host, size, origin and security-response-header checks. Gateway
requests should omit browser Origin headers and target the allowed local host.
The callback authenticates itself independently; a session or bearer action ID
is neither required nor sufficient. Do not expose the whole local application
to the internet. Use a narrow gateway/transport boundary with TLS and protected
key distribution. Run the existing single application process/worker per store;
the adapter uses its shared reentrant store lock, not a multi-process lock.

## Administrator-only local configuration

`data/config/chatops.json` (relative to the selected state directory) contains
exactly these fields; unknown fields, wrong types, missing file, disabled config
and invalid data fail closed with `503 ChatOps unavailable`:

```json
{
  "schema_version": 1,
  "enabled": true,
  "secret_ref": "chatops/signing-key",
  "actors": {"gateway:tenant-123:user-456": "admin"},
  "action_ttl_seconds": 300,
  "max_age_seconds": 60
}
```

The version must be integer 1 (not boolean). TTL is an integer 1–3600;
freshness is an integer 1–300 seconds. Configuration is capped at 64 KiB.
`actors` is a nonempty map with at most 1000 exact, case-sensitive external
identifiers (1–200 characters) to local usernames (1–80 characters). Use stable,
tenant-qualified IDs, never mutable display names. The mapped account must
exist **and currently have role admin on every callback**. Account deletion,
role revocation, mapping removal, and disabling configuration take effect on
subsequent requests without restarting. Configuration is administrator-managed
local state, not accepted via these APIs.

The HMAC key is resolved per callback through the **currently selected secrets
provider** using `secret_ref`, never an inline key. References follow the secret
provider's logical-name grammar (lowercase segments, maximum 128 characters).
Configure `config/secrets.json`/provider bindings separately. Restricted-file
keys require the existing provider's owner-only 0600, no-symlink restrictions.
Use random key bytes, at least 32 and at most 4096 bytes; bytes including any
newline are used literally. Provider failures and invalid keys return the same
sanitized 503. Rotate the provider reference/key and gateway together; no old-key
fallback exists. No raw key, provider error, callback body, signature, or external
actor is written into jobs, events or audit records by this adapter.

## Issuing an action / frontend hook

Authenticated administrators POST `/api/chatops/actions`, with the normal
`kw_session` cookie and `X-CSRF-Token` header. Exact JSON schema:

```json
{"job_id":"32-lowercase-hex-job-id","decision":"approve"}
```

`decision` is exactly `approve` or `reject`; the actual job ID must be 32
lowercase hexadecimal characters. Only pending, non-manual-review jobs are
eligible. Response 201 has exactly:

```json
{
  "schema_version": 1,
  "action_id": "64-lowercase-hex-random-id",
  "job_id": "32-lowercase-hex-job-id",
  "graph_digest": "64-lowercase-hex-sha256",
  "decision": "approve",
  "expires_at": 1790000300,
  "callback_path": "/api/chatops/callback"
}
```

The descriptor includes no graph, secret or precomputed signature. It is safe
to pass to a configured gateway; it is **not a bearer approval token**.
`graph_digest` uses the existing Jobs canonical digest: SHA-256 of UTF-8
`json.dumps(graph, sort_keys=True, separators=(',', ':'), allow_nan=False)`.
`expires_at` is Unix seconds. Decision/job/digest/expiry are persisted server
side and cannot be overridden by callback fields.

Frontend integration: offer an admin-only “Create ChatOps approval/rejection”
control on pending-job detail; POST the schema above using existing authenticated
fetch/CSRF handling; display/copy the descriptor to a trusted gateway. Do not
sign in JavaScript, expose a signing key to the browser, auto-approve on issue,
or treat issuance as deployment completion. Poll the normal authenticated
`GET /api/jobs/{id}` (or use existing events) for real deployment outcome.

## Callback wire protocol v1

POST `/api/chatops/callback`, `Content-Type: application/json`. Exact body:

```json
{"schema_version":1,"action_id":"64-lowercase-hex-id","actor":"gateway:tenant-123:user-456","nonce":"unique-random-base64url-value"}
```

No extra/missing fields, duplicate JSON object keys, nonfinite numbers, coerced
types, or non-object bodies are accepted. Body cap is **4096 bytes**, enforced
while streaming even without Content-Length. Nonce is 20–128 ASCII letters,
digits, `_` or `-`; generate at least 128 bits of random entropy. Actor is 1–200
characters and must exactly match the configured map. Version is integer 1.

Exactly one of each header is required:

* `X-KW-Timestamp`: Unix seconds, decimal ASCII, 10–12 digits, no leading zero,
  sign, whitespace or fraction. Absolute difference from server time must not
  exceed configured freshness (future timestamps are bounded too).
* `X-KW-Signature`: `v1=` followed by 64 lowercase hexadecimal characters.

Compute HMAC-SHA256 over the **exact raw HTTP entity body**, without JSON
normalization or decoding/reencoding:

```python
body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
timestamp = str(int(time.time()))
signed_bytes = b'v1.' + timestamp.encode('ascii') + b'.' + body
signature = 'v1=' + hmac.new(key_bytes, signed_bytes, hashlib.sha256).hexdigest()
# Send body unchanged, timestamp and signature as the headers above.
```

The fixed version prefix separates this protocol from other HMAC consumers;
provision a dedicated ChatOps signing key, not a shared key for unrelated APIs.
Comparison is constant-time. Whitespace changes after signing invalidate the
signature. Use a fresh nonce for each new decision attempt; never sign arbitrary
user-selected callbacks. Gateway retries must understand the semantics below.

## Durable state, errors and failure semantics

Actions and hashed nonce keys are durably stored in the existing SQLite settings
table under `chatops.action.*` / `chatops.nonce.*`, inaccessible through normal
settings responses. Nonce scope is global to this store, including across
actors, actions and key rotations. Records are intentionally retained across
restarts, with no automatic pruning. Administrators should account for growth;
do not delete replay records while previously issued requests could be valid.

Under the shared store lock the adapter rechecks current admin authority,
action state/expiry, pending job state/manual-review flag, and graph digest.
It persists the nonce and marks the action `inflight` **before** invoking the
actual `app.state.jobs.approve` or `.reject`. All normal admission checks,
provenance pinning, local GitOps commits, required remote GitOps and serialized
worker revalidation remain in that path. There is no alternate executor or
policy bypass. The callback releases its outer store lock before invoking Jobs
approval, so real remote push/readback never inherits that lock. Jobs compares
the action's reviewed graph digest again while taking its durable per-job
approval claim, closing the gap between action reservation and job reservation.
Rejection rechecks the graph under the store lock immediately before rejecting.

* Successful decision consumes the action permanently. Other descriptors for
  the same job cannot decide it again because it is no longer pending.
* A known failed approval (e.g. changed policy or required remote failure)
  returns its failing HTTP status with sanitized `ChatOps decision failed`.
  The action returns to `ready` **only if the durable job is still pending and
  does not require manual review**;
  its nonce remains spent. After correcting the failure, retry the same action
  with a **new signed nonce**, before expiry. Never interpret 503 as approval.
* If an exception occurs after the job has left pending, the action stays
  consumed. Unexpected exceptions return sanitized 503, not fabricated success.
* Process death during `inflight` leaves it blocked on restart, never silently
  reset or replayed. Inspect the durable job and target state manually; if still
  safe/pending without a Jobs manual-review claim, issue a new descriptor after
  review rather than altering replay records. An interrupted Jobs publication
  claim stays blocked; a fresh descriptor does not clear it. Nonce insertion and action-state writes are separate durable writes;
  death between them may spend one nonce without invoking Jobs. A fresh nonce
  can retry a still-ready action. This favors non-replay over automatic recovery.
* Callback 200 contains only `{ "job_id": "...", "status": "queued" }` for
  approval or status `rejected` for rejection. **Queued is not deployed**.
  A later worker failure is durable in the normal job/event API; the consumed
  action cannot rerun it. Submit a fresh reviewed plan when appropriate.

Stable adapter errors: 400 `Invalid ChatOps request`; 401 `ChatOps authentication
failed`; 403 `ChatOps actor denied`; 409 `ChatOps replay rejected` or `ChatOps
action unavailable`; 413 `ChatOps body too large`; 503 `ChatOps unavailable`.
Jobs decision failures preserve their HTTP status but replace detail with
`ChatOps decision failed`. Normal session/CSRF/role errors still govern issue.
The ordinary job lookup may return 404 for an unknown job on issuance.
Audit records contain local username, job ID and fixed decision/failure labels,
not credentials or upstream error text.

## Verification

`.venv/bin/python -m pytest tests/test_chatops.py -q` exercises real signed TestClient
HTTP, the real queued worker with RecordingExecutor, signature/raw-byte
integrity, stale/future time, revoked/unmapped actors, global durable nonce
replay, consumed actions across restart, graph drift for both decisions,
expiry/inflight states, policy and required-remote failure retry semantics,
strict schemas/body cap, issue roles, and subsequent worker policy failure.
Tests use disposable state/key files and an isolated adapter HTTP app sharing
the actual app state; parent middleware wiring must also be covered by the
application integration suite. No external platform or real human is contacted.
