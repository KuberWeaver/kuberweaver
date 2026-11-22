# esxi-runtime

Hypervisor provider for a standalone VMware ESXi host: inventory discovery and
destruction of a VM this deployment owns. Nothing else.

## Contract

| Field | Value |
| --- | --- |
| `id` | `esxi-runtime` |
| `api_version` | `1` |
| `capabilities` | `providers:hypervisor` |

It reaches the host through the connection recorded for it, using the credential
bound in the vault. It does not create or power on a guest: provisioning belongs
to a separate brick (`vmware-vm`, engine `opentofu`).

## Refusals worth knowing

- A VM is destroyed only when an owner marker matches the job that owns it. A
  foreign VM is refused, not deleted.
- A host that cannot be reached produces an explicit unavailable result, never a
  success claim.

## Enable it deliberately

This extension talks to a real hypervisor. Install it only when you intend to use
it, and review the pinned upstream library in its requirements before enabling.
