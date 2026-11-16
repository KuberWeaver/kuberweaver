# OpenTofu / libvirt trusted extension

**Status: planned / not live-certified. Disabled by default.** Historical real provider
initialization, validation and a saved create plan were exercised against the
existing hypervisor. **Current strict host-trust checks block that transport** (see
below); the historical planning evidence is not current acceptance. No apply or
destroy has been authorized or run. Installing
or registering the extension does not make the catalogue entry executable.

## Deliberately narrow scope

- Executor name `opentofu`; extension `examples/extensions/opentofu-runtime`.
- One independent `libvirt-vm` node, `dev` only, no edges/failure injection.
- Integer CPU 1–2, memory 64–512 MiB, blank qcow2 disk 1–2 GiB.
- A **stopped VM definition**, not a bootable OS, cloud-init service or working
  guest. Network attachment uses existing `lab-nested`; storage uses existing
  `infra-lab`. The only managed resources are `libvirt_domain.vm` and
  `libvirt_volume.disk`. Existing network/pool/base image are never managed.
- No updates, import/adoption, replace, arbitrary HCL, host, URI, image, shell,
  graph-supplied path, provisioning script, or automatic failed-apply cleanup.
- The existing CirrOS disk is attached to running `nested-smoke`. It is **not an
  immutable template** and this extension never clones, uses or changes it.
  A separately reviewed immutable OS image and bootstrap/boot verification are
  prerequisites for extending this into a usable guest provisioning capability.

## Actual read-only discovery and evidence

Authorized transport is exclusively:

```sh
ssh -F "$KUBERWEAVER_SSH_CONFIG" infra-hypervisor \
  'virsh -c qemu:///system version'
```

Observed libvirt 10.0.0, QEMU 8.2.2, accessible `/dev/kvm`, user `ubuntu` in
`libvirt`/`kvm`, running `nested-smoke`, active pool `infra-lab` and NAT network
`lab-nested` (192.168.250.0/24). `default` pool does not exist; default network is
inactive. All discovery commands were read-only; no remote sudo/install used.

OpenTofu 1.13.9 was downloaded from the official GitHub release to
`/home/ubu/.hermes/cache/scratch/kw-tofu-tools/tofu`. The ZIP SHA256 was compared
with the official release `tofu_1.13.9_SHA256SUMS`:

```text
bc44eb6b06446ba83bd4072114891987f590f66cca712737a7f1147c50c539ce
```

This verifies the published checksum over HTTPS, **not a separately verified
Cosign/GPG signature**. Scratch is temporary; provision an administrator-owned
binary for durable use, never a user-selected binary from an API request.

Reproduce without guest mutations:

```sh
.venv/bin/python scripts/tofu_preflight.py \
  --binary /home/ubu/.hermes/cache/scratch/kw-tofu-tools/tofu \
  --state-dir /home/ubu/.hermes/cache/scratch/kw-tofu-acceptance \
  --remote --plan
.venv/bin/python -m pytest tests/test_tofu_execution.py -q
.venv/bin/python scripts/plugins.py doctor examples/extensions/opentofu-runtime --execute --trust
```

`--plan` executes real `tofu init`, `tofu validate`, `tofu plan -out=create.plan`
and `tofu show -json create.plan`. It never applies. Each invocation creates a new
private preflight namespace. The following is **historical evidence, predating
strict effective-host-trust enforcement**, not the latest acceptance result:

```json
{
  "status": "preflight-passed-not-applied",
  "tofu_version": "1.13.9",
  "mutations_performed": false,
  "plan": {
    "job_id": "preflight-495829e2792878b0",
    "plan_digest": "74928e23c682d00cdd796024e53e6a19e26e5dc58a7a09d3a4c66cb4573ca37e",
    "actions": {
      "libvirt_domain.vm": ["create"],
      "libvirt_volume.disk": ["create"]
    }
  }
}
```

Artifacts are under that state directory/job ID, private, not repository assets.
Do not attach raw binary plans, state or plan JSON to an API/event/log: they can
contain sensitive information. Source changes invalidate previous plan approvals.
A fresh remote inventory still contained only the original running nested guest
and original two pool entries after planning.

## Approval/host integration contract (not silently bypassed)

