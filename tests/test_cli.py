"""kw CLI integration tests: in-process against create_app(testing=True) via ASGITransport.

No hypervisor lab. Reuses the connection test boundaries (recording driver +
connection-secrets source). Verifies the CLI is a thin client: same routes, roles,
CSRF and session persistence as the GUI, no direct database writes.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import httpx
import pytest
from click.testing import CliRunner

from test_connections import make_app, boundaries, spec  # noqa: E402
from test_core_auth import bootstrap  # noqa: E402

from kw import client as kwclient  # noqa: E402
from kw.main import cli  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    import threading
    import time
    import uvicorn

    # Isolate BEFORE importing kuberweaver.app (it builds a module-level app on
    # first import): point it at a scratch state dir and force SQLite, so we never
    # touch data/ or share the PostgreSQL test database / advisory lock.
    monkeypatch.setenv("KUBERWEAVER_STATE_DIR", str(tmp_path / "module-app"))
    monkeypatch.delenv("KUBERWEAVER_DATABASE_URL", raising=False)
    monkeypatch.delenv("KUBERWEAVER_DATABASE_URL_FILE", raising=False)

    from test_core_graph import manifest as sample_manifest
    from kuberweaver.app import create_app
    from kuberweaver.connections import install_connections_api
    plugins = tmp_path / "plugins"
    sample_manifest(plugins)
    app = create_app(state_dir=tmp_path / "state", plugins_dir=plugins, testing=True)
    if not hasattr(app.state, "connections"):
        install_connections_api(app)
    boundaries(app)
    # Real encrypted local vault, writable, bound as the default secrets source.
    from kuberweaver.local_vault import initialize, LocalVaultProvider
    vault_dir = tmp_path / "vault"
    initialize(vault_dir)
    app.state.registry.register("secrets", "test-vault",
                                LocalVaultProvider(vault_dir, allow_write=True), {})
    app.state.provider_bindings["secrets"] = "test-vault"
    # The product binds no execution provider on a fresh install; this suite
    # submits and approves jobs, so it installs its review fixture explicitly.
    from test_core_jobs import RecordingExecutor, install_executor
    install_executor(app, RecordingExecutor())
    # A real local HTTP server (ephemeral port) exercises cookies + CSRF exactly
    # as production; the sync CLI cannot use the async-only ASGITransport.
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "test server did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    monkeypatch.setenv("KW_CONFIG_DIR", str(tmp_path / "kwcfg"))
    monkeypatch.setenv("KW_URL", f"http://127.0.0.1:{port}")
    creds = bootstrap(app.state.state_dir)
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    runner = CliRunner()
    yield app, runner
    server.should_exit = True
    thread.join(timeout=5)


def run(runner, *args):
    result = runner.invoke(cli, list(args))
    return result


def test_login_whoami_logout(env):
    app, runner = env
    r = run(runner, "auth", "login", "--username", "admin")
    assert r.exit_code == 0, r.output
    # session persisted 0600
    import os
    from kw import config
    sess = config.session_path()
    assert sess.exists() and os.stat(sess).st_mode & 0o777 == 0o600
    r = run(runner, "--json", "auth", "whoami")
    assert r.exit_code == 0, r.output
    import json
    assert json.loads(r.output)["username"] == "admin"
    r = run(runner, "auth", "logout")
    assert r.exit_code == 0, r.output
    assert not config.session_path().exists()


def test_whoami_requires_auth(env):
    app, runner = env
    r = run(runner, "auth", "whoami")
    assert r.exit_code == 2, r.output


def test_connectors_add_list_get(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "--json", "connectors", "add",
            "--label", "Lab hypervisor", "--driver", "recording",
            "--endpoint", "https://192.0.2.10", "--secret-ref", "hypervisors/lab",
            "--secrets-provider", "connection-secrets")
    assert r.exit_code == 0, r.output
    import json
    conn = json.loads(r.output)
    assert conn["label"] == "Lab hypervisor"
    # references only: no secret value in the CLI output
    assert "password" not in r.output.lower()
    r = run(runner, "--json", "connectors", "list")
    assert r.exit_code == 0 and len(json.loads(r.output)) == 1
    r = run(runner, "--json", "connectors", "get", conn["id"])
    assert r.exit_code == 0 and json.loads(r.output)["id"] == conn["id"]


def test_connectors_test_and_discover(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    conn = json.loads(run(runner, "--json", "connectors", "add",
                          "--label", "H", "--driver", "recording",
                          "--endpoint", "https://192.0.2.10", "--secret-ref", "hypervisors/lab",
                          "--secrets-provider", "connection-secrets").output)
    for action in ("test", "discover"):
        r = run(runner, "--json", "connectors", action, conn["id"])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["status"] == "reachable"


def test_ssh_key_generate_list_revoke(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    r = run(runner, "--json", "keys", "generate", "--name", "deploy-key")
    assert r.exit_code == 0, r.output
    key = json.loads(r.output)
    # private material never printed
    assert "PRIVATE" not in r.output
    r = run(runner, "--json", "keys", "list")
    assert r.exit_code == 0 and len(json.loads(r.output)) == 1
    r = run(runner, "--json", "keys", "revoke", key["id"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["enabled"] is False


def test_viewer_cannot_add_connector(env, monkeypatch):
    app, runner = env
    creds = bootstrap(app.state.state_dir)
    monkeypatch.setenv("KW_PASSWORD", creds["viewer"])
    run(runner, "auth", "login", "--username", "viewer")
    r = run(runner, "connectors", "add", "--label", "x", "--driver", "recording",
            "--endpoint", "https://192.0.2.1", "--secret-ref", "a/b",
            "--secrets-provider", "connection-secrets")
    assert r.exit_code == 3, r.output  # permission denied


def test_connectors_credentials_stored_in_vault(env, monkeypatch):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    conn = json.loads(run(runner, "--json", "connectors", "add",
                          "--label", "H", "--driver", "recording",
                          "--endpoint", "https://192.0.2.10", "--secret-ref", "hypervisors/lab",
                          "--secrets-provider", "test-vault").output)
    monkeypatch.setenv("KW_PASSWORD", "connector-secret-value")
    r = run(runner, "--json", "connectors", "credentials", conn["id"], "--username", "test-user")
    assert r.exit_code == 0, r.output
    assert "connector-secret-value" not in r.output
    persisted = json.dumps(app.state.store.all("SELECT * FROM connections"))
    assert "connector-secret-value" not in persisted


def test_ssh_key_export_reauth_and_file(env, monkeypatch, tmp_path):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    import os
    key = json.loads(run(runner, "--json", "keys", "generate", "--name", "exp").output)
    # export requires admin reauthentication with the admin password (fixture KW_PASSWORD)
    dest = tmp_path / "exported.key"
    r = run(runner, "--json", "keys", "export", key["id"], "--output", str(dest))
    assert r.exit_code == 0, r.output
    assert dest.exists() and os.stat(dest).st_mode & 0o777 == 0o600
    assert b"PRIVATE KEY" in dest.read_bytes()      # real key material in the file
    assert "PRIVATE KEY" not in r.output            # never printed to the terminal


def _graph_file(tmp_path):
    import json
    from test_core_graph import graph
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph()))
    return str(path)


def test_blueprints_create_get_copy_list(env, tmp_path):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    gf = _graph_file(tmp_path)
    bp = json.loads(run(runner, "--json", "blueprints", "create", "--name", "Web", "--graph-file", gf).output)
    assert bp["name"] == "Web"
    got = json.loads(run(runner, "--json", "blueprints", "get", bp["id"]).output)
    assert got["id"] == bp["id"]
    copy = json.loads(run(runner, "--json", "blueprints", "copy", bp["id"], "--name", "Web copy").output)
    assert copy["id"] != bp["id"] and copy["graph"] == bp["graph"]
    listing = json.loads(run(runner, "--json", "blueprints", "list").output)
    assert len(listing) == 2


def test_deploy_request_approve_watch(env, tmp_path):
    app, runner = env
    from test_core_jobs import RecordingExecutor, install_executor
    install_executor(app, RecordingExecutor())
    run(runner, "auth", "login", "--username", "admin")
    import json
    job = json.loads(run(runner, "--json", "deploy", "request", "--graph-file", _graph_file(tmp_path)).output)
    assert job["status"] == "pending_approval"
    r = run(runner, "--json", "deploy", "approve", job["id"])
    assert r.exit_code == 0, r.output
    r = run(runner, "--json", "deploy", "watch", job["id"], "--timeout", "30")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["status"] == "succeeded"


def test_deploy_validate_dry_run_creates_no_job(env, tmp_path):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    before = len(json.loads(run(runner, "--json", "deploy", "list").output))
    r = run(runner, "--json", "deploy", "validate", "--graph-file", _graph_file(tmp_path))
    assert r.exit_code == 0, r.output
    assert "allowed" in json.loads(r.output)
    after = len(json.loads(run(runner, "--json", "deploy", "list").output))
    assert before == after


def test_inventory_finops_overview_audit_logs(env, tmp_path):
    app, runner = env
    from test_core_jobs import RecordingExecutor, install_executor
    install_executor(app, RecordingExecutor())
    run(runner, "auth", "login", "--username", "admin")
    import json
    job = json.loads(run(runner, "--json", "deploy", "request", "--graph-file", _graph_file(tmp_path)).output)
    run(runner, "deploy", "approve", job["id"])
    run(runner, "deploy", "watch", job["id"], "--timeout", "30")
    assert run(runner, "--json", "inventory", "list").exit_code == 0
    fin = json.loads(run(runner, "--json", "finops").output)
    assert "monthly_total" in fin
    ov = json.loads(run(runner, "--json", "finops", "overview").output)
    assert "counts" in ov
    audit = json.loads(run(runner, "--json", "audit").output)
    assert isinstance(audit, list) and audit
    logs = json.loads(run(runner, "--json", "logs", job["id"]).output)
    assert isinstance(logs, list)


def test_modules_artifacts_create_list(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    pin = "sha256:" + "a" * 64
    r = run(runner, "--json", "modules", "artifacts", "create",
            "--kind", "oci", "--reference", "registry.lab.example/kuberweaver/web:1.4.0",
            "--pin", pin)
    assert r.exit_code == 0, r.output
    art = json.loads(r.output)
    assert set(art) == {"id", "kind", "reference", "pin"}
    assert art["kind"] == "oci" and art["pin"] == pin
    # Unpinned artifact: explicit null; registration is metadata only, no job.
    r = run(runner, "--json", "modules", "artifacts", "create",
            "--kind", "vm-image", "--reference", "vmdk.lab.example/ubuntu-2404-base")
    assert r.exit_code == 0, r.output
    unpinned = json.loads(r.output)
    assert unpinned["pin"] is None
    listing = json.loads(run(runner, "--json", "modules", "artifacts", "list").output)
    assert [row["id"] for row in listing] == [unpinned["id"], art["id"]]  # newest first
    assert all(set(row) == {"id", "kind", "reference", "pin"} for row in listing)
    assert json.loads(run(runner, "--json", "deploy", "list").output) == []
    # Default table output renders the register too.
    r = run(runner, "modules", "artifacts", "list")
    assert r.exit_code == 0 and "REFERENCE" in r.output


def test_modules_artifacts_get_unavailable(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    art = json.loads(run(runner, "--json", "modules", "artifacts", "create",
                         "--kind", "oci", "--reference", "registry.lab.example/x:1").output)
    # No GET /api/studio/artifacts/{id} backend route: the CLI must say so,
    # not stub a client-side lookup.
    r = run(runner, "modules", "artifacts", "get", art["id"])
    assert r.exit_code != 0
    text = r.output + r.stderr
    assert "unavailable" in text.lower() and "/api/studio/artifacts" in text
    h = run(runner, "modules", "artifacts", "get", "--help")
    assert h.exit_code == 0 and "unavailable" in h.output.lower()


def test_modules_offerings_create_list(env, tmp_path):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    bp = json.loads(run(runner, "--json", "blueprints", "create",
                        "--name", "Web", "--graph-file", _graph_file(tmp_path)).output)
    r = run(runner, "--json", "modules", "offerings", "create",
            "--title", "Web stack", "--blueprint-id", bp["id"])
    assert r.exit_code == 0, r.output
    off = json.loads(r.output)
    assert set(off) == {"id", "title", "version"}
    assert off["title"] == "Web stack" and off["version"] == 1
    # The studio API exposes no cost field: the CLI must not invent one.
    assert "cost" not in r.output.lower()
    # Same title republished: a new immutable version, never a rewrite.
    r = run(runner, "--json", "modules", "offerings", "create",
            "--title", "Web stack", "--blueprint-id", bp["id"])
    assert r.exit_code == 0, r.output
    off2 = json.loads(r.output)
    assert off2["id"] != off["id"] and off2["version"] == 2
    listing = json.loads(run(runner, "--json", "modules", "offerings", "list").output)
    assert [row["id"] for row in listing] == [off2["id"], off["id"]]
    assert all(set(row) == {"id", "title", "version"} for row in listing)


def test_modules_offerings_get_unavailable(env, tmp_path):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    bp = json.loads(run(runner, "--json", "blueprints", "create",
                        "--name", "Web", "--graph-file", _graph_file(tmp_path)).output)
    off = json.loads(run(runner, "--json", "modules", "offerings", "create",
                         "--title", "Web stack", "--blueprint-id", bp["id"]).output)
    # No GET /api/studio/offerings/{id} backend route: explicit unavailability.
    r = run(runner, "modules", "offerings", "get", off["id"])
    assert r.exit_code != 0
    text = r.output + r.stderr
    assert "unavailable" in text.lower() and "/api/studio/offerings" in text
    h = run(runner, "modules", "offerings", "get", "--help")
    assert h.exit_code == 0 and "unavailable" in h.output.lower()


def test_modules_offerings_create_unknown_blueprint(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "--json", "modules", "offerings", "create",
            "--title", "Ghost", "--blueprint-id", "does-not-exist")
    assert r.exit_code == 1, r.output
    assert "Unknown blueprint" in r.output + r.stderr


def test_modules_role_boundaries(env, monkeypatch, tmp_path):
    app, runner = env
    creds = bootstrap(app.state.state_dir)
    import json
    # viewer: no artifact registration, but offerings list is open to any user.
    monkeypatch.setenv("KW_PASSWORD", creds["viewer"])
    run(runner, "auth", "login", "--username", "viewer")
    r = run(runner, "modules", "artifacts", "create",
            "--kind", "oci", "--reference", "registry.lab.example/x:1")
    assert r.exit_code == 3, r.output  # permission denied
    r = run(runner, "--json", "modules", "offerings", "list")
    assert r.exit_code == 0 and json.loads(r.output) == []
    # operator: artifact registration allowed, offering publication admin-only.
    monkeypatch.setenv("KW_PASSWORD", creds["operator"])
    run(runner, "auth", "login", "--username", "operator")
    r = run(runner, "--json", "modules", "artifacts", "create",
            "--kind", "ansible-role", "--reference", "galaxy.lab.example/nginx/1.0")
    assert r.exit_code == 0, r.output
    bp = json.loads(run(runner, "--json", "blueprints", "create",
                        "--name", "Op", "--graph-file", _graph_file(tmp_path)).output)
    r = run(runner, "modules", "offerings", "create",
            "--title", "Op stack", "--blueprint-id", bp["id"])
    assert r.exit_code == 3, r.output  # publish requires admin


def test_modules_help_marks_backend_gaps(env):
    app, runner = env
    r = run(runner, "modules", "--help")
    assert r.exit_code == 0
    text = r.output.lower()
    assert "unavailable" in text and "cost" in text and "get-by-id" in text
    for group in ("artifacts", "offerings"):
        h = run(runner, "modules", group, "create", "--help")
        assert h.exit_code == 0
        assert "cost/month: unavailable" in h.output.lower()


def _published_offering(runner, tmp_path, title="Web stack"):
    """Blueprint + immutable offering via the CLI; returns the offering row."""
    import json
    bp = json.loads(run(runner, "--json", "blueprints", "create",
                        "--name", title, "--graph-file", _graph_file(tmp_path)).output)
    return json.loads(run(runner, "--json", "modules", "offerings", "create",
                          "--title", title, "--blueprint-id", bp["id"]).output)


def test_services_request_list_decommission(env, tmp_path):
    app, runner = env
    from test_core_jobs import install_executor
    from test_service_decommission import RetiringExecutor
    executor = install_executor(app, RetiringExecutor())
    run(runner, "auth", "login", "--username", "admin")
    import json
    off = _published_offering(runner, tmp_path)
    r = run(runner, "--json", "services", "request", off["id"])
    assert r.exit_code == 0, r.output
    req = json.loads(r.output)
    assert set(req) == {"job_id", "status"}
    assert req["status"] == "pending_approval"  # no approval bypass
    job_id = req["job_id"]
    # Ownership-scoped list with the API's exact row shape.
    rows = json.loads(run(runner, "--json", "services", "list").output)
    assert [row["job_id"] for row in rows] == [job_id]
    assert set(rows[0]) == {"job_id", "title", "status", "monthly_estimate"}
    assert rows[0]["title"] == "Web stack" and rows[0]["status"] == "pending_approval"
    job = json.loads(run(runner, "--json", "deploy", "get", job_id).output)
    assert rows[0]["monthly_estimate"] == pytest.approx(job["cost"]["monthly"])
    # Default table output renders the register too.
    r = run(runner, "services", "list")
    assert r.exit_code == 0 and "JOB_ID" in r.output
    # Approval stays an explicit administrator decision, then the job succeeds.
    assert run(runner, "deploy", "approve", job_id).exit_code == 0
    done = json.loads(run(runner, "--json", "deploy", "watch", job_id, "--timeout", "30").output)
    assert done["status"] == "succeeded"
    assert json.loads(run(runner, "--json", "services", "list").output)[0]["status"] == "succeeded"
    # Governed retirement: queued by the API, verified by the pinned provider.
    r = run(runner, "--json", "services", "decommission", job_id)
    assert r.exit_code == 0, r.output
    queued = json.loads(r.output)
    assert queued["status"] == "decommission_queued" and queued["operation"] == "decommission"
    done = json.loads(run(runner, "--json", "deploy", "watch", job_id, "--timeout", "30").output)
    assert done["status"] == "decommissioned"
    assert json.loads(run(runner, "--json", "services", "list").output)[0]["status"] == "decommissioned"
    # Retirement is never rewritten into a rollback.
    assert executor.calls == [("execute", job_id), ("decommission", job_id)]


def test_services_list_is_ownership_scoped(env, tmp_path, monkeypatch):
    app, runner = env
    creds = bootstrap(app.state.state_dir)
    run(runner, "auth", "login", "--username", "admin")
    import json
    off = _published_offering(runner, tmp_path)
    # The operator requests the offering; the row belongs to the operator only.
    monkeypatch.setenv("KW_PASSWORD", creds["operator"])
    run(runner, "auth", "login", "--username", "operator")
    req = json.loads(run(runner, "--json", "services", "request", off["id"]).output)
    rows = json.loads(run(runner, "--json", "services", "list").output)
    assert [row["job_id"] for row in rows] == [req["job_id"]]
    # The administrator does not inherit another principal's service requests.
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    run(runner, "auth", "login", "--username", "admin")
    assert json.loads(run(runner, "--json", "services", "list").output) == []
    # Listing is open to any user, but a viewer sees only its own (none).
    monkeypatch.setenv("KW_PASSWORD", creds["viewer"])
    run(runner, "auth", "login", "--username", "viewer")
    assert json.loads(run(runner, "--json", "services", "list").output) == []


def test_services_role_boundaries_and_governance(env, tmp_path, monkeypatch):
    app, runner = env
    from test_core_jobs import RecordingExecutor, install_executor
    install_executor(app, RecordingExecutor())
    creds = bootstrap(app.state.state_dir)
    import json
    run(runner, "auth", "login", "--username", "admin")
    off = _published_offering(runner, tmp_path)
    # Unauthenticated access is refused.
    run(runner, "auth", "logout")
    assert run(runner, "services", "list").exit_code == 2
    # A viewer cannot request an offering.
    monkeypatch.setenv("KW_PASSWORD", creds["viewer"])
    run(runner, "auth", "login", "--username", "viewer")
    assert run(runner, "services", "request", off["id"]).exit_code == 3
    # An operator can request but never retire.
    monkeypatch.setenv("KW_PASSWORD", creds["operator"])
    run(runner, "auth", "login", "--username", "operator")
    job_id = json.loads(run(runner, "--json", "services", "request", off["id"]).output)["job_id"]
    assert run(runner, "services", "decommission", job_id).exit_code == 3
    # Admin: a never-approved request has nothing to retire; state untouched.
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "services", "decommission", job_id)
    assert r.exit_code == 1, r.output
    assert "succeeded" in (r.output + r.stderr).lower()
    assert json.loads(run(runner, "--json", "deploy", "get", job_id).output)["status"] == "pending_approval"
    # A plain deployment job is not a service request: retirement is 404.
    plain = json.loads(run(runner, "--json", "deploy", "request",
                           "--graph-file", _graph_file(tmp_path)).output)
    r = run(runner, "services", "decommission", plain["id"])
    assert r.exit_code == 1 and "Unknown service request" in r.output + r.stderr
    r = run(runner, "services", "decommission", "0" * 32)
    assert r.exit_code == 1 and "Unknown job" in r.output + r.stderr


def test_services_decommission_requires_provider_capability(env, tmp_path):
    app, runner = env
    from test_core_jobs import RecordingExecutor, install_executor
    executor = install_executor(app, RecordingExecutor())  # rollback only
    run(runner, "auth", "login", "--username", "admin")
    import json
    off = _published_offering(runner, tmp_path)
    job_id = json.loads(run(runner, "--json", "services", "request", off["id"]).output)["job_id"]
    run(runner, "deploy", "approve", job_id)
    done = json.loads(run(runner, "--json", "deploy", "watch", job_id, "--timeout", "30").output)
    assert done["status"] == "succeeded"
    job = json.loads(run(runner, "--json", "deploy", "get", job_id).output)
    assert job["can_rollback"] is True and job["can_decommission"] is False
    # Rollback capability never implies retirement: refused, nothing runs.
    r = run(runner, "services", "decommission", job_id)
    assert r.exit_code == 1, r.output
    assert "decommission" in (r.output + r.stderr).lower()
    assert json.loads(run(runner, "--json", "deploy", "get", job_id).output)["status"] == "succeeded"
    assert executor.calls == [("execute", job_id)]


def _declaration_world(app, tmp_path):
    """Pin one bundle in a scratch Git repository and wire it into the running
    app; only the OpenTofu boundary is faked (Recorder), the service path is real.
    Same boundary substitution as tests/test_declarations.py."""
    from test_declarations import make_repository, write_bundle, commit, Recorder
    from kuberweaver.declarations import GitRepository
    repo = make_repository(tmp_path / "git")
    bundle = write_bundle(repo)
    revision = commit(repo)
    applier = Recorder()
    app.state.declarations.repository = GitRepository(repo)
    app.state.declarations.applier = applier
    return revision, bundle, applier


def _import_pins(revision, bundle):
    return ["--environment", "dev", "--stack", "vm-app", "--revision", revision,
            "--bundle-path", bundle["path"], "--bundle-sha256", bundle["sha256"]]


def test_resources_create_default_unavailable(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    # No default-form creation route exists: the CLI must say so, not stub it.
    r = run(runner, "resources", "create", "--default")
    assert r.exit_code != 0
    text = (r.output + r.stderr).lower()
    assert "unavailable" in text and "default" in text and "deploy" in text
    h = run(runner, "resources", "create", "--help")
    assert h.exit_code == 0 and "unavailable" in h.output.lower()
    # SSH key injection belongs to OpenTofu, never to the CLI surface.
    assert "opentofu" in h.output.lower() and "--ssh" not in h.output
    # Flag discipline and schema-mirroring input validation.
    assert run(runner, "resources", "create").exit_code == 2
    assert run(runner, "resources", "create", "--default", "--import").exit_code == 2
    r = run(runner, "resources", "create", "--import")
    assert r.exit_code == 2 and "--import requires" in r.output + r.stderr
    assert run(runner, "resources", "create", "--import",
               "--environment", "prod").exit_code == 2


def test_resources_import_verify_then_execute(env, tmp_path):
    app, runner = env
    import hashlib
    import json
    from test_declarations import PLAN
    revision, bundle, applier = _declaration_world(app, tmp_path)
    run(runner, "auth", "login", "--username", "admin")
    pins = _import_pins(revision, bundle)
    # Dry-run verifies the exact pins and writes nothing.
    r = run(runner, "--json", "resources", "create", "--import", "--dry-run", *pins)
    assert r.exit_code == 0, r.output
    verified = json.loads(r.output)
    assert verified["revision"] == revision and verified["environment"] == "dev"
    assert verified["plan"]["sha256"] == hashlib.sha256(PLAN).hexdigest()
    assert verified["base_generation"] == 0
    assert "plan_bytes" not in verified          # artifact bytes never leave the API
    assert str(tmp_path) not in r.output         # no private repository path either
    assert applier.calls == []
    status = json.loads(run(runner, "--json", "resources", "status").output)
    assert status["repository"] == {"status": "configured"} and status["runs"] == []
    assert json.loads(run(runner, "--json", "inventory", "list").output) == {"states": []}
    # The import applies the exact pinned plan; the human admin session is the gate.
    r = run(runner, "--json", "resources", "create", "--import", *pins)
    assert r.exit_code == 0, r.output
    executed = json.loads(r.output)
    assert executed["executed"] is True and executed["generation"] == 1
    assert executed["plan_sha256"] == hashlib.sha256(PLAN).hexdigest()
    assert len(applier.calls) == 1 and applier.calls[0]["plan_bytes"] == PLAN
    states = json.loads(run(runner, "--json", "inventory", "list").output)["states"]
    assert [s["stack"] for s in states] == ["vm-app"]
    assert states[0]["generation"] == 1 and states[0]["resource_count"] == 2
    assert [row["address"] for row in states[0]["resources"]] == [
        "libvirt_domain.vm", "libvirt_volume.disk"]
    status = json.loads(run(runner, "--json", "resources", "status").output)
    assert status["runs"][0]["status"] == "executed"
    # The same base generation cannot be applied twice.
    r = run(runner, "resources", "create", "--import", *pins)
    assert r.exit_code == 1, r.output
    assert "generation changed" in r.output + r.stderr
    assert len(applier.calls) == 1


def test_resources_import_pin_mismatch_refused(env, tmp_path):
    app, runner = env
    import json
    revision, bundle, applier = _declaration_world(app, tmp_path)
    run(runner, "auth", "login", "--username", "admin")
    pins = _import_pins(revision, bundle)
    pins[pins.index("--bundle-sha256") + 1] = "f" * 64
    r = run(runner, "resources", "create", "--import", *pins)
    assert r.exit_code == 1, r.output
    assert "bundle digest" in r.output + r.stderr
    assert applier.calls == []
    assert json.loads(run(runner, "--json", "inventory", "list").output) == {"states": []}
    assert json.loads(run(runner, "--json", "resources", "status").output)["runs"] == []


def test_resources_role_boundaries(env, tmp_path, monkeypatch):
    app, runner = env
    import json
    revision, bundle, applier = _declaration_world(app, tmp_path)
    creds = bootstrap(app.state.state_dir)
    pins = _import_pins(revision, bundle)
    adopt_args = ["--environment", "dev", "--stack", "vm-adopted",
                  "--connection-id", "c" * 32,
                  "--uuid", "11111111-1111-4111-8111-111111111111",
                  "--marker", "kuberweaver-job:" + "a" * 32]
    # Unauthenticated access is refused.
    assert run(runner, "resources", "status").exit_code == 2
    assert run(runner, "resources", "create", "--import", *pins).exit_code == 2
    for role in ("viewer", "operator"):
        monkeypatch.setenv("KW_PASSWORD", creds[role])
        run(runner, "auth", "login", "--username", role)
        # Reads are open to any signed-in user.
        assert run(runner, "--json", "resources", "status").exit_code == 0
        assert run(runner, "--json", "resources", "adoptions").exit_code == 0
        # Verify, execute and adopt are human-administrator mutations.
        assert run(runner, "resources", "create", "--import", "--dry-run", *pins).exit_code == 3
        assert run(runner, "resources", "create", "--import", *pins).exit_code == 3
        assert run(runner, "resources", "adopt", *adopt_args).exit_code == 3
    assert applier.calls == []
    assert json.loads(run(runner, "--json", "inventory", "list").output) == {"states": []}


def test_resources_adopt_idempotent_and_list(env):
    app, runner = env
    import json
    from test_adoption import live, UUID, MARKER, OTHER_MARKER, CONNECTION
    # Only the hypervisor read boundary is faked; the adoption service is real.
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live()
    run(runner, "auth", "login", "--username", "admin")
    args = ["--environment", "dev", "--stack", "vm-adopted",
            "--connection-id", CONNECTION, "--uuid", UUID, "--marker", MARKER]
    r = run(runner, "--json", "resources", "adopt", *args)
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["adopted"] is True and payload["unchanged"] is False
    state = payload["state"]
    assert state["generation"] == 1 and state["source"] == "adoption"
    assert state["resources"] == [{"address": "esxi_vm." + UUID, "type": "esxi_vm",
                                   "name": UUID, "mode": "managed"}]
    # Re-importing the same proven VM is a no-op, never a second generation.
    again = json.loads(run(runner, "--json", "resources", "adopt", *args).output)
    assert again["adopted"] is False and again["unchanged"] is True
    assert again["state"]["generation"] == 1
    rows = json.loads(run(runner, "--json", "resources", "adoptions").output)["adoptions"]
    assert len(rows) == 1 and rows[0]["uuid"] == UUID and rows[0]["stack"] == "vm-adopted"
    r = run(runner, "resources", "adoptions")
    assert r.exit_code == 0 and UUID in r.output
    # The adopted stack is visible in the inventory with its ownership source.
    states = json.loads(run(runner, "--json", "inventory", "list").output)["states"]
    assert [s["stack"] for s in states] == ["vm-adopted"]
    assert states[0]["source"] == "adoption" and states[0]["resource_count"] == 1
    # A marker that does not match the live annotation is refused; nothing recorded.
    foreign = ["--environment", "dev", "--stack", "other-stack",
               "--connection-id", CONNECTION, "--uuid", UUID, "--marker", OTHER_MARKER]
    r = run(runner, "resources", "adopt", *foreign)
    assert r.exit_code == 1, r.output
    assert "marker" in (r.output + r.stderr).lower()
    states = json.loads(run(runner, "--json", "inventory", "list").output)["states"]
    assert [s["stack"] for s in states] == ["vm-adopted"]


def test_rollback_succeeded_job(env, tmp_path):
    app, runner = env
    from test_core_jobs import RecordingExecutor, install_executor
    executor = install_executor(app, RecordingExecutor())
    run(runner, "auth", "login", "--username", "admin")
    import json
    job = json.loads(run(runner, "--json", "deploy", "request", "--graph-file", _graph_file(tmp_path)).output)
    run(runner, "deploy", "approve", job["id"])
    done = json.loads(run(runner, "--json", "deploy", "watch", job["id"], "--timeout", "30").output)
    assert done["status"] == "succeeded" and done["can_rollback"] is True
    # Governed mutation: the API queues the restore under the pinned provider.
    r = run(runner, "--json", "rollback", job["id"])
    assert r.exit_code == 0, r.output
    queued = json.loads(r.output)
    assert queued["operation"] == "rollback" and queued["rollback_requested_by"] == "admin"
    assert queued["status"] in {"queued", "rolling_back", "rolled_back"}
    done = json.loads(run(runner, "--json", "deploy", "watch", job["id"], "--timeout", "30").output)
    assert done["status"] == "rolled_back", done
    assert done["result"]["targets"][0]["restored"] is True
    assert executor.calls == [("execute", job["id"]), ("rollback", job["id"])]
    # A rolled-back job is not rollback-able again: the API refuses, nothing runs.
    r = run(runner, "rollback", job["id"])
    assert r.exit_code == 1, r.output
    assert "Rollback requires a successful job" in r.output + r.stderr
    assert json.loads(run(runner, "--json", "deploy", "get", job["id"]).output)["can_rollback"] is False
    assert executor.calls == [("execute", job["id"]), ("rollback", job["id"])]


def test_rollback_governance_boundaries(env, tmp_path, monkeypatch):
    app, runner = env
    from test_core_jobs import RecordingExecutor, install_executor
    executor = install_executor(app, RecordingExecutor())
    creds = bootstrap(app.state.state_dir)
    run(runner, "auth", "login", "--username", "admin")
    import json
    job = json.loads(run(runner, "--json", "deploy", "request", "--graph-file", _graph_file(tmp_path)).output)
    job_id = job["id"]
    # Unauthenticated access is refused.
    run(runner, "auth", "logout")
    assert run(runner, "rollback", job_id).exit_code == 2
    # Rollback is a human administrator mutation: viewer and operator are denied.
    for role in ("viewer", "operator"):
        monkeypatch.setenv("KW_PASSWORD", creds[role])
        run(runner, "auth", "login", "--username", role)
        assert run(runner, "rollback", job_id).exit_code == 3
    # Admin: a never-approved job has nothing to roll back; state untouched.
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "rollback", job_id)
    assert r.exit_code == 1, r.output
    assert "Rollback requires a successful job" in r.output + r.stderr
    assert json.loads(run(runner, "--json", "deploy", "get", job_id).output)["status"] == "pending_approval"
    r = run(runner, "rollback", "0" * 32)
    assert r.exit_code == 1 and "Unknown job" in r.output + r.stderr
    assert executor.calls == []
    # Once succeeded, the default table output renders the queued restore too.
    run(runner, "deploy", "approve", job_id)
    done = json.loads(run(runner, "--json", "deploy", "watch", job_id, "--timeout", "30").output)
    assert done["status"] == "succeeded"
    r = run(runner, "rollback", job_id)
    assert r.exit_code == 0, r.output
    assert "STATUS" in r.output and "OPERATION" in r.output and "rollback" in r.output
    done = json.loads(run(runner, "--json", "deploy", "watch", job_id, "--timeout", "30").output)
    assert done["status"] == "rolled_back"
    assert executor.calls == [("execute", job_id), ("rollback", job_id)]


def test_resources_add_unavailable_and_help_gaps(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "resources", "add")
    assert r.exit_code != 0
    text = (r.output + r.stderr).lower()
    assert "unavailable" in text and "existing stack" in text
    h = run(runner, "resources", "--help")
    assert h.exit_code == 0
    text = h.output.lower()
    assert "unavailable" in text and "default" in text and "add" in text


def _destruction_world(app):
    """Fake only the hypervisor read/write boundaries; the destruction service,
    quarantine clock and routes are real (same substitution as test_destruction)."""
    from test_destruction import Clock, inspector, recorder
    clock = Clock()
    destroyer = recorder()
    app.state.vm_adoption.inspector = inspector()
    app.state.vm_destruction.inspector = inspector()
    app.state.vm_destruction.destroyer = destroyer
    app.state.vm_destruction.clock = clock
    return clock, destroyer


def _adopt_stack(runner, stack, uuid, marker, environment="dev"):
    import json
    from test_destruction import CONNECTION
    r = run(runner, "--json", "resources", "adopt",
            "--environment", environment, "--stack", stack,
            "--connection-id", CONNECTION, "--uuid", uuid, "--marker", marker)
    assert r.exit_code == 0, r.output
    return json.loads(r.output)["state"]


def test_decommission_request_execute_flow(env):
    app, runner = env
    import json
    from test_destruction import UUID, MARKER, CONNECTION
    clock, destroyer = _destruction_world(app)
    run(runner, "auth", "login", "--username", "admin")
    state = _adopt_stack(runner, "vm-owned", UUID, MARKER)
    identity = state["id"]
    request_args = ["decommission", "request", identity, "--connection-id", CONNECTION,
                    "--uuid", UUID, "--marker", MARKER]
    # Request: ownership proven, quarantine opened, the host is untouched.
    r = run(runner, "--json", *request_args)
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["changed"] is True
    destroy = payload["destroy"]
    assert destroy["status"] == "quarantined"
    assert destroy["uuid"] == UUID and destroy["marker"] == MARKER
    assert destroy["connection_id"] == CONNECTION and destroy["name"] == "vm-owned"
    assert destroy["requested_by"] == "admin"
    assert destroy["not_before"] == "2026-11-17T12:15:00+00:00"
    assert destroy["requires_manual_review"] is False
    assert payload["state"]["generation"] == 1
    assert destroyer.calls == []
    # A repeat request keeps the same window, never a second quarantine.
    again = json.loads(run(runner, "--json", *request_args).output)
    assert again["changed"] is False
    assert again["destroy"]["destroy_id"] == destroy["destroy_id"]
    assert again["destroy"]["not_before"] == destroy["not_before"]
    # Status shows the recorded destruction through the read route.
    status = json.loads(run(runner, "--json", "decommission", "status", identity).output)
    assert status["destroy"]["status"] == "quarantined"
    # Execute before the window closes is refused; resolve is not a shortcut.
    r = run(runner, "decommission", "execute", identity, "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 1, r.output
    assert "window" in (r.output + r.stderr).lower()
    r = run(runner, "decommission", "resolve", identity, "--destroy-id", destroy["destroy_id"],
            "--outcome", "destroyed")
    assert r.exit_code == 1 and "unverified" in r.output + r.stderr
    assert destroyer.calls == []
    # After the window: the API re-proves ownership and destroys exactly this VM.
    clock.advance(minutes=16)
    r = run(runner, "--json", "decommission", "execute", identity,
            "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 0, r.output
    done = json.loads(r.output)
    assert done["destroyed"] is True and done["destroy"]["status"] == "destroyed"
    emptied = done["state"]
    assert emptied["generation"] == 2 and emptied["resources"] == []
    assert emptied["source"] == "destroy" and emptied["adopted"] is None
    assert emptied["destroyed"]["uuid"] == UUID and emptied["destroyed"]["by"] == "admin"
    assert destroyer.calls == [(CONNECTION, UUID, MARKER)]
    # The emptied stack is what the inventory now shows; table output renders too.
    states = json.loads(run(runner, "--json", "inventory", "list").output)["states"]
    assert [s["stack"] for s in states] == ["vm-owned"]
    assert states[0]["resource_count"] == 0 and states[0]["source"] == "destroy"
    r = run(runner, "decommission", "status", identity)
    assert r.exit_code == 0 and "destroyed" in r.output
    # A destroyed stack cannot be destroyed again: its resources are gone.
    r = run(runner, *request_args)
    assert r.exit_code == 1 and "not owned" in r.output + r.stderr
    assert destroyer.calls == [(CONNECTION, UUID, MARKER)]


def test_decommission_cancel(env):
    app, runner = env
    import json
    from test_destruction import UUID, MARKER, CONNECTION
    clock, destroyer = _destruction_world(app)
    run(runner, "auth", "login", "--username", "admin")
    state = _adopt_stack(runner, "vm-owned", UUID, MARKER)
    identity = state["id"]
    destroy = json.loads(run(runner, "--json", "decommission", "request", identity,
                               "--connection-id", CONNECTION, "--uuid", UUID,
                               "--marker", MARKER).output)["destroy"]
    # A stale destroy id is refused; the quarantine stands.
    r = run(runner, "decommission", "cancel", identity, "--destroy-id", "e" * 32)
    assert r.exit_code == 1 and "changed" in r.output + r.stderr
    assert json.loads(run(runner, "--json", "decommission", "status", identity).output
                      )["destroy"]["status"] == "quarantined"
    # Cancel inside the window: no host call, the resources are kept.
    r = run(runner, "--json", "decommission", "cancel", identity,
            "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["cancelled"] is True and payload["destroy"]["status"] == "cancelled"
    assert payload["state"]["generation"] == 1 and len(payload["state"]["resources"]) == 1
    assert destroyer.calls == []
    # A cancelled destruction can no longer be executed, even after the window.
    clock.advance(minutes=16)
    r = run(runner, "decommission", "execute", identity, "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 1, r.output
    assert destroyer.calls == []


def test_decommission_isolated_to_the_deployment_id(env):
    app, runner = env
    import json
    from test_destruction import UUID, MARKER, OTHER_UUID, OTHER_MARKER, CONNECTION
    clock, destroyer = _destruction_world(app)
    run(runner, "auth", "login", "--username", "admin")
    one = _adopt_stack(runner, "vm-one", UUID, MARKER)
    two = _adopt_stack(runner, "vm-two", OTHER_UUID, OTHER_MARKER)
    # Targeting stack one with stack two's VM is refused; nothing is recorded.
    r = run(runner, "decommission", "request", one["id"],
            "--connection-id", CONNECTION, "--uuid", OTHER_UUID, "--marker", OTHER_MARKER)
    assert r.exit_code == 1, r.output
    assert "not owned" in r.output + r.stderr
    assert json.loads(run(runner, "--json", "decommission", "status", one["id"]).output
                      )["destroy"] is None
    # The full flow runs against stack two's id only.
    destroy = json.loads(run(runner, "--json", "decommission", "request", two["id"],
                               "--connection-id", CONNECTION, "--uuid", OTHER_UUID,
                               "--marker", OTHER_MARKER).output)["destroy"]
    clock.advance(minutes=16)
    r = run(runner, "--json", "decommission", "execute", two["id"],
            "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 0, r.output
    assert destroyer.calls == [(CONNECTION, OTHER_UUID, OTHER_MARKER)]
    # Stack one is untouched: same generation, resources and no destruction record.
    untouched = json.loads(run(runner, "--json", "decommission", "status", one["id"]).output)
    assert untouched["generation"] == 1 and untouched["destroy"] is None
    assert [row["address"] for row in untouched["resources"]] == ["esxi_vm." + UUID]
    emptied = json.loads(run(runner, "--json", "decommission", "status", two["id"]).output)
    assert emptied["generation"] == 2 and emptied["resources"] == []


def test_decommission_resolve_unverified(env):
    app, runner = env
    import json
    from test_destruction import UUID, MARKER, CONNECTION
    clock, _ = _destruction_world(app)
    run(runner, "auth", "login", "--username", "admin")
    state = _adopt_stack(runner, "vm-owned", UUID, MARKER)
    identity = state["id"]
    request_args = ["decommission", "request", identity, "--connection-id", CONNECTION,
                    "--uuid", UUID, "--marker", MARKER]
    destroy_id = json.loads(run(runner, "--json", *request_args).output)["destroy"]["destroy_id"]

    def failing(connection_id, uuid, marker):
        raise RuntimeError("synthetic hypervisor text")

    app.state.vm_destruction.destroyer = failing
    clock.advance(minutes=16)
    # The driver fails: the record is unverified, never assumed either way.
    r = run(runner, "decommission", "execute", identity, "--destroy-id", destroy_id)
    assert r.exit_code == 1, r.output
    assert "synthetic hypervisor text" not in r.output + r.stderr
    stored = json.loads(run(runner, "--json", "decommission", "status", identity).output)["destroy"]
    assert stored["status"] == "rollback_failed" and stored["requires_manual_review"] is True
    # An unverified destruction cannot be cancelled or re-requested; resolve only.
    assert run(runner, "decommission", "cancel", identity,
               "--destroy-id", destroy_id).exit_code == 1
    assert run(runner, *request_args).exit_code == 1
    # Observed intact: the API re-reads the live VM, then restores the retry path.
    r = run(runner, "--json", "decommission", "resolve", identity,
            "--destroy-id", destroy_id, "--outcome", "intact")
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["resolved"] == "intact" and payload["destroy"]["status"] == "failed"
    assert payload["destroy"]["reason"] == "resolved_intact"
    assert payload["state"]["generation"] == 1 and len(payload["state"]["resources"]) == 1
    retry = json.loads(run(runner, "--json", *request_args).output)
    assert retry["changed"] is True
    # Second failure, then observed destroyed: the emptied generation is committed.
    retry_id = retry["destroy"]["destroy_id"]
    clock.advance(minutes=16)
    assert run(runner, "decommission", "execute", identity,
               "--destroy-id", retry_id).exit_code == 1
    r = run(runner, "--json", "decommission", "resolve", identity,
            "--destroy-id", retry_id, "--outcome", "destroyed")
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["resolved"] == "destroyed" and payload["destroy"]["status"] == "destroyed"
    assert payload["state"]["generation"] == 2 and payload["state"]["resources"] == []
    # Resolving twice is refused.
    assert run(runner, "decommission", "resolve", identity, "--destroy-id", retry_id,
               "--outcome", "destroyed").exit_code == 1


def test_decommission_governance_boundaries(env, monkeypatch):
    app, runner = env
    import json
    import os
    import httpx
    from test_destruction import UUID, MARKER, CONNECTION
    clock, destroyer = _destruction_world(app)
    creds = bootstrap(app.state.state_dir)
    run(runner, "auth", "login", "--username", "admin")
    state = _adopt_stack(runner, "vm-owned", UUID, MARKER)
    identity = state["id"]
    request_args = ["decommission", "request", identity, "--connection-id", CONNECTION,
                    "--uuid", UUID, "--marker", MARKER]
    # Unauthenticated access is refused.
    run(runner, "auth", "logout")
    assert run(runner, *request_args).exit_code == 2
    # Viewer and operator: every destruction step is a human-administrator mutation.
    for role in ("viewer", "operator"):
        monkeypatch.setenv("KW_PASSWORD", creds[role])
        run(runner, "auth", "login", "--username", role)
        assert run(runner, *request_args).exit_code == 3
        assert run(runner, "decommission", "execute", identity,
                   "--destroy-id", "a" * 32).exit_code == 3
        assert run(runner, "decommission", "cancel", identity,
                   "--destroy-id", "a" * 32).exit_code == 3
        assert run(runner, "decommission", "resolve", identity, "--destroy-id", "a" * 32,
                   "--outcome", "intact").exit_code == 3
    assert destroyer.calls == []
    assert json.loads(run(runner, "--json", "decommission", "status", identity).output
                      )["destroy"] is None
    # An admin-scoped agent token can read the state but never decommission.
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    run(runner, "auth", "login", "--username", "admin")
    from kw import config
    sess = config.load_session()
    with httpx.Client(base_url=os.environ["KW_URL"], trust_env=False) as hc:
        hc.cookies.set("kw_session", sess["cookie"])
        hc.headers["X-CSRF-Token"] = sess["csrf"]
        issued = hc.post("/api/agent-tokens",
                         json={"label": "destroyer", "role": "admin",
                               "scopes": ["submit", "configure", "read:infrastructure"],
                               "expires_in_seconds": 3600})
        assert issued.status_code == 201, issued.text
        token = issued.json()["token"]
    monkeypatch.setenv("KW_AGENT_TOKEN", token)
    assert run(runner, *request_args).exit_code == 3
    assert run(runner, "decommission", "execute", identity,
               "--destroy-id", "a" * 32).exit_code == 3
    assert run(runner, "decommission", "cancel", identity,
               "--destroy-id", "a" * 32).exit_code == 3
    assert run(runner, "decommission", "resolve", identity, "--destroy-id", "a" * 32,
               "--outcome", "intact").exit_code == 3
    r = run(runner, "--json", "decommission", "status", identity)
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["id"] == identity
    assert destroyer.calls == []


def test_decommission_validation_and_help(env):
    app, runner = env
    from test_destruction import UUID, MARKER, CONNECTION
    _destruction_world(app)
    run(runner, "auth", "login", "--username", "admin")
    state = _adopt_stack(runner, "vm-owned", UUID, MARKER)
    identity = state["id"]
    # Unknown state id: the API's 404 is surfaced, nothing recorded.
    r = run(runner, "decommission", "request", "f" * 32, "--connection-id", CONNECTION,
            "--uuid", UUID, "--marker", MARKER)
    assert r.exit_code == 1 and "Unknown environment state" in r.output + r.stderr
    # The API schema is the authority: an invalid uuid is refused (422).
    r = run(runner, "decommission", "request", identity, "--connection-id", CONNECTION,
            "--uuid", "not-a-uuid", "--marker", MARKER)
    assert r.exit_code == 1, r.output
    # Missing required flags and an unknown outcome are usage errors.
    assert run(runner, "decommission", "request", identity).exit_code == 2
    assert run(runner, "decommission", "execute", identity).exit_code == 2
    assert run(runner, "decommission", "cancel", identity).exit_code == 2
    assert run(runner, "decommission", "resolve", identity, "--destroy-id", "a" * 32,
               "--outcome", "gone").exit_code == 2
    # Help documents the governed flow and its gates.
    h = run(runner, "decommission", "--help")
    assert h.exit_code == 0
    text = h.output.lower()
    assert "quarantine" in text and "human" in text and "agent" in text
    for sub in ("request", "execute", "cancel", "resolve", "status"):
        assert run(runner, "decommission", sub, "--help").exit_code == 0


def test_tokens_lifecycle_via_cli(env, tmp_path, monkeypatch):
    app, runner = env
    import hashlib
    import json
    import os
    run(runner, "auth", "login", "--username", "admin")
    assert json.loads(run(runner, "--json", "tokens", "list").output) == []
    secret_file = tmp_path / "automation.token"
    r = run(runner, "--json", "tokens", "create", "--label", "automation-1",
            "--role", "operator", "--scopes", "read", "--scopes", "plan",
            "--scopes", "submit", "--expires-in", "3600",
            "--token-file", str(secret_file))
    assert r.exit_code == 0, r.output
    meta = json.loads(r.output)
    assert meta["label"] == "automation-1" and meta["role"] == "operator"
    assert meta["scopes"] == ["read", "plan", "submit"]
    assert meta["revoked_at"] is None and meta["token_file"] == str(secret_file)
    secret = secret_file.read_text()
    assert secret.startswith("kw_agent_") and len(secret) == len("kw_agent_") + 43
    assert os.stat(secret_file).st_mode & 0o777 == 0o600
    assert secret not in r.output  # the one-time secret is never printed
    # The API stores only the hash; the secret never enters the audit trail.
    row = app.state.store.one("SELECT * FROM agent_tokens WHERE id=?", (meta["id"],))
    assert row["token_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    listing = json.loads(run(runner, "--json", "tokens", "list").output)
    assert [t["id"] for t in listing] == [meta["id"]]
    assert all("token" not in t and "token_hash" not in t for t in listing)
    # KW_AGENT_TOKEN automation mode: Bearer identity, no login, no CSRF.
    monkeypatch.setenv("KW_AGENT_TOKEN", secret)
    who = json.loads(run(runner, "--json", "auth", "whoami").output)
    assert who["principal_type"] == "agent" and who["username"] == "agent:" + meta["id"]
    assert who["role"] == "operator"
    assert run(runner, "--json", "deploy", "list").exit_code == 0
    # Rotation is a human-admin action: new secret only, identity/expiry kept.
    monkeypatch.delenv("KW_AGENT_TOKEN")
    rotated_file = tmp_path / "rotated.token"
    r = run(runner, "--json", "tokens", "rotate", meta["id"],
            "--token-file", str(rotated_file))
    assert r.exit_code == 0, r.output
    rotated = json.loads(r.output)
    assert rotated["id"] == meta["id"] and rotated["expires_at"] == meta["expires_at"]
    assert rotated["scopes"] == meta["scopes"] and rotated["role"] == meta["role"]
    secret2 = rotated_file.read_text()
    assert secret2 != secret and secret2 not in r.output
    assert os.stat(rotated_file).st_mode & 0o777 == 0o600
    monkeypatch.setenv("KW_AGENT_TOKEN", secret)
    assert run(runner, "auth", "whoami").exit_code == 2  # old secret is dead
    monkeypatch.setenv("KW_AGENT_TOKEN", secret2)
    assert run(runner, "auth", "whoami").exit_code == 0
    # Revocation kills the secret immediately and is idempotent.
    monkeypatch.delenv("KW_AGENT_TOKEN")
    r = run(runner, "--json", "tokens", "revoke", meta["id"])
    assert r.exit_code == 0 and json.loads(r.output)["revoked_at"] is not None
    assert run(runner, "--json", "tokens", "revoke", meta["id"]).exit_code == 0
    monkeypatch.setenv("KW_AGENT_TOKEN", secret2)
    assert run(runner, "auth", "whoami").exit_code == 2
    # A revoked token cannot be rotated back to life; unknown ids are 404.
    monkeypatch.delenv("KW_AGENT_TOKEN")
    r = run(runner, "tokens", "rotate", meta["id"],
            "--token-file", str(tmp_path / "x.token"))
    assert r.exit_code == 1 and "Revoked" in r.output + r.stderr
    r = run(runner, "tokens", "rotate", "0" * 32,
            "--token-file", str(tmp_path / "x.token"))
    assert r.exit_code == 1 and "Unknown agent token" in r.output + r.stderr
    r = run(runner, "tokens", "revoke", "0" * 32)
    assert r.exit_code == 1 and "Unknown agent token" in r.output + r.stderr
    audit = json.dumps(app.state.store.all("SELECT * FROM audit"))
    assert secret not in audit and secret2 not in audit
    # Default table output renders the register too.
    r = run(runner, "tokens", "list")
    assert r.exit_code == 0 and "LABEL" in r.output


def test_tokens_management_is_human_admin_only(env, tmp_path, monkeypatch):
    app, runner = env
    import json
    creds = bootstrap(app.state.state_dir)
    token_file = str(tmp_path / "t.token")
    create_args = ["tokens", "create", "--label", "x", "--role", "viewer",
                   "--expires-in", "3600", "--token-file", token_file]
    # Unauthenticated access is refused.
    assert run(runner, "tokens", "list").exit_code == 2
    assert run(runner, *create_args).exit_code == 2
    # Viewer and operator humans cannot manage tokens.
    for role in ("viewer", "operator"):
        monkeypatch.setenv("KW_PASSWORD", creds[role])
        run(runner, "auth", "login", "--username", role)
        assert run(runner, "tokens", "list").exit_code == 3
        assert run(runner, *create_args).exit_code == 3
        assert run(runner, "tokens", "rotate", "0" * 32,
                   "--token-file", token_file).exit_code == 3
        assert run(runner, "tokens", "revoke", "0" * 32).exit_code == 3
    # Even an admin-role agent token with broad scopes cannot manage tokens.
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "--json", "tokens", "create", "--label", "manager",
            "--role", "admin", "--scopes", "read", "--scopes", "submit",
            "--scopes", "configure", "--scopes", "approve", "--scopes", "rollback",
            "--expires-in", "3600", "--token-file", token_file)
    assert r.exit_code == 0, r.output
    monkeypatch.setenv("KW_AGENT_TOKEN", (tmp_path / "t.token").read_text())
    assert run(runner, "tokens", "list").exit_code == 3
    assert run(runner, *create_args).exit_code == 3
    assert run(runner, "tokens", "rotate", "0" * 32,
               "--token-file", token_file).exit_code == 3
    assert run(runner, "tokens", "revoke", "0" * 32).exit_code == 3
    monkeypatch.delenv("KW_AGENT_TOKEN")
    assert len(json.loads(run(runner, "--json", "tokens", "list").output)) == 1


def test_tokens_create_validation_is_the_api(env, tmp_path):
    app, runner = env
    import json
    run(runner, "auth", "login", "--username", "admin")
    dest = tmp_path / "v.token"

    def create(*extra):
        return run(runner, "tokens", "create", "--label", "valid", "--role", "viewer",
                   "--expires-in", "3600", "--token-file", str(dest), *extra)

    # The API schema is the authority: role/scope mismatch, duplicates, unknown
    # scopes, expiry bounds and the label pattern are all refused server-side.
    for extra in (["--scopes", "approve"],
                  ["--scopes", "read", "--scopes", "read"],
                  ["--scopes", "root"],
                  ["--expires-in", "59"],
                  ["--expires-in", "2592001"],
                  ["--label", "bad\nlabel"]):
        r = create(*extra)
        assert r.exit_code == 1, (extra, r.output)
        assert not dest.exists()  # no secret file on a refused issuance
    assert json.loads(run(runner, "--json", "tokens", "list").output) == []
    # Omitted scopes: the API default applies, the CLI invents nothing.
    r = run(runner, "--json", "tokens", "create", "--label", "dflt", "--role", "admin",
            "--expires-in", "60", "--token-file", str(dest))
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["scopes"] == ["read"]
    # The secret destination is mandatory: the secret is never printed.
    r = run(runner, "tokens", "create", "--label", "x", "--role", "viewer",
            "--expires-in", "3600")
    assert r.exit_code == 2


def test_agent_automation_cannot_approve_rollback_decommission(env, tmp_path, monkeypatch):
    app, runner = env
    import json
    from test_core_jobs import RecordingExecutor, install_executor
    executor = install_executor(app, RecordingExecutor())
    run(runner, "auth", "login", "--username", "admin")
    job = json.loads(run(runner, "--json", "deploy", "request",
                         "--graph-file", _graph_file(tmp_path)).output)
    assert job["status"] == "pending_approval"
    secret_file = tmp_path / "auto.token"
    r = run(runner, "--json", "tokens", "create", "--label", "ci", "--role", "operator",
            "--scopes", "read", "--scopes", "plan", "--scopes", "submit",
            "--expires-in", "3600", "--token-file", str(secret_file))
    assert r.exit_code == 0, r.output
    monkeypatch.setenv("KW_AGENT_TOKEN", secret_file.read_text())
    # Automation mode works for read / validate / request / follow...
    assert run(runner, "--json", "deploy", "validate",
               "--graph-file", _graph_file(tmp_path)).exit_code == 0
    submitted = json.loads(run(runner, "--json", "deploy", "request",
                               "--graph-file", _graph_file(tmp_path)).output)
    assert submitted["status"] == "pending_approval"  # no approval bypass
    assert run(runner, "--json", "deploy", "get", job["id"]).exit_code == 0
    assert run(runner, "--json", "logs", job["id"]).exit_code == 0
    # ...but approve, reject, rollback and decommission are refused.
    assert run(runner, "deploy", "approve", job["id"]).exit_code == 3
    assert run(runner, "deploy", "reject", job["id"]).exit_code == 3
    assert run(runner, "rollback", job["id"]).exit_code == 3
    assert run(runner, "services", "decommission", job["id"]).exit_code == 3
    assert run(runner, "decommission", "request", "f" * 32,
               "--connection-id", "a" * 32, "--uuid", "b" * 32,
               "--marker", "m").exit_code == 3
    assert executor.calls == []
    # An admin-role token without the approve/rollback scopes is refused too.
    monkeypatch.delenv("KW_AGENT_TOKEN")
    admin_file = tmp_path / "admin.token"
    r = run(runner, "--json", "tokens", "create", "--label", "no-approve",
            "--role", "admin", "--scopes", "read", "--scopes", "submit",
            "--expires-in", "3600", "--token-file", str(admin_file))
    assert r.exit_code == 0, r.output
    monkeypatch.setenv("KW_AGENT_TOKEN", admin_file.read_text())
    assert run(runner, "deploy", "approve", job["id"]).exit_code == 3
    assert run(runner, "rollback", job["id"]).exit_code == 3
    assert run(runner, "services", "decommission", job["id"]).exit_code == 3
    # Nothing moved: both jobs still await a human, and the human path works.
    monkeypatch.delenv("KW_AGENT_TOKEN")
    for ident in (job["id"], submitted["id"]):
        assert json.loads(run(runner, "--json", "deploy", "get", ident).output
                          )["status"] == "pending_approval"
    assert run(runner, "--json", "deploy", "approve", job["id"]).exit_code == 0
    done = json.loads(run(runner, "--json", "deploy", "watch", job["id"],
                          "--timeout", "30").output)
    assert done["status"] == "succeeded"
    assert executor.calls == [("execute", job["id"])]


def test_agent_approve_needs_explicit_human_scope_grant(env, tmp_path, monkeypatch):
    """Approve/rollback are scope-gated by the API, not principal-gated: only a
    human admin can issue those scopes, and only to an admin-role token. The
    CLI forwards the API's decision and adds no policy of its own. Decommission
    has no machine scope path at all, so it stays human-only even for an
    admin agent holding approve/rollback."""
    app, runner = env
    import json
    from test_core_jobs import RecordingExecutor, install_executor
    executor = install_executor(app, RecordingExecutor())
    run(runner, "auth", "login", "--username", "admin")
    # The API itself refuses to grant approve to non-admin roles.
    r = run(runner, "tokens", "create", "--label", "op-approve", "--role", "operator",
            "--scopes", "approve", "--expires-in", "3600",
            "--token-file", str(tmp_path / "no.token"))
    assert r.exit_code == 1 and not (tmp_path / "no.token").exists()
    job = json.loads(run(runner, "--json", "deploy", "request",
                         "--graph-file", _graph_file(tmp_path)).output)
    secret_file = tmp_path / "granted.token"
    r = run(runner, "--json", "tokens", "create", "--label", "explicit-grant",
            "--role", "admin", "--scopes", "read", "--scopes", "approve",
            "--scopes", "rollback", "--expires-in", "3600",
            "--token-file", str(secret_file))
    assert r.exit_code == 0, r.output
    monkeypatch.setenv("KW_AGENT_TOKEN", secret_file.read_text())
    # Explicit human grant: the API permits approve/rollback for this token.
    assert run(runner, "--json", "deploy", "approve", job["id"]).exit_code == 0
    done = json.loads(run(runner, "--json", "deploy", "watch", job["id"],
                          "--timeout", "30").output)
    assert done["status"] == "succeeded"
    assert run(runner, "--json", "rollback", job["id"]).exit_code == 0
    done = json.loads(run(runner, "--json", "deploy", "watch", job["id"],
                          "--timeout", "30").output)
    assert done["status"] == "rolled_back"
    # Decommission is never a machine path, even with approve/rollback granted.
    assert run(runner, "services", "decommission", job["id"]).exit_code == 3
    assert run(runner, "decommission", "request", "f" * 32,
               "--connection-id", "a" * 32, "--uuid", "b" * 32,
               "--marker", "m").exit_code == 3
    assert executor.calls == [("execute", job["id"]), ("rollback", job["id"])]


def test_tokens_help_documents_automation_mode(env):
    app, runner = env
    h = run(runner, "tokens", "--help")
    assert h.exit_code == 0
    assert "KW_AGENT_TOKEN" in h.output
    text = h.output.lower()
    assert "human" in text and "approve" in text and "decommission" in text
    for sub in ("list", "create", "rotate", "revoke"):
        assert run(runner, "tokens", sub, "--help").exit_code == 0
    h = run(runner, "tokens", "create", "--help")
    assert "KW_AGENT_TOKEN" in h.output and "0600" in h.output
    assert "read" in h.output and "plan" in h.output and "submit" in h.output


# --- error-paths: consistent 404 / 422 / unreachable handling across groups ---


def test_unknown_id_is_clean_404_across_groups(env, tmp_path):
    """Every group surfaces the API's 404 as one stderr line, exit 1, no traceback."""
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    missing = "0" * 32
    cases = [
        (["blueprints", "get", missing], "Unknown blueprint"),
        (["blueprints", "copy", missing, "--name", "x"], "Unknown blueprint"),
        (["deploy", "get", missing], "Unknown job"),
        (["deploy", "approve", missing], "Unknown job"),
        (["deploy", "reject", missing], "Unknown job"),
        (["rollback", missing], "Unknown job"),
        (["logs", missing], "Unknown job"),
        (["inventory", "get", missing], "Unknown environment state"),
        (["connectors", "get", missing], "Unknown connection"),
        (["connectors", "test", missing], "Unknown connection"),
        (["keys", "get", missing], "Unknown SSH key"),
        (["keys", "revoke", missing], "Unknown SSH key"),
        (["services", "decommission", missing], "Unknown job"),
        (["services", "request", missing], "Unknown offering"),
        (["modules", "offerings", "create", "--title", "t",
          "--blueprint-id", missing], "Unknown blueprint"),
        (["tokens", "revoke", missing], "Unknown agent token"),
        (["tokens", "rotate", missing,
          "--token-file", str(tmp_path / "r.token")], "Unknown agent token"),
        (["decommission", "status", missing], "Unknown environment state"),
    ]
    for args, needle in cases:
        r = run(runner, *args)
        assert r.exit_code == 1, (args, r.exit_code, r.output, r.stderr)
        assert r.stderr.startswith("error:"), (args, r.stderr)
        assert needle in r.stderr, (args, r.stderr)
        assert "Traceback" not in r.stderr and r.stdout == ""
    assert not (tmp_path / "r.token").exists()  # no secret file on a refused rotate


