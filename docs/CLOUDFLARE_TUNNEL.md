# Cloudflare tunnels (cloudflared)

Named-tunnel capability on the same governance model as DNS changes and
deployments: a durable request, human approval, pinned execution, vault-only
credentials. Live use requires a real tunnel-purpose API token and account;
everything here is tested against an in-process mock of the Cloudflare API.

## What a create request does

`POST /api/cloudflare/tunnel/requests` with `operation=create` proposes:

1. a remotely managed named tunnel (`config_src=cloudflare`),
2. its ingress rules (hostname -> local service, catch-all last),
3. an optional proxied CNAME route `<hostname> -> <tunnel_id>.cfargotunnel.com`,
4. storage of the cloudflared connector credential in the vault.

Nothing touches Cloudflare before approval. Approval re-verifies the connector
fingerprint and the payload digest, resolves the API token from the vault,
verifies it live (`/accounts/{id}/tokens/verify`), then executes exactly the
approved payload. The created tunnel id is persisted immediately
(`upstream_tunnel_id`) so an interrupted execution leaves a cleanup trace.

## Credential contract

- The API token is the reference-only connector credential from
  `CLOUDFLARE_SECRETS.md` (purpose `tunnel`: Account.Cloudflare Tunnel Edit,
  Zone.Zone Read, Zone.DNS Edit, scoped to the specific account/zone).
- The tunnel token returned by `GET .../cfd_tunnel/{id}/token` is a secret.
  It is shape-checked fail-closed (base64 of the closed `{a, t, s}` JSON object,
  bound to the exact account and tunnel id), stored through the provider write
  path in a closed `{"tunnel_token": ...}` envelope with provider readback, and
  never cached. Default vault reference: `cloudflare/tunnel-<tunnel_id>`;
  override with `tunnel_secret_ref` on the request.
- A pre-existing vault reference is a conflict, never an overwrite.
- For the guest install, the approved create also stages the token as a private
  handoff file `<state_dir>/cloudflare-tunnels/<tunnel_id>/token` (dir 0700,
  file 0600, no trailing newline). The vault stays the source of truth; the
  staging path (never the value) appears in the result. Delete wipes the
  staging file and, when the provider supports deletion, the vault entry
  (local-vault retains it; the result reports this honestly).
- No credential material enters a request record, audit detail, log, response,
  plan or argv. Upstream evidence is allowlisted, bounded and redacted.

## Exposing the supervision VM websites

The execution result carries a deterministic, secret-free `cloudflared` bundle:
pinned release `2026.11.1`, the official SHA256 of `cloudflared-linux-amd64.deb`
(`744ce90b...c9b6`, verified against the release checksums and the GitHub asset
digest), the systemd unit text and the install steps. The unit reads the token
from `EnvironmentFile=/etc/kuberweaver/cloudflared/tunnel.env` (0600), so the
credential never sits in a world-readable unit file or in argv.

Runbook once real credentials exist (human-approved end to end):

```
kw auth login --username admin
kw cloudflare tunnel request --connector-id <id> --operation create \
    --name supervision-web --zone-id <zone> \
    --ingress-json '[{"hostname":"zabbix.lab.example","service":"http://127.0.0.1:80"}]'
kw cloudflare tunnel approve <request-id>
```

Then follow `result.cloudflared.steps`: scp the 0600 staging file to the guest,
`sed 's/^/TUNNEL_TOKEN=/'` it into the env file (token never in argv), shred the
transfer copy, download the pinned .deb, `sha256sum -c`, dpkg, install the unit,
`systemctl enable --now kw-cloudflared`. Grafana is the same flow with
`http://127.0.0.1:3000` (or a second ingress rule on the same tunnel).

Reads (`kw cloudflare tunnel list/show`) are authenticated and audited, no
approval needed. `configure` replaces ingress; `delete` removes the tunnel, the
optional DNS record, the staging file and (provider permitting) the vault entry.

## Governance

- Humans: operator may submit, admin approves/rejects; viewer reads only.
- Agents: reads need `read:infrastructure`, submit needs `submit`, approve/reject
  need an explicit human-granted `approve` scope. Credential ingestion stays
  human-admin-only (no agent scope mapping), as for connectors.
- CSRF/origin guards, per-connector execution locks, durable `executing` rows,
  PostgreSQL production contract (`cloudflare_tunnel_requests` registered in
  COLUMNS/DDL/ORDERED) all match the DNS capability.

## Tests

`tests/test_cloudflare_tunnel.py` (mock transport, real local vault, leakage
scans of rows/audit/responses/tracebacks, PostgreSQL isolated-schema lifecycle)
and the tunnel CLI journeys in `tests/test_cli.py`. No test contacts Cloudflare;
all tokens are disposable fixtures.