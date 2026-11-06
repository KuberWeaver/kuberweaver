# Replaceable credentials and the OpenBao lab

## Small provider contract

`backend/kuberweaver/secrets.py` has no third-party dependencies and no registry,
executor, database or HTTP-server imports. The application injects a provider:

```python
class SecretProvider(Protocol):
    def resolve(self, name: str) -> ContextManager[SecretValue]: ...
    def describe(self) -> dict: ...
```

Inside `with provider.resolve("operations-ssh") as secret`, `secret.bytes()` is
available. `repr(secret)` is always `<SecretValue redacted>`. Exiting the context
wipes the owned bytearray; further reads raise `SecretError`. This reduces
lifetime, **not** guaranteed zeroization: Python, JSON/HTTP parsing, SSH tools and
caller-created byte/string copies can retain plaintext. Never log local variables,
responses, request headers, credential bytes or exception tracebacks with locals.
There is no credential database, disk cache or serialized secret response.

`describe()` is safe for capability discovery: provider identifier, read-only
capabilities, sorted approved logical names, `persistent_secret_cache: false`,
and (for OpenBao) a transport label. It excludes URLs, paths and credentials.
Provider `repr` does not include its configuration. Provider errors have static,
sanitized messages and suppress upstream exception formatting.

Logical names are administrator-approved strings, matched exactly, using
`[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*`, maximum 128 characters. Unknown names,
traversal, percent escapes, URL references and arbitrary client file paths fail
closed. Keep these mappings in trusted provider configuration, **not** request
parameters. Secret *references* may enter a resource record; resolved values may
not enter a resource record, audit event, job output, Git, plugin descriptor or
browser response. Do not expose a raw secret-read endpoint to clients.

### Explicit protected-file option

```python
from kuberweaver.secrets import RestrictedFileProvider

provider = RestrictedFileProvider({"operations-ssh": approved_existing_key_path})
with provider.resolve("operations-ssh") as secret:
    use_in_memory(secret.bytes())  # caller's narrow credential consumer
```

Only those exact files are accessible. Reads require an owner-matching regular
file with **exactly 0600**, no symlink components, no extra hard links, and
nonempty contents up to 1 MiB. The file descriptor is checked after opening.
This is a permissions-based fallback, not encrypted storage or a production
secrets manager. Existing SSH keys stay in their existing approved location;
setup does **not** import them into OpenBao. Selecting this provider is explicit;
network failure never silently falls back to a file, fabricated value or mock.

### OpenBao KV v2 option

```python
from kuberweaver.secrets import OpenBaoProvider

provider = OpenBaoProvider(
    "http://127.0.0.1:18200",
    {"demo": "kuberweaver/demo"},
    token_file="data/bao-token",
    allow_loopback_http=True,  # explicit lab-only transport exception
)
with provider.resolve("demo") as secret:
    use_in_memory(secret.bytes())
```

Exact constructor:

```text
OpenBaoProvider(endpoint, approved, *, mount="secret", token_file=None,
                token_env=None, allow_loopback_http=False, timeout=5.0)
```

Each approved mapping value must be a validated path **under `kuberweaver/`**.
The GET target is `/v1/{mount}/data/{approved_path}`, with the scalar UTF-8 string
at `data.data.value`; binary credentials can be application-encoded before
provisioning. Version selection, list/write/admin operations and caller-supplied
URLs are deliberately not part of the contract. Other providers may implement
this same byte contract without modifying consumers.

Exactly one token source is required. `token_file` follows the same 0600,
owner/regular-file/no-symlink/no-hardlink restrictions; alternatively `token_env`
is an environment **variable name**, not a token. Sources are reloaded on every
resolution so an atomic token replacement works without rebuilding the provider.
Environment storage is less isolated from process inspection; prefer the file.

HTTPS uses Python's default validating SSL context, including certificate and
hostname verification. There is no insecure TLS switch. HTTP is rejected unless
`allow_loopback_http=True` **and** the hostname is a numeric loopback IP (not a
DNS name). URL credentials, paths, query strings and fragments are rejected.
Redirects and environment proxies are disabled for provider reads so tokens
cannot be forwarded to another server. Errors/403/404/5xx/invalid KV payloads
are redacted and never become substitute credentials.

## Actual lab deployment

Owned guest resources only:

- SSH target `infra-operations` (`192.168.122.10`) via the existing
  `/home/ubu/Desktop/infra-platform-lab/ssh_config`, using `sudo -n`.
- Root-owned configuration directory `/opt/kuberweaver-secrets` (0700).
- Container `kw-openbao`, volume `kw-openbao-data`, both labelled
  `io.kuberweaver.owner=secrets`; existing unlabelled resources are refused.
- The official `openbao/openbao` image is actually pulled, then the container is
  started by its immutable repository digest, **not dev mode**. Actual pulled and
  running image digests, version and container ID are in evidence.
- OpenBao encrypted single-node Raft backend in the persistent volume, owned by
  UID:GID `100:1000`, mode 0700. Container runs as that user, with all Linux
  capabilities dropped and `no-new-privileges`; restart policy `unless-stopped`.
- Only published guest address: **`127.0.0.1:18200`**. Container cluster port 8201
  is not published. No LAN binding, host route/firewall edits, or modifications
  to Gitea, NetBox, Semaphore, CI or other guests are needed.

