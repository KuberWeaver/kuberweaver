# Contributing

KuberWeaver is an early-stage, self-hosted infrastructure management prototype:
one FastAPI kernel (`backend/kuberweaver/`), the `kw` CLI (`kw/`), a declarative
catalogue of workload bricks (`modules/services/`) and separately installed Python
extensions (`extensions/`). This document is written for someone who has
never seen the repository. It does not describe a finished product: the project is
an early-stage prototype with one maintainer.

Before asking a question, read [SUPPORT.md](SUPPORT.md). If you found something
that looks like a vulnerability, read [SECURITY.md](SECURITY.md) and report it
privately instead of opening an issue.

## Repository map

| Path | Contents |
| --- | --- |
| `backend/kuberweaver/` | FastAPI kernel: HTTP, RBAC, approvals, durable state, audit, orchestration, catalogue and extension registry |
| `kw/` | Thin CLI client, the reference client over the API |
| `modules/resources/` | Infrastructure recipes and manifests |
| `modules/services/` | Service recipes and manifests |
| `blueprints/` | Bundled compositions of modules |
| `plugins/` | Generated runtime catalogue, do not edit by hand |
| `extensions/` | Independently installable Python extensions (protocol v1) |
| `tests/` | pytest suite, including PostgreSQL-backed contracts |
| `scripts/` | Lifecycle, acceptance and release commands |
| `ops/` | User systemd units and CI helpers |

## Prerequisites

- Python 3.14 (`requires-python = ">=3.14"`); the pinned dependencies and CI are
  tested on 3.14. An older interpreter is not supported, even though the kernel
  itself does not need the newest language features.
- `uv`
- Optional: PostgreSQL when you want to exercise the durable-state fixtures.
  Outside those fixtures, the kernel falls back to SQLite.

## Setup

Follow the README installation section. The equivalent commands are:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-execution.txt -r requirements-mcp.txt -r requirements-infrastructure.txt -r requirements-postgresql.txt -r requirements-local-vault.txt
uv pip install --python .venv/bin/python -e . --no-deps   # installs the `kw` CLI
```

A shell glob (`-r requirements*.txt`) also works. Install the six files above
explicitly; CI installs exactly that set. There is no optional browser
acceptance dependency: the portal that those tests drove was removed, and a real
browser acceptance for a new interface has to be written against that interface,
not carried over.

## PostgreSQL fixtures

Some tests need a dedicated PostgreSQL server, not SQLite. The suite reads
`KUBERWEAVER_TEST_DATABASE_URL_FILE`, a file containing one connection URL
(mode 0600). It defaults to `data/postgresql/test-url`. Create it once on your
machine and never commit it:

```sh
docker run --rm --name kw-pg -e POSTGRES_PASSWORD=kwtest \
  -p 55432:5432 -d postgres:17-alpine
printf 'host=127.0.0.1 port=55432 user=postgres password=kwtest dbname=postgres' \
  > data/postgresql/test-url
chmod 600 data/postgresql/test-url
```

Without that file the durable-state fixtures skip, and the run still exits green.
So read the last line, not just the exit code: with the test database present the
suite is `1918 passed, 2 skipped`; without it, `1876 passed, 44 skipped`. A green
run reporting 44 skips means the PostgreSQL half did not execute, and its green
tells you nothing about durability. CI always provides a fresh test database, so
its count is authoritative.

Each fixture creates its own schema and drops it, so the database stays reusable.
`data/` is git-ignored.

## Running the tests

Run tests against disposable state, never against your live `data/` directory or
your real database. From the repository root:

```sh
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL \
  KUBERWEAVER_STATE_DIR="$state" PYTHONPATH=backend \
  .venv/bin/python -m pytest tests -q
```

Two more checks before opening a pull request:

```sh
python3 ops/ci/secret_scan.py
git diff --check

