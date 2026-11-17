# Git-backed declarations

A stack declaration can live in a Git repository instead of being typed into the
portal. CI commits a **bundle** that pins one declaration file and one saved
OpenTofu plan by SHA-256; the portal reads both blobs from the repository's
object database at one exact commit and executes the plan only while every
digest still matches.

The feature is deliberately narrow:

- the repository path is administrator configuration, never a request field and
  never a graph value;
- every Git command is local and read-only (`rev-parse`, `ls-tree`,
  `cat-file`); nothing is fetched, cloned, checked out or pushed;
- the working tree is never read, so an uncommitted edit cannot change what
  runs and a commit that was never pushed cannot be executed by accident;
- verification writes nothing, and execution applies the exact plan bytes
  through the plan applier described below;
- no OpenTofu configuration or variable file is generated, `tofu init` is never
  run and no new plan is produced: a saved plan is executed as-is or refused.

## Administrator configuration

The only source is `<state_dir>/config/declarations.json` (normally
`data/config/declarations.json`). Install it as an administrator-owned file with
mode 0600 inside the private state directory. There is no configuration-write
API.

```json
{
  "schema_version": 1,
  "enabled": true,
  "repository": "/srv/kuberweaver/declarations"
}
```

| Field | Type / default | Meaning |
| --- | --- | --- |
| `schema_version` | integer, 1 | Only version 1 is accepted. |
| `enabled` | boolean, false when the file is absent | Missing file means disabled. |
| `repository` | absolute canonical path, required when enabled | A worktree (with a real `.git` directory) or a bare repository. |

The path must be absolute, already canonical (no `..`, no trailing separator, no
symbolic link in the path or its ancestors) and must exist as a directory when a
request is served. Relative paths, remote URLs, credentials, query strings and
SSH destinations are not accepted; the module performs no network operation.
Unknown fields, duplicate JSON keys, a non-boolean flag, an unsupported version,
malformed JSON, an oversized file and a symlinked configuration file all fail
closed and are reported as `configuration_error`.

`GET /api/declarations` reports `disabled`, `configured` or
`configuration_error` and never the path. **Configured does not mean the
repository is readable or that a plan is valid.**

## Bundle layout

CI writes three files at one commit:

```
stacks/<environment>/<stack>/bundle.json
stacks/<environment>/<stack>/declaration.json
stacks/<environment>/<stack>/plan.tfplan
```

`bundle.json` is the pin; it names the other two files and their digests:

```json
{
  "schema_version": 1,
  "engine": "opentofu",
  "base_generation": 0,
  "declaration": {"path": "stacks/dev/vm-app/declaration.json",
                  "sha256": "<64 hex>"},
  "plan": {"path": "stacks/dev/vm-app/plan.tfplan",
           "sha256": "<64 hex>"}
}
```

| Field | Meaning |
| --- | --- |
| `schema_version` | Only 1 is accepted. |
| `engine` | Only `opentofu` is accepted. A plan from another engine is refused before execution. |
| `base_generation` | The durable state generation the plan was produced from. Execution refuses if the stack has moved. |
| `declaration` | Repository-relative path and SHA-256 of the declaration file at the same commit. |
| `plan` | Repository-relative path and SHA-256 of the saved OpenTofu plan at the same commit. |

`declaration.json` names the stack it declares and carries the same graph the
job API accepts:

```json
{
  "schema_version": 1,
  "environment": "dev",
  "stack": "vm-app",
  "graph": {
    "name": "vm-app",
    "environment": "dev",
    "nodes": [{"id": "vm1", "plugin_id": "libvirt-vm",
               "config": {"cpu": 2, "memory_mb": 2048}}],
    "edges": []
  }
}
```

The declaration's `environment` and `stack` must equal the request, the graph
must validate as a `Graph`, and the graph's own `name` and `environment` must
match too. A mismatch in any of the four is refused rather than silently
reinterpreted.

Paths are repository-relative and use a conservative ASCII subset
(`A-Z a-z 0-9 _ . / -`), start with a letter, digit or underscore, and may not
contain an empty component, `.`, `..`, a dot-prefixed component or a `.lock`
suffix. Absolute paths, backslashes and query-style characters are refused. A
path that resolves to a tree, a symbolic link (Git mode `120000`) or a
submodule (`160000`) is refused; only regular files with mode `100644` or
`100755` are read.

## Revision pinning

The request names one full commit id: 40 hexadecimal characters (SHA-1) or 64
(SHA-256). Abbreviated ids, branch names, tags, `HEAD`-relative syntax and
uppercase ids are refused before any Git command runs. The resolved
`<id>^{commit}` must equal the submitted string, so a pin can never name an
ambiguous or moving ref.

Every file is read through `git ls-tree` and `git cat-file` at that commit, in
order:

1. the bundle blob, whose SHA-256 must equal `bundle_sha256` from the request;
2. the declaration blob at the path recorded in the bundle, whose SHA-256 must
   equal the bundle's `declaration.sha256`;
3. the plan blob at the path recorded in the bundle, whose SHA-256 must equal
   the bundle's `plan.sha256`, and whose first bytes must be a ZIP local file
   header (`PK\x03\x04`), which is how OpenTofu writes a saved plan.

A digest is compared over the exact blob bytes. There is no normalization, no
JSON re-serialization and no line-ending conversion. Editing any of the three
files, or committing a new plan under an existing bundle, produces a mismatch
and a `409`; the plan applier is never called.

## Execution

```
POST /api/declarations/execute
```

The route is administrator-only, requires the session CSRF token and is not
mapped to any agent scope. Execution repeats the entire verification first; a
previous `verify` call is not evidence and is never trusted. It then:

1. refuses with `503` when the repository is disabled or unreadable, or when no
   plan applier is available;
