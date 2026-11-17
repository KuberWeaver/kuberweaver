# VMware VM with Ubuntu 24.04

Optional trusted extension `esxi-ovf`, blueprint `vmware-vm`. OpenTofu prepares a saved plan; after approval, its local provisioner invokes VMware OVF Tool. This is a **create-only composition**, not a complete OpenTofu VM lifecycle provider: no update, deletion, rollback or automatic cleanup after an ambiguous error.

## Local installation

Prerequisites: Linux user namespaces and `/usr/bin/bwrap`, administrator-installed OVF Tool 5.0.0, verified `tools/tofu` 1.13.9, ESXi driver and vault dependencies. OVF Tool is not redistributed by this repository.

Acquire the official image, verify checksums and their Canonical signature, then validate the package with OVF Tool:

```sh
.venv/bin/python scripts/setup_vmware_image.py
```

The script requires `/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg`. It stores the OVA and `image.json` under `data/os-images/ubuntu-24.04/`, outside Git. A different existing image is never silently replaced.

```sh
.venv/bin/python scripts/plugins.py --data-dir data install examples/extensions/esxi-ovf
.venv/bin/python scripts/plugins.py --data-dir data enable esxi-ovf --trust
```

Restart the application afterward. Enabling the extension does not create a VM.

## Portal workflow

1. In Connections, configure ESXi, record the accepted certificate SHA-256 fingerprint and choose Discover. A changed certificate requires a new explicit trust decision, not an automatic trust update.
2. Add **VMware VM** in Canvas.
3. Select the server, datastore, networks and pool from actual inventory. VMware identifiers are preserved exactly, including spaces.
4. Supply name, CPU, RAM, disk and SSH public key, or select a managed key where supported. The initial image is Ubuntu Server 24.04, with guest user `ubuntu` and password login disabled. Disk size must be at least 10 GiB. See [managed SSH keys](../../../docs/SSH_KEYS.md); private keys do not belong in blueprint configuration.
5. Validate and submit for approval. The worker prepares the actual local plan without remote import. Review name and resources, then approve.
6. Import starts powered off. Guest OS properties, cloud-init and resources are configured before power-on. Verify IP address and SSH access independently afterward.

## Transport and limitations

Credentials remain in the vault and Connections service's access scope. The SDK checks its actual socket fingerprint and supplies a one-use session ticket. OVF Tool receives that ticket only through stdin; an additional `no` response rejects certificate-change prompts. Neither password nor ticket is placed in command arguments or the plan.

OVF Tool runs with an explicit SHA-256 fingerprint, private profile and masked CA stores in an unprivileged mount namespace. PEM and TLS-disable options are forbidden: explicit PEM trust could otherwise take precedence over the fingerprint. Execution fails closed if isolation is unavailable. No system trust store or ESXi certificate is changed. This mechanism does not sandbox a malicious Python extension.

Plan and approval bind sources, binaries, image and connection. Import never permits overwrite. The new UUID, job annotation and placement must match before reconfiguration or power-on. A durable marker prevents automatic replay of an ambiguous apply.

No full CRUD or universal rollback is provided. An ambiguous import may leave remote resources requiring an authorized manual investigation; do not retry, destroy or clean them up automatically. Managed-key disabling does not revoke a key already installed in the guest, and automatic remote rotation is not guaranteed.

## Verification

Local tests run real OpenTofu with an injected importer; they do not alone prove Ubuntu boots. Local TLS tests check wrong-pin rejection before an HTTP request. Import, OS and SSH acceptance require an actually approved plan against the target ESXi. Passing local tests is not production-maturity evidence.

```sh
KUBERWEAVER_STATE_DIR=/path/to/test-state PYTHONPATH=backend .venv/bin/python -m pytest tests/test_ovf_provisioning.py tests/test_ovf_transport.py -q
```
