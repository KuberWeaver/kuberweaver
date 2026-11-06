# Redis: separately installed trusted runtime + declarative blueprint

## Validation status: planned, not remotely verified

The shipped manifest deliberately has **`capability: planned`** until an operator
proves a real isolated-lab rollout, persistent backup/restore and absence
restoration. Admission and this runtime refuse the unpromoted candidate before
commands. Tests promote only private copied manifests; mocked commands are not
a reason to mark the shipped blueprint live. No live guest validation or install
was performed here. The independently installable extension may be reviewed and
registered, but activation is disabled by default and registration is not proof
of a remotely working workload. Promotion requires a new reviewed source change,
restart/reload and fresh approval; never edit an approved in-flight manifest.

## What is implemented

`plugins/redis/manifest.yaml` is a data-only catalogue entry. Its closed JSON
Schema drives the existing configuration UI: allowlisted targets, management
port, resources and a rollout/probe version. No image, executable, filesystem
path, credential or password can be supplied in the graph. `isolated_lab` must
be true. The `version` field is a deployment/probe revision, **not an image tag**.
The reviewed image is `redis:7.4.2-alpine`; it must already exist locally on the
guest. The helper resolves its image ID and never pulls images automatically.

The separately installable `examples/extensions/redis-runtime` package registers
execution provider **`redis`**. Its small concrete wrapper imports the supported
`kuberweaver.redis_execution.RedisExecutor` package. This is not an HTTP server
pretending to be Redis: it runs Redis with AOF `appendfsync always`, publishes
6379 only on the fixed guest management IP, and performs actual RESP2
`PING`, `SET`, `GET` readbacks. The reserved key is
`__kuberweaver_probe:<graph/node owner hash>`. Applications must not use that
namespace. Probe readback returns booleans/PONG, never application data.

The runtime reuses only the core executor's serialized lock, ownership ledger,
DAG ordering, private snapshots/journal, one-canary-per-node, conservative rolling
batches, reverse rollback and partial-restoration retry. It supplies its own
strict Redis manifest validation, Ansible operation boundary, guest lifecycle,
RESP probes and `redis://` endpoints. Mixed/unsupported graphs are rejected before
any guest command, including mixed persisted rollback journals. A failed or
missing proof cannot return success. The default HTTP executor does **not**
accept the `redis-container-v1` protocol.

## Explicit trust and activation (disabled by default)

Run from the application root. These commands are operator instructions, not
steps performed automatically by installing the catalogue entry:

```sh
.venv/bin/python scripts/plugins.py doctor examples/extensions/redis-runtime
.venv/bin/python scripts/plugins.py install examples/extensions/redis-runtime
.venv/bin/python scripts/plugins.py list
# Review plugin.yaml, module.py, backend/kuberweaver/redis_execution.py,
# the inherited execution/credential code and EVERY plugins/redis file first.
.venv/bin/python scripts/plugins.py enable redis-runtime --trust
# Restart, or use the authenticated idle-only administrator activation API.
```

Installation is local-only and disabled by default. No dependency install,
network fetch, pip hook, or guest mutation occurs during registration. Explicitly
select `{"bindings":{"execution":"redis"}}` through the administrator
`PATCH /api/providers` endpoint (session cookie + `X-CSRF-Token`). Enabling does
not select the provider and selecting does not approve jobs. Normal admission,
approval, source pinning, worker serialization and administrator rollback still
apply. Disabling removes this provider; a selected-but-missing `redis` binding
fails closed, without falling back to HTTP execution.

**Trusted means application OS privileges, not sandboxing.** The wrapper
intentionally derives the host data root from its SDK namespace and shares
`<data>/ownership.json` and `<data>/jobs/` with the serialized host executor.
This is required for cross-provider ownership and kernel retry eligibility;
it is privileged access beyond private extension data. Do not enable unreviewed
variants. Restrict source/config/state writes to the service owner. The concrete
wrapper is pinned by host provider provenance; the Redis runtime additionally
checks its own full inherited implementation, credential-boundary source and
Redis blueprint tree before operations and again immediately before invoking the
runner. It preserves the parent's command wrapper rather than replacing it.
Source drift after a mutation also blocks restore: expect `rollback_failed` and
operator review, never automatic execution of changed code.

## Non-negotiable isolated-lab prerequisites

Only `app` (`192.168.122.40`) and `branch-client` (`192.168.122.60`) are supported,
through the existing reviewed SSH aliases/configuration. Ports are restricted
to **18090..18109**. Redis has **no password/TLS support here** and must not be
exposed to an unauthenticated LAN or used as a production datastore.

Binding a management IP alone is insufficient: Docker publishing can bypass
ordinary host INPUT/UFW rules. Before *every* lifecycle operation, the helper
reads `iptables -S DOCKER-USER` and requires its **first append rule**, exactly:

```text
-A DOCKER-USER ! -s 192.168.122.1/32 -j DROP
```

The lab administrator must separately review/provision this controller-only
policy and prove the controller really uses `192.168.122.1` (no client-source
NAT masquerading unrelated LAN clients as that address). The helper does not
insert/remove firewall rules. This intentionally restrictive rule affects **all
Docker forwarding on the guest**, including other services; do not apply it to
a shared/non-isolated host. Earlier ACCEPT/RETURN rules, missing iptables,
missing chain or different policy fail closed. Host-local access remains trusted.
Docker must use its normal iptables bridge forwarding path; alternate nft-only,
rootless, Swarm/host/macvlan and IPv6 exposure configurations are unsupported.
Keep the management bridge isolated at the hypervisor/router as well. Do not
weaken this gate to make a rollout pass.

