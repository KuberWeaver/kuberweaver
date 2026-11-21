# Cloudflare Tunnel Ansible service

`cloudflare-tunnel` connects an existing remotely managed Cloudflare Tunnel from an Ubuntu amd64 guest. It does not create or delete the remote tunnel, configure ingress, or publish a hostname. Set those up in Cloudflare before requesting the service. The legacy vendor API and standalone CLI command are retired.

1. Store the connector token in a vault according to [Cloudflare credentials](CLOUDFLARE_SECRETS.md). Do not use a Cloudflare API token for this service.
2. Bind its alias for `cloudflare-tunnel` using [Workload credentials](WORKLOAD_SECRETS.md).
3. Use a single-node blueprint with `plugin_id: cloudflare-tunnel`, an existing `target_uuid`, the corresponding `ssh_key_id`, and the vault alias as `tunnel_token_ref`. Use the ordinary `kw deploy` request and approval workflow.

Ansible refuses to replace an existing cloudflared installation. It checks the package checksum, installs a dedicated `kw-cloudflared.service`, stores its token in a root-only environment file, and verifies that the local readiness endpoint sees a connected Cloudflare edge. Readiness does not prove that a particular hostname or application is reachable. On deployment failure, rollback is limited to the guest, not existing Cloudflare resources.

The workload API does not expose manual removal of a successful job. The removal playbook requires a matching installation marker and does not remove the tunnel or records from Cloudflare.
