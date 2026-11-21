# Cloudflare DNS workload brick

`cloudflare-dns` uses the normal [workload job and approval flow](WORKLOADS.md).
The legacy `/api/cloudflare` endpoints and standalone `kw cloudflare` commands
are not used. The generic workload executor still requires an existing guest
and managed SSH key; the DNS role itself runs on the controller without SSH.

## Configure a job

Use a single-node blueprint with `plugin_id: cloudflare-dns`. For example, its
node configuration can be:

```json
{
  "target_uuid": "6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b",
  "ssh_key_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "api_token_ref": "cloudflare/dns-example",
  "zone_id": "cccccccccccccccccccccccccccccccc",
  "record_type": "A",
  "record_name": "demo.example.com",
  "address": "192.0.2.42",
  "ttl": 300
}
```

An administrator must bind the logical `api_token_ref` alias for this brick in
`config/workload-secrets.json` to an approved vault provider and reference. The
token needs DNS edit access scoped to the selected zone. Do not put token
material in that file or the blueprint. Ansible receives it only at execution;
the role suppresses task logging and diffs, and sends it only to the fixed
Cloudflare HTTPS origin with certificate verification, no proxy, and no redirects.

## Lifecycle and limits

- **Snapshot and apply:** Require an unused fully qualified ASCII DNS name, then
  create one DNS-only A or AAAA record. `ttl` defaults to `1` (automatic). The
  brick refuses to adopt or overwrite a record, even if its values match.
- **Verify:** Read back the returned record ID and match the approved values and
  the job's ownership marker. This is API verification, not a resolver query.
- **Rollback and removal:** Use the original job's private receipt and configuration.
  The brick deletes only the exact receipted ID after checking for edits, then
  confirms absence. The generic workload API does not expose manual removal or
  rollback of a successful job.
- **Uncertain writes:** The receipt records intent before sending a mutation.
  If the reply is lost or cannot be validated, the brick refuses to retry or
  guess which record to delete. Preserve the private job directory and reconcile
  manually. Cloudflare's record endpoints do not provide an atomic conditional
  write here, so avoid concurrent changes to the same name or ID.

This brick does not manage zones, existing records, wildcard records, proxying,
or record types other than A and AAAA. Tests use an injected HTTP transport and
local Ansible execution with a socket-free module fixture; no live Cloudflare
operation was performed.
