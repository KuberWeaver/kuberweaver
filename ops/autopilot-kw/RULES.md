# kw CLI unattended build — contract (read first, every iteration)

You are a fresh agent with NO memory of prior sessions. Everything you need is here.

## Repo / branch
- Repo: /home/ubu/Desktop/infra-platform-lab/Prototype/kuberweaver
- Branch: feat/kw-cli (already checked out). NEVER switch to main, NEVER merge, NEVER push,
  NEVER force-push, NEVER deploy, NEVER install anything on the host. Local commits only.
- Python: .venv/bin/python (a uv venv; use `uv pip install --python .venv/bin/python` if a
  package is truly required, but prefer the stdlib + click + httpx already present).

## Mission
Build the `kw` CLI (package `kw/`) as a THIN HTTP client over the existing FastAPI API.
Spec: docs/cahier-des-charges-kw-cli.md. The API is the product; the CLI adds no business
logic, no direct database writes, no approval bypass. Same routes/roles/pins as the GUI.

## Hard rules
1. ONE task per iteration: the task returned by `next.py`. Do only that task, nothing nearby.
   No opportunistic refactors.
2. If a capability has no backend route, do NOT stub it client-side and do NOT add backend
   routes. Mark it clearly unavailable in the CLI help and record the gap in the task note;
   set the task `blocked` with the reason. (Decision: wrap existing routes only.)
3. Tests before commit. Add/extend tests in tests/test_cli.py (or tests/test_cli_<task>.py).
   Integration tests run a real local uvicorn server in a thread (see the `env` fixture in
   tests/test_cli.py) — the sync CLI cannot use the async-only httpx.ASGITransport.
4. Verification gate (run verbatim, must pass before commit):
     .venv/bin/python -m pytest tests/test_cli.py -q -p no:cacheprovider
   If you added a separate test file, also run it. Then: git diff --check
5. Secrets: never in argv, logs, plans, or responses. Passwords via KW_PASSWORD /
   KW_PASSWORD_FILE / prompt. Private keys written 0600, never printed. Session file 0600.
6. Commit protocol: git add <only the files you changed>; git commit -m "feat(kw): <task>".
   Leave the tree CLEAN at the end (git status --short empty). If you cannot finish, commit
   the working partial with tests green, set the task `partial`, and note what remains.
7. Three consecutive failed attempts on one task => set it `blocked` with the reason, keep
   the tree clean, move on. Do not thrash.
8. Update ops/autopilot-kw/STATE.json: set the task status (done/partial/blocked), attempts,
   and a one-line note. This is the only intent store.
9. Deadline: do not start new work after the deadline in STATE.json. If past it, stop.
10. Reply in 2-3 lines: task id, what changed, test count, commit sha. No questions.

## Existing CLI surface (already built, tested)
kw auth (login/logout/whoami), kw connectors (drivers/list/add/get/credentials/test/discover),
kw keys (list/generate/get/revoke/enable/export). Core: kw/config.py, kw/client.py,
kw/output.py, kw/main.py, kw/commands/common.py (helpers: client(ctx), out(ctx,...), @api).
Register new groups in kw/main.py::_register().

## API route map (backend/kuberweaver/*.py)
- jobs/deploy: api.py  POST/GET /api/jobs, GET /api/jobs/{id}, POST .../approve|reject|rollback,
  GET /api/jobs/{id}/events, POST /api/plans/validate
- blueprints: api.py  GET/POST /api/blueprints, GET /api/blueprints/{id}
- studio (modules/services): studio.py  /api/studio/artifacts, /api/studio/offerings,
  /api/studio/offerings/{id}/request, /api/studio/services, /api/studio/services/{id}/decommission
- inventory/resources: environment_state.py  /api/environment-states, /{id}, /{id}/lease/release;
  api.py /api/infrastructure; declarations.py /api/declarations (verify/execute) for import
- decommission: destruction.py  /api/environment-states/{id}/destroy (+cancel/execute/resolve)
- finops: api.py  /api/finops, /api/reporting, /api/audit, /api/overview
- agent tokens: agent_auth.py  /api/agent-tokens (GET/POST), /{id}/rotate, DELETE /{id}
Read the actual handler + Pydantic model before writing a command; do not guess the schema.
