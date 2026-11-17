# Environment and stack state

A stack is one graph name inside one environment (`dev`, `staging` or
`production`). KuberWeaver keeps exactly one durable state record per pair, and
that record is the source of truth for what the stack owns. An execution
provider's per-job folder remains its own OpenTofu (or other engine) working
copy; the portal never treats that copy as ownership evidence.

Only two paths write state: an execution provider through the apply claim, and
the audited adoption path, which accepts one VM's UUID and ownership marker,
verifies that VM against the hypervisor, and records the single resource itself.
No route accepts a resource list or a document from a client, so a client cannot
forge a resource list or advance a generation.

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
| `job_id` | Job that committed the current generation, `null` for an adopted or destroyed generation. |
| `source` | `apply` for an execution provider, `adoption` for an imported VM, `destroy` for an emptied generation. |
| `adopted` | `{uuid, name, marker, connection_id, by}` for an adopted generation, otherwise `null`. |
| `destroyed` | `{uuid, at, by}` for a destroyed generation, otherwise `null`. |
| `created_at`, `updated_at` | First claim and last commit timestamps. |
| `encrypted` | Always `true`: the document is stored as ciphertext. |
| `lease` | `{job_id, acquired_at}` while an apply holds the claim, otherwise `null`. |
| `destroy` | The destruction record below, or `null` when the stack was never asked to destroy anything. |

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

## Adoption of an existing virtual machine

A VM that the portal already created can enter state without being recreated.
An administrator submits its UUID and the ownership marker the portal wrote into
the VM annotation when it created that VM:

```
POST /api/environment-states/adoptions
{"environment": "dev", "stack": "vm-app", "connection_id": "<32 hex>",
 "uuid": "11111111-1111-4111-8111-111111111111",
 "marker": "kuberweaver-job:<32 hex job id>"}
```

The route is administrator-only, needs the session CSRF token, and is not mapped
to any agent scope. It reads the VM once through the recorded connection and
never creates, reconfigures, powers on, stops or deletes anything.

The ownership proof is the pair (UUID, annotation). The ESXi extension reports
`ownership_sha256` for a VM whose annotation starts with `kuberweaver-job:` and
`null` otherwise, because an annotation is free-form operator text; the portal
hashes the submitted marker and compares digests. These are refused with 409:

- the VM is missing or cannot be read, so ownership is unverified;
- the live UUID is not the requested UUID;
- the VM carries no portal marker, or its marker is not the submitted one;
- the stack already has a committed generation, so applied and adopted
  resources are never mixed;
- an apply holds the stack's lease;
- another stack already recorded the same UUID.

The first adoption writes generation 1 with `source: "adoption"`, `job_id: null`,
one resource row `esxi_vm.<uuid>` and a document holding the UUID, marker, name,
power state, CPU and memory read from the host. Repeating the same request
verifies the live VM again, returns `{"adopted": false, "unchanged": true}`,
keeps the generation and writes one `environment_state.adoption_repeated` audit
row, so a re-import cannot duplicate the resource or advance the generation. A
changed annotation makes the repeat fail. `GET
/api/environment-states/adoptions` lists the UUID index for any signed-in role.

A driver that does not report ownership evidence fails closed: an older ESXi
extension makes adoption answer 409 rather than adopt an unproven VM.

## Destruction of an owned VM

A destroy is irreversible, so it is an explicit state machine tied to one
recorded stack. The portal changes nothing on the host before the last step, and
only a VM that the record owns and whose live annotation still matches is ever
handed to a driver.

```
POST /api/environment-states/{id}/destroy
{"connection_id": "<32 hex>", "uuid": "11111111-1111-4111-8111-111111111111",
 "marker": "kuberweaver-job:<32 hex job id>"}
```

