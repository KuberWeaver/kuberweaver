# Capability evidence and limitations

This is an index of **historical acceptance records**, not a new test run or a production-readiness certification. Earlier records describe the local acceptance of 6 November 2026; later PostgreSQL, vault and hypervisor records are indexed in `V2_HYPERVISORS.md`. Secrets and runtime state are excluded from publication.

## External-agent access

KuberWeaver exposes its business API through an optional stdio MCP adapter without embedding a model, AI-provider key or mandatory model subscription. Optional LLM diagnostics is separate and not registered by default.

- Only human administrator sessions issue, list and revoke tokens.
- Maximum role and scopes are cumulative; expiry and revocation persist.
- Targeted reads such as `read:finops` do not implicitly grant job or inventory access.
- Delegated approval requires admin role and explicit `approve` scope.
- MCP cannot mint its own privileges, resolve raw secrets, fetch arbitrary URLs or run shell commands.
- Tokens are shown once in Agent access, not persisted in browser localStorage/sessionStorage.
- Event streams revalidate tokens; browser sessions retain CSRF protection.

See [API rights](AGENT_ACCESS.md) and [MCP](MCP.md).

## Historical verification

The earlier backend command was `.venv/bin/python -m pytest -q --tb=short --junitxml=/home/ubu/.hermes/cache/scratch/kuberweaver-final-backend.xml`.

That record reported 527 passing backend tests and one skip for missing `redis-server`, 47 frontend tests in nine files and a successful TypeScript/Vite build. These counts are historical, not results for the current tree. The skipped test does not prove Redis persistence.

Recorded protocol evidence covered real SDK ClientSession → stdio → isolated Uvicorn API exchanges, independent blueprint HTTP readback, pending submissions, 403 approval denial without scope and 401 after revocation. Local notification and signed ChatOps exchanges exercised replay denial. Remote Git publication used disposable bare repositories and SHA readback, not an external hosting service. Independent reviews covered the implemented scope, not remote certification. A historical Gitleaks scan covered a 176-source-file snapshot, excluding private data and presentations; it is not a current or universal secret-safety guarantee.

Test accounts, tokens, settings and destinations were disposable and isolated from live `data/` and real external services.

## Capability matrix

| Capability | Implementation/evidence | Remaining boundary |
| --- | --- | --- |
| Agent API/MCP | Typed tools, revocable tokens, roles/scopes, real local protocol exchanges. | No automatic Hermes setup or Internet exposure. Historical tool counts may differ from the current adapter. |
| Notifications | Durable outbox, worker-independent dispatcher, bounded retry, uncertain deliveries not automatically replayed, API readback. | No real external SaaS recipient configured; no receiver-side exactly-once guarantee. |
| Remote GitOps | Explicit destination, exact commit and remote SHA, publication outside the shared lock, durable approval reservation and queue revalidation. | Test repositories only; actual Git/SSH and remote policy still require configuration. |
| Execution secrets | Server-controlled references, ephemeral 0600 key, identity/environment isolation, result suppression and cleanup; mapping/source revalidation at command boundaries. | Does not imply existing lab keys were replaced or vault-to-real-guest acceptance completed. |
| ChatOps | Exact-plan action, timestamped HMAC callback, external actor mapped to admin, durable nonce, shared approval services. | Generic gateway adapter, not certified Slack/Teams authentication or a deployed external gateway. |
| Observability | Authenticated snapshot and process/job metrics; Prometheus rules and Grafana dashboard validation. | Collection/visualization stack not deployed; configuration is not reachability evidence. |
| Persistent Redis | Ownership, AOF, stopped backup, restore and partial retry tests; SSH isolation tests. | Manifest remains planned, extension disabled, firewall prerequisite unchanged; no remote acceptance. |
| OpenTofu/libvirt | Saved plans/digests, CLI/provider/transport guards, separate deletion and live/persistent disk checks. | Planned; current SSH trust guard blocks transport; no authorized real create/delete acceptance. |
| Blank-disk vSphere | Saved-plan protocol and local provider checks. | Planned/blocked by PKI and powered-off-create contract; separate from OVF import. |
| VMware OVF | Separate create-only composition documented in the extension guide. | Local tests do not prove target OS/SSH startup; no update/delete/rollback or automatic ambiguous-failure cleanup. |
| Cisco/pfSense and frontend/backend stack | Requirements and catalogue placeholders retained. | No validated adapter/target; no automatic network wiring. |
| Measured FinOps | Existing estimates identify their source. | No cloud billing, payments or comprehensive actual consumption allocation. |

A planned flag, available registration, configuration file or mocked test is never remote acceptance. Passing tests do not establish production maturity.

## Historical local service readbacks

The earlier integration record reports a restart of `kuberweaver.service` after checking for active jobs and privately backing up SQLite, followed by active/running status, `/healthz` returning `{"status":"ok","service":"kuberweaver"}`, portal HTTP 200, agent/ChatOps/operations routes and readable storage/live-worker metrics. This is a historical observation, not an assertion of current service state; see PostgreSQL documentation for the later backend selection.

No real agent token was automatically issued and no external connector, VM or network rule was changed by that acceptance. Ambiguous approvals remain blocked for manual review rather than automatic cleanup or replay.
