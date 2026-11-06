# Optional read-only LLM diagnostics

## Contract and activation

`register_llm_diagnostics(registry, app)` registers diagnostics provider **`llm`**.
The application must call this helper during startup and select `llm` through its
normal provider binding mechanism. Registration never resolves credentials or
makes network requests. Core registry/API/UI wiring is outside this module.

The implementation advertises `requires_explicit_run: true`:

- Synchronous, zero-argument **`read()`** never performs inference. Before the
  first successful explicit run it returns `mode: llm`, `status: not_run`,
  `read_only: true`, empty findings and a zero summary. After a successful run it
  returns a defensive copy of the validated in-memory report, `status: cached`.
- Synchronous **`run()`** performs one bounded inference request, validates the
  response and replaces the cache only on success. It must be exposed only by an
  explicitly invoked authenticated administrator action, not GET polling or page
  loading. The application integration should use `POST /api/diagnostics/run`.
- A failed run raises sanitized HTTP **503**, never rules or fabricated model
  output. An earlier successful cache remains available as a cached snapshot;
  it is not evidence that the failed run succeeded. Cache is not persisted and
  is reset by process restart. Reports cover the snapshot supplied to that run,
  not subsequent job changes.

Administrator-owned **`data/config/diagnostics-llm.json`**, relative to the
application's state directory (usually `data`), controls activation:

```json
{
  "enabled": true,
  "base_url": "https://approved-inference.example/v1",
  "model": "approved-model",
  "api_key_ref": "diagnostics/api-key",
  "timeout": 5.0
}
```

The exact base URL in this file is the administrator's approved destination.
There is no endpoint supplied by browsers, prompts or model output. Protect this
file and the state directory from untrusted writes, and apply deployment egress
controls to the approved destination. HTTPS uses certificate verification.
Credentials in URLs, queries, fragments, redirects and inherited proxy settings
are forbidden. No environment credential guessing occurs.

For an explicitly approved local compatible server only:

```json
{
  "enabled": true,
  "base_url": "http://127.0.0.1:8000/v1",
  "allow_loopback_http": true,
  "model": "local-model",
  "timeout": 5.0
}
```

HTTP requires both the explicit flag and a **numeric loopback IP** (`localhost`
is intentionally not accepted for HTTP). `api_key_ref` may be omitted only for
numeric loopback destinations, including loopback HTTPS. A configured reference
is resolved using the currently selected secrets provider **on each explicit
run**, not at registration or cached `read()`. Rotation therefore takes effect on
the next run. The key is used only as a Bearer header, is not cached by this
provider, and is never included in the prompt, result or descriptor. Exact key
reflections in model findings are rejected. Python memory erasure and arbitrary
transformed/encoded secret detection cannot be guaranteed.

`timeout` must be finite, numeric, nonboolean and greater than zero through 60
seconds; default is 5. HTTPX socket-operation timeouts are combined with a
monotonic deadline checked for received chunks. This is not a hard cancellation
of DNS resolution or the separate secrets provider's own bounded operation.
Configuration is loaded at startup; restart after changes. Unknown config fields,
raw `api_key`, invalid values or malformed JSON fail closed. Configuration files
are capped at 8 KiB. Missing config is `unconfigured`, `enabled: false` is
`disabled`, invalid config is `unavailable`. None exposes an active
implementation. `configured` means valid administrator configuration, **not** a
successful connectivity check or verified real-model deployment.

## Evidence, protocol and limits

Only persisted job summaries are sent. No graph, configuration, name, user,
address, raw log, error, result, snapshot or event message is serialized.

- Job IDs must exactly match the platform-generated 32-character lowercase hex
  shape; malformed IDs are omitted, never repaired or normalized.
- Status is taken from a closed set of platform statuses; unknown values become
  `unknown`. Phases come from a closed set of built-in phase names; extension or
  unknown strings are omitted rather than truncating possible secrets into a
  prompt.
- Evidence includes job ID, status, unique phase names, and `{id, phase}` event
  summaries. At most 1,000 candidate events are inspected per job and 16 distinct
  valid event IDs included. Numeric event IDs are scoped to their job.
- At most 50 jobs are included in persisted listing order (newest first in the
  standard store), with an additional **8,192-byte evidence cap**. Older or excess
  evidence is intentionally absent. Findings cannot cite omitted evidence.
- Full request JSON is capped at **16,384 bytes**; raw upstream response at
  **32,768 bytes**, streamed with a byte counter even without Content-Length.
  Compressed response bodies are rejected to avoid decompression expansion.
- One `POST <base_url>/chat/completions` uses a strict structured `json_schema`
  response format and `max_tokens: 2048`. Reported completion usage above that
  cap and any finish reason other than `stop` are rejected. Without a trusted
  model-specific tokenizer, token enforcement depends on the compatible server
  honoring `max_tokens`; the independent byte cap is always enforced locally.
- No tools, functions, autonomous calls, subprocesses, deployment operations or
  remediation are exposed. Unknown fields in diagnostic JSON and the model
  message/choice/envelope are rejected rather than silently dropped, including
  tool/function fields with empty or null values. Duplicate JSON object keys in
  configuration, the response envelope and assistant content are also rejected.

Expected assistant JSON:

```json
{
  "read_only": true,
  "findings": [
    {
      "id": "review-failure",
      "severity": "warning",
      "title": "Review failed execution",
      "message": "The persisted status is failed.",
      "resource": "0123456789abcdef0123456789abcdef",
      "event_ids": [1, 2],
      "recommendation": "Have an administrator review local execution evidence."
    }
  ]
}
```

The canonical finding fields remain `id`, `severity`, `title`, `message`,
`resource`, `recommendation`; `event_ids` adds explicit citations and is required
by this provider's model schema (`[]` is valid for a job-level finding).
`resource` must be an exact job ID provided in that request; every event ID must
belong to that same job and be present in the supplied event summaries. Invented
references, duplicate finding/event IDs, unsupported severity, unknown fields,
non-strict types and `read_only` other than boolean true fail closed. There are
at most 20 findings; text lengths are bounded (ID 80, title 160, message 2,000,
recommendation 1,000 characters). Findings and prose are untrusted suggestions:
reference validation does not establish root-cause correctness. The UI must
render plain text safely and must not execute recommendations or treat arbitrary
prose as additional verified citations.

## Verification and deployment status

```sh
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_llm_diagnostics.py -q
```

Tests exercise a **real local HTTP compatible-protocol server**, inspect the
redacted requests, use the actual restricted-file secret provider for rotation,
and exercise real SQLite-persisted summaries. They cover cached GET behavior,
explicit runs, invalid config, schema/citation failures, secret echoes, timeout,
HTTP errors, malformed JSON, token/byte limits and tool-call rejection.

A local protocol server returning controlled JSON is **not a real model call**.
A real deployment still requires administrator approval of an endpoint/model,
secret-source configuration where needed, application wiring, and an explicit
successful run against that chosen service. No live config is created here.
