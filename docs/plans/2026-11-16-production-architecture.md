# Production architecture: infrastructure as code, versioning, rollback

Status: proposed. This document describes the target architecture for running
KuberWeaver as a production tool rather than a catalogue of demonstrations. It
does not describe features that already exist.

## The gap to close first

A production infrastructure tool needs three properties that the current build
does not have:

1. **A durable state per environment and stack.** Today every job holds its own
   state directory and its own `terraform.tfstate`. Jobs are islands. Without a
   long-lived state, a plan can only say "create", never "this VM exists, that
   field differs, that resource is missing".
2. **A destroy path.** A VM can be created and never removed. Rollback is
   therefore impossible by construction: the only undo is a deletion the tool
   cannot perform.
3. **Drift detection.** Nothing compares what was declared to what is actually
   running. Artifact drift (a plan edited between preparation and apply) is
   already handled; inventory drift is not, and it is a different problem.

Anything else, including environments named `production`, is decoration until
these three exist.

## Three planes

    desired state (Git)  ->  control plane (KuberWeaver)  ->  execution backends
         declarations              admission, approval,          OpenTofu (VM)
         review, history           evidence, audit               Ansible (in-guest)

The contract between planes is intentionally narrow: a declaration in, an
artefact out. No plane reaches around another.

### 1. Desired state plane

Git is the source of truth for intent. Layout:

    infra/
      envs/dev/stack.yaml
      envs/staging/stack.yaml
      envs/production/stack.yaml
      profiles/lab.yaml
      profiles/guarded.yaml
      profiles/strict.yaml

Rules:

- One file per stack, in the schema the portal already validates.
- A pull request is the change request. Merge is the approval of intent.
- CI validates schema and policy, produces the plan, and stores the plan
  artifact. The artifact is immutable and identified by its digest.
- Secrets never enter this repository. Only references, always.

### 2. Control plane

Responsibilities that must not leak into the backends:

- admission (schema, policy, quotas, security profile resolution)
- approval, bound to the exact plan digest, with actor and timestamp
- execution of the provided plan artifact, never a re-plan at apply time
- resource lifecycle bookkeeping
- evidence and audit, append-only, shipped off the box

The current digest-bound approval model is sound and should stay. The change is
that the plan must come from CI and be executed as-is, so that what a human
approved is byte-identical to what runs.

### 3. Execution backends

**VM lifecycle: OpenTofu plus the native OVF import.** Keep the pins, the
private namespace, and the SDK readback. Add the lifecycle actions below.

**In-guest configuration: Ansible.** Acceptable only under strict conditions:
the playbook is a named, versioned role from Git, pinned by commit and checksum
in the plan; every role ships apply, verify and restore; no ad-hoc playbook, no
free-form variables. The existing container executor already demonstrates the
pattern with snapshot, apply, verify, restore; generalize that contract, not its
implementation.

## State model

One state object per environment and stack, stored encrypted, with a lock:

    generation      monotonic counter, incremented per successful apply
    declared        digest of the graph that produced this state
    resources[]     name, uuid, kind, annotations, managed fields, last verify
    history[]       generation, digest, actor, timestamp, outcome

Job directories keep evidence only: plan, approval, output logs, verification
report. State lives outside them and outlives them.

Consequences worth stating plainly:

- A plan becomes a diff: create, update, replace, destroy, no-op.
- `import` becomes mandatory, so pre-existing VMs can be adopted into state
  instead of being recreated or ignored.
- Two concurrent applies on the same stack are refused by the lock, not by luck.

## Lifecycle and rollback, per resource class

| Action | Reversible | How it is undone |
| --- | --- | --- |
| VM create | no | destroy, with ownership proof and quarantine |
| VM reconfigure | yes | snapshot of the config spec before apply, restore on failure |
| In-guest configuration | yes | snapshot/restore or revert the declaration and converge |
| Data inside the guest | no | out of scope, never automatic |

Rules that follow:

- **Destroy requires proof of ownership.** The job marker annotation, the UUID,
  and a state record must all agree. A VM the state does not own is never
  destroyed, even if the name matches.
- **Destroy is quarantined.** Power off, wait a configurable window, then delete.
  Cancellation is possible during the window.
- **Replace is destroy then create, in that order, with the destroy verified
  first.** Never the reverse, or two resources race for the same name.
- **A failed rollback is a first-class state.** The job ends `rollback_failed`,
  the resource stays flagged, and further applies on that stack are blocked
  until a human resolves it. Silent success is worse than a stuck resource.
- **Rollback never invents state.** If the previous spec was not recorded, the
  honest outcome is "manual review", not a best guess.

## Versioning, precisely

| Subject | Pinned by |
| --- | --- |
| Declaration | Git commit sha |
| Plan | digest of the canonical plan document |
| VM image | sha256 of the OVA, already enforced |
| In-guest role | Git sha plus checksum, recorded in the plan |
| Container image | digest, never a tag |
| Secret | vault binding revision, already enforced |
| Tool binaries | sha256 pins, already enforced |

A deployment is reproducible when all six resolve to the same values. Any
mutable tag anywhere breaks that property.

## Promotion

