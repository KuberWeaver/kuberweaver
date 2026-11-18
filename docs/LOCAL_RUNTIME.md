# Local runtime: rootless OCI and QEMU TCG

`local-runtime-v1` is an administrator-controlled execution provider for the
two reviewed bricks `local-oci` and `local-qemu`. It runs real local runtimes on
the controller; it is not a simulator and not an untrusted-code sandbox.

- `local-oci` starts a rootless runc container from a private bundle and serves
  one job marker over `127.0.0.1`.
- `local-qemu` boots a fixed kernel/initramfs under QEMU TCG, proves guest init
  through a serial marker, and confirms QMP reports a running process.

No graph field selects a binary, container image, bundle, kernel, disk, socket,
mount, environment variable or command. Those are administrator-pinned assets
or fixed constants in `backend/kuberweaver/local_execution.py`. Nothing is
pulled from a registry or the network at execution time.

## Administrator configuration

The provider reads exactly one file:

```text
<state_dir>/config/local-runtime.json
```

It must be a regular file, mode `0600`, owned by the portal account. The
`config` directory must not be a symlink. Missing configuration makes
`ready()` return `False`; invalid, unsafe or tampered configuration raises
`ValueError`.

```json
{
  "schema_version": 1,
  "assets": {
    "runc": "/srv/kuberweaver/local-runtime/oci/usr/sbin/runc",
    "busybox": "/srv/kuberweaver/local-runtime/oci/usr/bin/busybox",
    "qemu_system": "/srv/kuberweaver/local-runtime/qemu/usr/bin/qemu-system-x86_64",
    "qemu_img": "/srv/kuberweaver/local-runtime/qemu/usr/bin/qemu-img",
    "cpio": "/srv/kuberweaver/local-runtime/qemu/usr/bin/cpio",
    "kernel": "/srv/kuberweaver/local-runtime/qemu/vmlinuz-virt",
    "bios": "/srv/kuberweaver/local-runtime/qemu/usr/share/seabios/bios-256k.bin",
    "qemu_share": "/srv/kuberweaver/local-runtime/qemu/usr/share/qemu",
    "qemu_lib": "/srv/kuberweaver/local-runtime/qemu/usr/lib/x86_64-linux-gnu"
  },
  "sha256": {
    "runc": "<sha256>",
    "busybox": "<sha256>",
    "qemu_system": "<sha256>",
    "qemu_img": "<sha256>",
    "cpio": "<sha256>",
    "kernel": "<sha256>",
    "bios": "<sha256>",
    "qemu_share": "<tree sha256>",
    "qemu_lib": "<tree sha256>"
  }
}
```

All paths must be absolute, canonical, outside symlinks, and not writable by
group or others. File assets are pinned by SHA-256. Directory assets are pinned
by a deterministic tree hash: sorted relative paths, with `D`, `F` or `L`
records; file records include the SHA-256 of the bytes; relative symlinks are
allowed only when their resolved target remains inside the tree; hard links,
absolute links and escaping links are rejected.

The tested extraction shape comes from the distribution packages:

```bash
apt-get download runc busybox-static qemu-system-x86 qemu-utils cpio
mkdir -p /srv/kuberweaver/local-runtime/oci /srv/kuberweaver/local-runtime/qemu
dpkg-deb -x runc_*.deb /srv/kuberweaver/local-runtime/oci
dpkg-deb -x busybox-static_*.deb /srv/kuberweaver/local-runtime/oci
dpkg-deb -x qemu-system-x86_*.deb /srv/kuberweaver/local-runtime/qemu
dpkg-deb -x qemu-system-common_*.deb /srv/kuberweaver/local-runtime/qemu
dpkg-deb -x qemu-system-data_*.deb /srv/kuberweaver/local-runtime/qemu
dpkg-deb -x qemu-utils_*.deb /srv/kuberweaver/local-runtime/qemu
dpkg-deb -x cpio_*.deb /srv/kuberweaver/local-runtime/qemu
```

