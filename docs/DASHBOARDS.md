# External dashboards

KuberWeaver does not ship a dashboard renderer. It keeps a registry of external
observability tools, so the portal can point at the tool an operator already
runs, and a later read-only probe can reach its health endpoint without adding
vendor code to the kernel. Grafana, Uptime Kuma, Prometheus and Netdata are the
documented kinds; `custom` covers anything else that speaks HTTP.

A registry record is a bookmark with structure. It holds no credentials, is
never fetched while it is created or edited, and does not copy dashboard data.

## Record

| Field | Type | Rules |
| --- | --- | --- |
| `label` | string | 1 to 80 characters, no control characters, no surrounding spaces. |
| `kind` | string | `grafana`, `uptime-kuma`, `prometheus`, `netdata` or `custom`. |
| `url` | string | Absolute `http` or `https` URL, at most 2048 characters, printable ASCII, no userinfo, no fragment. A path and query are allowed for a deep link. |
| `health_path` | string or null | Optional absolute path on the same origin. It may not start with `//`, contain `..`, a query or a fragment. Null selects the kind default. |
| `embed` | boolean | Default `false`. `true` is accepted only when the URL origin is on the deployment allowlist. |
| `enabled` | boolean | Default `true`. A disabled record stays listed and is excluded from probing. |

Read responses add three computed fields that are never written to storage:

- `origin`: scheme, host and port of the URL, lowercased, with the default port
  removed. Two records share an origin when this field matches.
- `probe_path`: `health_path` when set, otherwise the kind default below.
- `embeddable`: `embed` **and** the origin is on the current allowlist. It can
  be `false` for a stored `embed: true` record when the allowlist has since
  shrunk; the verifier then falls back to a link instead of a frame.

### Default health probe

| Kind | Probe path |
| --- | --- |
| `grafana` | `/api/health` |
| `uptime-kuma` | `/` |
| `prometheus` | `/-/healthy` |
| `netdata` | `/api/v1/info` |
| `custom` | `/` |

The path is always resolved against the record origin, so a probe cannot leave
the host the operator registered.

## Endpoints

| Route | Role | Contract |
| --- | --- | --- |
| `GET /api/dashboards` | any signed-in role | `{dashboards, embed_origins, embed_error}`; newest first. |
| `GET /api/dashboards/{id}` | any signed-in role | One record, with the computed fields. |
| `POST /api/dashboards` | administrator | `201` with the stored record. |
| `PATCH /api/dashboards/{id}` | administrator | Partial update; `revision` is required and the stored revision increments. Unset fields are unchanged; an explicit `health_path: null` clears the override. |
| `DELETE /api/dashboards/{id}?revision=N` | administrator | Deletes the record when `N` is the current revision. |

Browser writes carry the session CSRF token. Machine clients use the existing
scope map: reads need `read:integrations`, writes need `configure`. A registry
record contains no secret, so an agent with `configure` may manage it the same
way it manages a connection.

`404` answers an unknown or malformed identifier, `409` a stale revision and
`422` an invalid field or a refused embedding. The revision check makes a
concurrent edit visible rather than silently overwriting it, and a delete that
does not name the current revision is refused.

## Embed allowlist

`KUBERWEAVER_DASHBOARD_EMBED_ORIGINS` is a comma-separated list of origins:

```bash
export KUBERWEAVER_DASHBOARD_EMBED_ORIGINS=https://grafana.lab,http://kuma.lab:3001
```

Each entry is `scheme://host[:port]`. A trailing slash is tolerated; a path,
query, fragment, credentials or a wildcard is refused. Default ports are
normalized, so `https://grafana.lab:443` and `https://grafana.lab` are the same
entry. Duplicates are a configuration error.

The allowlist is empty by default, which means every `embed: true` request is
refused. The comparison is on the whole origin: `https://grafana.lab` does not
allow `http://grafana.lab` or `https://grafana.lab:8443`.

A malformed value does not stop the service. The registry reports the problem in
`embed_error`, keeps `embed_origins` empty and refuses embedding, so a typo in an
optional observability setting cannot take the portal down. Fix the variable and
restart to enable embedding.

## Storage and audit

Records live in the `dashboards(id, body)` table, the same JSON-body shape as
connections and SSH keys, and the PostgreSQL schema translation registers it
with insertion order preserved. Every change writes one audit row:
`dashboard.created`, `dashboard.updated` or `dashboard.deleted`, with the record
id as the resource and `label`, `kind` and `embed` in the detail.

## Scope

This registry is the data half of the observability story. It does not frame
anything, generate a CSP header, probe a target or render a time series; those
belong to the embedding policy and the periodic probe that consume this registry.
The tools themselves remain external and are not certified by KuberWeaver.