def test_invalid_input_is_clean_422_across_groups(env, tmp_path):
    """API schema validation (422) is surfaced readably; nothing is created."""
    app, runner = env
    import json
    run(runner, "auth", "login", "--username", "admin")
    bad = tmp_path / "bad-graph.json"
    bad.write_text(json.dumps({"name": "bad", "nodes": []}))  # nodes min_length=1
    cases = [
        (["blueprints", "create", "--name", "bad", "--graph-file", str(bad)],
         "graph.nodes"),
        (["deploy", "request", "--graph-file", str(bad)], "graph.nodes"),
        (["deploy", "validate", "--graph-file", str(bad)], "nodes:"),
    ]
    for args, needle in cases:
        r = run(runner, *args)
        assert r.exit_code == 1, (args, r.exit_code, r.output, r.stderr)
        assert r.stderr.startswith("error:"), (args, r.stderr)
        assert needle in r.stderr, (args, r.stderr)  # field path, not raw dict repr
        assert "Traceback" not in r.stderr and r.stdout == ""
    # Several validation errors at once are all rendered, still one clean line.
    bad2 = tmp_path / "bad-env.json"
    bad2.write_text(json.dumps({"name": "bad", "environment": "prod", "nodes": []}))
    r = run(runner, "deploy", "request", "--graph-file", str(bad2))
    assert r.exit_code == 1
    assert "graph.environment" in r.stderr and "graph.nodes" in r.stderr
    # Token issuance: the API schema is the authority (scope/role, expiry bounds).
    dest = tmp_path / "x.token"
    r = run(runner, "tokens", "create", "--label", "x", "--role", "viewer",
            "--scopes", "approve", "--expires-in", "3600", "--token-file", str(dest))
    assert r.exit_code == 1 and "Scopes are not permitted" in r.stderr
    r = run(runner, "tokens", "create", "--label", "x", "--role", "viewer",
            "--expires-in", "59", "--token-file", str(dest))
    assert r.exit_code == 1 and "expires_in_seconds" in r.stderr
    assert not dest.exists()
    # No side effects from any refused input.
    assert json.loads(run(runner, "--json", "blueprints", "list").output) == []
    assert json.loads(run(runner, "--json", "deploy", "list").output) == []