Extract the distribution kernel used for the guest, for example a
`linux-image-*-virtual` package's `vmlinuz`, to
`/srv/kuberweaver/local-runtime/qemu/vmlinuz-virt`. Keep it with the same
lifetime and permissions as the other assets.

Generate the exact configuration object instead of calculating tree hashes by
hand:

```python
from kuberweaver.local_execution import LocalExecutor

configuration = LocalExecutor.build_config({
    "runc": "/srv/kuberweaver/local-runtime/oci/usr/sbin/runc",
    "busybox": "/srv/kuberweaver/local-runtime/oci/usr/bin/busybox",
    "qemu_system": "/srv/kuberweaver/local-runtime/qemu/usr/bin/qemu-system-x86_64",
    "qemu_img": "/srv/kuberweaver/local-runtime/qemu/usr/bin/qemu-img",
    "cpio": "/srv/kuberweaver/local-runtime/qemu/usr/bin/cpio",
    "kernel": "/srv/kuberweaver/local-runtime/qemu/vmlinuz-virt",
    "bios": "/srv/kuberweaver/local-runtime/qemu/usr/share/seabios/bios-256k.bin",
    "qemu_share": "/srv/kuberweaver/local-runtime/qemu/usr/share/qemu",
    "qemu_lib": "/srv/kuberweaver/local-runtime/qemu/usr/lib/x86_64-linux-gnu",
})
```

Persist it with mode `0600` and call `ready()` once. A changed path or byte
after approval is a configuration failure, not a reason to fall back to another
binary.

## Provider interface

```python
LocalExecutor(state_dir)
executor.ready() -> bool                 # False if unconfigured; ValueError if invalid
executor.execute(job, emit) -> dict      # status "succeeded" or a verified failure
executor.rollback(job, emit) -> dict     # status "rolled_back" or "rollback_failed"
executor.decommission(job, emit) -> dict # status "decommissioned" only when verified
executor.read() -> dict                  # small registry metadata, no paths
```

`execute` and `rollback` validate the durable approval pin
`approved_execution_provider.name == "local-runtime-v1"`, the approved graph
hash and, during `execute`, the administrator asset pins. `decommission` is
called by the serialized worker only for an eligible succeeded service request;
it rechecks the same provider pin and the journal's configuration hash.

A successful execution result is deliberately small:

```json
{
  "status": "succeeded",
  "operation": "create-owned-local-resources",
  "verified_resources": 1,
  "resources": [
    {
      "node_id": "web-a",
      "plugin_id": "local-oci",
      "kind": "oci",
      "verified": true,
      "removed": false,
      "endpoint": "http://127.0.0.1:18090",
      "container_id": "kw-0123456789ab-0"
    }
  ]
}
```

A QEMU resource has `kind: "qemu"`, `state`, and a typed Ansible hand-off
record:

```json
{
  "node_id": "vm-a",
  "plugin_id": "local-qemu",
  "kind": "qemu",
  "verified": true,
  "removed": false,
  "state": "running",
  "ansible": {
    "ready": false,
    "reason": "no guest network or SSH baseline"
  }
}
```

The pinned QEMU guest has `-nic none` and only a busybox initramfs. It is boot
evidence, not an SSH-reachable VM, so a workload-Ansible brick must not target
it. The `ansible.ready: false` record is the published hand-off contract: a
consumer that needs a typed VM target must treat it as unavailable rather than
invent an address. Adding an SSH-capable guest baseline would require a new
reviewed asset set and a new brick; it is not implied by this provider.

## What a job may select

`local-oci` schema:

| Field | Values | Meaning |
| --- | --- | --- |
| `targets` | exactly `["app"]` | local selector only; no remote host |
| `port` | `18090`..`18109` | loopback port for the fixed busybox server |

`local-qemu` schema (one VM per job, `admission_profile: virtual-machine`):

| Field | Values | Meaning |
| --- | --- | --- |
| `cpu` | `1`..`2` | QEMU `-smp` value |
| `memory_mb` | `128`..`1024` | QEMU `-m` value |
| `disk_mb` | `64`..`512` | private qcow2 scratch disk |

