"""End-to-end kw journey, covering the order documented in the README CLI section.

One chain of real `kw` invocations against the in-process uvicorn server from
test_cli.py's `env` fixture: isolated SQLite, sample-service plugin, real
encrypted local vault. Only provider boundaries are faked (recording executors,
hypervisor read/write recorders), the same substitutions as the per-command
suites; every governance gate (roles, approval, quarantine) is the API's own.

Spec steps with no backend route are skipped and asserted unavailable, never
stubbed client-side:
- modules get-by-id and cost/month (no GET /api/studio/artifacts/{id} or
  /api/studio/offerings/{id}; no studio route exposes a cost field)
- resources create --default and resources add (no backend route)

The recording executor writes no environment state (only the OpenTofu provider
commits one), so the journey covers both decommission surfaces: verified
service retirement by job id, and the environment-state destroy flow by
deployment id on an imported (adopted) stack.
"""
import json

import pytest  # noqa: E402

from test_cli import env, run, _graph_file, _destruction_world, _adopt_stack  # noqa: E402,F401
from test_core_auth import bootstrap  # noqa: E402
from test_core_jobs import install_executor  # noqa: E402
from test_service_decommission import RetiringExecutor  # noqa: E402

# Synthetic test-only secret: travels via KW_PASSWORD, never argv, and must
# never surface in CLI output, stored rows or the audit trail.
_CONNECTOR_SECRET = "journey-connector-secret-value"


def _vault_prelude(app, runner, monkeypatch):
    """Spec steps 1-3: SSH key into the vault, connector whose credentials go
    to the vault, then test + discover. Leaves an admin session; returns the
    connector row."""
    r = run(runner, "auth", "login", "--username", "admin")
    assert r.exit_code == 0, r.output
    # 1. Generate an SSH key: private material stays in the vault.
    r = run(runner, "--json", "keys", "generate", "--name", "journey-key")
    assert r.exit_code == 0, r.output
    key = json.loads(r.output)
    assert "PRIVATE" not in r.output
    listing = run(runner, "--json", "keys", "list")
    assert listing.exit_code == 0 and key["id"] in listing.output
    # 2. Add a connector: connection info only, secrets by vault reference.
    r = run(runner, "--json", "connectors", "add",
            "--label", "Journey hypervisor", "--driver", "recording",
            "--endpoint", "https://192.0.2.10", "--secret-ref", "hypervisors/journey",
            "--secrets-provider", "test-vault")
    assert r.exit_code == 0, r.output
    conn = json.loads(r.output)
    assert "password" not in r.output.lower()
    # Credentials: password via environment, stored in the vault, never echoed
    # and never persisted in the connections table.
    monkeypatch.setenv("KW_PASSWORD", _CONNECTOR_SECRET)
    r = run(runner, "--json", "connectors", "credentials", conn["id"],
            "--username", "journey-user")
    assert r.exit_code == 0, r.output
    assert _CONNECTOR_SECRET not in r.output
    monkeypatch.setenv("KW_PASSWORD", bootstrap(app.state.state_dir)["admin"])
    persisted = json.dumps(app.state.store.all("SELECT * FROM connections"))
    assert _CONNECTOR_SECRET not in persisted
    # 3. Test and discover through the same routes as the GUI.
    for action in ("test", "discover"):
        r = run(runner, "--json", "connectors", action, conn["id"])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["status"] == "reachable"
    return conn


