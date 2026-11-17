# Liveness and readiness

KuberWeaver separates two questions a supervisor asks. **Liveness** proves the
process is running and serving HTTP. **Readiness** proves the dependencies needed
to do useful work are usable. Both endpoints are local, unauthenticated and
cheap: they exist for a supervisor on the same host or pod, not for remote
monitoring.

| Endpoint | Question | Success | Body |
| --- | --- | --- | --- |
| `GET /healthz` | Is this process alive? | `200` | `{"status":"ok","service":"kuberweaver"}` |
| `GET /readyz` | Can this process serve its dependencies? | `200` | structured, see below |
| `GET /readyz` | Dependency unusable | `503` | same structure with `status: not_ready` |

`/healthz` never touches storage, the vault or the catalogue. It answers even
while a dependency is down, which is what keeps a supervisor from restarting a
process that is merely waiting for PostgreSQL.

## Readiness body

```json
{
  "status": "ready",
  "service": "kuberweaver",
  "checks": {
    "database": {"status": "ok", "backend": "postgresql"},
    "vault": {"status": "unconfigured", "provider": "restricted-file"},
    "plugins": {"status": "ok", "manifests": 11}
  }
}
```

`status` is `ready` when every check is `ok` or `unconfigured`, otherwise
`not_ready`. The HTTP status is `200` in the first case and `503` in the second.
`checks` always carries the same three keys, so a failing dependency never hides
the state of the others. Responses are sent with `Cache-Control: no-store`.

### `database`

Runs one `SELECT 1` through the store lock against the active backend and reports
`backend` as `sqlite` or `postgresql`. When PostgreSQL is selected, this is a real
round trip; with SQLite it proves the local file is readable. Driver errors,
DSNs and SQL never reach the body.

### `vault`

Checks the selected `secrets` provider only. It never resolves a secret, lists
approved names, calls `describe()` or contacts an endpoint.

- `status: ok` with `verification: material`: the provider exposed a read-only
  `verify()` hook and the existing local material was accepted. The hook must
  raise, or return exactly `False`, to refuse. The built-in `local-vault` provider
  validates its Fernet key and authenticated marker under the same pinned
  descriptors it uses for secrets; it creates nothing and reads no ciphertext.
- `status: ok` with `verification: registration`: the provider was resolved and
  declares itself `configured`, but has no local material check. This proves the
  binding and consumer protocol, **not** that an individual approved file or
  remote path is readable.
- `status: unconfigured`: no secret source is selected for use yet. This is the
  default installation. It is reported explicitly and does **not** make the app
  unready, because the core catalogue, planning and execution paths do not read
  secrets.
- `status: unavailable`: the selected provider is missing, incompatible, its
  configuration is rejected, or its local material failed verification. This
  makes the app unready, because jobs that reference a secret would fail after
  approval instead of before.

### `plugins`

Requires the configured plugin directory to exist, be a real directory (not a
symlink) and contain at least one `*/manifest.yaml`. `manifests` counts candidate
entries; parsing, schema validation and catalogue errors stay on `/api/plugins`
and in the application log. An empty or mistyped mount is treated as unready
because a process with no catalogue cannot plan anything.

## What the probe deliberately does not do

- It does not read or decrypt a secret, so an unauthenticated poller cannot use
  it as a credential oracle.
- It does not call GNS3, OpenBao, a hypervisor or any remote endpoint, so a
  probe interval does not become remote load.
- It does not start the worker, run the notification dispatcher, parse manifests
  or write to the state directory.
- It does not return filesystem paths, provider errors, exception text, job data
  or user identities.

`/readyz` is not a substitute for `/metrics` or the operations snapshot: it
reports three dependencies, not job throughput, provider reachability or guest
health. Remote monitoring remains the separate, reviewed setup described in
[observability](OBSERVABILITY.md).

## Supervisor notes

The HTTP middleware accepts only a loopback `Host` header (`localhost`,
`127.0.0.1`, `::1`). A probe must present one of those even when it connects on a
pod or bridge address:

```sh
curl -fsS -H 'Host: localhost' http://127.0.0.1:8088/healthz
curl -fsS -H 'Host: localhost' http://127.0.0.1:8088/readyz
```

Use short liveness timeouts and a readiness period long enough not to hammer the
database. A probe waits on the store lock and on the driver, so keep the
supervisor's own request timeout shorter than the process restart policy. Do not
weaken the Host check or publish the API to satisfy a probe.

## Reproduce

```sh
.venv/bin/python -m pytest tests/test_health.py -q
```