Promotion moves an artefact, it does not re-plan:

    plan in dev -> approve -> apply -> verify
    promote the same plan digest to staging -> apply -> verify
    promote the same digest to production -> canary -> apply -> verify

Different environments mean different credentials, different state, different
network boundaries. They must not mean a different plan. Re-planning per
environment is how staging stops predicting production.

Production gates, in addition to the digest: canary of one, four-eyes approval,
a freeze window, and a recorded rollback target.

## Drift

Nightly, read-only, per stack: compare declared resources with live inventory on
UUID, name, CPU topology, memory, disk, attached networks, annotation and power
state. Produce a diff report; never remediate automatically.

Remediation is a new plan, approved like any other. Automatic remediation
without approval turns a detection bug into an outage.

## Resource kinds, adapters and the interface

The interface must not expose *how* something is deployed. It exposes resources
and lifecycles. The backend is a property of the catalog entry, resolved by the
control plane and invisible to the operator.

### One lifecycle contract, N adapters

Every adapter, whatever it drives, implements the same six operations:

    plan      produce a diff against recorded state
    apply     execute the approved diff
    verify    read the live result back from the source of truth
    snapshot  record what is needed to undo apply, when undo exists
    rollback  restore the recorded target, or report irreversibility
    destroy   remove owned resources, with proof of ownership

An adapter that cannot verify, or cannot state its irreversibility, does not
enter the production catalog. That single rule is what keeps the model small:
no backend gets a private lifecycle, a private approval path, or a private
screen.

### Three resource kinds

| Kind | Represents | Typical adapter |
| --- | --- | --- |
| `compute.vm` | one machine | OpenTofu plus native OVF |
| `compute.cluster` | one Kubernetes control plane and its nodes | OpenTofu plus a pinned bootstrap |
| `workload` | one deployable unit on a target | packages and config for a VM, a pinned release for a cluster |

A workload names its target, and the target's kind decides the adapter. The
operator picks "nginx, on this target"; the tool resolves whether that means a
package plus a site configuration or a release on a cluster.

### Kubernetes, specifically

A cluster is a resource. Provisioning it is the same shape as a VM: state,
ownership, destroy. Two things change after that:

- the cluster becomes the natural place to get **revisioned rollback**. A pinned
  release records a revision; rolling back is a recorded, addressable operation
  rather than a reconstruction from memory. This is the primitive the VM path
  lacks and the reason Kubernetes is worth it for anything with a release
  cadence.
- continuous reconciliation belongs to the cluster, not to this tool. Once the
  cluster exists, Argo CD or Flux reconcile declared state inside it. The
  control plane owns the cluster lifecycle and the release revision it
  authorized; it does not become a Kubernetes dashboard and does not duplicate
  a reconciler.

The boundary in one line: **KuberWeaver provisions and audits, the cluster
reconciles.** Two reconcilers on one object is a split brain waiting to happen.

### The interface, concretely

One list, not one screen per backend:

    name                kind              target      desired      live      drift
    kuberweaver-test    compute.vm        esxi-lab     a1b2c3d      ok        none
    lab-cluster         compute.cluster   esxi-lab     f4e5d6a      ok        none
    nginx-edge          workload          lab-cluster  0c1d2e3      ok        cpu request
    api                 workload          kuberweaver-test  99887766  ok      none

Row actions are identical everywhere: plan, apply, verify, rollback, destroy.
The detail view shows the diff, the artefact digests (image, role, chart), the
recorded rollback target and the evidence trail. Nothing else.

The catalog filters by environment and by capability. It never offers an action
the adapter cannot honour, and it never shows a backend name as a configuration
field.

### Docker on a VM

Not a resource kind. Running a container on a single VM adds a layer without
adding a primitive: no revisioned rollback beyond the image tag, no scheduler,
no reconciliation. It is a `workload` on `compute.vm`, and if the simplest
package plus configuration does the job, that is the correct answer. Adopt
containers when you need reproducible artefacts, and a cluster when you need
scheduling and release history. Not before.

## Order of work

Each step is useful on its own and none can be skipped.

1. **Environment-scoped durable state, and import.** Without it, no diff, no
   drift, no safe destroy.
2. **Destroy and replace, with ownership proof, quarantine and verified
   ordering.**
3. **Git-backed declarations, with CI-produced plan artifacts executed as-is.**
4. **Drift report.**
5. **Ansible executor generalized under the snapshot/apply/verify/restore
   contract, with pinned roles only.**
6. **`compute.cluster` as a resource:** provision, kubeconfig handover, upgrade,
   destroy. Same state and ownership rules as a VM.
7. **`workload` with a pinned release artefact**, plus revisioned rollback from
   the recorded release, and handover of continuous reconciliation to Argo CD or
   Flux.

## What must not be relaxed

- The environment restriction on the VM blueprint stays until steps 1 to 3 are
  complete. A `production` label without destroy, state and drift is a false
  assurance.
- No arbitrary playbook execution, no ad-hoc command, no free-form variable
  reaching a backend. The whole safety story rests on the narrow contract.
- No re-plan at apply time.
- No automatic remediation of drift.
- No secrets in Git, plans, logs or evidence.

## One sentence

Production means the tool can say what it owns, prove what it did, and undo what
it must. Creation alone satisfies none of those.
