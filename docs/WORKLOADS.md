# Workload bricks

A workload brick deploys one reviewed piece of software on one existing guest.
It wraps a pinned upstream Ansible role, validates a closed set of inputs, runs a
snapshot, apply and verify lifecycle, and proves the result with declared
assertions. It carries no shell, no free-form variables and no secret.

`plugins/web-nginx` is the reference brick. Its contract is described in
`plugins/web-nginx/README.md`; this page describes how the portal prepares,
approves and executes a brick.

## From the portal

1. A user submits a job whose graph has exactly one node using a manifest with
   `kind: workload`. The node names the guest UUID and the SSH key to use.
2. The job is queued for preparation, not for approval. Preparation validates
   the brick contract and resolves the target from recorded evidence, then saves
   a plan. Nothing reaches the guest.
3. The saved plan is visible in the deployment view: fingerprint, actions,
   summary and the bindings the operator is approving. A non-administrator
   cannot approve or execute it.
4. An administrator approves the exact plan. The plan is committed to the local
   GitOps repository and the job becomes eligible for the serialized worker.
5. The worker re-checks the durable approval, stages the selected private key
   into a `0600` file, runs the lifecycle and removes the key material.
6. The job result carries the sanitized evidence: status, brick and role
   versions, target UUID and address, the declared assertions and the changed
   task count. Raw playbook output stays private.

Manual rollback is not offered for a workload job. A brick that fails during
apply or verify runs its own rollback playbook against the snapshot it took
before touching the guest; a failed rollback is reported as `rollback_failed`
and requires manual review.

## Target resolution

The workload target is not a host name supplied by the caller. It is the guest
that a succeeded, approved provisioning job in the same environment recorded:
the job's evidence must name the submitted `target_uuid` together with a
`guest_ip`, a `poweredOn` power state and a VM name.

- No matching evidence: preparation is refused.
- Evidence that names two different addresses for the same UUID: refused as
  ambiguous.
- Several jobs recording the same address: the latest one is pinned, and its
  identity is part of the plan digest.
- With a hypervisor connection wired, the guest is re-read once during
  preparation and once before execution; a changed UUID, name or power state
  refuses the job.

The address is therefore the one the provisioning path verified, not a fresh
guest-agent read. A stale address fails loudly in the brick's own `http-200` or
service assertion, and never silently at another host.

## Inputs

| Field | Owner | Meaning |
| --- | --- | --- |
| `server_name` | brick | virtual host served by the brick |
| `listen_port` | brick | port the brick listens on, default 80 |
| `document_root` | brick | directory served by the brick |
| `proxy_pass` | brick | optional upstream for a reverse proxy |
| `target_uuid` | portal | UUID of the guest the brick runs on |
| `ssh_key_id` | portal | managed SSH key used for the guest login |

Both portal inputs are validated by the manifest schema, bound into the plan
digest and never returned as key material. The private key itself is read from
the managed key registry at execution time.

## Refusals

Preparation fails closed, with `requires_manual_review`, when:

- the brick does not pin its upstream role, or declares no assertion, data class
  or lifecycle playbook;
- the installed role does not match the pinned version, or `roles.lock.json` is
  missing;
- the configuration violates the brick schema or carries an undeclared field;
- the target has no recorded deployment evidence, or that evidence is ambiguous;
- the selected SSH key is unknown, disabled or inconsistent;
- the Ansible executable configured by `KUBERWEAVER_ANSIBLE_PLAYBOOK` is absent.

Submission itself is refused when the graph carries more than one node, an edge
or a failure mode, or when a container node shares the job: one job uses one
execution provider.

## Execution evidence

- `<state_dir>/workloads/<job>/review.json` — the signed plan the approval pins
  (brick, configuration, target, key fingerprint, digests), mode `0600`.
- `<state_dir>/workloads/<job>/plan.json`, `snapshot.json`, `evidence.json` —
  the private lifecycle record, mode `0600`.
- Job events and the prepared plan are served over the API; the HTTP job view
  never includes playbook output, private paths or key material.

`scripts/run_brick.py` remains the standalone driver for a brick outside the
portal; it takes the UUID, address, key and configuration on the command line.

## Limitations

- One brick per job. A multi-brick stack is a preset of dependency-ordered
  one-brick jobs; see [PRESETS.md](PRESETS.md).
- No manual rollback policy for a succeeded brick; the brick's own rollback
  covers a failed apply or verify.
- No removal route in the portal. The brick declares `remove`; the CLI drives it.
- The address comes from recorded provisioning evidence and is not re-read from
  the guest agent.