The provider also refuses: non-`dev` environments, dependency edges, failure
injection, a QEMU job with more than one node, a mixed OCI/QEMU job, duplicate
node identifiers, duplicate OCI ports within a job, a port already owned by
another job, and any configuration that does not match the reviewed manifest.

## Durable ownership

```text
<state_dir>/local-runtime/
  .lock                       # provider-wide serialization
  ownership.json              # job claims; ports are globally unique
  jobs/<job_id>/
    journal.json              # durable evidence and configuration hash
    runc-state/               # job-private runc root
    oci-0/
      config.json             # rootless specification, rewritten 0600
      rootfs/bin/busybox      # copied from the pinned asset
      rootfs/www/index.html
    qemu-0/
      guest.cpio
      guest.qcow2
      qmp.sock
      console.log
      qemu.log
```

All job state is `0700`/`0600`. `journal.json` records the job, graph hash,
configuration hash, resource identities, PIDs and process start times, asset
and specification hashes, HTTP/QMP readback hashes, and removal results. Raw
runtime output stays in the private job directory; provider exceptions and
command output are not copied into the public job result.

Before a resource starts, its intended ownership is written. A crash therefore
leaves evidence for checked removal instead of an orphan that cannot be named.
`execute` refuses an existing job folder or ledger entry; it never adopts,
reuses or replaces a container, VM or port.

If execution fails after one or more resources started, the provider removes
the resources it created and verifies absence. The job returns `failed` when
cleanup is verified, or `rollback_failed` with `requires_manual_review` when it
is not.

## Checked removal

OCI removal:

1. Read `runc state` for the exact container ID in the job-private runc root.
2. If running, verify the recorded PID and `/proc` start time, then send TERM.
3. Poll until stopped, then `runc delete --force`.
4. Verify `runc state` fails, `runc list --format json` no longer contains the
   ID, and the loopback port is closed.

QEMU removal:

1. Verify the recorded PID still has the recorded `/proc` start time.
2. Ask QMP to `quit`, then wait for the process to disappear.
3. Only if QMP cannot stop that exact process, signal it through a pidfd after
   rechecking the start time; TERM and then KILL each have a bounded wait.
4. Reap the child when it is ours, remove the private qmp socket and qcow2 disk,
   and verify the process, socket and disk are absent.

`rollback` keeps the private journal and job directory as evidence and releases
the port claims; its result has `verified_removed` and `resources` with
`removed: true`. `decommission` first performs the same checked removal, then
deletes the entire private job directory and records a tombstone in
`ownership.json`. It returns `{"status": "decommissioned"}` only after the
directory is absent. A failed retirement raises; it is never reported as a
successful `rolled_back`.

## Failure and configuration changes

- `ready()` is `True` only after every file and tree digest verifies.
- Missing configuration is `False`, which the registry should present as
  unconfigured.
- A changed asset, unsafe path, writable file, symlinked directory, missing
  executable or malformed JSON raises `ValueError`.
- Each journal stores the configuration hash used at execution. If an
  administrator changes asset paths or digests afterwards, `rollback` and
  `decommission` fail closed instead of using a different runtime. Restore the
  exact configuration, or treat the job as requiring manual review.
- A stale or foreign port claim, missing journal, invalid record, unknown
  resource kind or failed absence check also fails closed.

## Scope and non-goals

- Rootless OCI and QEMU TCG only. No Docker daemon, registry pull, KVM
  requirement, guest image download or arbitrary playbook.
- OCI exposure is loopback-only. QEMU has no network device.
- No VM-to-Ansible SSH hand-off; see the typed `ansible.ready: false` record.
- The provider serializes its own resource operations. Core authentication,
  admission, approval, GitOps publication and queueing remain authoritative.
- This is trusted administrator code, not a hostile-filesystem security proof.
  Filesystem races outside the provider's owned state are out of scope.
