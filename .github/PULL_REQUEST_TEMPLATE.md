<!-- Target branch: develop. Do not open this pull request against main. -->

## Behavior change

What changed and why. Describe the effect an operator or another developer can
observe, not the list of files. If nothing observable changed, say so explicitly.

## Tests

Which tests were added or updated, and the exact command you ran locally. Cover
refusal and failure cases, not only the happy path. If no test applies, explain
why.

```sh
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL \
  KUBERWEAVER_STATE_DIR="$state" PYTHONPATH=backend \
  .venv/bin/python -m pytest tests -q
```

## Documentation

Which documentation was updated (README, `docs/`, CONTRIBUTING.md) and which claim
changed. If the change moves a capability limit, update the README capability
table here as well. If no documentation change is needed, say why.

## Checklist

- [ ] The branch is rebased on `develop` and carries one topic per commit.
- [ ] Commit subjects follow `type(area): what`: lowercase, no trailing period.
- [ ] Local checks pass: the pytest command above, `python3 ops/ci/secret_scan.py`
      and `git diff --check`.
- [ ] CI is green. A red check blocks the merge.
- [ ] No secret, credential, `data/`, `docs/prompt/`, `*.pem` or `*.key` content
      was added, in this commit or in the branch history.
- [ ] No new claim of production readiness, multi-tenancy, SSO coverage or
      extension sandboxing.
- [ ] A reviewer can reproduce the result from this description alone.
- [ ] User-visible changes are recorded under `## [Unreleased]` in CHANGELOG.md.
