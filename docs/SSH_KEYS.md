# Managed SSH keys

KuberWeaver provides a managed **create, select, disable and explicit export** workflow. Automatic guest-key rotation and remote revocation are separate capabilities and are not guaranteed by this workflow.

## Create and select

1. Open the SSH key management page in the portal with the required administrative access.
2. Create a labelled key. KuberWeaver generates an Ed25519 key pair and keeps the private key in the shared encrypted vault; ordinary list/detail responses must not expose it.
3. Select the key in a compatible blueprint. The blueprint uses its public key and managed reference, not private key material. A reference is an identifier, not permission to export the private key.
4. Review and approve the deployment normally. Creating or selecting a key does not itself install it on a guest, bypass approval or prove SSH connectivity.

## Guest delivery

The public key reaches the guest through VMware `guestinfo` keys set on the VM's `extraConfig` before the first boot: `guestinfo.metadata`, `guestinfo.userdata` and their `base64` encoding counterparts. cloud-init's VMware datasource reads them through open-vm-tools and writes the key into the default user's `authorized_keys`.

This channel replaces OVF properties (`--prop:`), which ovftool accepts but never delivers on standalone ESXi: vApp options require vCenter, so the guest boots with no datasource, no address and no key. Do not reintroduce property-based injection as the primary path.

Consequences worth knowing:

- open-vm-tools must be present in the image. Images without it need a NoCloud seed ISO attached as a CD-ROM instead.
- `guestinfo` carries no network configuration, so the guest uses cloud-init's DHCP fallback. Static addressing needs the seed ISO.
- The default user is the image's own (`ubuntu` for the official cloud image). The workflow does not create a second account or set a password; root stays locked.
- A deployment is reported with the guest address read from VMware Tools. That proves the guest configured itself; the SSH key is verified separately by an authenticated login.

## Disable

Disable a key to prevent **new use through KuberWeaver**. This does not remove public keys already installed in guest `authorized_keys`, invalidate a previously exported private key or revoke existing SSH sessions. Removing access on existing guests requires a separate authorized guest change and independent verification. Do not assume disabling a key changes an approved or running deployment silently.

## Explicit private-key export

Private export requires an administrator and password confirmation through the dedicated export action. Export deliberately releases the private key outside the vault: protect the downloaded file, restrict it to its intended OS user (normally 0600 in a private directory), and never put it in a blueprint, Git, logs, screenshots, URLs or chat. Do not substitute an agent token or ordinary metadata read for this confirmation.

## Vault configuration

Key creation uses the selected global secrets provider unless a dedicated provider is configured in `data/config/ssh-keys.json`:

```json
{"secrets_provider": "local-vault"}
```

This operator-owned file must have mode `0600`. The named provider must be installed, explicitly trusted and enabled, initialized, and support verified encrypted writes. The dedicated setting does not change the global secrets binding or existing connection credentials. Invalid configuration is rejected without a fallback. Each created key retains its selected vault identity and source fingerprint; provider upgrades that change that source require administrator review before stored keys can be used again.

## API surface

- `GET /api/ssh-keys`: list safe key metadata and public material.
- `POST /api/ssh-keys`: generate a managed key.
- `PATCH /api/ssh-keys/{id}`: change `enabled`.
- `POST /api/ssh-keys/{id}/export`: password-confirmed private export.

Creation accepts `{"name":"Homelab access"}`. Updates accept `{"enabled":false}` or `{"enabled":true}`. Export accepts `{"password":"<current administrator password>"}` and returns an attachment with `Cache-Control: no-store`. `GET /api/ssh-keys/{id}` also lists referencing jobs and blueprints; those references are not proof that a guest is running or reachable.

Browser mutations retain normal session, role and CSRF checks. Agents with `read:infrastructure` can read public metadata, but key generation, changes and private export require a human administrator session. Export reauthentication attempts are rate-limited within the running controller process.

## Backup and recovery

Back up the encrypted vault's **key, authenticated marker and ciphertext together**, privately, along with the application database and references needed to recover the catalogue. Losing the vault encryption key makes ciphertext unusable. Restoring only the database does not restore private keys. Test recovery in isolated state before relying on backups.

Local encrypted storage does not protect against compromise of the service OS account or root. File permissions and encryption at rest are not hardware-backed isolation, independent custody or guaranteed memory erasure. See the [local vault guide](../examples/extensions/local-vault/README.md) for its exact threat model.

For future rotation, plan how a replacement public key reaches every guest, verify new access before removing the old key, and retain an authorized recovery path. The delivered create/select/disable/export workflow does **not** promise automatic or guaranteed remote rotation.
