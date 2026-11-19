# Autopilot enterprise services — contract

You are a fresh agent with NO memory of the parent session. Read this file, then run
`python3 ops/autopilot-enterprise/next.py` and do EXACTLY the task it returns. One task per
iteration. Nothing nearby, no opportunistic refactor.

## Goal
Make the kw CLI useful for real enterprise use cases by delivering, on the existing lab:
a Zabbix supervision stack monitoring the ESXi, a Grafana dashboard of live services/costs/
resources, and a SOTA vault-backed Cloudflare (DNS + tunnel) capability. All infrastructure
operations go through the kw CLI / the platform API, never direct DB writes.

## Fixed facts
- Repo: /home/ubu/Desktop/infra-platform-lab/Prototype/kuberweaver. Branch: feat/enterprise-services.
- kw API: http://127.0.0.1:8088. Admin password: read data/bootstrap-credentials.txt (admin line),
  pass via KW_PASSWORD env, NEVER print it. Login: `kw auth login --username admin`.
- Supervision VM: ubuntu@192.168.10.63 (LAN_pfSense 192.168.10.0/24), uuid and ssh_key_id in
  STATE.json context. Managed private key: export with `kw keys export <key_id> --output <0600 file>`
  (admin re-auth), delete the file after use.
- Workload bricks target a guest by target_uuid+ssh_key_id (execution_provider ansible-workload-v1).
  Deploy = create blueprint (single node) -> `kw deploy request` -> `kw deploy approve` -> `kw deploy watch`.
- Brick private evidence: data/workloads/<job_id>/evidence.json (apply_stdout is only the last 1500
  chars; for the full Ansible output run the brick playbook manually with the managed key).
- Collections under plugins/*/collections are gitignored; their test symlinks were dereferenced so
  catalog.fingerprint passes. If you reinstall a collection, dereference symlinks again.

## Safety (never weaken)
- Human approval for every deploy/rollback/decommission; pins (SHA256) of plan/source; ownership
  proof; PostgreSQL in production (SQLite only for isolated tests). Secrets only in the vault, by
  reference; never in plans/logs/responses/argv/git. Do not print credentials or private keys.
- Never merge to main, never force-push, never deploy to production. Push only feat/enterprise-services.
  A human opens/merges PRs. Do not install host packages with sudo unless a task explicitly needs it
  and the password is provided through the secure vault path (never in chat/argv).
- Do not touch unrelated VMs/resources. Only the supervision VM and resources this track creates.

## Quality gates (run before commit)
- Verify command (in STATE.json `verify`): run it verbatim; it must pass.
- For a brick change: re-deploy via kw and confirm the job reaches succeeded (or capture the exact
  private evidence if it fails). For backend code: add/adjust a failing test first (TDD), then fix.
- `git diff --check` clean. No secrets in the diff. Clean tree at the end (commit or revert).

## Commit protocol
- Commit on feat/enterprise-services with a discreet message. Push the branch. Update STATE.json
  (task status done/partial/blocked + attempts + note). Three failed attempts on a task -> blocked
  with the reason; move on. Keep the tree clean.

## Stop conditions
- next.py prints action=stop when the deadline (2026-11-20T08:39:59+01:00) passed or no task remains.
- If a STOP file exists in this directory, stop. Do not start new work after the deadline.

## Reporting
- Reply in 2-3 lines: task id, what changed, verification result. Write durable evidence to
  ops/autopilot-enterprise/reports/<task-id>.md (outside git is fine; it is gitignored).