def test_unreachable_api_is_clean_error(env, monkeypatch):
    """No server on the URL: one stderr line naming the URL, exit 4, no traceback."""
    app, runner = env
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()  # nothing is listening on this port now
    dead = f"http://127.0.0.1:{dead_port}"
    run(runner, "auth", "login", "--username", "admin")
    for args in (["deploy", "list"], ["blueprints", "list"], ["inventory", "list"],
                 ["finops"], ["keys", "list"], ["auth", "whoami"],
                 ["auth", "login", "--username", "admin"],
                 ["deploy", "watch", "0" * 32, "--timeout", "5"],
                 ["logs", "0" * 32]):
        r = run(runner, "--url", dead, *args)
        assert r.exit_code == 4, (args, r.exit_code, r.output, r.stderr)
        assert "cannot reach" in r.stderr and dead in r.stderr, (args, r.stderr)
        assert "Traceback" not in r.stderr and r.stdout == ""
    # Same behavior when the URL comes from KW_URL instead of --url.
    monkeypatch.setenv("KW_URL", dead)
    r = run(runner, "deploy", "list")
    assert r.exit_code == 4 and "cannot reach" in r.stderr and dead in r.stderr


def test_invalid_graph_file_is_clean_usage_error(env, tmp_path):
    """Malformed JSON in --graph-file: clean usage error, not a traceback."""
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json")
    for args in (["blueprints", "create", "--name", "x", "--graph-file", str(bad)],
                 ["deploy", "request", "--graph-file", str(bad)],
                 ["deploy", "validate", "--graph-file", str(bad)]):
        r = run(runner, *args)
        assert r.exit_code == 2, (args, r.exit_code, r.output, r.stderr)
        assert "invalid JSON" in r.stderr, (args, r.stderr)
        assert "Traceback" not in r.stderr and r.stdout == ""


