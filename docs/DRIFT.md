# Drift reports

A drift report compares what the portal recorded about a stack with what the
hypervisor reports right now. It is evidence, not a control loop: a report is
written, a changed field is shown, and nothing is repaired. The portal never
powers a VM on or off, reconfigures it, applies a plan or deletes anything as
part of a check.

## What is compared

The declared side is the encrypted state document of one
`(environment, stack)` record (see [environment and stack state](ENVIRONMENT_STATE.md)).
The live side is one read of the recorded resource through its own connection:

- an adoption document carries the VM attributes at the top level;
- an applied document is the engine's own state, where the same attributes sit
  in the resource's `values` object.

Only managed resources of type `esxi_vm` are read, and only fields the document
actually declares are compared. An attribute the document does not declare is
not compared and does not appear in `checked`; it is not reported as a drift.

| Field | Declared source | Live source |
| --- | --- | --- |
| `uuid` | Address `esxi_vm.<uuid>` | The UUID in the row returned by `read_resource` |
| `name` | Document attribute | Live inventory |
| `power_state` | Document attribute | Live inventory |
| `cpu` | Document attribute | Live inventory |
| `memory_mb` | Document attribute | Live inventory |

A resource whose type has no read-only reader, such as `libvirt_domain`, is
reported as `unsupported` instead of being silently skipped. A `data` resource
is a lookup, not owned infrastructure, so it is not compared either.

## Statuses

One report exists per state record and is replaced by the next sweep.

| Status | Meaning |
| --- | --- |
| `in_sync` | Every compared field matched and every resource was read. |
| `drift` | At least one compared field differs; `fields` names each one with its declared and live value. |
| `unverified` | Nothing drifted, but a resource could not be read, has no recorded connection, has no live reader wired, or an apply lease or destruction is in progress. |
| `unsupported` | No readable resource type is present, so nothing could be compared. |
| `empty` | The record declares no resources (generation 0 or an emptied generation). |
| `unavailable` | The recorded document cannot be decrypted, so the declared side is unknown. |

A read failure is never reported as drift: the portal cannot prove a difference
it could not observe, and it does not infer that a VM is absent because a driver
raised. `reason` carries a fixed explanation; it never carries a provider
message, a document value, a path or a credential.

Report body:

```json
{
  "id": "<state identity>",
  "environment": "dev",
  "stack": "vm-app",
  "generation": 1,
  "source": "adoption",
  "checked_at": "2026-11-17T03:00:00+00:00",
  "status": "drift",
  "reason": null,
  "summary": {"resources": 1, "in_sync": 0, "drift": 1,
              "unverified": 0, "unsupported": 0},
  "resources": [
    {
      "address": "esxi_vm.11111111-1111-4111-8111-111111111111",
      "type": "esxi_vm",
      "mode": "managed",
      "status": "drift",
      "reason": null,
      "checked": ["uuid", "name", "power_state", "cpu", "memory_mb"],
      "fields": [{"field": "cpu", "declared": 2, "live": 8}]
    }
  ]
}
```

## Running a check

```
POST /api/environment-states/drift/run
```

The run is administrator-only, needs the session CSRF token, and is not mapped
to any agent scope. It compares every state record and writes one
`environment_state.drift_checked` audit row with the counts. A concurrent run is
refused with 409 rather than interleaved.

The nightly sweep runs from the portal process every
`KUBERWEAVER_DRIFT_SECONDS` (default `86400`, `0` disables it, values from `60`
to `604800` are accepted). An unusable value falls back to the default and is
reported in `schedule.error` without stopping the app. The sweep writes no audit
row: the report timestamps are the evidence. A failed sweep never ends the loop,
and one unreadable stack never prevents the other reports.

The loop only starts outside `testing` mode; a test opts in by naming the
interval in the environment.

Read routes, available to any signed-in role and to an agent token with
`read:infrastructure`:

```
GET /api/environment-states/drift
GET /api/environment-states/{state id}/drift
```

The collection carries `reports`, a `summary` of statuses and the effective
`schedule`. The detail route answers 404 for an unknown record and for a record
that has never been checked.

## What a check writes

A check writes one row in `environment_state_drift` (the report) and, for the
manual route, one audit row. It never writes an `environment_state` record, a
document, a claim, a destruction record or a dashboard record, and the state
generation, digest and document bytes are unchanged afterwards. The only driver
call is `read_resource`; a driver that does not implement it is reported as
unreadable, never asked for a discovery or a write.

Reports are stored in the same database as state, so a backup that includes
PostgreSQL or the SQLite file includes them. Losing a report loses no state: the
next sweep recreates it. The PostgreSQL backend registers
`environment_state_drift`; the SQLite migration script accepts the same table.
