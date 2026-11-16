# Manifest-driven execution

## Implemented and exercised

- **NGINX and Apache are live**: Ansible provisions real Docker containers on both allowlisted GNS3 guests.
- Frontend/backend stack, Cisco IOS, pfSense, and Cloud VM are **planned/disabled**. OpenTofu is deliberately unsupported here; there is no simulated `terraform_data` provisioning.
- The kernel has no per-plugin ID dispatch. Discovery is data-only; every reviewed live container blueprint uses the same lifecycle protocol. The image, document root, port, schema, defaults and playbook paths come from its manifest.
- Core owns authentication, admission, explicit approval, queueing and audit. This provider additionally serializes execution and enforces remote/local resource ownership.

## Contract and installation

```python
Executor(state_dir: Path, plugins_dir: Path | None = None)
executor.execute(job: dict, emit: Callable[[str, str, str], None]) -> dict
executor.rollback(job: dict, emit: Callable[[str, str, str], None]) -> dict
```

`job` contains `id` and `graph`. `emit` receives `(phase, level, message)`. Successful deployments return `status="succeeded"`, endpoint records, actual HTTP readback evidence, and private snapshot references. Deployment failure with verified automatic restoration returns `status="failed"` and `rollback.status="rolled_back"`. Any failed restoration returns **`rollback_failed`**, never a success claim. Preflight denial can raise `ValueError` before workload mutation.

Manual rollback accepts the original job, `{"id": original_job_id}`, or `{"rollback_of": original_job_id}`. It reads durable snapshots instead of the submitted graph. Stale rollback is rejected when a newer deployment owns the same resource. A partially failed restore can be retried directly after administrator review; already restored hosts are not overwritten again. Core recovery/manual-review policy remains authoritative.

```bash
uv pip install --python .venv/bin/python -r requirements-execution.txt
.venv/bin/python -m pytest tests/test_execution.py -q \
  --junitxml=data/evidence/execution-tests.xml
.venv/bin/python scripts/execution_live.py
```

The live script intentionally mutates only KuberWeaver-owned services and leaves a working v1 NGINX baseline. Do not run it concurrently with another operator's acceptance sequence: the provider serializes individual jobs, not a multi-job test scenario.

For safe tests only, assign `executor.runner` a callable with the `subprocess.run(argv, **kwargs)` interface. Unit-test simulation is explicitly separate from `execution-live.json`; production defaults to real `subprocess.run` and real Ansible.

## Blueprint layout and extension

```text
plugins/
  nginx/
    manifest.yaml
    iac/snapshot.yml
    iac/playbook.yml
    iac/verify.yml
    iac/rollback.yml
  apache/ ...
  _shared/container.yml
  _shared/guest.py
```

A container blueprint requires these manifest fields:

- Catalog: `id`, `name`, `version`, `description`, `category`, `icon`, `price_monthly`, `capability`, `engine`.
- Configuration: embedded JSON Schema in `schema`, plus `defaults` and `resources`.
- Security: `security.status: local_checks` and the actual local checks. This is **not** a signed/vetted/sandboxed marketplace claim.
- Provider: `executor: ansible-container-v1`, `engine: ansible`, `capability: live`.
- `service`: `image`, `document_root`, `container_port`, `health_path`.
- `iac`: relative paths for `snapshot`, `apply`, `verify`, `restore`, all confined to that plugin's directory.

Add a reviewed directory/manifest and the lifecycle playbooks to extend the catalog; do not add ID conditionals to the kernel. `scripts/execution_blueprints.py` reproducibly generates the initial catalog, but is not required at runtime. A test adds an entirely new `custom-web` manifest and proves generic lifecycle execution without modifying the executor.

Local plugin/playbook/helper files are **trusted administrator code**, not a security sandbox. No API-provided image, path, shell command, host or arbitrary executable is accepted. JSON Schema references are rejected rather than fetched from local files or the network.

## Graph and defaults

```json
{
  "name": "live-acceptance",
  "environment": "dev",
  "nodes": [{
    "id": "web-live",
    "plugin_id": "nginx",
    "label": "NGINX",
    "position": {"x": 80, "y": 80},
    "config": {
      "targets": ["app", "branch-client"],
      "port": 18090,
      "version": "1.0.0",
      "cpu": 1,
      "memory_mb": 256,
      "disk_gb": 2,
      "public_cidr": "127.0.0.1/32"
    }
  }],
  "edges": [],
  "strategy": {"canary": 1, "batch_size": 1},
  "failure_mode": "none"
}
```

Defaults are identical for NGINX and Apache. To place multiple services on the same guest, select different management ports. Targets are fixed to `app` and `branch-client`; ports are restricted to **18090–18109**. Unknown properties, arbitrary target strings, invalid versions, duplicate targets, non-finite numbers and unsafe identifiers are rejected.

Executor IDs use 1–64 lowercase alphanumeric/hyphen characters. Graph names/environments use 1–80 alphanumeric, space, underscore, period, colon or hyphen characters and start alphanumerically. The API may be more permissive syntactically; provider checks still apply. Use the exact baseline graph name/environment/node above to update that owned baseline, or use a fresh node and free port for isolated API acceptance. Do not rename a graph to bypass ownership.

Edges impose real **topological deployment ordering**: a dependency completes its canary and rollout before its dependent is deployed. Cycles and missing/self dependencies are denied. Edges **do not create network wiring**, discovery, firewall rules, backend URLs or inter-container application dependencies. Both services are currently static versioned web payloads; they are not a frontend/backend application stack.

