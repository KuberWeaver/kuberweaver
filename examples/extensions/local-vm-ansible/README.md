# local-vm-ansible extension (example)

Opt-in registration of the compound execution provider
`local-vm-ansible-v1`. It composes a local `local-runtime-v1` provisioner, an
attested SSH host identity check, and the digest-pinned playbooks in
`plugins/local-vm-ansible`.

Nothing here is sandboxed: an extension is trusted Python. Approval, plan
digests, provenance pins and the command-boundary guards still apply unchanged.

## Install

1. Copy this directory into `<state>/extensions/local-vm-ansible` (no symlinks).
2. Install the reviewed `plugins/local-vm-ansible` brick in the catalog
   directory.
3. Install `ansible-core==2.20.11` and an OpenSSH client on the controller.
4. Provide a guest image the local runtime can boot with the `ubuntu` account,
   `/usr/bin/python3`, and the selected SSH key already injected.
5. Supply the runtime port. Add `runtime_adapter.py` next to `module.py`:

```python
from pathlib import Path

def runtime():
    from your_local_runtime import LocalExecutor   # reviewed integration code
    return LocalExecutor(Path('<state>/local-runtime'))
```

`runtime()` must return an object whose `contract` (or `contract_version`) is
`local-runtime-v1` and which exposes `provision(request)` or `handoff(identity)`
returning the typed handoff. Without this file the provider registers as
`unavailable` and every preparation is refused.

Current `local-runtime-v1` status: `LocalExecutor` exposes
`execute/rollback/decommission/read` only, and `local-qemu` boots with
`-nic none`, so it emits no guest address or SSH host key. Passing it directly
registers as unavailable with the precise reason
`Local runtime does not expose a typed handoff port`. Integration requires the
runtime to add that port (and a guest network path) before this composition can
prepare a job; there is no fallback that guesses an address or trusts a
first-seen host key.

6. Enable it explicitly:

```bash
curl -s -X POST "$BASE/api/extensions/local-vm-ansible/enable" \
  -H 'Content-Type: application/json' -d '{"trusted": true}'
```

7. Bind it as the execution provider in `config/providers.json` and confirm the
   provider listing reports it as available.

## Job shape

One node, no edges, `environment: dev`, `failure_mode: none`:

```yaml
nodes:
  - id: guest
    plugin_id: local-vm-ansible
    config:
      vm_name: lab-web-01
      ssh_key_id: <32 hex characters>
```

## Precise limits

- The local runtime must attest the guest SSH host key. There is no
  trust-on-first-use fallback: a handoff without a host key is refused.
- Only loopback, RFC1918/link-local IPv4 or loopback/link-local/ULA IPv6 guest
  addresses are accepted. Remote targets are impossible by construction.
- Only the fixed `ubuntu` login is supported; playbooks run without `become`.
- No teardown, no rollback and no automatic VM destroy. A failed apply leaves
  the guest running and the job marked for manual review.
- Because QEMU networking may be absent, the readiness step fails closed; there
  is no user-mode networking or port-forward fallback here.
- The provider does not fetch roles or collections. `ansible-core` must already
  be installed at the configured `KUBERWEAVER_ANSIBLE_PLAYBOOK`.
