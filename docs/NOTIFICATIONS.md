# Durable webhook notifications

`WebhookNotifications(app, config)` is the optional synchronous notifications
provider named **`webhook`**. `send(payload)` **only validates and durably enqueues**;
it never resolves a credential or performs HTTP. Delivery must run outside the
execution worker using the explicit bounded `retry_pending()` method. There is no
implicit background thread, fallback provider, real endpoint configuration, or
live configuration write in this module.

## Integration (host-owned)

Call this when constructing each fresh builtin registry, after registering secret
providers (including the invalid-secret-config path):

```python
from .notifications import register_notifications
register_notifications(registry, app)
```

The helper reads `<app.state.state_dir>/config/notifications.json` (normally
`data/config/notifications.json`). Missing, invalid, oversized (>16 KiB), symlinked,
or inaccessible configuration registers `webhook` with implementation `None` and
safe `status: unavailable` metadata. Disabled configuration registers `None` with
`status: disabled`. This does not change any selected binding. The host must
explicitly select `notifications: webhook` using its existing administrator flow.
Other providers are unaffected. A configured descriptor is not a remote-health
check; endpoint resolution and TLS validation happen only during delivery.

The main application lifespan starts/stops a host-owned `NotificationDispatcher`
thread which periodically resolves the current selected notifications provider and
calls `retry_pending(limit=10)` if supported. **Do not call it from
`Jobs.notify`, the serialized execution worker, the event loop, or while holding
the core store lock.** Stop/join that host-owned dispatcher on shutdown. No
`start()`/`close()` methods are necessary: this provider has no persistent client,
thread, or open database connection. Registration/reconfiguration should occur
when the dispatcher is idle. Concurrent delivery/recovery uses a nonblocking
process file lock; a busy attempt fails with a fixed safe error, not a second
HTTP delivery. The queued record remains durable.

An administrator can explicitly invoke `retry_pending(limit=1)` off-worker for
manual processing. Exposing HTTP operations for status/retry is host-owned;
require administrator + CSRF for retries. Do not expose secret resolution.

## Administrator configuration schema

Example **non-secret configuration only**, not installed by this implementation:

```json
{
  "enabled": true,
  "transport": "json",
  "endpoint_secret_ref": "notifications/webhook",
  "timeout": 5.0,
  "max_attempts": 3,
  "allow_loopback_http": false
}
```

- `enabled`: required JSON boolean.
- `transport`: required `json`, `discord`, or `teams`.
- `endpoint_secret_ref`: required approved logical secret name, <=128 characters,
  matching `[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*`. The selected secrets provider
  resolves it for **each attempt**, via its context-managed `SecretValue.bytes()`;
  no URL credential is stored in config or delivery state. Rotation takes effect
  on the next attempt. Changing selected secrets/reference/transport intentionally
  retargets pending deliveries; inspect pending work before reconfiguration.
- `timeout`: optional finite number, `0 < timeout <= 60` seconds; default 5.
  Applied to socket network operations, not a strict overall wall-clock guarantee
  for operating-system DNS. No response body is consumed, so a slow/large body
  cannot prolong delivery. Delivery is always isolated from deployment execution.
- `max_attempts`: optional integer 1–5, default 3, total durable attempt budget per
  occurrence across retries/restarts. Booleans are rejected. Each dispatcher call
  makes **at most one attempt per selected record**; no tight retry/sleep loop.
- `allow_loopback_http`: optional boolean, default false. The sole plaintext
  exception is explicitly enabled **numeric loopback** (`127.0.0.1`, `::1`, etc.).
  `localhost` and non-loopback HTTP are refused. Use only for isolated tests.

Unknown fields, direct `endpoint` values, missing required fields, invalid types,
NaN/infinite timeouts, and out-of-range limits fail closed. Disabled configurations
still require a valid schema. Configuration belongs to trusted administrators;
there is no user-supplied destination URL.

Endpoints require HTTPS with system CA verification except the explicit test
exception. Userinfo, fragments, invalid ports, control/whitespace/non-ASCII URL
characters, and non-HTTP(S) schemes are refused before transport. Secret URLs may
contain webhook paths/query credentials. Redirects are never followed and proxy
environment variables are never inherited. This is not an SSRF sandbox: approved
HTTPS endpoints can address private infrastructure. Trusted secret/config writers
control destinations; restrict their OS permissions and egress separately.

