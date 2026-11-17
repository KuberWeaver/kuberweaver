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

Read responses add four computed fields that are never written to storage:

- `origin`: scheme, host and port of the URL, lowercased, with the default port
  removed. Two records share an origin when this field matches.
- `probe_path`: `health_path` when set, otherwise the kind default below.
- `embeddable`: `embed` **and** the origin is on the current allowlist. It can
  be `false` for a stored `embed: true` record when the allowlist has since
  shrunk; the verifier then falls back to a link instead of a frame.
- `health`: the last read-only observation, or a state that explains why there
  is none. See [Health probe](#health-probe).

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
| `GET /api/dashboards` | any signed-in role | `{dashboards, embed_origins, embed_error, probe}`; newest first. Each record carries its `health` column. |
| `GET /api/dashboards/{id}` | any signed-in role | One record, with the computed fields. |
| `GET /api/dashboards/{id}/embed` | any signed-in role | HTML page that frames the URL when allowed, otherwise a deep link; see below. |
| `POST /api/dashboards/{id}/probe` | administrator | Runs one probe now and returns the record with its new `health`. A disabled record is refused with `409`. |
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

## Health probe

Probing is read-only and periodic. Every enabled record is observed once per
interval with one `GET` on `origin + probe_path`; a disabled record is skipped
and reported as `disabled`. The default interval is 300 seconds and the default
timeout is 5 seconds:

```bash
export KUBERWEAVER_DASHBOARD_PROBE_SECONDS=300   # 0 disables the periodic loop
export KUBERWEAVER_DASHBOARD_PROBE_TIMEOUT=5
```

The interval accepts `0` (no periodic sweep) or a value between 1 second and 24
hours. The timeout accepts 0.5 to 30 seconds. An unusable value is not fatal:
the registry keeps the default, reports the problem in `probe.error` of the
collection and keeps serving.

The prober sends `User-Agent: kuberweaver-dashboard-probe/1`, `Accept: */*` and
`Connection: close`; it sends no cookie, bearer token, client certificate or
record credential. It does not follow a redirect: a 3xx is recorded as
`malformed`, so a probe cannot move to an origin the operator did not register.
TLS certificates are verified. At most 64 KiB of body is read; a larger answer
is recorded as `malformed`. A record is operator-supplied, so the probe can
reach any host the portal can reach; creating a record and triggering a probe
are both administrator actions.

### States

| `health.status` | Meaning |
| --- | --- |
| `healthy` | A 2xx answer matched the shape below. |
| `unhealthy` | The endpoint answered outside 2xx, or a Grafana health document reported `database` other than `ok`. |
| `unreachable` | No HTTP answer: refused, DNS failure, TLS failure, timeout or an invalid probe URL. |
| `malformed` | A 2xx or 3xx answer whose shape the probe refuses: redirect, wrong content type, invalid JSON, empty document, missing marker or oversized body. |
| `unknown` | The record is enabled but has no observation for its current probe URL, including after the URL, kind or probe path changed. |
| `disabled` | The record is disabled and is not probed. |

`health.drift` is `true` for `unhealthy`, `unreachable` and `malformed`: the
declared bookmark and the observed endpoint disagree. It is `false` for
`healthy`, `unknown` and `disabled`. Drift is reported, never repaired: a probe
does not edit, disable or delete the record, does not change the probe path and
does not retry harder on failure.

### Expected shape

| Kind | Accepted answer |
| --- | --- |
| `grafana` | `application/json` object; an optional `database` field must be `ok`. |
| `netdata` | `application/json` object. |
| `prometheus` | `text/plain` whose body contains `Healthy`. |
| `uptime-kuma` | non-empty `text/html`. |
| `custom` | any 2xx; the body is not inspected. |

The body is never copied into the observation, so a dashboard cannot inject its
content into the portal and an error document cannot leak through the status
column.

### The status column

Each record in `GET /api/dashboards` and `GET /api/dashboards/{id}` carries:

```json
"health": {
  "status": "healthy",
  "observed_at": "2026-11-17T09:30:00+00:00",
  "http_status": 200,
  "latency_ms": 12,
  "detail": null,
  "drift": false
}
```

`observed_at` is the time of the last observation and the only freshness signal;
an old timestamp means the loop has not run since then. `http_status` and
`latency_ms` are `null` when no HTTP answer arrived. `detail` is a fixed
explanation, never response content. The collection also reports the policy in
`probe` (`interval_seconds`, `timeout_seconds`, `error`).

Only the last observation per record is kept, in the
`dashboard_health(dashboard_id, body)` table. An observation is tied to the probe
URL it measured: editing the label or the embed flag keeps it, while editing the
URL, kind or probe path turns the column back to `unknown`. Deleting the record
deletes its observation. The periodic sweep writes no audit row; an explicit
`POST /api/dashboards/{id}/probe` writes one `dashboard.probed` row with the
status and HTTP status, is limited to administrators (agent scope `configure`)
and answers `409` for a disabled record.

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
with insertion order preserved. Observations live in
`dashboard_health(dashboard_id, body)`, one row per record and no history. Every
change of a record writes one audit row: `dashboard.created`,
`dashboard.updated` or `dashboard.deleted`, with the record id as the resource
and `label`, `kind` and `embed` in the detail. A manual probe writes
`dashboard.probed`; the periodic sweep writes no audit row.

## Scope

This registry is the data half of the observability story. It does not proxy or
render a dashboard, generate a time series or certify the external tool. The
embed page below frames an allowlisted URL, the periodic probe consumes the
probe path and reports health and drift, and all of it leaves the dashboard
itself on the operator's server. No probe result changes a record or triggers a
remediation.

## Embed page

`GET /api/dashboards/{id}/embed` answers `text/html` with `Cache-Control:
no-store`. Any signed-in role may read it, and a machine client needs
`read:integrations`, like the JSON routes.

The page frames the recorded URL only when all three conditions hold: `embed` is
`true`, `enabled` is `true`, and the URL origin is on
`KUBERWEAVER_DASHBOARD_EMBED_ORIGINS`. The policy is built for that one record:

```
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline';
  base-uri 'none'; form-action 'none'; frame-src https://grafana.lab;
  frame-ancestors 'self'
X-Frame-Options: SAMEORIGIN
```

`frame-src` names the record origin and nothing else, so the page cannot frame a
second host; `frame-ancestors 'self'` lets the portal frame the page while
keeping other sites out. The framed document is an `<iframe>` with
`loading="lazy"` and `referrerpolicy="no-referrer"`, and a small
`Open in a new tab` link sits over it.

When the record is not framed, the page carries no iframe, sets
`frame-src 'none'`, states the reason and offers the deep link with
`target="_blank" rel="noopener noreferrer"`. The three reasons are: embedding is
not requested for this record, the record is disabled, or its origin is not on
the deployment allowlist. The third case covers a stored `embed: true` record
after the allowlist has shrunk.

Record values are HTML-escaped before they reach the markup, so a label or a
deep link containing markup stays text. The page loads no remote script, style
or font; the stylesheet is inline and allowed by `style-src 'unsafe-inline'`.

Every other response from the portal keeps the default boundary:
`frame-ancestors 'none'` with `X-Frame-Options: DENY`. The embed route sets both
headers itself, and the middleware leaves an explicit value alone.

The allowlist says what KuberWeaver may frame; it cannot make the remote tool
accept a frame. A tool that sends `X-Frame-Options` or a restrictive
`frame-ancestors` of its own will be refused by the browser, and the overlaid
link is the way in.
