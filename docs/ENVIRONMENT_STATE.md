# Environment and stack state

A stack is one graph name inside one environment (`dev`, `staging` or
`production`). KuberWeaver keeps exactly one durable state record per pair, and
that record is the source of truth for what the stack owns. An execution
provider's per-job folder remains its own OpenTofu (or other engine) working
copy; the portal never treats that copy as ownership evidence.

Only an execution provider writes state. There is no HTTP route that creates or
replaces a document, so a client cannot forge a resource list or advance a
generation.

## Record

| Field | Meaning |
| --- | --- |
| `id` | Stable 32-hex identity derived from the environment and stack name. |
| `environment` | `dev`, `staging` or `production`. |
| `stack` | Graph name, 1 to 160 characters, no surrounding spaces or control characters. |
| `generation` | Monotonic counter. `0` means claimed but never applied; each commit adds one. |
| `resources` | Address, type, name and mode of every resource declared by the last applied document, sorted by address. |
| `resource_count` | Length of `resources`. |
| `digest` | SHA-256 of the canonical plaintext document, `null` before the first commit. |
| `job_id` | Job that committed the current generation. |
| `created_at`, `updated_at` | First claim and last commit timestamps. |
| `encrypted` | Always `true`: the document is stored as ciphertext. |
| `lease` | `{job_id, acquired_at}` while an apply holds the claim, otherwise `null`. |

Read responses never include the document itself. `GET /api/environment-states`
lists records and `GET /api/environment-states/{id}` returns one; both are
available to any signed-in role and to an agent token with
`read:infrastructure`.

## Generation and claims

An apply takes an exclusive claim on its stack before it touches a host:

- a second claim for the same stack is refused while the first is held, even
  from another process, because the claim row's primary key arbitrates;
- the claim records the generation the caller observed, and a commit whose
  generation no longer matches is refused. A commit also requires the exact
  claim token, so an apply cannot commit after its claim was released;
- a commit writes the new document, then the record, then drops the claim, and
  finally reads both back. A write that cannot be confirmed is an error, never a
  silent success.

A failed apply keeps its claim when the engine may already have mutated the
host. That blocks the stack until an administrator inspects the host and the
provider evidence, then releases the claim explicitly:

```
POST /api/environment-states/{id}/lease/release
{"job_id": "<the job named by the lease>"}
```

The route is administrator-only and writes one
`environment_state.lease_released` audit row. It is the only way to clear a
lease: nothing expires, retries or remediates on its own.

Every commit writes one `environment_state.applied` audit row with the
environment, stack, generation, job and resource count.

## Encryption at rest

The state document is a JSON object, encrypted with Fernet before it is stored.
The key lives at `<state_dir>/environment-state/state.key`, mode `0600` inside a
`0700` directory; the key file must be a regular file owned by the service
account with no extra hard link, and the path is refused when an ancestor is a
symbolic link.

A missing key is never replaced while documents exist: a fresh key would make
every stored document unreadable, so reads and commits fail with a message that
names the missing key. Back up the key with the database; a copied database or
a leaked dump alone does not disclose provider attributes, but a compromised
service account does, because the key sits beside the data. Key rotation is not
implemented.

The envelope stores the algorithm, the digest and the token. The digest is
verified after decryption, so a truncated, replaced or foreign token is reported
as an unavailable document rather than parsed.

## Limits and refusals

| Rule | Value |
| --- | --- |
| Document | JSON object, at most 4 MiB of canonical JSON. |
| Resources | At most 256 rows, each `address` unique. |
| Address | 1 to 200 characters from `A-Z a-z 0-9 _ . [ ] " : -`. |
| Type and name | 1 to 120 characters from the same set. |
| Mode | `managed` (default) or `data`. |

Rows with unknown fields, duplicate addresses, a relative or traversal-shaped
address, an unsupported mode or a non-JSON document are refused before anything
is stored.

## Execution provider integration

`TofuExecutor` uses the store when the kernel resolves it inside the portal
(`provider.environments`). It then:

- claims the graph's environment and stack before the apply and releases the
  claim when a failure happened before any attempt marker;
- refuses to apply when the stack already has a committed generation from
  another job, because updates are not supported;
- commits the applied state and its two owned resource addresses, so the portal
  record is written before the job reports success;
- requires a removal to be performed by the job named in the record and commits
  the emptied state as a new generation instead of deleting the record.

A standalone executor constructed outside the portal (`environments=None`) keeps
its previous behaviour and its per-job folder remains its only record.

The PostgreSQL backend registers `environment_state`,
`environment_state_documents` and `environment_state_claim`; the SQLite
migration script accepts the same three tables.
