# Local application observability

This package monitors **the KuberWeaver app**, not the guest infrastructure stack.
It does not change the parent repository's existing monitoring configuration,
monitoring host, network, firewall, credentials, or provider bindings.
Configuration files are **not evidence of a deployed monitoring service**.

## Contents and integration contract

- `ops/observability/prometheus/prometheus.yml`: 15-second, GET-only `/metrics`
  scrape of `127.0.0.1:8088`, with required `job=kuberweaver` and
  `service=kuberweaver` labels. Keep these labels when adapting the target.
- `ops/observability/prometheus/alerts.yml`: eight local-app alert rules.
- `ops/observability/prometheus/rules.test.yml`: real promtool fixtures for a
  missing target, missing service label, old app without health metrics, and a
  correctly labeled healthy app.
- `ops/observability/grafana/`: loopback-only server configuration, a dedicated
  non-default Prometheus datasource, file provisioning, and a 17-panel dashboard.
- `backend/kuberweaver/observability.py`: `snapshot(app)` and `metrics_text(app)`.

The API owner should call `snapshot(app)` from the administrator-authorized
operations inspection route and `metrics_text(app)` from `/metrics`, returning
Prometheus text with content type `text/plain; version=0.0.4`. These helpers do not
install routes or alter existing authentication. The public loopback metrics
surface exports only aggregate evidence; retain the app's local Host checks.
An old running build will not gain new metrics until its normal, explicitly
approved reload. A successful old-build scrape triggers `KuberWeaverTelemetryMissing`
rather than inventing health.

## Read-only evidence and limitations

`snapshot(app)` returns schema version 1:

- `storage.status`: `ok` only after actual SQLite job/audit reads, otherwise
  `unavailable`. Unreadable storage returns **null** jobs/audit evidence, not zero.
- `jobs`: persisted total, allowlisted current-status counts (including `unknown`),
  invalid-record count, explicit manual-review count, and
  `submission_to_finish_seconds` observations/sum/max/invalid-timestamp count.
  This is durable data, not a process-local job completion counter.
- `worker`: actual thread liveness, pause flag, whether an identity is active, and
  approximate queue depth. Queue depth may include a shutdown sentinel; it is
  not a durable queue count. Read-only inspection never starts or wakes a worker.
- `capabilities`: each selected provider's identifier, compatibility/availability,
  allowlisted registration configuration status, and cached probe evidence.
  `available` means compatible implementation, **not configured or reachable**.
  `provider_configuration=valid` means binding configuration was accepted, not
  that every optional capability is configured. In particular the default secret
  provider can be compatible and `unconfigured`.
- `status`: local storage/worker/binding/record-integrity aggregate. It is not
  guest, service, secret availability, successful deployment, or upstream health.
  Persisted failures/manual-review and probe freshness have separate metrics.

The health read does **not** call inventory/diagnostic `read`, secret `resolve`,
notification delivery, execution, retry, catalog discovery, provider `describe`,
network requests, or write/audit methods. Registry metadata and cached provider
fields are inspected; arbitrary metadata, errors, graph/config bodies, job IDs,
user identities, event messages, and secret references/values are not returned.
Trusted extension compatibility checks inspect method signatures, not consumer
results. Trusted Python extensions are not a security sandbox.

The built-in inventory records a successful observation only after its existing
nodes/links fetch succeeds. Health never refreshes it. Cached evidence is
`succeeded` only when the latest recorded attempt succeeded and the last success
is at most 300 seconds old; otherwise it is `stale`, `failed`, `never_observed`,
or `unknown`. Invalid, timezone-naive, or future last-success timestamps do not
become successful observations. A failed recent attempt does not erase the
last-success age. Providers without cached evidence are never reported as probed
successfully. Probe attempt timestamps/durations are not available in the existing
provider contract and are not fabricated.

Duration evidence uses valid timezone-aware `created_at` and `finished_at` from
terminal persisted records. It includes admission/approval waiting and is **not
execution latency**, an SLO, a histogram, or a monotonically increasing counter.
Missing/reversed timestamps are counted as invalid; no maximum is emitted when
there are no observations. Rejected/recovered jobs may lack finish timestamps.
Rollback updates can span the original submission to the latest finish.

### Metrics

Existing names remain: `kuberweaver_http_requests_total`, `kuberweaver_jobs{status}`,
`kuberweaver_audit_events_total`, `kuberweaver_queue_depth`. Request totals are
process-local and include scrapes; audit totals reflect the current persisted
store and can reset if state is replaced. Job-status series are gauges, not
completion throughput counters.

Additional gauges:

- `kuberweaver_operational_healthy`, `kuberweaver_storage_readable`
- `kuberweaver_worker_alive`, `kuberweaver_worker_paused`, `kuberweaver_worker_active`
- `kuberweaver_jobs_manual_review`, `kuberweaver_jobs_invalid_records`
- `kuberweaver_job_submission_to_finish_seconds{statistic="sum|max"}`
- `kuberweaver_job_submission_to_finish_observations`
- `kuberweaver_provider_configured{kind}` (configuration, not connectivity)
- `kuberweaver_provider_probe_observed{kind}`
- `kuberweaver_provider_probe_success{kind}` (omitted without observed evidence)
- `kuberweaver_provider_last_success_age_seconds{kind}` (omitted without a valid timestamp)

Missing series are not mapped to green, success, or `or vector(0)` defaults.
The dashboard displays **No data / unknown**; current-value stat panels use
instant queries rather than reducing old successful samples across a time range. Keep both required scrape labels;
missing labels trigger `KuberWeaverTargetMissing`. `up=1` only proves a scrape,
not healthy operations. Persisted-failure alerts describe existing failed records,
not new incidents. No automatic remediation or Alertmanager delivery is configured.

