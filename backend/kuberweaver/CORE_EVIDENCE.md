# Core handoff and execution evidence

## Run

From the prototype root:

```sh
uv pip install --python .venv/bin/python -r requirements.txt
PYTHONPATH=backend .venv/bin/uvicorn kuberweaver.app:app --host 127.0.0.1 --port 8088
```

The server serves `frontend/dist` on the same origin. `create_app(state_dir=..., testing=True, plugins_dir=..., frontend_dir=..., executor=...)` supports isolated tests and injected execution implementations. Production lifespans take an exclusive state-directory process lock; use one Uvicorn worker. Graceful shutdown waits for in-flight execution rather than relinquishing the lock while a provider still writes.

Bootstrap credentials are generated once in `data/bootstrap-credentials.txt` (0600; containing `username:password` lines for admin/operator/viewer). The state directory is 0700. Do not print this file in logs or documentation. SQLite contains PBKDF2 password hashes, hashed session identifiers, persistent CSRF tokens, approvals/jobs, blueprints, typed settings and audit records.

## Module boundaries

- `app.py`: local Host/Origin boundary, sessions, authentication, CSRF, static delivery, lifespan.
- `models.py`, `catalog.py`, `policy.py`: closed request envelope, data-only manifest discovery, securely merged schema/manifest defaults, admission gates and estimates.
- `storage.py`: SQLite persistence and serialized writers.
- `jobs.py`: explicit admin approval, real sanitized local Git commits with artifact readback, immutable graph/manifest/source fingerprints, one serialized consumer and explicit rollback.
- `providers.py`: registry providers for GET-only GNS3 inventory, rules diagnostics, local outbox, injectable execution, restricted-file/OpenBao secret-provider registration. No application/device-specific dispatch in these core modules.
- `runtime.py`: trusted extension activation and validated administrator configuration. Optional extension code is not sandboxed.
- `api.py`: frontend HTTP contract, metrics and terminal/replayable SSE job events.

Execution, extension SDK and secret provider implementation modules belong to their other owners and were not edited.

## Configuration and contracts

- Policies/pricing load from one `.yaml`, `.yml` or `.json` file each under `data/config/`. API updates persist both the typed config and SQLite. Invalid files deny execution rather than silently relaxing policy.
- The SDK CLI activation authority is `data/extensions.json` (`{"schema_version":1,"enabled":[...]}`). `data/config/extensions.json` is accepted instead if present. Having both authorities fails closed. API enable requires a strict `trusted: true` boolean; package installs remain disabled until explicit activation. Runtime enable/disable requires an idle execution queue.
- Extension packages are discovered under `data/extensions/`; read-only inspection does not import disabled packages. The registry exposes built-ins and trusted SDK providers. Hook inputs are copies; hooks cannot provide approval or replace admission decisions.
- Optional secret config is `data/config/secrets.json`: `restricted_files` maps approved logical names to protected files; `openbao` accepts the scoped OpenBao provider constructor configuration (token file/environment reference, never an inline token). No secret values are accepted in graph config or written to GitOps/audit.
- Target and exposure restrictions are hard safety floors, not editable widening policies: `app` / `branch-client`, ports `18090..18109`, loopback public CIDR. Failure injection is admin-only. Exactly one canary is supported, matching the isolated executor.
- Every environment, including dev, requires approval. Queued/running/interrupted rollback jobs become failed/rollback_failed with `requires_manual_review=true` on restart and are never re-enqueued automatically.
- FinOps is explicitly declared-resource estimation, not cloud billing. Policies currently enforce per-plan quotas/budget. Remote Git publishing, enterprise identity, external notification delivery and AI inference are visibly planned/unconfigured. Inventory performs real upstream GETs only.

## Verified results

Vertical RED/GREEN slices were executed for auth; graph/catalog/policies; approval/worker/GitOps/rollback; providers/extensions; source drift, policy drift, config persistence, queue serialization, host/origin/payload boundaries and same-origin static routing. Missing routes, missing validation and unsafe acceptance were observed failing before implementation.

Final core command:

```text
.venv/bin/python -m pytest tests/test_core_*.py -q
32 passed in 20.23s
```

Final complete backend/SDK/executor/secrets suite:

```text
.venv/bin/python -m pytest tests -q
113 passed in 23.84s
```

`compileall` passed and installing `requirements.txt` verified all 38 pinned packages. No project-root Git commits or pushes were made (the root branch has no commits); only intentionally isolated local GitOps repositories were committed by the tested approval flow.

A real Uvicorn subprocess was started on an ephemeral numeric-loopback port with an isolated scratch state directory, authenticated without printing credentials, exercised over real HTTP and terminated in `finally`. It returned 6 plugins, an allowed normalized NGINX plan, actual read-only GNS3 inventory of 12 nodes / 20 links, and HTTP 200 for overview, FinOps, rules diagnostics, extension discovery, integrations and metrics. No deployment was sent to the live lab by the core agent; remote deployment acceptance belongs to the parent/executor owner.

Raw HTTP smoke evidence: `/home/ubu/.hermes/cache/scratch/kw-core-smoke-38dzhvnw/evidence.json` (scratch evidence is subject to normal pruning).