2. takes the exclusive `environment_state` claim for `(environment, stack)`,
   the same claim an apply uses, so a concurrent apply, a held lease or an
   active destruction is refused with `409`;
3. compares the recorded generation with the bundle's `base_generation` and
   refuses a mismatch with `409` (a plan produced against another state is
   stale, not a retry);
4. writes one `declaration_run` row with status `executing`;
5. hands the exact verified plan bytes to the plan applier;
6. requires `{"state": <engine state document>, "resources": [<rows>]}` back,
   and commits `generation + 1` through the claim, with source `apply`;
7. writes one `declaration.executed` audit row and marks the run `executed`.

The generation write is the portal's source of truth, exactly as for any other
apply, so the drift report and later executions see the new resource list. An
emptied resource list is a valid generation.

A failure after the applier has been called keeps the claim: the plan may have
changed the host, and the portal never guesses. The run row is `failed` with
`requires_manual_review` and a fixed `reason` (`execution_failed` or
`state_unconfirmed`), and an administrator clears the claim with the existing
`POST /api/environment-states/{id}/lease/release` route after inspecting the
target. Nothing is retried, rolled back or destroyed automatically.

## Plan applier

`PlanApplier` is the only writer this module owns. It:

- refuses a plan whose digest does not match the verified bytes, before it
  creates any directory;
- refuses when the OpenTofu binary is absent, so `/api/declarations` reports
  `applier.available: false` and execution answers `503` before taking a claim;
- creates `<state_dir>/declarations/<execution id>/` with mode `0700`, writes
  the plan as `declaration.tfplan` (mode `0600`, `O_EXCL`, `O_NOFOLLOW`),
  re-reads and re-hashes it, and writes only `cli.tfrc`
  (`disable_checkpoint = true`);
- runs `tofu apply -input=false -no-color -lock=true -lock-timeout=5s
  declaration.tfplan` with an allowlisted environment
  (`PATH`, `HOME`, `TMPDIR`, `SSH_AUTH_SOCK`, `TF_PLUGIN_CACHE_DIR`,
  `TF_IN_AUTOMATION`, `TF_INPUT`, `TF_CLI_CONFIG_FILE`, `CHECKPOINT_DISABLE`);
- never passes `plan`, `init`, `-out=` or a configuration path, so no new plan
  can be produced and no configuration is read;
- re-hashes the plan after the apply, refuses any unexpected file or directory
  in the folder, and forces files to `0600`;
- reads the result with `tofu show -json` and returns the state document and its
  resource rows.

Provider plugins must be available to the service account, normally through
`TF_PLUGIN_CACHE_DIR`, because the plan artifact does not carry plugin
binaries. A missing provider, an unaccepted plan or a timeout is reported as a
fixed message; provider stdout, stderr and exception text never leave the
process. The private folder is kept as evidence.

## Refusals

| Situation | Status |
| --- | --- |
| Repository disabled, unreadable or misconfigured | `503` |
| Plan applier unavailable (no binary, not wired) | `503` |
| Revision not a full commit id, or not a commit in the repository | `409` |
| Bundle, declaration or plan digest differs from its pin | `409` |
| Bundle or declaration shape, version, engine or generation invalid | `409` |
| Declaration names another environment or stack, or the graph is invalid | `409` |
| Pinned path missing, not a regular file, or outside the repository | `409` |
| Plan is not a saved OpenTofu plan | `409` |
| Stack generation differs from `base_generation` | `409` |
| Apply lease held, or a destruction is quarantined or executing | `409` |
| Run record cannot be written before the applier runs | `503`, claim released |
| Plan applier failed after starting | `502`, claim held, manual review |
| State cannot be committed after the apply | `503`, claim held, manual review |
| Not an administrator, not a human session, or missing CSRF | `403` |

Invalid request fields are `422`; an unknown route is `404`. No refusal
includes a repository path, a plan byte, a command line or provider output.

## What is stored

`declaration_run` holds one row per stack, replaced by the next execution and
created only by execution: environment, stack, status (`executing`, `executed`,
`failed`), revision, bundle path and digest, declaration digest, plan digest and
size, base and resulting generation, execution id, timestamps, actor,
`requires_manual_review` and a fixed `reason`. It never stores plan bytes, the
state document or the repository path. The PostgreSQL backend registers the
table and the SQLite migration script accepts it.

`GET /api/declarations` returns the repository status, the applier status and
the runs, sorted by environment and stack. Any signed-in role may read it.
Verification and execution are administrator-only; agent tokens have no
declaration scope.

## Limits

| Rule | Value |
| --- | --- |
| Configuration file | 16 KiB. |
| Bundle | 64 KiB. |
| Declaration | 256 KiB. |
| Plan artifact | 8 MiB. |
| Repository path in a request | 400 characters. |
| Git command | 20 s, combined output capped, process group killed on timeout. |
| Plan apply | 600 s, output capped, sanitized. |

## Test evidence

`tests/test_declarations.py` builds real repositories under pytest's
`tmp_path`: pinned verification, a working-tree edit that is never read, a newer
commit that does not change a pinned execution, digest mismatch for each of the
three files, a plan edited after its bundle, unknown, short and symbolic
revisions, a symlinked blob, traversal and absolute paths, every invalid bundle
and declaration shape, duplicate JSON keys, a non-plan artifact, base-generation
and lease refusals, administrator/CSRF/agent refusals, configuration statuses,
the PostgreSQL table, the kernel wiring and the routes. The plan applier is
exercised with real staging, modes and digests behind an injected process
boundary that records `apply` and `show` argument vectors and writes a fake
state, so no OpenTofu binary or hypervisor is contacted. The repository is
local; no network Git service was used.