# --- destructive-gating-tests: KW_AGENT_TOKEN denied for every human-gated mutation ---


def test_agent_denied_for_every_destructive_op(env, tmp_path, monkeypatch):
    """The documented automation token (operator, read/plan/submit) is refused for
    every destructive/human-gated op, against real objects: a pending job, a live
    key, a registered connector and a quarantined stack whose window has closed.
    Nothing moves; the human path afterwards completes every refused operation."""
    app, runner = env
    import json
    from test_core_jobs import RecordingExecutor, install_executor
    from test_destruction import UUID, MARKER, CONNECTION
    executor = install_executor(app, RecordingExecutor())
    clock, destroyer = _destruction_world(app)
    creds = bootstrap(app.state.state_dir)
    run(runner, "auth", "login", "--username", "admin")
    # Real objects, created through the human CLI path.
    job = json.loads(run(runner, "--json", "deploy", "request",
                         "--graph-file", _graph_file(tmp_path)).output)
    assert job["status"] == "pending_approval"
    key = json.loads(run(runner, "--json", "keys", "generate", "--name", "human-key").output)
    conn = json.loads(run(runner, "--json", "connectors", "add",
                          "--label", "Human", "--driver", "recording",
                          "--endpoint", "https://192.0.2.10", "--secret-ref", "hypervisors/human",
                          "--secrets-provider", "test-vault").output)
    state = _adopt_stack(runner, "vm-owned", UUID, MARKER)
    identity = state["id"]
    request_args = ["decommission", "request", identity, "--connection-id", CONNECTION,
                    "--uuid", UUID, "--marker", MARKER]
    destroy = json.loads(run(runner, "--json", *request_args).output)["destroy"]
    assert destroy["status"] == "quarantined"
    # Automation mode: a human-issued operator token, no session, no CSRF.
    secret_file = tmp_path / "auto.token"
    r = run(runner, "--json", "tokens", "create", "--label", "ci", "--role", "operator",
            "--scopes", "read", "--scopes", "plan", "--scopes", "submit",
            "--expires-in", "3600", "--token-file", str(secret_file))
    assert r.exit_code == 0, r.output
    monkeypatch.setenv("KW_AGENT_TOKEN", secret_file.read_text())
    who = json.loads(run(runner, "--json", "auth", "whoami").output)
    assert who["principal_type"] == "agent"
    sentinel = "agent-ingest-sentinel"
    monkeypatch.setenv("KW_PASSWORD", sentinel)
    export_dest = tmp_path / "agent-export.key"
    refused = [
        ["deploy", "approve", job["id"]],
        ["deploy", "reject", job["id"]],
        ["rollback", job["id"]],
        ["services", "decommission", job["id"]],
        request_args,
        ["decommission", "cancel", identity, "--destroy-id", destroy["destroy_id"]],
        ["decommission", "resolve", identity, "--destroy-id", destroy["destroy_id"],
         "--outcome", "intact"],
        ["keys", "generate", "--name", "agent-key"],
        ["keys", "revoke", key["id"]],
        ["keys", "export", key["id"], "--output", str(export_dest)],
        ["connectors", "add", "--label", "Agent", "--driver", "recording",
         "--endpoint", "https://192.0.2.11", "--secret-ref", "hypervisors/agent",
         "--secrets-provider", "test-vault"],
        ["connectors", "credentials", conn["id"], "--username", "agent-user"],
    ]
    for args in refused:
        r = run(runner, *args)
        assert r.exit_code == 3, (args, r.exit_code, r.output, r.stderr)
        assert "Traceback" not in r.stderr
    # The window closes: execute is now refused by the human gate alone, not the clock.
    clock.advance(minutes=16)
    r = run(runner, "decommission", "execute", identity, "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 3, (r.exit_code, r.output, r.stderr)
    assert not export_dest.exists()
    assert executor.calls == [] and destroyer.calls == []
    # No side effects: reads (permitted for the agent) show everything untouched.
    assert json.loads(run(runner, "--json", "deploy", "get", job["id"]).output
                      )["status"] == "pending_approval"
    keys = json.loads(run(runner, "--json", "keys", "list").output)
    assert len(keys) == 1 and keys[0]["id"] == key["id"] and keys[0]["enabled"] is True
    conns = json.loads(run(runner, "--json", "connectors", "list").output)
    assert len(conns) == 1 and conns[0]["id"] == conn["id"]
    assert json.loads(run(runner, "--json", "decommission", "status", identity).output
                      )["destroy"]["status"] == "quarantined"
    persisted = json.dumps(app.state.store.all("SELECT * FROM connections"))
    assert sentinel not in persisted and "agent-user" not in persisted
    # The human path still completes every operation the agent was refused.
    monkeypatch.delenv("KW_AGENT_TOKEN")
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    assert run(runner, "--json", "deploy", "approve", job["id"]).exit_code == 0
    done = json.loads(run(runner, "--json", "deploy", "watch", job["id"],
                          "--timeout", "30").output)
    assert done["status"] == "succeeded"
    assert executor.calls == [("execute", job["id"])]
    assert json.loads(run(runner, "--json", "keys", "revoke", key["id"]).output
                      )["enabled"] is False
    monkeypatch.setenv("KW_PASSWORD", "human-secret-value")
    r = run(runner, "--json", "connectors", "credentials", conn["id"], "--username", "test-user")
    assert r.exit_code == 0, r.output
    assert "human-secret-value" not in r.output
    assert "human-secret-value" not in json.dumps(app.state.store.all("SELECT * FROM connections"))
    monkeypatch.setenv("KW_PASSWORD", creds["admin"])
    r = run(runner, "--json", "decommission", "execute", identity,
            "--destroy-id", destroy["destroy_id"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["destroy"]["status"] == "destroyed"
    assert destroyer.calls == [(CONNECTION, UUID, MARKER)]


def test_admin_agent_broad_scopes_still_human_gated(env, tmp_path, monkeypatch):
    """Even an admin-role agent token holding approve/rollback/configure cannot
    generate/revoke/export SSH keys or ingest connector credentials: those routes
    have no machine scope mapping and require a human administrator session.
    Connector registration is the API's one 'configure' machine write (metadata
    only): refused without the explicit human grant, permitted with it, and it
    never opens a path to secrets. The CLI adds no policy of its own."""
    app, runner = env
    import json
    run(runner, "auth", "login", "--username", "admin")
    key = json.loads(run(runner, "--json", "keys", "generate", "--name", "human-key").output)
    conn = json.loads(run(runner, "--json", "connectors", "add",
                          "--label", "Human", "--driver", "recording",
                          "--endpoint", "https://192.0.2.10", "--secret-ref", "hypervisors/human",
                          "--secrets-provider", "test-vault").output)
    broad_file = tmp_path / "broad.token"
    r = run(runner, "--json", "tokens", "create", "--label", "broad", "--role", "admin",
            "--scopes", "read", "--scopes", "submit", "--scopes", "approve",
            "--scopes", "rollback", "--scopes", "configure",
            "--expires-in", "3600", "--token-file", str(broad_file))
    assert r.exit_code == 0, r.output
    # Token management is human-only: issue the second token before switching.
    noconfig_file = tmp_path / "noconfig.token"
    r = run(runner, "--json", "tokens", "create", "--label", "no-config", "--role", "admin",
            "--scopes", "read", "--scopes", "submit", "--scopes", "approve",
            "--scopes", "rollback", "--expires-in", "3600", "--token-file", str(noconfig_file))
    assert r.exit_code == 0, r.output
    monkeypatch.setenv("KW_AGENT_TOKEN", broad_file.read_text())
    sentinel = "broad-ingest-sentinel"
    monkeypatch.setenv("KW_PASSWORD", sentinel)
    export_dest = tmp_path / "broad-export.key"
    for args in (["keys", "generate", "--name", "agent-key"],
                 ["keys", "revoke", key["id"]],
                 ["keys", "export", key["id"], "--output", str(export_dest)],
                 ["connectors", "credentials", conn["id"], "--username", "agent-user"]):
        r = run(runner, *args)
        assert r.exit_code == 3, (args, r.exit_code, r.output, r.stderr)
    assert not export_dest.exists()
    assert json.loads(run(runner, "--json", "keys", "list").output)[0]["enabled"] is True
    assert sentinel not in json.dumps(app.state.store.all("SELECT * FROM connections"))
    # Explicit human 'configure' grant: registration (metadata) is a machine path...
    r = run(runner, "--json", "connectors", "add",
            "--label", "Configured", "--driver", "recording",
            "--endpoint", "https://192.0.2.11", "--secret-ref", "hypervisors/configured",
            "--secrets-provider", "test-vault")
    assert r.exit_code == 0, r.output
    configured = json.loads(r.output)
    assert len(json.loads(run(runner, "--json", "connectors", "list").output)) == 2
    # ...but secret ingestion stays human-only even on the connector it created.
    assert run(runner, "connectors", "credentials", configured["id"],
               "--username", "agent-user").exit_code == 3
    assert sentinel not in json.dumps(app.state.store.all("SELECT * FROM connections"))
    # Without the configure grant, even an admin-role token cannot register.
    monkeypatch.setenv("KW_AGENT_TOKEN", noconfig_file.read_text())
    r = run(runner, "connectors", "add", "--label", "Denied", "--driver", "recording",
            "--endpoint", "https://192.0.2.12", "--secret-ref", "hypervisors/denied",
            "--secrets-provider", "test-vault")
    assert r.exit_code == 3, (r.exit_code, r.output, r.stderr)
    assert run(runner, "keys", "generate", "--name", "still-no").exit_code == 3
    assert len(json.loads(run(runner, "--json", "connectors", "list").output)) == 2
    # Human path intact.
    monkeypatch.delenv("KW_AGENT_TOKEN")
    monkeypatch.setenv("KW_PASSWORD", bootstrap(app.state.state_dir)["admin"])
    assert json.loads(run(runner, "--json", "keys", "revoke", key["id"]).output
                      )["enabled"] is False


def test_providers_list_and_bind(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    import json
    r = run(runner, "--json", "providers", "list")
    assert r.exit_code == 0, r.output
    listing = json.loads(r.output)
    assert "bindings" in listing and "available" in listing
    # Bind a builtin capability; the server validates and persists the binding.
    r = run(runner, "--json", "providers", "bind", "--kind", "diagnostics", "--name", "rules")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["bindings"]["diagnostics"] == "rules"
    # Readback through the API confirms the persisted binding.
    assert app.state.provider_bindings["diagnostics"] == "rules"


def test_auth_passwd_changes_the_admin_password_and_clears_the_warning(env, monkeypatch):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    # Fresh install: the development default is still active.
    assert "development password" in run(runner, "auth", "whoami").output

    new_password = "a long enough passphrase 1"
    r = runner.invoke(cli, ["auth", "passwd"], input=f"{new_password}\n{new_password}\n")
    assert r.exit_code == 0, r.output

    # The plaintext line is gone and only the new password authenticates.
    assert 'admin:admin' not in (app.state.state_dir / 'bootstrap-credentials.txt').read_text()
    assert app.state.users.authenticate('admin', 'admin') is None
    assert app.state.users.authenticate('admin', new_password) is not None

    # Sign back in with the new password: the warning is gone.
    monkeypatch.setenv("KW_PASSWORD", new_password)
    run(runner, "auth", "login", "--username", "admin")
    assert "development password" not in run(runner, "auth", "whoami").output