## Payloads and wire formats

Only canonical lifecycle fields are retained:
`event`, `job_id`, `status`, `operation`, `environment`, `time`.
Arbitrary extra keys/plugin output are discarded. Events are `before_plan`,
`after_approval`, `job_started`, `job_finished`; operation is `execute` or
`rollback`, with one narrowly scoped additional host summary: validation is
`event: before_plan`, `operation: validate`, `job_id: null`, `status: planning`.
Other event/status/job combinations with `validate` are rejected. The authenticated
`POST /api/plans/validate` emits this occurrence before admission, even when the
plan is subsequently denied. Graphs, names, plugin configuration, arbitrary extra
keys, and secret material are not forwarded. The same six-field allowlist and
value checks apply to validation; unknown fields are still discarded, not added
to the wire schema. Field strings are bounded to 128 characters with identifier/timestamp
characters (`A-Za-z0-9_.:+-`); null job/environment are permitted. Status and time
are required. Inputs must be the host's sanitized lifecycle summaries, never
credentials or arbitrary user text. No arbitrary sensitive strings can be
reliably identified by a generic sanitizer.

- `json`: POST the six-field summary as JSON.
- `discord`: POST `{"content": "<JSON summary>", "allowed_mentions":{"parse":[]}}`.
- `teams`: POST `{"text": "<JSON summary>"}` for compatible incoming webhooks.

All requests carry `Content-Type: application/json` and stable `Idempotency-Key`.
Vendor-specific tenant policy/schema changes can reject a webhook; local wire
format tests are not a claim of live Discord/Teams acceptance.

## Identity and delivery guarantees

Occurrence ID is SHA-256 of a canonical JSON array:
`[event, job_id, operation, status, time]`. This distinguishes execute from rollback,
repeated rollback operations, and separate validation calls with different host
occurrence timestamps (validation has no job ID). `send()` persists the original
summary including its host-generated `time`; transport retries and provider
restarts reuse that stored payload and identity, never regenerate the event time.
A replay through `send()` **must preserve the original `time`** to deduplicate.
Re-emitting a logical event with a new timestamp is a **new occurrence**, not a
transport retry. The host currently generates a fresh lifecycle timestamp on
notification emission; it does not promise a durable host occurrence journal or
exactly-once host emission. Timestamp identity cannot distinguish two otherwise
identical host emissions with exactly the same timestamp. There is no inference
that job/status alone identify an operation occurrence.

State is private `data/notifications/deliveries.sqlite3` (`0600`) under a `0700`
directory, independent of the platform store, with SQLite FULL synchronous
transactions and process `delivery.lock`. It stores only allowlisted payloads,
identity, state, attempt count, fixed safe error, and update timestamp. It does
not store endpoint URLs, credential references, response bodies, or exception
strings. Pre-existing notification-directory/database symlinks are refused.
Trusted OS owners must protect state/config and its ancestor directories; this
is not protection against hostile same-UID code.

States:

- `pending`: durable enqueue, secret/endpoint validation unavailable, or an
  explicit non-2xx rejection. Retry explicitly with `retry_pending()`.
- `in_flight`: attempt marker committed before credential/HTTP work.
- `delivered`: observed 2xx and committed locally. Replays return this record
  without HTTP. This proves HTTP acknowledgement, **not human receipt**.
- `uncertain`: network exception after transport begins, or interrupted
  `in_flight` recovered on construction. Remote acceptance is unknown.
  **Never automatically resent.** `retry_pending(include_uncertain=True)` is an
  explicit operator acknowledgement of duplicate risk after remote inspection.
  The dispatcher and administrator HTTP retry use the default and leave uncertain
  records untouched; this change adds no force/uncertain-retry HTTP option.
- `exhausted`: attempt budget consumed; no further delivery attempts even after
  restart or budget configuration changes for already exhausted records.

The remote can accept a request and the process can die before local acknowledgement
commit. A receiver may honor the idempotency key, but Discord/Teams need not. A
non-2xx response is retryable rejection by policy; a receiver that mutates before
returning non-2xx can also produce duplicates. Exactly-once delivery is impossible
without receiver cooperation. Transport exceptions are conservatively uncertain,
including connection errors where acceptance may in fact be impossible.

