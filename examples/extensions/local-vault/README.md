# Optional local Fernet vault

No Docker, network service, database or new orchestrator is required. The OpenBao connector remains independent and available; do not delete its container or Raft state.

From the repository root:

```sh
.venv/bin/python -m pip install -r requirements-local-vault.txt
.venv/bin/python scripts/setup_local_vault.py --data-dir "$PWD/data"
# Optional: explicitly trust only THIS extension's Python code.
.venv/bin/python scripts/setup_local_vault.py --data-dir "$PWD/data" --install
```

Without `--install`, installation, activation and global bindings do not change. The host uses `data/plugin-data/local-vault` through `ctx.plugin_data_dir()`, not `data/plugins`. The `key`, authenticated `check` marker and `sha256(reference).fernet` files are separate from package sources in `data/extensions/local-vault`. The parent of `--data-dir` must already exist. Private directories are 0700 and files 0600. A different existing installation is rejected, never overwritten. Activation respects `data/config/extensions.json` when it is the sole authority. Restart the application after installation; CLI success does not attest to the active runtime.

Explicitly select `local-vault` for new connections. Human/agent controls remain those of the host Connections API; the extension adds no parallel write route. No global secrets binding is changed. The Python provider is **read-only by default**; `allow_write=True` must be an explicit boolean. The enabled extension permits storage for new connections through host controls. Managed SSH-key private material uses encrypted vault storage; see `kw keys` for its separate administration and export workflow.

## Storage and integrity

References follow the host's logical contract, up to 128 characters. Nonempty binary values are limited to 128 KiB before writes. Fernet authenticates the reference inside ciphertext: substituting files from different references fails. Reads and metadata create nothing. The host may create the plugin directory during registration, but never the encryption key.

I/O uses relative NOFOLLOW descriptors, including ancestors; symlinks and owners other than root/current user are rejected. Ancestors owned by root/current user may be 0775: that does not make their group trusted. World-writable ancestors before a current-user 0700 barrier are rejected. The final vault requires exactly 0700 and current-user ownership; keys/data require 0600, the same user and one hard link.

A group member able to rename an ancestor cannot traverse the existing private vault, redirect descriptor-pinned I/O or supply a vault owned by that member. They may still move/delete a path, deny service or present an older valid vault with the same owner: availability and anti-rollback are not guaranteed. Ancestor permissions are not changed. An owner-only 0700 ancestor forms a barrier for descendants.

Linux `flock`, exclusive temporary files and atomic replacement serialize writes. Results are read back and checked, with restoration on verification failure. Interruption before publication preserves the old value; power loss after publication but before confirmation needs manual inspection. Missing key with existing data, wrong key or altered ciphertext fails closed; there is no automatic reset.

## Threat model and backups

A local key beside ciphertext **does not protect against compromise of the OS account or root**. Privately back up **key, authenticated marker and ciphertext together**; never commit or display them. Database-only backups do not recover vault material. Test restoration in isolated state.

Encryption-key rotation, replay prevention for an old value of the same reference, multi-user ACLs, guaranteed Python-memory erasure and a Vault protocol are not implemented. Hashed names and encryption do not fully hide metadata such as size or Fernet timestamp. Select the existing OpenBao provider when centralized guarantees are required; its own custody and transport limits still apply.
