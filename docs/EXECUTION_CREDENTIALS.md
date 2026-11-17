# Operation-scoped Ansible SSH credentials

This local, administrator-controlled Ansible injection facility does not add graph fields for arbitrary SSH hosts, usernames, paths or private keys. It does not modify the lab's SSH configuration or existing key files. The separate [managed SSH-key workflow](SSH_KEYS.md) supports creation, selection, disabling and password-confirmed administrator export; it does not guarantee guest revocation or remote rotation.

## Administrator configuration

Under the executor's state directory (normally `data`), create `config/execution-credentials.json`:

```json
{
  "enabled": true,
  "targets": {
    "app": {"private_key_ref": "ssh/app", "username": "ubuntu"},
    "branch-client": {"private_key_ref": "ssh/branch-client"}
  }
}
```

References must already be approved by the **selected** secrets provider (`config/secrets.json` for the builtins). They are logical names, not paths or URLs. Only executor-known target IDs are accepted. Optional usernames match `[a-z_][a-z0-9_-]{0,31}`. Unknown configuration fields, unknown targets, invalid references/usernames, malformed configuration, and missing target mappings fail closed. An enabled configuration never silently falls back to the legacy key when provider resolution fails. All supplied mappings are validated even when disabled.

An absent file, or a valid `{"enabled": false, "targets": {}}`, preserves existing SSH behavior. `null` is invalid, not equivalent to an absent file. Protect this administrator configuration from application users; do not commit real credentials. No real configuration or lab resources are changed by the tests.

## Host integration hook

The executor supports either:

```python
Executor(state_dir, secret_resolver=resolve_selected_secret)
# or, for the existing shared instance:
executor.secret_resolver = resolve_selected_secret
```

The callback takes **one logical reference** and returns a synchronous context manager yielding the existing `SecretValue` interface (`bytes()`). Wire selection lazily, not to a builtin captured at startup:

```python
def resolve_selected_secret(reference):
    return resolve_provider(app, 'secrets').resolve(reference)
```

An unavailable/disabled selected provider must raise, never select another provider. The resolver is called immediately before each snapshot/apply/verify/restore command, and its context closes afterward. Host integration must wire this hook through provider selection and verify it with authenticated consumer tests; the helper alone does not establish that wiring.

### Required approval binding

**Do not enable this path in the application without approval drift enforcement.** Import:

```python
from kuberweaver.execution import TARGETS
from kuberweaver.execution_credentials import credential_configuration_fingerprint

fingerprint = credential_configuration_fingerprint(
    state_dir, TARGETS,
    {'name': selected_secrets_name, 'provenance': selected_secrets_source_provenance},
)
```

Persist this reference-only fingerprint at approval and compare it in worker preflight and `RuntimeExecutor._invoke`'s existing per-command source guard, including restore. When credentials are absent/disabled, pass `None` for `secrets_binding` and do not require an available secrets provider; preserve legacy execution compatibility. Also include `execution_credentials.py` in approved execution dependency provenance. Resolve the provider binding afresh for each check. Invalid/unavailable selected provider configuration must fail closed. The helper is deterministic over validated target/reference/username/enabled mappings plus the caller-supplied selected provider identity/provenance; it never resolves, stores or hashes a secret value. Omitted versus disabled configuration intentionally differs. Value rotation under the same approved logical reference is permitted. Mapping/provider identity/source drift requires reapproval. The helper alone does **not** enforce approval.

## Command boundary and cleanup

* A fresh `0700` temporary directory contains a `0600` identity file, isolated Ansible config, and filtered SSH config. None is a job artifact, inventory credential, graph/job field, event, Git object, or returned value.
* Only the temporary identity **path** enters `ANSIBLE_PRIVATE_KEY_FILE`. Arguments remain an argv list; no shell interpolation or raw key in arguments/environment. A reference-only extra-vars override supplies the temporary SSH config path because Ansible inventory variables otherwise override environment/CLI defaults.
* The operation-only SSH config preserves current HostName/User/ProxyCommand/ProxyJump/known-hosts and other routing policy, but removes all `IdentityFile`, `IdentityAgent`, and `CertificateFile` entries. SSH `Include` is refused rather than allowing an unfiltered included identity. The original SSH config is never written. Relative SSH configuration paths are not rewritten; the current lab uses absolute paths.
* `IdentitiesOnly=yes`, `IdentityAgent=none`, `CertificateFile=none`, public-key-only authentication, strict host-key checking, batch mode, and disabled connection multiplexing prevent target identity/agent/default-key or existing-session fallback. A missing username retains the SSH-config user. Existing bastion ProxyCommand behavior is preserved; its independent bastion authentication is not replaced by the target credential.
* Provider tokens, arbitrary `ANSIBLE_*` overrides, SSH agent sockets, and other unrelated environment values are not forwarded. Configured execution uses a minimal environment and a fresh accepted `.cfg` file, preventing inherited callback/logging settings from writing a key-bearing diagnostic elsewhere.
* Credentialed stdout/stderr are suppressed in private logs. Exceptions are sanitized. Result JSON containing full credential lines is refused and removed before evidence is returned. This is defense in depth for reviewed local playbooks, not a sandbox against malicious trusted code or every possible transformation of secret data.
* Normal success, runner exceptions, nonzero exit, timeout, and context failures remove temporary files. The existing runner must obey `subprocess.run`'s blocking/timeout contract and not retain background children. SIGKILL, power failure, filesystem failure, OS memory/swap/core dumps, or a malicious trusted runner are not guarantees of secure erasure; Python/provider copies are best-effort lifetime reduction only.

## Verification

```sh
.venv/bin/python -m pytest tests/test_execution_credentials.py tests/test_execution.py -q
```

Tests generate an isolated disposable Ed25519 key, resolve it through the real restricted-file provider, and launch a real child process that successfully uses the transient key with `ssh-keygen -y` (public output discarded). They exercise cleanup, redacted failures/output/result handling, invalid configuration, safe environment, accepted Ansible configuration, and `ssh -G` effective identities/routing without contacting any host. No live private key is read or printed, and no lab mutation is performed. Separate integration checks are required for API/provider selection and approval drift.
