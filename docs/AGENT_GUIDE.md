# Working in this repository with an AI agent

Short version for a coding agent that has never seen KuberWeaver. Read the file
that matches your task; do not read everything.

## Read this first, and nothing else

1. `AGENTS.md` at the repository root, when it exists. It is the canonical,
   shortest instruction set and it wins over this file.
2. This page (`docs/AGENT_GUIDE.md`) if `AGENTS.md` does not exist yet. It is the
   same contract in slightly more words.
3. The README section you need (documentation index for a feature, capability
   table before any claim).
4. One `docs/<NAME>.md` page for the subsystem you touch.

Avoid scanning whole directories. Use a pattern search with a path. Do not
re-read files already established above.

## Commands that matter

```sh
# tests, disposable state, never the live data/ directory
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL \
  KUBERWEAVER_STATE_DIR="$state" PYTHONPATH=backend \
  .venv/bin/python -m pytest tests -q --tb=short

# one file
.venv/bin/python -m pytest tests/test_workloads.py -q

# credential scan, tracked files and history
python3 ops/ci/secret_scan.py --history
```

```sh
# one command: hygiene, credential scan over history, then the test suite
python3 ops/ci/check.py

# or only the fast part
python3 ops/ci/check.py --fast
```

The full suite takes about 14 minutes when Chromium is available; browser
acceptance tests skip themselves without it. `check.py` runs against a disposable
state directory and never touches `data/`.

## Before committing

- One topic per commit. Subject `type(area): what`, lowercase, no trailing
  period.
- Tests must cover refusal paths, not only the happy path.
- Never weaken admission, approval or verification to make something pass.
- `git diff --check` clean.
- No secret, no `data/`, no `docs/prompt/`.

## What not to do

- Do not add `if plugin_id` branches to the kernel. A service belongs in
  `plugins/<id>/`, a provider or hook in `examples/extensions/<id>/`.
- Do not document an intention as a delivered capability. If it is planned and
  disabled, say so.
- Do not rewrite history on `develop` or `main`.
- Do not force-push, disable branch protection or skip checks.
- Stop and report rather than inventing a missing contract.

## Adding a service

Copy `plugins/web-nginx/` (manifest plus `iac/` playbooks). Change the manifest:
`id`, `name`, `version`, `category`, `engine`, `schema`, `defaults`,
`implementation`, `verify.assertions`. Keep the lifecycle files
`snapshot`/`apply`/`verify`/`rollback`/`remove`. Add refusal tests. See
`CONTRIBUTING.md` for the field list.

## Where things are

| Path | Role |
| --- | --- |
| `backend/kuberweaver/` | kernel, HTTP, sessions, RBAC, approvals, storage |
| `kw/` | reference CLI over the API |
| `plugins/<id>/` | service catalogue entries (manifest + Ansible) |
| `examples/extensions/<id>/` | Python extensions, protocol v1 |
| `tests/` | pytest suite |
| `presets/` | saved multi-brick graphs |
| `ops/ci/secret_scan.py` | credential scanner used by CI |

## Repository state conventions

Runtime state lives in `data/` and is never committed. Private notes live in
`docs/prompt/` and are never committed. Downloaded Ansible roles and collections
under `plugins/*/roles/` and `plugins/*/collections/` are never committed, except
first-party roles such as `plugins/cloudflare-tunnel/roles/`.

The project is a prototype with one maintainer. There is no merge guarantee and
no review deadline.
