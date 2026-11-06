# Optional remote GitOps

Remote publication is **off by default**. The existing local repository at
`data/gitops` still receives the same approved artifact and exact commit/readback
check. Remote publication is an additional approval gate, not a replacement for
admission, source fingerprints, provider provenance, graph hashes or serialization.
No destination is inferred from the project repository, `origin`, a job or a plugin.

## Administrator configuration

The only source is `<state_dir>/config/gitops.json` (normally
`data/config/gitops.json`). Install this administrator-owned file with mode 0600
inside the private state directory. Do not put credentials in it. Changes apply to
the next approval; there is no configuration-write API in this module. Use atomic
file replacement, and keep the execution queue idle while changing policy.

SSH example (replace the illustrative destination with a separately approved
GitOps repository; do not reuse the application source repository):

```json
{
  "schema_version": 1,
  "enabled": true,
  "remote_url": "ssh://git@gitops.example.org/team/approved-plans.git",
  "branch": "kuberweaver/approved",
  "required_before_execution": true
}
```

`git@gitops.example.org:team/approved-plans.git` is also accepted.
The administrator file itself is the destination approval authority. No separate
host discovery, automatic remote selection or network approval is performed.

| Field | Type / default | Meaning |
| --- | --- | --- |
| `schema_version` | integer, 1 | Only version 1 is accepted. |
| `enabled` | required boolean | Missing file means disabled. |
| `remote_url` | string, required when enabled | Approved SSH destination, or explicitly permitted absolute local test path. |
| `branch` | string, required when enabled | Branch name, not a refspec; publication targets exactly `refs/heads/<branch>`. |
| `required_before_execution` | boolean, true | Unverified publication blocks approval/queueing when true. |
| `allow_local_test_path` | boolean, false | Allow an explicit canonical absolute filesystem destination for disposable tests. |

Unknown fields, duplicate JSON fields, non-boolean flags, unsupported versions,
malformed JSON, oversized files and symlinked config files fail closed. If a
configuration is invalid, `required_before_execution` is treated as true, even if
the malformed file requested false. Provided destination/branch fields are
validated even when disabled.

Branch names use a conservative ASCII letters/digits/`._/-` subset, starting
with a letter or digit. Empty components, `..`, dot-prefixed components,
`.lock` suffixes and trailing dots/slashes are rejected. SSH accepts ordinary
DNS/IPv4-style hostnames, optional username and optional URL port (1–65535).
Passwords, HTTP(S), tokens in URLs, query strings, percent encoding, spaces,
quoting, shell metacharacters, custom remote helpers and SSH-config aliases with
custom options are not supported. IPv6 URL literals are not currently supported.

Explicit local test example, **not a production destination**:

```json
{
  "enabled": true,
  "remote_url": "/absolute/private/scratch/disposable-gitops.git",
  "allow_local_test_path": true,
  "branch": "approved",
  "required_before_execution": true
}
```

Create a dedicated disposable bare repository at that path first. Relative,
noncanonical/symlinked local destinations and the local artifact repository
itself are rejected. Never configure the project source repository as this
explicit test destination.

Disable with the minimal file, or remove the file:

```json
{"enabled": false}
```

## Publication and failures

1. Existing admission and approval-evidence checks run unchanged.
2. The unchanged local commit and artifact readback finish first.
3. An isolated temporary bare repository shares only local Git object storage;
   it does not read the local repository's remotes, URL rewrites or hooks.
4. Git pushes the approved SHA to the one configured branch, without force,
   deletion, tags, mirror mode or automatic merging.
5. A separate `git ls-remote --refs` must return exactly the approved SHA and ref.
   Successful push exit alone is not verified publication.

A non-fast-forward destination is not overwritten. An administrator must review
and resolve divergence outside the application; it is not automatically fetched,
merged, reset or force-pushed. The local approved artifact remains available.

When required publication fails, approval returns a generic HTTP 503, the job
stays `pending_approval`, sanitized publication evidence is persisted, and the
worker is never enqueued. A later approval is explicit and re-runs all existing
approval checks and local commit/readback. When publication is optional, failure
is persisted as an unverified status and warning event, but execution is queued.
Disabled publication omits `remote_gitops` evidence and preserves the local artifact schema. Old warning events
remain historical evidence after an explicit retry; stale publication summaries
are replaced or removed for the new approval.

A timeout or failed readback is **not evidence that the destination was untouched**:
the push may already have succeeded. Inspect the remote separately before any
manual recovery. Verification is a point-in-time ref readback, not a remote lock
or a promise that another administrator cannot later change the branch.

## Approval reservation and shared-lock safety

After the exact local commit/readback, Jobs durably reserves the job with
`status: approving`, `approval_claim.state: inflight` and
`requires_manual_review: true`. Only then does it release the shared store lock.
Remote publication, including actual push and ls-remote, runs without that lock;
the signed ChatOps caller also releases its outer lock before invoking approval.
Other jobs and worker event/status writes remain unblocked. Concurrent approve,
reject and rollback requests for the reserved job return 409.

