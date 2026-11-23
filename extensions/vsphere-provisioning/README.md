# Optional vSphere saved-plan provisioning

The Python saved-plan protocol is implemented and locally tested. **VM creation is not advertised as operational:** the shipped blueprint remains `capability: planned`, the extension is disabled by default and status is `blocked`. Discovery, vault access and the separate OVF workflow are distinct capabilities; this blocker does not disable them.

## Two blocking prerequisites

1. **Valid standard PKI for the HTTPS endpoint.** A pin accepted by the Python SDK is not a trust setting in the official provider. A trusted chain and matching endpoint name/SAN are required. No `allow_unverified_ssl`, bypass proxy or provider patch is supplied. Python and Go share an explicit pinned CA bundle; inherited TLS/proxy configuration is not passed through. Another CA can only come from administrator-trusted code (`executor.ca_bundle`), never a graph.
2. **Powered-off creation.** The real `vmware/vsphere 2.18.0` schema makes `power_state` computed-only and has no `power_on`. `resourceVSphereVirtualMachineCreateBare` always invokes `PowerOn`. `force_power_off` concerns shutdown for update/delete, not creation. The template is syntactically valid but **does not guarantee a stopped VM**. The executor refuses preparation/application with a static error instead of creating then stopping outside the plan. No setting bypasses the gate; changing scope requires review and real acceptance.

Local checks below need no ESXi credential. They validate no actual remote plan, apply, readback or booted OS.

## Integration contract

```python
SavedPlanExecutor(state_dir, template_dir, binary=None, runner=None)
provider.connections = connections_service  # injected by resolve_provider
prepared = provider.prepare_plan(job, emit)
# The kernel persists prepared in job['prepared_plan'] and displays its summary.
provider.validate_plan(job)  # local files and connection fingerprint only
# After human approval, the kernel pins SHA256(canonical JSON(prepared)) in
# job['approved_prepared_plan'], plus approved_by/at/graph_hash/provider.
result = provider.execute(job, emit)  # same approved job, never re-plan
```

`prepared` contains only `plan_digest`, `actions`, `summary`, `bindings`. `plan_digest` fingerprints the private review directory, binary plan, inputs and digests; it is not an independent identity signature. `approved_prepared_plan` is the digest of the **complete public summary**, not a direct copy of `plan_digest`. Persistence and approval enforcement belong to the kernel, without a vSphere branch in Jobs.

`validate_plan` may run under the Jobs lock: no vault access, secret decoding or TLS/SDK/CLI call. It compares local files and connection/revision/provenance fingerprints. Secret bytes and transport are rechecked only in `prepare_plan`/`execute`, outside the shared Jobs lock; secret rotation without revision therefore fails execution. Validation is reentrant and does not reacquire `flock`; the kernel also invokes it at command boundaries. Active-attempt state is accepted only for the same job/thread already owning execution; after return, interruption, thread change or restart it prevents replay. Schema references are rejected and the registry permits no retrieval.

`Connections.access(id)` yields `(connection, credential_bytes)` in a context. The secret document contains only `username` and `password`. Connections also provides `get`, `fingerprint`, `driver`; the driver provides independent `discover`. The SDK does not assume access to an `app` object. `register(ctx)` uses only `ctx.plugin_data_dir()` and `ctx.register(...)`.

The graph is limited to one independent `dev` VM, no dependencies or injected failure. Standard Graph `strategy`, `label`, `position` metadata are accepted; `strategy` adds no execution waves for one independent VM. Closed configuration: connection reference, VM name, actual datastore/network/pool IDs, CPU 1–8, RAM 128–16384 MiB, blank disk 1–100 GiB, fixed `otherLinux64Guest`. Remote name is `kw-<first 12 job characters>-<name>`. `x-options-source` selects existing observations without implicit discovery. No guest port/endpoint, OS or bootstrap is invented.

## Private protocol and security limits

- OpenTofu **1.13.9 Linux amd64**, binary SHA-256 `9ce9173bf161f6466ef57dab7edebe8f55089fdf81caa967415323c25d004990`.
- Official **vmware/vsphere 2.18.0**, real `tofu init` lockfile. Initialize with `-lockfile=readonly`, no inherited provider cache or override; pin local provider binary/lockfile afterward.
- Pin template, manifest, Python sources, generated HCL/variables, exact CLI configuration (`disable_checkpoint = true\n`), CA bundle, connection, secret document and binary plan. Check before/after commands; only `apply ... create.plan` is allowed after approval.
- Plan must create exactly `vsphere_virtual_machine.vm` with `['create']`. Import, adoption, drift, update, replacement, delete and other managed resources are rejected; data-source reads remain allowed.
- Standalone ESXi 8 prechecks require explicitly true API-write entitlement, exact discovered references, accessibility/free space and name absence. These are **not** exhaustive creation-permission evidence.
- 0700 private directories, 0600 files (provider executables 0700), local `flock`, closed artifact list. Symlinks/hardlinks and override files are rejected. The lock is neither distributed nor a hypervisor lock.
- Credentials only in subprocess `VSPHERE_USER`/`VSPHERE_PASSWORD`, without `TF_VAR`, inherited debug/log/proxy settings or disk session persistence. Raw stdout/stderr are not published/logged. `show` JSON/artifacts, including plan ZIP members, are checked for known secrets. Suspect artifacts stay private and unapprovable; this is not universal DLP.
- A durable synchronized attempt marker is written **before** apply. Any prior attempt/state prevents replay. Ambiguous error/interruption gives `failed` and `requires_manual_review: true`, including after restart. No destructive remote cleanup. `supports_rollback=False`, `can_rollback=False`; rollback explicitly fails without running commands. No import/update/delete/destroy.
- Success-path fixtures require a consistent state UUID and independent SDK discovery matching UUID, name, CPU/RAM and expected power state. This is not evidence of real remote creation.

This is trusted Python and provenance checking, not a sandbox against a malicious local administrator. Digests do not reserve remote resources; other hypervisor actors form a separate concurrency domain. The provider may retain server-side sessions: `persist_session=false` forbids local persistence but does not promise logout.

## Reproducible checks

```sh
.venv/bin/python -m pytest tests/test_provisioning.py -q
KW_TOFU_LOCAL_CHECK=1 .venv/bin/python -m pytest \
  tests/test_provisioning.py::test_local_template_init_validate_and_real_schema -q
```

The first group explicitly injects CLI/TLS/SDK boundaries. Plan-mechanics fixtures bypass the stopped-create barrier **only in tests**, not in the real provider. Jobs/engine tests may copy the manifest to disposable state and set `capability: live` there without changing the shipped blueprint.

The second test actually runs `version`, `init -backend=false -lockfile=readonly`, `validate` and `providers schema` in `TMPDIR`, with public dummy values satisfying required defaults. No plan/apply, configured endpoint or actual credential is used. Any network access is only for public provider registry/distribution. Passing tests do not establish production maturity.

### Versioned upstream references

- [VM resource and limitations](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/docs/resources/virtual_machine.md)
- [Schema and bare-create workflow](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/vsphere/resource_vsphere_virtual_machine.go)
- [Environment authentication, TLS and sessions](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/vsphere/provider.go)
- [ESXi/API-write compatibility](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/docs/index.md)

Promotion to `live` requires explicitly authorized remote acceptance with licensing/permissions, compliant PKI, a resolved power-state contract, a real approved plan, exact application and SDK readback. None is claimed here.