## Portable native operation (not started by this change)

Use separate local binaries and **separate state paths**, never the existing
monitoring service's configuration or data directory. Review free ports first:

```sh
ss -ltn '( sport = :9091 or sport = :3301 )'
```

From the KuberWeaver repository root, with `prometheus` installed:

```sh
ROOT="$PWD"
mkdir -p "$HOME/.local/state/kuberweaver-observability/prometheus"
cd "$ROOT/ops/observability/prometheus"
prometheus --config.file=prometheus.yml \
  --web.listen-address=127.0.0.1:9091 \
  --storage.tsdb.path="$HOME/.local/state/kuberweaver-observability/prometheus" \
  --storage.tsdb.retention.time=7d
```

Run that command in the foreground and stop with Ctrl-C. Rule paths are relative
to this working directory. No reload/admin API is enabled. Prometheus must share
the app's host network namespace for the default loopback target; **container
localhost is not host localhost**. Do not expose the app on a bridge or weaken
its Host validation merely to make scraping work. Adapt an independently reviewed
scrape topology if using containers; no compose deployment is supplied here.

For a separate installed Grafana binary, from the repository root in another
terminal (installation-specific homepath may also be required):

```sh
ROOT="$PWD"
export KW_DASHBOARDS_PATH="$ROOT/ops/observability/grafana/dashboards"
export GF_PATHS_PROVISIONING="$ROOT/ops/observability/grafana/provisioning"
export GF_PATHS_DATA="$HOME/.local/state/kuberweaver-observability/grafana/data"
export GF_PATHS_LOGS="$HOME/.local/state/kuberweaver-observability/grafana/logs"
export GF_PATHS_PLUGINS="$HOME/.local/state/kuberweaver-observability/grafana/plugins"
mkdir -p "$GF_PATHS_DATA" "$GF_PATHS_LOGS" "$GF_PATHS_PLUGINS"
grafana server --config "$ROOT/ops/observability/grafana/grafana.ini"
```

The config binds `127.0.0.1:3301`, disables anonymous access/signup and reporting,
and connects the dedicated datasource to `http://127.0.0.1:9091`. Set a unique
Grafana administrator credential through your approved private credential
mechanism before use; do not place passwords in this repository, shell history,
or monitoring datasource URLs. Neither app credentials nor secret resolver access
are needed for metrics scraping. Do not provision this package into a shared
Grafana instance without explicit review of paths, datasource URL, and permissions.

## Reproduce validation

Tests follow RED→GREEN development of each helper behavior. From the repository root:

```sh
.venv/bin/python -m pytest tests/test_observability.py -q
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m json.tool ops/observability/grafana/dashboards/kuberweaver.json >/dev/null
```

A Docker daemon was available without sudo. Validation used the existing official
Prometheus image `prom/prometheus:v3.15.1`, resolved locally to
`prom/prometheus@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2`.
The official [installation documentation](https://prometheus.io/docs/prometheus/latest/installation/)
identifies `prom/prometheus`; the official [v3.15.1 release](https://github.com/prometheus/prometheus/releases/tag/v3.15.1)
confirms that version. Grafana's [provisioning documentation](https://grafana.com/docs/grafana/latest/administration/provisioning/)
describes file provisioning and environment substitution. No Grafana image or
compose stack is required or claimed to be deployed.

```sh
IMAGE='prom/prometheus@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2'
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  -v "$PWD/ops/observability/prometheus:/etc/prometheus:ro" \
  -w /etc/prometheus --entrypoint /bin/promtool "$IMAGE" \
  check config prometheus.yml
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /scratch:rw,noexec,nosuid,size=64m,mode=1777 -e TMPDIR=/scratch \
  -v "$PWD/ops/observability/prometheus:/etc/prometheus:ro" \
  -w /etc/prometheus --entrypoint /bin/promtool "$IMAGE" \
  test rules rules.test.yml dashboard.test.yml
```

Actual validation during implementation:

- Helper tests: **10 passed**, real app fixture and actual SQLite state, including
  malformed records, closed storage, stopped worker, secret/nonmutation sentinels,
  cached stale/failed/successful observations, and duration bounds.
- Initial full backend suite: **385 passed, 1 skipped**. A later concurrent-work
  full run returned **394 passed, 1 skipped, 1 failed**: the Redis extension's
  authenticated-worker test received HTTP 409 when disabling immediately after
  terminal rollback. That Redis test plus all helper tests passed on isolated
  rerun (**11 passed**); the apparent worker-finalization race is outside this
  package's owned files and is not represented as a clean latest full-suite run.
- Real promtool: config valid; **8 alert rules valid**; rule fixtures **SUCCESS**.
  Generated helper exposition also passed `promtool check metrics` with exit 0.
  All **16 dashboard PromQL expressions** returned no invented samples on empty
  input; a persisted-duration fixture proved the mean query actually matches
  labels and returns 120 seconds for 240 seconds across two observations.
- Dashboard: JSON parsed; **17 unique panel IDs**; all six YAML files parsed.
- First promtool rule-test attempt failed because its temporary TSDB needed write
  space under a read-only container root; rerun with the explicit `/scratch` tmpfs
  succeeded. Config validation did not require writable state.

No monitoring services were deployed, no ports published, and no live monitoring
or app process restarted. Validation containers used `--rm`, no network, no named
volumes, and no persistent handles; there is no deployed stack to clean up.
Grafana UI rendering, real app scrape collection by a running Prometheus, alert
routing/delivery, and end-to-end monitoring persistence are **not verified** by
these static/probe tests and must not be inferred from the YAML/dashboard files.