Before queueing, Jobs reacquires the lock, compares the current durable job with
the reserved snapshot (allowing only event appends), and revalidates graph,
manifest/source fingerprints, credential mapping, provider provenance, admission
settings/report and the remote publication configuration. A graph/status change
is never overwritten by a stale approved snapshot. Any drift blocks queueing
with a sanitized 409 and durable manual-review requirement, even if the old exact
commit was successfully published. Publication does not authorize another graph.

A normally returned required publication failure durably returns to
`pending_approval` with a completed claim, sanitized remote status and warning;
there is no enqueue. A later explicit approval may retry using freshly captured
evidence and a new local commit. Optional failures still require final evidence
revalidation before queueing.

Unexpected exceptions or process death during publication or final revalidation
leave the durable inflight claim/manual-review flag intact. Restart never clears,
expires, steals or queues such claims. Inspect the local artifact, remote and
execution targets manually; submit a fresh reviewed job as appropriate. There is
no automatic cleanup, force-push, retry or stale-claim override. This uses the
existing single application process/worker per store, not distributed locking.

## Command and transport boundaries

All application subprocesses use argument arrays, `shell=False`, no stdin, and
an allowlisted environment. Publication ignores inherited Git config/environment
and system/global Git config. It disables local Git hooks and uses a fixed
`git -c core.hooksPath=/dev/null receive-pack` receiver command, including for
explicit local tests; no user-supplied receiver command is accepted. The SSH
server must permit that fixed command. Restricted hosted Git SSH services that
only allow literal `git-receive-pack` may reject it; that is reported as a real
failure, never simulated success or a fallback push. Remote servers remain
trusted external systems; a client cannot enforce the internals of a separately
administered SSH service.

SSH uses a fixed command with `-F /dev/null`, `BatchMode=yes`,
`StrictHostKeyChecking=yes` and `ConnectTimeout=10`. Provision the service user's
SSH identity/agent and verified `known_hosts` entries out of band. Unknown host
keys fail; the application does not accept them interactively, disable checking,
write keys, prompt for passwords, or honor per-host SSH proxy commands.

Publication has a 20-second overall subprocess deadline (separate from the
existing local commit timeout), a 65,536-byte combined-output cap per subprocess,
and process-group termination on failures/timeouts. Raw output, URLs, destination
paths, SSH errors and exception strings are never returned, persisted in jobs,
events or audit, or logged by this module. Only fixed status labels are public.
The administrator must secure filesystem/process access separately: the Git
process argument list necessarily includes its configured destination.

## Integration contract

Safe configuration summary for API/UI integration:

```python
from kuberweaver.gitops import safe_status
summary = safe_status(app.state.state_dir)
```

The helper performs no network operation, returns no URL/path/config contents and
returns only `status`, `required_before_execution`, and `verified_commit` (null for
configuration summaries). Configuration status is one of `disabled`, `configured`
or `configuration_error`. **Configured does not mean connected or published.**

Approval jobs use `remote_gitops` with the same three fields when publication is
configured or invalid. Publication status is `verified`, `configuration_error`,
`publish_failed`, `verification_failed`, `timeout` or `output_limit`.
Only `verified` includes `verified_commit`; it must equal `git_commit`.
Do not infer remote success from job execution status or the local commit.
No new API route, provider metadata or UI registration is added here.

## Test evidence

`tests/test_remote_gitops.py` uses actual bare repositories under pytest's
`tmp_path` in the configured scratch directory. It covers exact SHA/artifact
readback, subsequent fast-forward, divergent history without force, required and
optional failures, disabled compatibility, rejected config, bounded process
output/timeouts, incorrect remote readback, hook suppression and local rewrite
isolation. Timeout/output/readback tests inject a disposable Git executable
boundary while still exercising real subprocess supervision; no real SSH
server or external Git service was contacted.

`tests/test_remote_approval_safety.py` additionally pauses real publication to
prove claim exclusion, concurrent approval queues exactly once, rejection/graph
drift cannot queue stale work, and manifest/source/credential/provider/admission
and remote-policy drift all fail closed. The remote tests check lock ownership
at the actual push/ls-remote subprocess boundary through direct API and signed
callbacks, and reopen SQLite after simulated publication/revalidation process
loss to prove durable inflight claims cannot be retried. Callback known-failure
retry tests preserve the pending action while permanently consuming its nonce.

Observed RED → GREEN steps during implementation:

- Missing publisher module: 1 failed → 1 passed.
- Approval lacked remote verification evidence: 1 failed → 2 passed.
- Invalid configuration was accepted: 16 failed → 20 passed.
- Optional-failure warning was overwritten: 1 failed → 26 passed.
- Duplicate administrator fields were accepted: 1 failed → 28 passed.
- Local-only retry retained stale failure evidence: 1 failed → passing retry tests.

The baseline tracked backend suite was also exercised: **159 passed** after
integration. Final combined-workspace verification returned **372 passed,
1 skipped**; `git diff --check` passed. Earlier combined-suite failures occurred
while another agent's LLM diagnostics implementation was incomplete; the final
combined run passed. These results do not claim live SSH acceptance.