The route is administrator-only, needs the session CSRF token, and is not mapped
to any agent scope. It is refused unless the stack has a committed generation
with exactly one managed resource whose address is `esxi_vm.<uuid>`, no apply
holds the stack's lease, and the live VM proves the same ownership as adoption:
the UUID matches and the annotation digest matches the submitted marker. For an
adopted record the submitted marker must also equal the recorded one, so
re-annotating the VM with a different marker does not transfer ownership.

The request writes a destruction record with status `quarantined`, a random
`destroy_id` and `not_before = requested_at + 15 minutes`. Nothing is read from
or changed on the host after the proof read. Repeating the same connection, UUID
and marker returns `{"changed": false}` with the original window; a different VM
or a second concurrent request is refused. While a destruction is `quarantined`
or `executing`, an apply claim on that stack is refused.

```
POST /api/environment-states/{id}/destroy/cancel  {"destroy_id": "..."}
```

Cancellation is allowed only during the quarantine window. It writes `cancelled`
and changes nothing else; a host call cannot have happened. Once execution has
started, cancellation is refused because the driver may already be deleting.

```
POST /api/environment-states/{id}/destroy/execute  {"destroy_id": "..."}
```

Execution is refused before `not_before`, after a cancellation, and on a second
call. The transition to `executing` is the arbiter, so a cancel racing it loses.
The portal then re-reads the VM and re-proves the UUID and annotation, because
the annotation can change while the window is open, and only then calls the
driver's `destroy_resource(connection, credentials, uuid, marker)`. The method
must return `True` only after it has confirmed the VM is gone; any other return
value is treated as unproven.

On success the stack receives generation `n + 1` with `resources: []`,
`source: "destroy"`, `job_id: null` and `destroyed: {uuid, at, by}`, and the
destruction record becomes `destroyed`. The emptied generation is written
before the destruction record, so a failure in between leaves the record
`executing` and is reported as unverified rather than as success.

| Situation | Destruction status | Stack record |
| --- | --- | --- |
| The live proof fails before the driver is called | `failed`, `reason: ownership_unverified` | unchanged |
| The driver has no `destroy_resource` method | `failed`, `reason: destruction_unsupported` | unchanged |
| The driver raises or does not return `True` | `rollback_failed`, `requires_manual_review: true` | unchanged |
| The VM was destroyed but the empty generation could not be written | `rollback_failed`, `requires_manual_review: true` | unchanged or partially written, never trusted |

A `failed` record changes nothing on the host, so it can be replaced by a new
request once the cause is fixed. A `rollback_failed` record is closed only by an
administrator who has looked at the host:

```
POST /api/environment-states/{id}/destroy/resolve
{"destroy_id": "...", "outcome": "intact"}
```

`intact` re-reads the VM and requires the recorded UUID and marker to still be
present; the destruction becomes `failed` with `reason: resolved_intact`, the
generation is untouched, and a new request is allowed. With `outcome:
"destroyed"` the administrator asserts that the VM is gone; the portal commits
the emptied generation and records `destroyed` with `reason:
resolved_destroyed`. Neither outcome is inferred by the portal and nothing is
retried automatically.

The state record is never deleted, and the UUID stays in the adoption index as a
tombstone, so a destroyed VM's UUID cannot later be adopted by another stack.
The `destroy` object in a read response carries the status, UUID, marker,
connection, requested/started/finished timestamps, `reason` and
`requires_manual_review`; it never carries a document.

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

A Git-backed declaration bundle (see [declarations](DECLARATIONS.md)) is the
other writer: `DeclarationService` verifies a pinned commit, takes the same
claim, refuses when the recorded generation differs from the bundle's
`base_generation`, applies the exact saved plan through its applier and commits
the resulting generation. It never initialises, plans, retries or rolls back,
and a failure after the applier started keeps the claim for manual review.

The PostgreSQL backend registers `environment_state`,
`environment_state_documents`, `environment_state_claim`,
`environment_state_adoption`, `environment_state_destroy`,
`environment_state_drift` and `declaration_run`; the SQLite migration script
accepts the same seven tables. The drift report itself is documented in
[drift reports](DRIFT.md).
