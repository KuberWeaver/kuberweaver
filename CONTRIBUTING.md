# Contributing

KuberWeaver is a single Python backend (`backend/kuberweaver`), the `kw` CLI
(`kw`), a pytest suite (`tests`) and operational scripts (`scripts`, `ops`).
Changes reach production through two protected branches and a pull request, so
no contributor needs write access to `main`.

## Branches

| Branch | Purpose | Written by |
| --- | --- | --- |
| `main` | released state | merge from `develop`, through a pull request |
| `develop` | integration | pull requests from feature branches |
| `feat/<topic>` | one topic per branch | contributors |
| `feat/production-track` | long-lived branch of the unattended loop | the loop only |

`main` and `develop` are protected: a pull request is required, the CI check
must be green, and nobody pushes to them directly. The unattended loop never
merges and never opens pull requests, because the host holds no GitHub token.
It pushes `feat/production-track`, and a human opens the pull request into
`develop` when the branch is worth merging.

`feat/production-track` is rebased on `origin/develop` at the start of every
iteration. If the rebase conflicts, the loop aborts it and leaves the tree
untouched instead of resolving the conflict by guesswork.

## Local checks

Run these before opening a pull request. They are the same commands CI runs.

```sh
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL \
  KUBERWEAVER_STATE_DIR=/tmp/kw-check PYTHONPATH=backend \
  .venv/bin/python -m pytest tests -q --tb=short

python3 ops/ci/secret_scan.py && git diff --check
```

A dirty `git diff --check` means trailing whitespace or a conflict marker was
left behind.

## Commits

Subjects follow `type(area): what`, lowercase, no trailing period:

- `feat(area):` a new capability
- `fix(area):` a corrected behaviour
- `test(area):` tests only
- `docs(area):` documentation only
- `chore:` tooling, dependencies, housekeeping

Rules that matter more than the prefix: one topic per commit, a body only when
the reason is not obvious from the subject, no commented-out code, no generated
build output. History is rewritten by rebase on feature branches only, never on
`develop` or `main`.

## Pull requests

1. Rebase the branch on `develop`, run the local checks, push.
2. Open the pull request against `develop` and describe the behaviour change,
   not the file list.
3. Wait for the CI check. A red check is a blocker, not a formality.
4. Squash or rebase as the reviewer prefers. Merge commits into `main` come from
   `develop` only.

## Releases

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) and
tags are `vMAJOR.MINOR.PATCH`.

Work accumulates under `## [Unreleased]`, grouped by `Added`, `Changed`,
`Deprecated`, `Removed`, `Fixed` and `Security`. A release promotes that section
to a dated version heading:

```sh
python3 scripts/release.py --dry-run     # prints the plan, writes nothing
python3 scripts/release.py               # minor bump by default
python3 scripts/release.py --version 1.0.0
```

The script refuses to run when the current branch is not `develop`, when tracked
files are modified, when `develop` is not in sync with `origin/develop`, when the
`Unreleased` section has no entries, when the target version is not greater than
the last tag, when the tag already exists, and when the changelog already
documents that version. Every refusal leaves the repository unchanged.

On success it rewrites `CHANGELOG.md`, commits only that file as
`chore(release): vX.Y.Z` and creates an annotated tag on that commit. It does not
push and does not merge: publishing stays a human decision, taken after the
release commit is on `develop`.

## Repository hygiene

- `data/` holds runtime state, credentials and the local vault, and stays out of
  Git. `docs/prompt/` holds private notes and stays out of Git as well.
- Never commit key material, tokens or `.env` files. `python3 ops/ci/secret_scan.py`
  scans tracked files, and CI runs it with `--history`.
- Tests must cover the refusal cases, not only the happy path. A capability that
  accepts bad input silently is not finished.
- Public documentation is in English, under `docs/`. Commit messages are short
  and factual.

## Unattended loop

`ops/autopilot/` drives one bounded iteration at a time. The backlog lives in
`ops/autopilot/STATE.json`, the contract in `ops/autopilot/RULES.md`, and the
next task is printed by `python3 ops/autopilot/next.py`. The loop implements one
task, runs the checks above, commits on `feat/production-track`, pushes, and
records the outcome in `STATE.json` in the same commit. It never merges, never
force-pushes, never installs system packages and never runs a destructive
operation against infrastructure.