Current core admission expects web-service `targets` and `port`, and denies
`capability: planned`. The new manifest intentionally has neither fake web
fields nor `live`. **There is currently no end-to-end HTTP provisioning claim.**
The parent kernel must implement a reviewed provider-neutral infrastructure
admission contract before this can be selected for real user jobs. Do not mark it
live or bypass quotas/RBAC to make a catalogue card look functional.

The executor provides the following exact interfaces for a future host integration:

```python
provider.read()                          # cheap safe local status
provider.preflight(remote=True)           # read-only remote capability probes
metadata = provider.prepare_plan(job)     # BEFORE human approval, job has id + graph
# Display metadata actions/name/graph_hash/plan_digest to the administrator.
# Host persists this server-produced digest ONLY after explicit approval:
job['approved_tofu_plan'] = metadata['plan_digest']
# Core must also persist unchanged normalized graph, approved_graph_hash,
# approved_by, approved_at and approved_execution_provider.name == 'opentofu'.
result = provider.execute(job, emit)       # only apply EXACT saved create.plan

removal = provider.prepare_removal(job)    # separate read-only saved destroy plan
# A separate explicit administrator removal approval pins this digest:
job['approved_tofu_removal'] = removal['plan_digest']
result = provider.rollback(job, emit)      # only apply EXACT saved destroy.plan
```

`prepare_plan(job)` is the preapproval callback, not `plan(graph)`: it requires the
server-issued job ID to create a dedicated state/ownership namespace. Call it
**after normalization/admission and job identity creation, before approval**.
Store/display its safe metadata with the pending job. Never generate it in
`execute()` or automatically approve its digest. Existing core `Jobs.rollback`
does not prepare/approve a saved removal plan; until a host remove-approval action
is wired, this executor correctly rejects rollback. No shared core files were
changed by this extension.

An installation is disabled until an administrator explicitly trusts it:

```sh
.venv/bin/python scripts/plugins.py install examples/extensions/opentofu-runtime
# Review Python, template, fixed SSH wrapper and manifest before enabling.
.venv/bin/python scripts/plugins.py enable opentofu-runtime --trust
```

The administrator may set `data/plugin-data/opentofu-runtime/runtime.json` to
`{"binary":"/absolute/administrator-owned/tofu"}` before enabling. This is local
trusted configuration, not graph input. With no configured binary, the extension
still loads but advertises `available: false`, `status: planned`. Enabling does
not select a binding, approve a plan or certify live operation. The default
binary path is `<project>/tools/tofu`; this task used scratch instead of creating
an unignored tools directory.

## Ownership, state and removal safety

Private directories (0700) contain plan/config/state/receipts (0600). A local
nonblocking `flock` serializes this executor state root; OpenTofu state locks are
also enabled. Use exactly one administrator-owned state root per lab; this is
**not a distributed lock against unrelated OpenTofu clients or hypervisor admins**.
The application must prevent simultaneous external mutations during apply.

The approved digest binds graph, generated names, saved binary plan, config,
lockfile, OpenTofu binary, provider distribution files, executor source, reviewed
template, SSH wrapper, SSH configuration, effective `ssh -G` settings and every
referenced user/global `known_hosts` file. `cli.tfrc` is both digest-pinned and
required to contain exactly `disable_checkpoint = true\n`; no `dev_overrides`
or other additions are accepted. Before every OpenTofu command, an artifact
allowlist rejects extra HCL/auto-tfvars/CLI config files, modules and symlinked
provider trees, and rechecks approved executable/configuration pins. Apply does
not re-init or re-plan.
The process environment excludes arbitrary `TF_*` overrides/logging. Raw provider
output and exception text are suppressed. Plans are never overwritten/reused;
any existing job state or previous apply attempt blocks create retries.

After a successful apply, provider state must contain exactly the two expected
addresses. Independent SSH readback matches domain name/UUID and volume name/key
and records inactive-domain/volume XML hashes. Only then is an ownership receipt
written and success returned. Failed/partial apply has no receipt and **never
triggers blind destroy**. Manual forensic review is required.

