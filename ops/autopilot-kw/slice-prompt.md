You are building the `kw` CLI for KuberWeaver, unattended. You have no memory of prior sessions.

Do exactly this, in order:
1. Read ops/autopilot-kw/RULES.md (the contract). Repo is the current directory; branch feat/kw-cli.
2. Run: .venv/bin/python ops/autopilot-kw/next.py
   - If it prints action=stop, reply "stop: <reason>" and do nothing else.
   - Otherwise it prints action=work with one task. Implement ONLY that task.
3. For the task: read the actual backend handler + Pydantic schema in backend/kuberweaver/
   before writing the command. Add the CLI command group under kw/commands/, register it in
   kw/main.py::_register(), and add integration tests to tests/test_cli.py (reuse the `env`
   fixture: real local uvicorn server, isolated SQLite, sample-service plugin, local vault).
   The CLI is a THIN client: no business logic, no direct DB writes, no approval bypass.
   If a capability has no backend route, mark it unavailable and set the task blocked with the
   reason; do NOT stub it client-side and do NOT add backend routes.
4. Verify (must pass): .venv/bin/python -m pytest tests/test_cli.py -q -p no:cacheprovider
   Then: git diff --check
5. Commit only your files: git add <files>; git commit -m "feat(kw): <task id>".
   Leave the tree CLEAN (git status --short empty). If you cannot finish, commit the green
   partial and set the task partial with a note of what remains.
6. Update ops/autopilot-kw/STATE.json: set the task status/attempts/note.
7. Reply in 2-3 lines: task id, what changed, test count, commit sha. No questions.

Never merge, push, deploy, force-push, touch main, or install on the host. Secrets never in
argv/logs/responses. Do not start new work after the deadline in STATE.json.