def test_journey_key_to_retired_service(env, tmp_path, monkeypatch):
    """Spec section 4 in one chain: key -> connector -> module -> blueprint ->
    deploy request -> human approval -> inventory -> logs -> decommission ->
    finops. The operator requests, a human admin approves and retires; the
    recording executor (RetiringExecutor = RecordingExecutor + verified
    retirement) fakes only the provider boundary."""
    app, runner = env
    executor = install_executor(app, RetiringExecutor())
    creds = bootstrap(app.state.state_dir)
    _vault_prelude(app, runner, monkeypatch)

    # Spec step 6, default form: no backend route, the CLI says so honestly.
    r = run(runner, "resources", "create", "--default")
    assert r.exit_code != 0
    assert "unavailable" in (r.output + r.stderr).lower()

    # 5. Module: a pinned catalog artifact (metadata only, no job created).
    pin = "sha256:" + "a" * 64
    r = run(runner, "--json", "modules", "artifacts", "create",
            "--kind", "oci", "--reference", "registry.lab.example/kuberweaver/journey:1.0.0",
            "--pin", pin)
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["pin"] == pin
    assert json.loads(run(runner, "--json", "deploy", "list").output) == []
    # 8. Blueprint, then publish it as an immutable offering (catalog module).
    bp = json.loads(run(runner, "--json", "blueprints", "create",
                        "--name", "Journey", "--graph-file", _graph_file(tmp_path)).output)
    r = run(runner, "--json", "modules", "offerings", "create",
            "--title", "Journey stack", "--blueprint-id", bp["id"])
    assert r.exit_code == 0, r.output
    off = json.loads(r.output)
    assert off["version"] == 1 and "cost" not in r.output.lower()
    # get-by-id: no backend route, asserted unavailable rather than stubbed.
    r = run(runner, "modules", "offerings", "get", off["id"])
    assert r.exit_code != 0
    assert "unavailable" in (r.output + r.stderr).lower()

    # 9. Deployment request by the operator: pending_approval, no bypass.
    monkeypatch.setenv("KW_PASSWORD", creds["operator"])
    run(runner, "auth", "login", "--username", "operator")
    r = run(runner, "--json", "deploy", "request", "--offering-id", off["id"])
    assert r.exit_code == 0, r.output
    req = json.loads(r.output)
    assert req["status"] == "pending_approval"
    job_id = req["job_id"]
    assert executor.calls == []  # nothing runs before a human approves
    rows = json.loads(run(runner, "--json", "services", "list").output)
    assert [row["job_id"] for row in rows] == [job_id]
    assert rows[0]["status"] == "pending_approval"
    # The requester cannot approve its own request.
    assert run(runner, "deploy", "approve", job_id).exit_code == 3

    # 10. Human approval: the administrator decides, then the job succeeds.
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "--json", "deploy", "approve", job_id)
    assert r.exit_code == 0, r.output
    done = json.loads(run(runner, "--json", "deploy", "watch", job_id, "--timeout", "30").output)
    assert done["status"] == "succeeded" and done["can_decommission"] is True

    # 11. Inventory: the deployed service in the ownership-scoped register.
    # Environment states stay empty under the recording executor (only the
    # OpenTofu provider commits them); the adopted-stack journey below covers
    # the resource inventory with real rows.
    monkeypatch.setenv("KW_PASSWORD", creds["operator"])
    run(runner, "auth", "login", "--username", "operator")
    rows = json.loads(run(runner, "--json", "services", "list").output)
    assert rows[0]["status"] == "succeeded"
    job = json.loads(run(runner, "--json", "deploy", "get", job_id).output)
    assert rows[0]["monthly_estimate"] == pytest.approx(job["cost"]["monthly"])
    assert json.loads(run(runner, "--json", "inventory", "list").output) == {"states": []}

    # 12. Logs: installation events retained server-side for the approver.
    events = json.loads(run(runner, "--json", "logs", job_id).output)
    assert events and any(e["phase"] == "verify" for e in events)

    # 14. Decommission by deployment id: human admin queues verified
    # retirement; the worker runs the provider's decommission, never rollback.
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "--json", "services", "decommission", job_id)
    assert r.exit_code == 0, r.output
    queued = json.loads(r.output)
    assert queued["status"] == "decommission_queued" and queued["operation"] == "decommission"
    done = json.loads(run(runner, "--json", "deploy", "watch", job_id, "--timeout", "30").output)
    assert done["status"] == "decommissioned"
    assert executor.calls == [("execute", job_id), ("decommission", job_id)]
    # The retired service stays visible to its owner, status never rewritten.
    monkeypatch.setenv("KW_PASSWORD", creds["operator"])
    run(runner, "auth", "login", "--username", "operator")
    rows = json.loads(run(runner, "--json", "services", "list").output)
    assert rows[0]["status"] == "decommissioned"

    # 15. Data retained for FinOps and audit; the connector secret never
    # surfaced in the audit trail.
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    run(runner, "auth", "login", "--username", "admin")
    fin = json.loads(run(runner, "--json", "finops").output)
    assert "monthly_total" in fin
    audit = json.loads(run(runner, "--json", "audit").output)
    assert isinstance(audit, list) and audit
    trail = json.dumps(app.state.store.all("SELECT * FROM audit"))
    assert _CONNECTOR_SECRET not in trail


def test_journey_adopted_stack_inventory_to_destroy(env, monkeypatch):
    """Resource leg of spec section 4: import (adopt) a proven VM, see it in
    the inventory, then decommission it by deployment id through the
    quarantine window, without touching the other stack. Same vault prelude;
    only the hypervisor read/write boundary is faked."""
    app, runner = env
    from test_destruction import UUID, MARKER, OTHER_UUID, OTHER_MARKER, CONNECTION
    clock, destroyer = _destruction_world(app)
    _vault_prelude(app, runner, monkeypatch)
    # 6-7 (import path): two proven VMs, each its own deployment id.
    one = _adopt_stack(runner, "vm-journey", UUID, MARKER)
    _adopt_stack(runner, "vm-journey-other", OTHER_UUID, OTHER_MARKER)
    # 11. Inventory: both stacks with their owned resources.
    states = json.loads(run(runner, "--json", "inventory", "list").output)["states"]
    assert {s["stack"] for s in states} == {"vm-journey", "vm-journey-other"}
    detail = json.loads(run(runner, "--json", "inventory", "get", one["id"]).output)
    assert [r["address"] for r in detail["resources"]] == ["esxi_vm." + UUID]
    # 14. Decommission request: ownership proven, quarantine opened, host
    # untouched; executing before the window closes is refused.
    r = run(runner, "--json", "decommission", "request", one["id"],
            "--connection-id", CONNECTION, "--uuid", UUID, "--marker", MARKER)
    assert r.exit_code == 0, r.output
    destroy = json.loads(r.output)["destroy"]
    assert destroy["status"] == "quarantined" and destroyer.calls == []
    r = run(runner, "decommission", "execute", one["id"], "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 1 and "window" in (r.output + r.stderr).lower()
    # After the window: the API re-proves ownership and destroys exactly this VM.
    clock.advance(minutes=16)
    r = run(runner, "--json", "decommission", "execute", one["id"],
            "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 0, r.output
    done = json.loads(r.output)
    assert done["destroyed"] is True and done["destroy"]["status"] == "destroyed"
    assert destroyer.calls == [(CONNECTION, UUID, MARKER)]
    # Inventory shows the emptied stack; the other deployment is untouched.
    states = {s["stack"]: s for s in
              json.loads(run(runner, "--json", "inventory", "list").output)["states"]}
    assert states["vm-journey"]["resource_count"] == 0
    assert states["vm-journey"]["source"] == "destroy"
    assert states["vm-journey-other"]["resource_count"] == 1
    assert states["vm-journey-other"]["generation"] == 1
    status = json.loads(run(runner, "--json", "decommission", "status", one["id"]).output)
    assert status["destroy"]["status"] == "destroyed"