The reviewed image must be preloaded by the administrator, Docker/Ansible/SSH
must already work, and guests need root-mediated access to Docker, iptables and
`/opt/kuberweaver/redis`. No credential is included in graphs. The existing
controller secret/SSH credential boundary remains separate from Redis auth.

When execution credentials are enabled, each Redis snapshot/apply/verify/restore
resolves the administrator-selected secret provider at the operation boundary.
It passes the fixed reviewed SSH configuration into the shared credential helper,
which creates a private operation-only key (0600 in a 0700 directory) and an SSH
configuration with existing identity/agent/certificate directives removed. Ansible
extra vars override the legacy inventory's SSH common args with that isolated
configuration; changing only the process environment would not override inventory
precedence. SSH agent fallback is disabled. Only allowlisted basic environment
variables survive; provider tokens, agent sockets and inherited Ansible overrides
do not. The parent's approved-command runner and source checks remain intact.

Redis never persists subprocess stdout/stderr, including credential-bearing output.
The helper screens the result file, including decoded JSON escape sequences,
**before** it can become returned evidence or a snapshot. Leaked secrets, malformed
JSON and runner exceptions fail with a sanitized credential error and remove the
result file. Temporary keys/configuration and operation vars are cleaned even on
parse failure or timeout. Missing selected references/resolvers/target mappings
fail closed, never falling back to legacy SSH. Disabled/absent credential
configuration retains the existing reviewed SSH path.

## Persistent backup and restoration

Every target is snapshotted before the first workload apply. For a running owned
Redis container, snapshot performs a read-only PING/GET, gracefully stops **only
that owned container**, confirms it is stopped, then archives the whole service
tree (including AOF manifest/base/incremental files and owner metadata). It
restarts the old workload in a `finally` block and checks PING. The stop/start is
an intentional brief backup-maintenance interruption, not a zero-downtime
snapshot. If archiving fails, no rollout is started.

Snapshots are private controller JSON files containing base64 compressed data,
file hashes/modes/UID/GID, immutable image ID, prior strict config, existence and
running-state evidence. They are limited to **16 MiB uncompressed data and
16 MiB compressed archive, 4096 entries**. Symlinks, hardlinks and special files
are rejected. `disk_gb` participates in admission/cost; it is not a filesystem
quota. Large datasets are unsupported, rather than silently truncated.

Restore validates the snapshot identity and archive members, stages it in a
private directory and verifies all bytes/metadata **before** removing the current
owned workload. It stops/removes only that container, replaces the data tree,
verifies it before starting Redis (AOF startup may legitimately modify files),
recreates the fixed reviewed command with the original immutable image and
resource/binding/security config, then checks running state and PING/old probe
readback. Previously stopped containers remain stopped. Previously absent
container/data paths are removed and absence is read back. Foreign labels,
mounts, paths, listeners or unsupported runtime options refuse mutation.
The helper never replays arbitrary saved Docker commands or environment input.

This is coherent **per-target** point-in-time data rollback, not distributed
Redis replication, consensus or an atomic multi-host transaction. Writes made
after backup are intentionally lost on rollback; clients must be quiesced if
that is unacceptable. Two targets are independent Redis instances, not replicas.
A crash/power loss during backup stop/start, external changes by root, disk
failure, unavailable prior image, firewall removal or failed recovery requires
operator inspection. There is no claim of crash-proof backup orchestration or
general restoration of arbitrary pre-existing Redis containers. Only this
runtime's strict owned containers are supported. Interruptions remain subject
to the host's manual-review policy; ordinary recoverable restoration failures
retain the journal for the existing retry gate.

## Minimal declarative graph

```json
{
  "name": "redis-lab", "environment": "dev",
  "nodes": [{"id": "cache", "plugin_id": "redis", "config": {
    "targets": ["app", "branch-client"], "port": 18095,
    "version": "1.0.0", "isolated_lab": true
  }}],
  "edges": [], "strategy": {"canary": 1, "batch_size": 1},
  "failure_mode": "none"
}
```

Catalogue defaults fill the remaining closed config fields. Submit/approve via
the existing authenticated jobs API only after reviewing prerequisite gates.
`failure_mode: "rolling"` (administrator only) triggers a failure **after** real
Redis probes, exercising reverse restoration of the failed target and canary.

## Verification and evidence boundary

```sh
.venv/bin/python -m pytest tests/test_redis_execution.py -q
```

Tests exercise actual orchestration with an injected subprocess runner,
authenticated extension selection and the approved worker, canary/rolling,
reverse absence restoration, old data restoration, stopped-state restoration,
unsafe inputs/mixed graphs, ownership/firewall/archive guards, archive-failure
restart, source drift after first mutation, and disabled activation. Credential
consumer tests use generated disposable SSH keys and the actual Redis executor:
private key permissions/lifetime, isolated SSH extra-vars overriding old inventory,
stripped environment/no agent fallback, stdout/stderr suppression, rejected raw
and JSON-escaped secret result evidence, parse/timeout cleanup, and unavailable
credential failure. Authenticated partial-restoration tests retry only the failed
target, skip the already restored target, and block every new command when the
private candidate's source drifts before retry. A local
`redis-server` test runs real loopback PING/SET/GET and durable AOF restart if the
executable is present; otherwise it explicitly skips without installing it.
Mocked command results are **not live deployment evidence**. No remote lab
mutation, image install, firewall modification or production activation is part
of this test run. Remote rollout/restore must still be proved separately on the
operator-approved isolated lab; unmet gates are failures, not stub success.