OpenBao 2.7.2 does not support the old filesystem backend (`storage "file"`).
The working configuration uses Raft. OpenBao also removed memory locking
upstream; `disable_mlock` is now ignored/warned about, so the configuration does
not pretend that it works or grant ineffective `IPC_LOCK`. Memory/swap exposure
is an explicit lab limitation; setup does not change guest/host swap settings.
See upstream [Raft configuration](https://openbao.org/docs/configuration/storage/raft/)
and [mlock removal](https://openbao.org/community/rfcs/mlock-removal/).

### Lifecycle commands

Run from the repository root, using the existing Python environment:

```sh
.venv/bin/python scripts/setup_bao.py setup
.venv/bin/python scripts/setup_bao.py verify
.venv/bin/python scripts/setup_bao.py restart-check
```

`setup` provisions only the owned resources, initializes if needed, stores the
bootstrap material locally, unseals, ensures the KV v2 mount and app policy, and
stores/reads back a newly generated demo value **without printing it**. It reads
back the mount and policy, confirms the app can read the demo, checks denied
write/list/admin/out-of-scope requests, and observes demo plaintext absent from
owned backend files/container logs. This last observation is not a full
cryptographic or leakage audit. Existing other container IDs/states are compared
before and after setup. Concurrent unrelated container activity can invalidate
that comparison; it does not authorize changing those stacks.

`verify` exercises existing state without initialization/reseeding. It requires
an already-unsealed service and unexpired app token. `restart-check` restarts
**only `kw-openbao`**, observes the sealed state, manually unseals, and compares
the existing demo value before/after **without rewriting it**. Expect a brief
secrets outage while this explicit failure test runs.

Each operation owns a temporary SSH forwarding subprocess, waits for it, and
closes/reaps it in `finally`. It never creates an untracked `nohup` tunnel.
For a long-running application, supervise this foreground command separately:

```sh
.venv/bin/python scripts/setup_bao.py tunnel
```

It execs SSH with `ExitOnForwardFailure=yes`, local bind `127.0.0.1:18200`, remote
`127.0.0.1:18200`, and keepalives. Keep that process alive under the app's managed
launcher. With that trusted tunnel already running, use `verify --external-tunnel`
or `unseal --external-tunnel`; otherwise an occupied port is refused rather than
silently trusted. The default temporary tunnel is closed when setup finishes.

The HTTP exception covers local loopback and the trusted guest Docker bridge;
SSH encrypts the inter-host hop. This is **not end-to-end TLS**, and local
administrators/other privileged guest workloads remain in the trust boundary.
Do not reuse this transport exception for a remote or production deployment.

### Recovery, token rotation and custody

Local files, already excluded by the repository's `data/` Git ignore rule:

| File | Contents / handling |
| --- | --- |
| `data/bao-bootstrap.json` | Root token plus all 3 Shamir shares (threshold 2), atomic 0600 file. Bootstrap/recovery operator only. |
| `data/bao-token` | Bare, least-privilege app token, atomic 0600 file. App must never load the bootstrap file. |
| `data/evidence/openbao.json` | No secret values: digest/version/bind/storage facts and live verification booleans. |

After a container/guest restart the volume persists but OpenBao is **sealed**:

```sh
.venv/bin/python scripts/setup_bao.py unseal
```

This uses restricted bootstrap material, unseals, and verifies the existing demo;
it does not generate a replacement demo. Losing the unseal shares can make the
persisted encrypted data unrecoverable. Never delete/reinitialize the volume to
hide a failed unseal, and never display bootstrap files in logs or chat. Back up
the encrypted volume and securely escrow the shares separately before relying
on this lab. Holding all shares and the root token together under one local
account is **not separation of duties**, despite mode 0600.

The app policy is only:

```hcl
path "secret/data/kuberweaver/*" { capabilities = ["read"] }
```

The token is an orphan with no default policy, no write/list/admin capability,
non-renewable and requested TTL 168h. Rotate it as the bootstrap operator with:

```sh
.venv/bin/python scripts/setup_bao.py setup --rotate-token
```

This revokes the prior app token and atomically replaces its local file (and
re-seeds the demo). With an existing supervised tunnel, add `--external-tunnel`.
Consumers reload that file on their next resolution. No silent renewal or root
credential use is performed by the application provider.

This is a single-node prototype: **no production HA, auto-unseal, hardware-backed
custody, automated encrypted off-host backups, identity federation, or claimed
state-of-the-art secret handling**. The protected-file provider remains useful
for explicit existing SSH keys without spreading those keys into a second store.

## Tests

```sh
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_secrets.py -q
```

Tests cover exact file permissions/ownership, hardlinks/symlinks/empty/nonregular
files, context lifetime/repr, explicit scoping, malformed/remote HTTP endpoints,
TLS validation (including a real rejected self-signed TLS handshake), exact KV v2
request/authentication shape, error-body redaction, redirects, safe descriptors,
bootstrap 0600 writes, policy/config constraints and tunnel TIME_WAIT handling.
The separate live setup/restart/verify commands exercise the actual guest; unit
HTTP fixtures never act as an offline fallback for live operations.