# the same three gates in one command
python3 ops/ci/check.py
```

The first refuses credential-like material in tracked files, the second catches
trailing whitespace and leftover conflict markers.

## Branch model

| Branch | Purpose |
| --- | --- |
| `feat/<topic>` or `chore/<topic>` | One topic per branch, opened by contributors |
| `fix/<topic>`, `docs/<topic>` | Small corrections and documentation-only work; same rules |
| `main` | Default and released state. Pull requests target it directly |
| `develop` | Historical integration branch. It receives nothing today; do not open a pull request against it |

Rebase your feature branch on `main` before opening or updating a pull request.
Never force-push `main`. `develop` is left in place for the history of older pull
requests and is not a merge target.

## Commits

Commit subjects follow `type(area): what`, lowercase, with no trailing period:

- `feat(area):` a new capability
- `fix(area):` a corrected behaviour
- `test(area):` tests only
- `docs(area):` documentation only
- `chore:` tooling, dependencies, housekeeping (no area needed)

Write a body only when the reason is not obvious from the subject. One topic per
commit. No commented-out code, no generated build output, no secret material.
Rewrite history by rebasing on feature branches only, never on `main`.

## Pull requests

1. Rebase the branch on `main`.
2. Fill in the pull request template: behaviour change, tests, documentation.
3. Target `main`. A pull request against `develop` will not be reviewed.
4. Run the local checks and push.
5. Wait for CI. A red check blocks the merge, it is not a formality.
6. Squash or rebase as the reviewer prefers.

## What CI checks

Three jobs run on every push to `main`, `feat/**` and `chore/**`, and on every
pull request that targets `main`. A pull request opened against another branch
does not trigger CI.

- **Secret scan** (`ops/ci/secret_scan.py --history`): private key blocks,
  token prefixes, cloud key IDs and forbidden paths such as `data/`,
  `archive/`, `*.pem` and `*.key`.
- **Hygiene**: refuses conflict markers and refuses runtime state or private
  notes under version control.
- **Tests**: the pytest suite against a disposable PostgreSQL service, with a
  private per-job state directory.

There is no separate lint or type-check job in CI, and `pyrightconfig.json` was
removed: it referenced a tool that is neither installed nor run. Do not add a
configuration file for a check the repository does not perform. Run whatever
editor checks you use locally, but the three jobs above are the gates.

## Adding a workload brick

A brick deploys one reviewed piece of software on one existing guest. It carries
a manifest, a closed JSON Schema and Ansible playbooks, not kernel branches.
`modules/services/web-nginx/` is the reference implementation.

1. Create `modules/services/<id>/` with:
   - `manifest.yaml`: `id`, `name`, `version`, `description`, `category`,
     `kind: workload`, `capability`, `engine`, `executor`, `requires`,
     `data_class`, `implementation` (pinned upstream role, version, license,
     source, reason), `schema`, `defaults`, `lifecycle` and `verify.assertions`.
   - `iac/snapshot.yml`, `iac/apply.yml`, `iac/verify.yml`, `iac/rollback.yml`
     and `iac/remove.yml`, referenced from `lifecycle`.
   - A `README.md` describing the contract and the refusals.
2. Pin the upstream Ansible role or collection in a requirements file. Never
   commit `modules/*/*/roles/` or `modules/*/*/collections/`.
3. Declare at least one assertion in `verify.assertions` and prove it on a real
   target. A brick without evidence is refused at preparation time.
4. Add tests for the refusal cases: unknown target, missing evidence, schema
   violation, mismatched role version, absent executable.

A multi-brick stack is a preset of dependency-ordered one-brick jobs, not one
large job.

## Adding an extension

An extension registers a provider or a hook through the versioned protocol v1.
Package layout:

```text
my-extension/
  plugin.yaml
  module.py
```

`plugin.yaml` declares `id`, semantic `version`, `entrypoint`, `api_version` and
unique capabilities (`providers:<kind>`, `hooks:<event>`, `data:read-write`).
Local lifecycle, from the application root:

```sh
.venv/bin/python scripts/plugins.py path
.venv/bin/python scripts/plugins.py doctor extensions/lab-notes
.venv/bin/python scripts/plugins.py install extensions/lab-notes
.venv/bin/python scripts/plugins.py enable lab-notes --trust
.venv/bin/python scripts/plugins.py disable lab-notes
```

Review all source and declarations before `--trust`. Enabled code runs inside the
server process with its OS permissions. There is no sandbox, no signature check
and no automatic dependency installation. Only install and enable code you
reviewed and trust.

## Secrets and repository hygiene

- `data/` holds runtime state, configuration, snapshots and vault keys, and stays
  out of Git. `archive/` holds reference copies and stays out of Git too.
- Never commit `*.pem`, `*.key`, tokens or `.env` files. The scanner blocks them
  in tracked files and in history.
- Treat any secret that reached Git as compromised: rotate it first, then remove
  it. Deleting the commit does not unexpose it.
- Tests must cover refusal cases, not only the happy path. A capability that
  accepts bad input silently is not finished.

## Documentation

Public documentation lives in the README and is in English. Keep capability
claims inside the limits of its capability table. Label design proposals as proposals,
not as delivered behaviour. Record user-visible changes in `CHANGELOG.md` under
`## [Unreleased]`.

## Releases

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html),
tagged `vMAJOR.MINOR.PATCH`.

```sh
python3 scripts/release.py --dry-run     # prints the plan, writes nothing
python3 scripts/release.py               # minor bump by default
python3 scripts/release.py --version 1.0.0
```

The script only runs on `main` with a clean tree in sync with `origin/main`
and an empty-refusal check on the changelog. It commits only `CHANGELOG.md` and
creates an annotated tag. It never pushes and never merges: publishing stays a
human decision.

## What a contributor grants

By opening a pull request, a contributor certifies that they wrote the change or
have the right to submit it, and they license it under GPL-3.0-or-later so it can
be merged and distributed with the project. They keep copyright on their own
work.

No contributor license agreement is required today. If the project starts selling
commercial licenses, that changes: a third party cannot relicense code it does not
own, so a CLA or a copyright assignment would be needed before any commercial
license is sold. That step is deliberately not taken now, because it adds a legal
barrier for contributors in exchange for a hypothetical revenue stream.

Do not add a `LICENSE` header to a file you did not write. Add it only when you
create a new source file, and use your own copyright line.

See [SUPPORT.md](SUPPORT.md) for the question channels and the realistic level of
attention a prototype with one maintainer can offer.