`retry_pending(limit=100, include_uncertain=False)` accepts only integer limits
1–100 and a boolean flag, processes oldest queued records, and returns safe
`{"deliveries": [...]}` results. There is no automatic backoff schedule; the host
controls dispatch cadence. `read()` returns safe status records, not payloads or
config. `describe()` exposes only provider identity/kind/configured-or-disabled
status/external-delivery flag/transport. Public errors are fixed messages. The
stdlib HTTP stack avoids credential-bearing `httpx` INFO request logs; response
bodies are never read. No secrets or upstream exception messages are printed.

Retention is administrator-managed; there is no automatic cleanup, scheduler,
dead-letter reset, or delivery-state migration utility. Keep dedupe state as long
as replays can occur. One provider namespace represents one logical destination.

## Verification and TDD evidence

Run from the repository root using its Python 3.14 environment:

```sh
.venv/bin/python -m pytest tests/test_notifications_delivery.py -q
.venv/bin/python -m pytest tests/ -q
```

Evidence source: `tests/test_notifications_delivery.py`. Its real
`ThreadingHTTPServer` binds only numeric loopback with temporary protected secret
files and temporary application data. No live config is read/written and no real
external endpoint is contacted. Tests read receiver payloads/headers and durable
state after restart, cover JSON/Discord/Teams wire formats, HTTP rejection/retry
budgets, exact replay dedupe, execute/rollback and timestamp occurrence identity,
configuration/endpoint validation, redirect/proxy refusal, secret-log/state leaks,
crash-after-acceptance and lost-ack uncertainty, registration failure isolation,
and storage symlink rejection.

Observed RED/GREEN commands (same commands rerun after implementation):

1. `.venv/bin/python -m pytest tests/test_notifications_delivery.py -q`:
   RED missing module (1 failed), GREEN 1 passed; later bounded durable retry
   slice RED HTTP 503 exception, GREEN 2 passed. Subsequent safety and recovery
   slices RED 22 failed / 5 failed respectively, GREEN 24 / 29 passed.
2. `.venv/bin/python -m pytest tests/test_notifications_delivery.py::test_send_only_enqueues_and_occurrence_time_distinguishes_repeated_operations -q`:
   RED `delivered != pending` (1 failed). Enqueue-only/occurrence-time implementation
   plus adjusted explicit-dispatch tests: GREEN full notification suite 30 passed.
3. `.venv/bin/python -m pytest tests/test_notifications_delivery.py::test_transport_failure_after_acceptance_is_uncertain_and_logs_no_urls -q`:
   RED `pending != uncertain`, with credential-bearing httpx INFO URL observed in
   captured test logs. Transport uncertainty handling and stdlib HTTP replacement:
   GREEN full notification suite **31 passed**.

4. `.venv/bin/python -m pytest tests/test_notifications_delivery.py::test_main_api_validation_enqueues_and_delivers_sanitized_summary -q`:
   RED `len(deliveries) == 0` instead of 1 for the real authenticated validation
   endpoint with selected webhook/secrets providers. Narrow validation-summary
   support: GREEN 1 passed. Added strict malformed-validation regressions and
   validation lost-ack/restart/default-retry coverage; notification suite **44 passed**.
   Main API integration proves a rejected secret-bearing graph still emits only
   the sanitized planning summary, enqueues without HTTP, delivers through the
   administrator retry endpoint to a real loopback receiver, reuses original time
   and idempotency key on retry, distinguishes a second validation occurrence,
   and records no `lifecycle.observer_failed`.

Latest focused run:
`.venv/bin/python -m pytest tests/test_notifications_delivery.py tests/test_notification_dispatcher.py tests/test_operations_api.py -q`
returned **50 passed**. Latest full concurrent-worktree run returned
**513 passed, 1 skipped, 1 failed**:
`tests/test_completion_integration.py::test_finops_only_agent_cannot_read_jobs_or_infrastructure`
(expected agent-token creation 201, received 422). This is outside notification
scope; no agent-auth code was changed by this fix.

The first full concurrent worktree run returned **358 passed, 1 skipped, 1 failed**
(`tests/test_imported_provider_provenance.py::test_extension_provider_pins_imported_implementation_source`).
That failure concerns imported execution-provider source drift in parent-owned
files, not notification delivery. The worktree contains other agents' changes;
this module's scope does not include altering their tests or implementation.
