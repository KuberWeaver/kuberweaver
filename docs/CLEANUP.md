# Prototype to production: what gets removed

The prototype catalogue proved the mechanism. It is not the product. This file
lists what must go, why, and what replaces it. Nothing here is removed silently:
each entry is a task with tests updated in the same change.

## Remove: hardcoded lab targets

`backend/kuberweaver/execution.py` resolves targets from a constant map of two
prototype hosts (`app`, `branch-client`) with fixed addresses and a developer SSH
config path. That cannot ship.

Replacement: `ansible-workload-v1` (`backend/kuberweaver/workload_execution.py`)
resolves a target from recorded deployment evidence and validates it. The
container engine is deleted once the last catalogue entry uses the brick engine.

## Remove: demonstration catalogue entries

| Entry | Why it goes |
| --- | --- |
| `plugins/nginx` | container demo on fixed hosts; replaced by `plugins/web-nginx` |
| `plugins/dev-stack` | multi-service demo, no verification contract |
| `plugins/redis` | demo of the container engine |
| `plugins/pfsense` | demo, touches a network appliance |
| `plugins/cisco-ios` | demo, no verifiable readback |
| `plugins/cloud-vm` | superseded by `plugins/vmware-vm` |

Each removal ships with its tests removed or rewritten, and with the catalogue
still loading cleanly.

## Remove: prototype drivers

`scripts/execution_live.py`, `scripts/execution_blueprints.py` and
`scripts/browser_acceptance.py` drive the container engine against the fixed lab.
They go with it. `scripts/run_brick.py` is the supported driver for bricks.

## Rewrite, do not delete

- `README.md`: currently mixes prototype history with instructions. Rewritten as
  a product document: what it does, what it refuses to do, how to run it.
- `docs/` plans: keep the reasoning, drop the "prototype" framing from the
  public pages.
- `/healthz`: becomes liveness only. Readiness gets its own endpoint.
- Ad-hoc admin session: replaced by the user store, roles and cookies.

## Inventory is opt-in

The GNS3 inventory provider was the prototype's default topology source. It stays
available, but no longer ships as a default binding: a lab network is a
convenience, not a requirement. With no binding, `/api/infrastructure` reports
`bound: false` and the Infrastructure page explains that a topology source is
optional instead of presenting a lab as the platform's view of the world. Bind
one in Providers only when you actually want to compare with a live network.

## Keep unchanged

- Plan digest, approval, evidence and lock discipline.
- The PostgreSQL requirement and the vault abstraction.
- The VMware import path and its pins.
- `plugins/web-nginx` as the reference brick.
