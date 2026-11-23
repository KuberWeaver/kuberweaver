# Optional local OpenBao for new connections

This distinct source registers as `secrets/openbao-local`. A new connection must explicitly select `secrets_provider: "openbao-local"`; do not change the global `data/config/providers.json` binding. No existing secret is migrated and no ESXi password is created or searched for. Setup never contacts the remote vault, GNS3 or its SSH tunnel.

## Scope and limitations

- Official **2.7.2** image, pinned in the script to `openbao/openbao@sha256:5046911075e137e154ce49ab051502f4ec7cf367d484ae4a4dce665d9b90561f`.
- Only allowed container: `kw-openbao-v2`, with stable project-path ownership label. Docker is explicitly targeted at `/var/run/docker.sock`, not an inherited remote context.
- Default host port `127.0.0.1:19200` (`--port`, integer 1024–65535). Lab HTTP, **not end-to-end TLS**. The server listens on the Docker network; host loopback publication does not isolate all bridge containers. Administration ignores proxies and redirects.
- Persistent OpenBao-encrypted Raft, no `-dev` mode or `IPC_LOCK`, running as operator UID/GID, `--cap-drop ALL`, `no-new-privileges`, read-only configuration mount. Direct entrypoint `/usr/bin/bao` follows the [official v2.7.2 Dockerfile](https://github.com/openbao/openbao/blob/v2.7.2/Dockerfile).
- No unrelated container is started, restarted or deleted. Existing owned containers must exactly match image, mounts and hardening. Docker `NetworkMode=default` and `NetworkMode=bridge` both name the default bridge here; other networks are rejected. An active container is not restarted. Restart policy is `no`.
- Application token only has `read/create/update` on `secret/data/kuberweaver/connections/*`: no `list`, `delete` or administration. It is orphaned, has no `default` policy, is nonrenewable and has requested/maximum TTL 168 h. A valid token is retained.
- Exact reference `setup-check` is reserved for internal diagnostics. `secrets/openbao-local` rejects it for reads/writes before network access. Choose another reference for connection credentials. The script retains its marker access; other secret providers do not inherit this reservation.

This is a single-node lab setup: no HA, auto-unseal or cryptographic certification. Docker administrators and the host user remain trusted. Automated protocol fixtures are not evidence of executing the real image.

## Private files

With `--state-dir "$STATE"` (default: repository-root `data/`):

| Relative location | Protection/content |
| --- | --- |
| `local-bao/`, `local-bao/raft/` | 0700 directories, persistent state. |
| `local-bao/config.hcl` | 0600 controlled configuration. |
| `local-bao/bootstrap.json` | 0600, root token and three unseal shares. |
| `local-bao/app-token` | 0600, only token consumed by the provider. |
| `local-bao/pending-revocation.json` | 0600, old/candidate tokens during rotation; `{}` after verified revocation. |
| `plugin-data/openbao-local/provider.json` | 0600 local provider configuration. |

**Bootstrap contains plaintext recovery material**, protected by the filesystem. All three shares on one host do not provide independent 2-of-3 custodians. Keep private consistent backups of bootstrap and Raft; never display, commit, attach to evidence or log them. File writes are atomic, synchronized and read back. Symlinks, shared hardlinks and excessive permissions are rejected, not silently repaired.

If Raft exists without bootstrap, or the server is initialized without local bootstrap, setup stops for manual recovery. **Do not delete Raft or reinitialize the vault to bypass that refusal.** An error after initialization may require manual intervention; no destructive cleanup is attempted.

## Operator procedure: review before execution

From the repository root, with local Docker available. Commands do not start a GNS3 lab; `setup` starts only the new local container or its own compatible stopped container.

```bash
STATE="$PWD/data"  # Or a durable directory dedicated to this portal instance.
.venv/bin/python scripts/setup_local_bao.py setup --state-dir "$STATE" --port 19200
.venv/bin/python scripts/setup_local_bao.py verify --state-dir "$STATE" --port 19200
# Repeated setup retains the container and valid token, with a new random marker.
.venv/bin/python scripts/setup_local_bao.py setup --state-dir "$STATE" --port 19200
```

Success JSON should report `initialized: true`, `sealed: false`, pinned digest, `setup_roundtrip: true` for `setup`, and verified HTTP 403 denials outside the namespace and for administration/delete/list. `verify` does not rewrite markers or configuration: `setup_marker_read: true` and `setup_roundtrip: false` distinguish read from write/readback. `credentials_written: false` means no actual connection credential was written; bootstrap/application tokens are still private files.

The script **does not install or activate the extension**. After reviewing its code:

```bash
.venv/bin/python scripts/plugins.py --data-dir "$STATE" doctor extensions/openbao-local
.venv/bin/python scripts/plugins.py --data-dir "$STATE" install extensions/openbao-local
.venv/bin/python scripts/plugins.py --data-dir "$STATE" enable openbao-local --trust
```

Install once; implicit replacement is refused. Restart the portal through its normal mechanism with the **same state directory**. Select the local provider only for new connections. Missing configuration reports `unconfigured`; invalid configuration reports `unavailable` without breaking registration. `configured` means valid configuration, not a startup network probe.

For an existing running but sealed container after restart:

```bash
.venv/bin/python scripts/setup_local_bao.py unseal --state-dir "$STATE" --port 19200
```

Use `setup` for a stopped container; only that mode starts it. Expired/replaced tokens require explicit rotation, which checks the new token, saves it, revokes the old token and verifies denial:

```bash
.venv/bin/python scripts/setup_local_bao.py setup --state-dir "$STATE" --port 19200 --rotate-token
```

Before replacing `app-token`, rotation durably records old/candidate tokens in `pending-revocation.json` under the 0700 private directory. After error/interruption, rerun **`setup` without `--rotate-token`** with the same state and port: it resumes rotation, retains the candidate and checks old-token denial before success. An already completed revocation is recoverable. Read-only `verify` refuses pending rotation rather than completing it. Do not delete or publish the private journal. This protects the old-token reference, not a distributed transaction for candidate creation before it reaches the journal.

## Historical real-Docker acceptance: blocked

Bridge handling was verified against owned-container inspection and automated tests. Real acceptance remained **blocked**, not passed: after privately archiving partial Raft whose API reported `initialized: false`, ordinary setup initialized the vault without producing `bootstrap.json` or an application token. The server then reported `initialized: true`. The five-second initialization client timeout is a possible cause, not a confirmed diagnosis.

The sole owned container was stopped; initialized Raft was retained without reinitialization. `verify`, application policy and real idempotency are therefore not certified. Sanitized evidence, exact container ID and archive path are in `data/evidence/v2-local-bao-recipe.json`. Recovery requires explicit authorization; do not delete state to rerun acceptance. That attempt enabled no extension and changed no global binding.

## Tests without Docker or real data

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_local_bao.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_local_bao.py tests/test_secrets.py tests/test_secret_writes.py \
  tests/test_extensions.py tests/test_provider_bindings.py -q
```

Docker calls are injected at `subprocess.run`. A loopback HTTP test server exercises real `OpenBaoProvider.store/resolve`, redirect/privilege denials, errors and private-file preservation. These local tests execute no actual Docker command and do not establish production readiness.