## Real transaction semantics

1. Validate every node, manifest, graph dependency, strategy and target/port claim.
2. Acquire a per-user process-safe global executor lock. Core separately has a single queue consumer.
3. Snapshot **every node/target before any workload change**. Capture the actual managed configuration tar archive, content hashes and permissions, full actual Docker inspect state, immutable image ID, running/stopped state and old HTTP payload/port. Initial container/config absence is explicit.
4. Deploy to the first configured target as the one-host canary. Read the actual management HTTP version payload and compare exact desired bytes.
5. Only after that succeeds, apply and healthcheck remaining targets in rolling batches. `batch_size` 1–2 is accepted; execution within a batch is conservative/sequential, not parallel.
6. On any apply/healthcheck error, restore **all modified entries in reverse order**, including earlier successful nodes/canaries and an apply that only partially completed. Continue attempting other restorations after an individual restore failure.
7. Restore the real old files and recreate the real old container with Docker Engine API using the saved immutable image ID, Config and HostConfig. Verify file hashes/modes, absence if originally absent, actual image, running state, CPU/memory, bindings, mounts, restart/security settings, environment/command/entrypoint/user/labels and the exact old HTTP payload. A running process alone is insufficient.
8. Persist per-host restoration progress and ownership so failed restoration remains visible and can be reviewed/retried. The previous snapshot is not replaced with desired-state assumptions.

The failure modes `canary` and `rolling` deliberately reject the managed service's HTTP payload healthcheck **after a real HTTP readback succeeds**. They never change physical networking or damage unrelated services. `rolling` requires more than one target to reach a rolling phase.

## Ownership and operational boundaries

| Target | SSH alias | Management binding |
|---|---|---|
| `app` | `infra-app` | `192.168.122.40` |
| `branch-client` | `infra-branch-client` | `192.168.122.60` |

SSH uses `/home/ubu/Desktop/infra-platform-lab/ssh_config`, BatchMode, existing host-key verification and noninteractive sudo. No private key/password is copied into the repository. Only `branch-client` needed Docker installed (`docker.io` via apt); no other guest was restarted/reconfigured.

Guest mutations are confined to `/opt/kuberweaver`, container names `kw-<node_id>`, and container lifecycle operations for graph-owned labels. No routing, LAN interface, existing `/opt` workload, firewall, host management or legacy port 8080 changes are made. No KuberWeaver networks/volumes are created. Images may be pulled into Docker's shared image cache, but existing images are not deleted.

Ownership is a stable fingerprint of graph name, environment and node ID. Local persistent claims cover target/node and target/port, and remote container labels plus an owner marker prevent adoption/deletion of foreign resources even with another state directory. A foreign listener or unsupported foreign mount blocks snapshot/preflight. Port reservations can outlive a port-changing deployment because old snapshots may still require them; there is no automated ownership/snapshot garbage collection.

Docker binds only to each guest's management IP, **not `0.0.0.0` and not the LAN IP**. `public_cidr` is restricted to its local-only intent default, but is **not implemented as a guest firewall rule**; management-plane reachability is the network security boundary. CPU and memory are actual Docker limits. `disk_gb` is admission/cost metadata, **not an enforced filesystem quota**. Prices are local estimates, not provider billing.

## Durable evidence and limitations

Controller state is private: state directory/job/snapshot directories use mode 0700, JSON snapshots/journals/logs use 0600. Private operation requests are removed from controller and guest after use. Full snapshots and logs must not be served as public static files or committed. The controller's JSON evidence summary contains only readback payloads, metadata and references, not snapshot archives or credentials.

- Live acceptance summary: `data/evidence/execution-live.json`.
- Unit-test machine-readable report: `data/evidence/execution-tests.xml`.
- Live test executor state: `data/execution/jobs/<job_id>/`.
- Default application executor state is whatever `state_dir` the core supplies (normally `data/`); manual rollback must use the original executor state directory.
- Private `snapshots/*.json`, `journal.json`, per-operation `*.result.json`, and `logs/*.json` are retained for reviewed recovery.

The acceptance script verified, with real Ansible and independent SSH `curl` on both guests:

1. Failed canary from initial absence removes its newly created container and config; the untouched target remains absent.
2. NGINX version 1.0.0 serves on both guests.
3. Version 2.0.0 canary succeeds, the second guest fails its intentional rolling healthcheck, and **both** return to exact 1.0.0 payloads.
4. A subsequent 2.0.0 rollout succeeds on both; explicit manual rollback restores 1.0.0 on both.
5. Apache serves a real version payload on both guests, then manual rollback restores initial container/config absence.
6. The final NGINX baseline remains available at both management-IP port-18090 endpoints, and the unrelated app port-8080 workload still returns HTTP 200.

Regression coverage includes executor orchestration, retry after prior restoration, exact container-environment restoration, rejected schema references and rejected NaN numeric bypasses. The historical execution suite reported 28 passing tests; that is not a current-tree result. Injected runners test orchestration boundaries; live JSON requires actual deployments and independent readbacks. Passing tests does not establish production maturity.

This executor manages a narrow web-container lifecycle, not general Docker backup. It snapshots managed config and container definitions, not arbitrary container writable-layer/application database data. Configuration symlinks/hardlinks and unsupported mounts are rejected. Images must remain available locally for rollback; snapshots reference immutable image IDs but do not export image tarballs. Interrupted controller processes are handled by the core's manual-review recovery policy; they are not silently resumed/reapplied. Neither signed marketplace validation nor OpenTofu/cloud/router provisioning is claimed.