Removal is not universal rollback: it only deletes objects created by this exact
approved job. It requires the unmodified create approval, matching state receipt,
unchanged independent XML, a no-drift refresh-only plan, no other domain referencing
the disk, and a separately approved saved two-delete plan. Foreign domains are
enumerated from all-domain and active inventories: persistent XML is required for
every foreign guest, with live XML additionally required for each active guest.
Unreadable/malformed XML blocks removal (transient guests without persistent XML
also block it). Disk sources/backing sources are parsed as XML, compared using
normalized absolute paths and `infra-lab` pool/volume identity; XML character
escaping cannot conceal an attachment. These checks repeat
before applying the saved delete plan. Remaining state must be empty and remote
names absent before `rolled_back` is returned. Interrupted or failed removal is
not blindly retried. Benign external XML/state changes also fail closed; there is
no promise to restore any preexisting VM, network, image or guest data.

## Effective host trust and current blocker

Host trust is inspected **before remote inspection or plan initialization**, and
again before application/removal commands. Strict host-key checking must be
`yes`; DNS-based trust, automatic host-key updates, `KnownHostsCommand`, dynamic
`Include`/`Match` configuration, proxy commands/jumps and multiplexed control
sockets are rejected. Referenced known-host files must be existing regular files
at unambiguous absolute paths with no symlink ancestors; their bytes and the
effective configuration are bound to approval. Missing/unsupported trust fails
closed rather than claiming it was pinned. No insecure flags are added.

The current fixed lab SSH config uses a `ProxyCommand` invoking `sshpass` and an
inner `ssh -S .../host-control`. Hashing the outer config or even its known-hosts
file **does not pin the inner SSH process, its implicit configuration/trust, or
the already-authenticated master behind that socket**. This executor therefore
rejects that transport rather than pretending to inspect its entire trust chain.
An administrator must separately review/provide a directly inspectable transport
before new plans can be produced; this task does not edit live SSH configuration,
close sockets, alter known-host entries or relax checking to get a plan.

Current real command evidence, using the existing scratch OpenTofu binary:
`tofu_preflight.py --remote --plan` returned exit 1, `status: blocked`,
`tofu_version: 1.13.9`, `mutations_performed: false`; all four remote checks were
false. Direct host-trust inspection reported
`Uninspectable SSH proxy/controlsocket/command trust`. No new remote plan/apply or
removal was executed. Prior saved plans are historical/private and invalidated
by the changed source pin.

Safety regression evidence: the original 14 cases pass, expanded to **41 passed**
in `tests/test_tofu_execution.py`. The repository suite returned **483 passed,
1 skipped**. New behavioral guards were exercised RED before implementation
(live/persistent attachments, unreadable live XML, changed CLI config/unexpected
artifacts, effective/known-host trust, and Include-equals parsing), then GREEN.
Existing interruption safety was additionally verified with timeout and keyboard
interrupt after a fake apply side effect for both create/removal, followed by a
new executor instance: attempt receipts prevented all blind retries. These are
fake-process-boundary tests, **not live apply/removal certification**.

The executor remains trusted local code, not protection against a malicious
same-user process racing file replacement. External hypervisor changes must be
excluded operationally; inventory/XML inspection is not an atomic libvirt lock.
Path comparison is lexical normalization, not remote inode/symlink equivalence;
removal must not be treated as certified for unreviewed storage aliases.

## Official syntax / transport references

Pinned provider is `dmacvicar/libvirt = 0.9.9`, not legacy 0.8 HCL. The template
uses the current XML-aligned `devices` attributes, `capacity`, `target.format`,
`memory_unit`, and `running = false`:

- [Domain resource, official versioned source](https://github.com/dmacvicar/terraform-provider-libvirt/blob/v0.9.9/docs/resources/domain.md)
- [Volume resource, official versioned source](https://github.com/dmacvicar/terraform-provider-libvirt/blob/v0.9.9/docs/resources/volume.md)
- [OpenTofu standalone installation and checksums](https://opentofu.org/docs/intro/install/standalone/)
- [Provider transport factory](https://github.com/dmacvicar/terraform-provider-libvirt/blob/v0.9.9/internal/libvirt/dialers/factory.go)
- [Provider native OpenSSH transport](https://github.com/dmacvicar/terraform-provider-libvirt/blob/v0.9.9/internal/libvirt/dialers/sshcmd.go)

The generated provider documentation still says remote transport is not
implemented; its versioned source and the real successful plan prove
`qemu+sshcmd` works. The fixed trusted wrapper adds the existing `-F` SSH config;
no insecure host-key bypass or alternate host is permitted by graph input.
