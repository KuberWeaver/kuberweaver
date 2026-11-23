# lab-notes

Canonical example of a Python extension: no infrastructure access, only
observation. It registers a diagnostics provider and a `job_finished` hook, and
records what actually happened with provenance.

## Contract

| Field | Value |
| --- | --- |
| `id` | `lab-notes` |
| `api_version` | `1` |
| `entrypoint` | `module.py` |
| capabilities | `providers:diagnostics`, `hooks:job_finished`, `data:read-write` |

It touches no guest, no hypervisor and no secret. Its only state is one local JSON
record, written by the hook. That is the point of the example: an extension can
be purely observational, and it must say so in its report.

## What it proves

- an extension declares `api_version: 1`, a file entrypoint and a capability list;
- the kernel loads the installed copy from `data/extensions/`;
- a provider returns a structured result (`mode`, `findings`, `summary`,
  `explanation`), and the explanation states what it does **not** check;
- a finding carries `provenance`: source event, record path, job id, time;
- `read_only: true` means it cannot mutate anything.

## Read before writing your own

1. Copy this directory to `extensions/<your-id>/`.
2. Change `plugin.yaml`: `id`, `description`, and the capabilities you really use.
   Remove every capability you do not implement; an unused declaration widens the
   trust surface for nothing.
3. Rewrite `module.py` against extension protocol v1.
4. State your limits in the `explanation` field. "Not a health check" belongs in
   the output, not only in a comment.

An extension registers a provider or a hook; it does not deploy software itself.
