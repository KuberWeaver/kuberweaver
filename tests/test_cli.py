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

    app = make_app(tmp_path, monkeypatch)
    boundaries(app)
    # Real encrypted local vault, writable, bound as the default secrets source.
    from kuberweaver.local_vault import initialize, LocalVaultProvider
    vault_dir = tmp_path / "vault"
    initialize(vault_dir)
    app.state.registry.register("secrets", "test-vault",
                                LocalVaultProvider(vault_dir, allow_write=True), {})
    app.state.provider_bindings["secrets"] = "test-vault"
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
