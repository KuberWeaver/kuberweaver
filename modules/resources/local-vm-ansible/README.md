# local-vm-ansible

Reviewed compound brick: **local provisioner → SSH host identity and guest
reachability → pinned Ansible**.

It is the only place in the catalogue where a workload both creates its guest
and configures it. The composition is fixed data; the adapter adds no branch.

## Order of operations

1. The saved plan is prepared locally: manifest, playbook digests, the
   `local-runtime-v1` contract, the SSH key binding and the VM shape are hashed.
   Preparation never creates a guest and never reads the runtime.
2. On approved execution the injected runtime provisions the VM and returns a
   typed handoff (`backend/kuberweaver/local_vm_ansible.py`, `HANDOFF_SCHEMA`).
3. The adapter validates the handoff, writes a private `known_hosts` entry from
   the attested guest host key, and runs a fixed
   `ssh ... -o StrictHostKeyChecking=yes ... true`. That proves the host key,
   the key login and reachability in one step.
4. Only then do `iac/baseline.yml` and `iac/verify.yml` run, still with strict
   host key checking against that same pinned entry.
5. `verify.yml` publishes its assertions to a controller-side JSON file; the
   adapter refuses anything that does not match the manifest's declared set.

## What a job may supply

`vm_name`, `ssh_key_id`, `cpu`, `memory_mb`, `disk_gb`. Nothing else. No
playbook, inventory, host, user, executable or command can come from graph
configuration; admission already rejects those field names.

## What must stay true

- Playbooks live in this directory and their SHA-256 values are pinned in
  `manifest.yaml`. Editing a playbook invalidates every prepared plan.
- No role, collection or download: `ansible-core` is pinned and only
  `ansible.builtin` modules are used.
- Guest addresses must be loopback, private or link-local. A handoff pointing
  at a remote address is refused before any command.
- A failed apply is reported for manual review. The VM is not destroyed
  automatically; the runtime owns that lifecycle.

## Runtime port

The provisioner is not part of this brick. It is injected, and the adapter
accepts exactly one shape:

```python
class Runtime:
    contract = 'local-runtime-v1'          # or contract_version

    def provision(self, request):          # create/ensure, then return handoff
        ...
    # or: def handoff(self, identity): ...
```

`request` is `{'job_id', 'vm': {'name', 'cpu', 'memory_mb', 'disk_gb'}}`; no
path crosses that boundary. The returned document must match `HANDOFF_SCHEMA`
in `backend/kuberweaver/local_vm_ansible.py`, including `vm.uuid`,
`vm.power_state == 'running'`, `guest.address`, `guest.ssh_port`,
`guest.user == 'ubuntu'` and the guest SSH host key.

The current `local-runtime-v1` implementation provisions `local-qemu` with
`-nic none` and reports `no guest network or SSH baseline`, so it cannot yet
produce this handoff. Until it does, the composition refuses preparation
instead of inventing an address or accepting an unverified host key.
