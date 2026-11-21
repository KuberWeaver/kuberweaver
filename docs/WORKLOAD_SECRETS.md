# Vault credentials for Ansible services

A trusted workload manifest declares `workload_secret_ref`, the name of a field in its closed config schema. The field takes a logical alias, never a credential or a controller file path.

An administrator first creates a dedicated secret in a configured vault using its own secure ingestion tool. Use the provider's existing secret format (the resolved value must be a plain token, not a JSON envelope). Old connector credentials are not implicitly migrated. Do not reuse a tunnel token as a DNS API token.

Under the controller state directory, create `config/workload-secrets.json`, owned by the controller account, mode `0600`. This file contains references only:

```json
{
  "cloudflare-tunnel": {
    "cloudflare/tunnel-example": {
      "provider": "openbao",
      "reference": "cloudflare/tunnel-example-v1",
      "revision": 1
    }
  }
}
```

The alias is authorized for this service only. Use a distinct, least-privilege vault credential for each service and trust boundary. On rotation, use a new versioned vault reference and increment the revision. Do not replace the value in place: the reviewed plan binds the reference, revision and provider code, not the bytes of the secret. A changed binding requires a new plan and approval.

The runner opens the vault only for execution. The secret reaches the trusted Ansible child as a process environment variable, not as an argument or on-disk extra-vars value. The child must use `no_log` on every consumer and must not echo or report credentials. Like the executor, the controller OS account and its administrators are trusted. Bricks without a credential declaration have unchanged behavior.

Create a blueprint from the service catalog and submit it with `kw deploy`. The standard approval applies. A missing or invalid binding blocks preparation. A missing vault value blocks execution before Ansible runs. For a credential-backed role that connects over SSH, register the independently verified guest public host key in the controller's system SSH known-hosts file first; the job refuses unknown and changed keys.
