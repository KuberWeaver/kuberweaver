"""Local-only OpenBao contract: Docker is always injected, HTTP uses fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
from backend.secrets import SecretError


def load_setup():
    path = ROOT / "scripts/setup_local_bao.py"
    assert path.is_file(), "local OpenBao setup is not implemented"
    spec = importlib.util.spec_from_file_location("setup_local_bao", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_layout_is_additive_and_rejects_redirects(tmp_path):
    setup = load_setup()
    state = tmp_path / "state"
    state.mkdir()
    state.chmod(0o755)
    unrelated = state / "config/providers.json"
    unrelated.parent.mkdir()
    unrelated.write_text('{"secrets":"openbao"}')
    local = setup.LocalBao(state)
    local.prepare()
    assert (state / "local-bao").stat().st_mode & 0o777 == 0o700
    assert local.raft.stat().st_mode & 0o777 == 0o700
    assert local.plugin_dir.stat().st_mode & 0o777 == 0o700
    assert state.stat().st_mode & 0o777 == 0o755
    assert unrelated.read_text() == '{"secrets":"openbao"}'
    alias = tmp_path / "alias"
    alias.symlink_to(state, target_is_directory=True)
    with pytest.raises(SecretError):
        setup.LocalBao(alias / "nested").prepare()
    local.raft.rmdir()
    local.raft.symlink_to(unrelated.parent, target_is_directory=True)
    with pytest.raises(SecretError):
        local.prepare()
    assert unrelated.read_text() == '{"secrets":"openbao"}'


def compatible_container(setup, local, *, running=True):
    return {
        "Id": "test-owned-id", "Name": "/kw-openbao-v2", "Image": "sha256:test-image-id",
        "State": {"Running": running},
        "Config": {"Labels": {setup.LABEL: setup.OWNER}, "Image": setup.IMAGE,
                   "User": f"{os.getuid()}:{os.getgid()}", "Entrypoint": ["/usr/bin/bao"],
                   "Cmd": ["server", "-config=/bao/config/bao.hcl"]},
        "HostConfig": {"CapDrop": ["ALL"], "CapAdd": None, "Privileged": False,
                       "SecurityOpt": ["no-new-privileges:true"], "NetworkMode": "default",
                       "PublishAllPorts": False,
                       "PortBindings": {"8200/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(local.port)}]}},
        "Mounts": [{"Type": "bind", "Source": str(local.raft), "Destination": "/bao/data", "RW": True},
                   {"Type": "bind", "Source": str(local.config), "Destination": "/bao/config/bao.hcl", "RW": False}],
    }


@pytest.mark.parametrize("network", ["default", "bridge"])
def test_default_bridge_network_aliases_are_compatible(tmp_path, network):
    """Docker's live inspect reports bridge for the default network."""
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    item = compatible_container(setup, local)
    item["HostConfig"]["NetworkMode"] = network
    local.validate(item, {"Id": item["Image"]})


@pytest.mark.parametrize("network", [None, "", "host", "none", "container:foreign", "custom-bridge", "Bridge", " bridge"])
def test_other_network_modes_remain_incompatible(tmp_path, network):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    item = compatible_container(setup, local)
    item["HostConfig"]["NetworkMode"] = network
    with pytest.raises(SecretError, match="incompatible"):
        local.validate(item, {"Id": item["Image"]})


class FakeDocker:
    """Only Docker's subprocess boundary is replaced; never runs the daemon."""
    def __init__(self, setup, local, item=None):
        self.setup, self.local, self.item, self.calls = setup, local, item, []

    def __call__(self, argv, **kwargs):
        from types import SimpleNamespace
        assert argv[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
        assert kwargs["capture_output"] is True and kwargs["text"] is True
        args = argv[3:]
        self.calls.append(args)
        if args[:2] == ["image", "inspect"]:
            value = [{"Id": "sha256:test-image-id", "RepoDigests": [self.setup.IMAGE]}]
        elif args[:2] == ["container", "inspect"]:
            if self.item is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="Error: No such container: kw-openbao-v2")
            value = [self.item]
        elif args[0] == "run":
            self.item = compatible_container(self.setup, self.local)
            return SimpleNamespace(returncode=0, stdout="test-owned-id", stderr="")
        elif args == ["start", "kw-openbao-v2"]:
            self.item["State"]["Running"] = True
            return SimpleNamespace(returncode=0, stdout="kw-openbao-v2", stderr="")
        else:
            raise AssertionError(f"unexpected Docker operation {args}")
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")


def test_new_container_is_digest_pinned_private_and_hardened(tmp_path, monkeypatch):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    docker = FakeDocker(setup, local)
    monkeypatch.setattr(setup.subprocess, "run", docker)
    item = local.container(allow_start=True)
    assert item["State"]["Running"] is True
    commands = [c for c in docker.calls if c[0] == "run"]
    assert len(commands) == 1
    command = commands[0]
    for flag, value in [("--name", "kw-openbao-v2"), ("--publish", "127.0.0.1:19200:8200"),
                        ("--user", f"{os.getuid()}:{os.getgid()}"), ("--cap-drop", "ALL"),
                        ("--security-opt", "no-new-privileges:true"), ("--entrypoint", "/usr/bin/bao")]:
        assert command[command.index(flag) + 1] == value
    assert command[-3:] == [setup.IMAGE, "server", "-config=/bao/config/bao.hcl"]
    assert setup.IMAGE == "openbao/openbao@sha256:5046911075e137e154ce49ab051502f4ec7cf367d484ae4a4dce665d9b90561f"
    assert "-dev" not in command and "--privileged" not in command
    assert f"type=bind,src={local.config},dst=/bao/config/bao.hcl,readonly" in command
    assert f"type=bind,src={local.raft},dst=/bao/data" in command
    config = local.config.read_text()
    assert 'storage "raft"' in config and 'cluster_addr = "https://127.0.0.1:8201"' in config
    assert 'api_addr = "http://127.0.0.1:19200"' in config and "mlock" not in config
    assert local.config.stat().st_mode & 0o777 == 0o600
    snapshot = list(docker.calls)
    local.container(allow_start=True)
    assert [c for c in docker.calls[len(snapshot):] if c[0] in ("run", "start")] == []


def test_foreign_listener_blocks_new_or_stopped_container(tmp_path, monkeypatch):
    import socket
    setup = load_setup()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        local = setup.LocalBao(tmp_path / "state", port=listener.getsockname()[1])
        local.prepare()
        docker = FakeDocker(setup, local)
        monkeypatch.setattr(setup.subprocess, "run", docker)
        with pytest.raises(SecretError, match="occupied"):
            local.container(allow_start=True)
        assert not any(c[0] in ("run", "start") for c in docker.calls)
        setup.private_write(local.config, local.hcl)
        docker.item = compatible_container(setup, local, running=False)
        with pytest.raises(SecretError, match="occupied"):
            local.container(allow_start=True)
        assert not any(c[0] in ("run", "start") for c in docker.calls)


@pytest.fixture
def http_api():
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    @contextmanager
    def serve(dispatch):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_api(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length)) if length else None
                reply = dispatch(self.command, self.path, self.headers.get("X-Vault-Token"), payload)
                if reply is None:
                    self.close_connection = True
                    return
                status, body, headers = reply
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

            do_GET = do_POST = do_PUT = do_DELETE = do_LIST = handle_api

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    return serve


def test_admin_api_ignores_proxies_and_never_redirects_root(tmp_path, monkeypatch, http_api):
    setup = load_setup()
    calls = []

    def dispatch(method, path, token, payload):
        calls.append((method, path, token))
        if path == "/v1/redirect":
            return 307, b"upstream-private", {"Location": "/v1/stolen"}
        return 200, {"initialized": True}, {}

    with http_api(dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("NO_PROXY", "")
        assert local.api("sys/init") == {"initialized": True}
        root_token = "test-root-private"
        with pytest.raises(SecretError) as caught:
            local.api("redirect", token=root_token)
    assert calls == [("GET", "/v1/sys/init", None), ("GET", "/v1/redirect", "test-root-private")]
    import traceback
    rendered = "".join(traceback.format_exception(caught.value))
    assert "upstream-private" not in rendered and "test-root-private" not in rendered


class BaoFixture:
    """Deterministic HTTP protocol fixture, NOT a live OpenBao proof."""
    def __init__(self):
        self.initialized, self.sealed, self.unseal_keys = False, True, []
        self.calls = []
        self.root = "test-root-private"
        self.shares = ["test-share-one", "test-share-two", "test-share-three"]
        self.mounts, self.policy, self.tokens, self.values = {}, None, {}, {}
        self.token_counter = 0

    def dispatch(self, method, path, token, payload):
        self.calls.append((method, path, token, payload))
        if path == "/v1/sys/init":
            if method == "GET":
                return 200, {"initialized": self.initialized}, {}
            if self.initialized:
                return 400, {"errors": ["must not reinitialize"]}, {}
            assert method == "PUT" and payload == {"secret_shares": 3, "secret_threshold": 2}
            self.initialized = True
            return 200, {"keys": self.shares, "root_token": self.root}, {}
        if path == "/v1/sys/seal-status":
            return 200, {"sealed": self.sealed, "initialized": self.initialized}, {}
        if path == "/v1/sys/unseal":
            assert payload["key"] in self.shares
            self.unseal_keys.append(payload["key"])
            self.sealed = len(set(self.unseal_keys)) < 2
            return 200, {"sealed": self.sealed}, {}
        if self.sealed:
            return 503, {"errors": ["sealed"]}, {}
        if token == self.root:
            if path == "/v1/sys/mounts":
                return 200, {"data": self.mounts}, {}
            if path == "/v1/sys/mounts/secret":
                self.mounts["secret/"] = payload
                return 204, b"", {}
            if path == "/v1/sys/policies/acl/kuberweaver-v2-connections":
                if method == "PUT":
                    self.policy = payload["policy"]
                    return 204, b"", {}
                return 200, {"data": {"policy": self.policy}}, {}
            if path == "/v1/auth/token/create-orphan":
                self.token_counter += 1
                created = f"test-app-private-{self.token_counter}"
                self.tokens[created] = {"policies": payload["policies"], "ttl": 604800,
                                        "explicit_max_ttl": 604800, "orphan": True,
                                        "renewable": payload["renewable"], "identity_policies": []}
                return 200, {"auth": {"client_token": created}}, {}
            if path == "/v1/auth/token/lookup":
                item = self.tokens.get(payload["token"])
                return (200, {"data": item}, {}) if item else (403, b"private denial", {})
            if path == "/v1/auth/token/revoke":
                self.tokens.pop(payload["token"], None)
                return 204, b"", {}
        item = self.tokens.get(token)
        if not item or item["ttl"] <= 0:
            return 403, b"private denial", {}
        expected = 'path "secret/data/kuberweaver/connections/*" { capabilities = ["read", "create", "update"] }\n'
        if (self.policy != expected or not path.startswith("/v1/secret/data/kuberweaver/connections/")
                or method not in ("GET", "POST")):
            return 403, b"private denial", {}
        if method == "POST":
            self.values[path] = payload["data"]["value"]
            return 200, {"data": {"version": 1}}, {}
        return 200, {"data": {"data": {"value": self.values[path]}}}, {}


def test_three_share_bootstrap_unseals_and_is_reusable(tmp_path, http_api, capsys):
    setup = load_setup()
    fixture = BaoFixture()
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        assert local.bootstrap(allow_init=True, fresh=True) == fixture.root
        saved = local.bootstrap_file.read_bytes()
        assert json.loads(saved) == {"keys": fixture.shares, "root_token": fixture.root}
        assert local.bootstrap_file.stat().st_mode & 0o777 == 0o600
        assert fixture.unseal_keys == fixture.shares[:2]
        assert local.bootstrap(allow_init=True, fresh=False) == fixture.root
        assert local.bootstrap_file.read_bytes() == saved
    assert len([call for call in fixture.calls if call[:2] == ("PUT", "/v1/sys/init")]) == 1
    assert capsys.readouterr() == ("", "")


def test_connections_only_policy_token_and_real_provider_roundtrip(tmp_path, http_api, capsys):
    setup = load_setup()
    fixture = BaoFixture()
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        root = local.bootstrap(allow_init=True, fresh=True)
        local.configure(root)
        token = local.token_file.read_bytes()
        assert local.token_file.stat().st_mode & 0o777 == 0o600
        evidence = local.verify(root, write=True)
        assert evidence["setup_roundtrip"] is True
        assert evidence["scope"] == "secret/data/kuberweaver/connections/*"
        assert evidence["out_of_scope_denied"] and evidence["admin_denied"]
        local.configure(root)
        assert local.token_file.read_bytes() == token
        assert fixture.token_counter == 1
        readonly_start = len(fixture.calls)
        local.configure(root, allow_write=False)
        local.verify(root)
        assert all(call[0] == "GET" or call[1] == "/v1/auth/token/lookup"
                   or call[2] == token.decode() for call in fixture.calls[readonly_start:])
    assert fixture.policy == 'path "secret/data/kuberweaver/connections/*" { capabilities = ["read", "create", "update"] }\n'
    assert fixture.mounts == {"secret/": {"type": "kv", "options": {"version": "2"}}}
    create = next(call[3] for call in fixture.calls if call[1] == "/v1/auth/token/create-orphan")
    assert create == {"policies": ["kuberweaver-v2-connections"], "no_default_policy": True,
                      "ttl": "168h", "explicit_max_ttl": "168h", "renewable": False,
                      "display_name": "kuberweaver-v2-connections"}
    rendered = json.dumps(evidence)
    assert fixture.root not in rendered and token.decode() not in rendered
    assert all(marker not in rendered for marker in fixture.values.values())
    assert str(local.token_file) not in rendered and local.endpoint not in rendered
    assert capsys.readouterr() == ("", "")


def test_expired_token_requires_explicit_rotation_and_revokes_old(tmp_path, http_api):
    setup = load_setup()
    fixture = BaoFixture()
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        root = local.bootstrap(allow_init=True, fresh=True)
        local.configure(root)
        old = local.token_file.read_text()
        fixture.tokens[old]["ttl"] = 0
        with pytest.raises(SecretError, match="explicit rotation"):
            local.configure(root)
        assert fixture.token_counter == 1 and local.token_file.read_text() == old
        local.configure(root, rotate_token=True)
        assert fixture.token_counter == 2
        assert local.token_file.read_text() != old
        assert old not in fixture.tokens
        revoked_index = next(i for i, call in enumerate(fixture.calls) if call[1] == "/v1/auth/token/revoke")
        assert any(call[1] == "/v1/auth/token/lookup" and call[3] == {"token": old}
                   for call in fixture.calls[revoked_index + 1:])


def test_setup_publishes_only_opt_in_extension_config_and_verifies_readonly(tmp_path, monkeypatch, http_api):
    setup = load_setup()
    fixture = BaoFixture()
    state = tmp_path / "state"
    global_config = state / "config/providers.json"
    global_config.parent.mkdir(parents=True)
    global_config.write_bytes(b'{"secrets":"openbao"}')
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(state, port=port)
        local.prepare()
        setup.private_write(local.config, local.hcl)
        docker = FakeDocker(setup, local, compatible_container(setup, local))
        monkeypatch.setattr(setup.subprocess, "run", docker)
        evidence = local.run("setup")
        config_file = local.plugin_dir / "provider.json"
        config = json.loads(config_file.read_bytes())
        assert config == {"endpoint": local.endpoint, "approved": {}, "token_file": str(local.token_file),
                          "namespace": "kuberweaver/connections", "allow_write": True, "allow_loopback_http": True}
        assert config_file.stat().st_mode & 0o777 == 0o600
        snapshot = {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()}
        fixture.calls.clear()
        readonly_evidence = local.run("verify")
        assert readonly_evidence["sealed"] is False and readonly_evidence["setup_marker_read"] is True
        assert readonly_evidence["setup_roundtrip"] is False
        assert evidence["extension_activation_changed"] is False and "extension_enabled" not in evidence
        assert snapshot == {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()}
        assert not any(c[1] == "/v1/sys/unseal" for c in fixture.calls)
        assert local.run("setup")["initialized"] is True
        assert fixture.token_counter == 1
        assert not any(c[0] in ("run", "start") for c in docker.calls)
    assert global_config.read_bytes() == b'{"secrets":"openbao"}'
    assert not (state / "extensions").exists() and not (state / "extensions.json").exists()
    assert evidence["container"] == "kw-openbao-v2" and evidence["id"] == "test-owned-id"
    assert evidence["image"] == setup.IMAGE and evidence["credentials_written"] is False
    assert fixture.root not in json.dumps(evidence)


def install_example(tmp_path):
    import shutil
    source = ROOT / "extensions/openbao-local"
    assert source.is_dir(), "optional local OpenBao extension is not implemented"
    extensions = tmp_path / "extensions"
    shutil.copytree(source, extensions / "openbao-local", ignore=shutil.ignore_patterns("__pycache__"))
    return extensions


def test_missing_extension_config_registers_safe_unconfigured_provider(tmp_path):
    from backend.extensions import ExtensionRegistry, validate_package
    extensions = install_example(tmp_path)
    manifest = validate_package(extensions / "openbao-local")
    assert set(manifest["capabilities"]) == {"providers:secrets", "data:read-write"}
    registry = ExtensionRegistry()
    assert registry.load_directory(extensions)[0]["status"] == "disabled"
    assert registry.list() == []
    assert registry.load_directory(extensions, {"openbao-local"})[0]["status"] == "loaded"
    assert registry.get("secrets", "openbao-local") is None
    metadata = registry.list("secrets")[0]
    assert metadata["available"] is False and metadata["status"] == "unconfigured"
    assert metadata["reference_only"] is True


@pytest.mark.parametrize("operation", ["store", "resolve"])
def test_registered_extension_reserves_setup_marker_before_network(tmp_path, monkeypatch, http_api, operation):
    from backend.extensions import ExtensionRegistry
    from backend.secrets import SecretValue
    import traceback

    setup = load_setup()
    fixture = BaoFixture()
    extensions = install_example(tmp_path)
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(tmp_path, port=port)
        local.prepare()
        setup.private_write(local.config, local.hcl)
        monkeypatch.setattr(setup.subprocess, "run", FakeDocker(setup, local, compatible_container(setup, local)))
        assert local.run("setup")["setup_roundtrip"] is True
        registry = ExtensionRegistry()
        assert registry.load_directory(extensions, {"openbao-local"})[0]["status"] == "loaded"
        provider = registry.get("secrets", "openbao-local")
        calls, values = list(fixture.calls), dict(fixture.values)
        credential = SecretValue(b"fixture-only-connection-credential")
        try:
            with pytest.raises(SecretError, match="reserved") as caught:
                if operation == "store":
                    provider.store("setup-check", credential)
                else:
                    with provider.resolve("setup-check"):
                        pass
        finally:
            credential.close()
        assert fixture.calls == calls
        assert fixture.values == values
        rendered = "".join(traceback.format_exception(caught.value))
        for forbidden in ("fixture-only-connection-credential", fixture.root, local.token_file.read_text(),
                          local.endpoint, str(local.token_file), *values.values()):
            assert forbidden not in rendered


@pytest.mark.parametrize("reference", ["test-connection", "setup-check-other", "setup-check/connection", "connection/setup-check"])
def test_configured_extension_reuses_provider_without_network_at_registration(tmp_path, monkeypatch, http_api, reference):
    from backend.extensions import ExtensionRegistry
    from backend.secrets import OpenBaoProvider, SecretValue
    setup = load_setup()
    fixture = BaoFixture()
    extensions = install_example(tmp_path)
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(tmp_path, port=port)
        local.prepare()
        setup.private_write(local.config, local.hcl)
        monkeypatch.setattr(setup.subprocess, "run", FakeDocker(setup, local, compatible_container(setup, local)))
        local.run("setup")
        count = len(fixture.calls)
        registry = ExtensionRegistry()
        assert registry.load_directory(extensions, {"openbao-local"})[0]["status"] == "loaded"
        assert len(fixture.calls) == count
        provider = registry.get("secrets", "openbao-local")
        assert isinstance(provider, OpenBaoProvider)
        marker = SecretValue(b"fixture-only-connection-marker")
        try:
            provider.store(reference, marker)
            with provider.resolve(reference) as value:
                assert value.bytes() == marker.bytes()
        finally:
            marker.close()
        connection_path = "/v1/secret/data/kuberweaver/connections/" + reference
        assert [call[:2] for call in fixture.calls[count:]] == [
            ("POST", connection_path), ("GET", connection_path), ("GET", connection_path)]
        assert local.run("setup")["setup_roundtrip"] is True
        assert fixture.values[connection_path] == "fixture-only-connection-marker"
        with provider.resolve(reference) as value:
            assert value.bytes() == b"fixture-only-connection-marker"
        assert local.run("verify")["setup_marker_read"] is True
        descriptor = provider.describe()
        assert descriptor["can_store"] and descriptor["namespaced_references"]
        assert descriptor["approved_names"] == []
    metadata = registry.list("secrets")[0]
    assert metadata["available"] and metadata["status"] == "configured" and metadata["reference_only"]
    rendered = json.dumps(registry.list())
    assert local.endpoint not in rendered and str(local.token_file) not in rendered
    assert "test-root-private" not in rendered and "test-app-private" not in rendered


@pytest.mark.parametrize("fault", ["invalid-json", "config-permissions", "config-symlink", "ancestor-symlink",
    "token-permissions", "token-symlink", "token-absent", "token-invalid", "endpoint", "namespace",
    "approved", "read-only", "extra-mount", "loopback-disabled", "bootstrap-token"])
def test_extension_bad_config_is_unavailable_not_a_portal_failure(tmp_path, fault):
    from backend.extensions import ExtensionRegistry
    setup = load_setup()
    extensions = install_example(tmp_path)
    local = setup.LocalBao(tmp_path)
    local.prepare()
    setup.private_write(local.token_file, b"test-app-private")
    config = {"endpoint": local.endpoint, "approved": {}, "token_file": str(local.token_file),
              "namespace": "kuberweaver/connections", "allow_write": True, "allow_loopback_http": True}
    mutations = {"endpoint": {"endpoint": "https://remote-private.example"},
                 "namespace": {"namespace": "kuberweaver"}, "approved": {"approved": {"broad": "kuberweaver/outside"}},
                 "read-only": {"allow_write": False}, "extra-mount": {"mount": "elsewhere"},
                 "loopback-disabled": {"allow_loopback_http": False},
                 "bootstrap-token": {"token_file": str(local.bootstrap_file)}}
    config.update(mutations.get(fault, {}))
    path = local.plugin_dir / "provider.json"
    setup.private_write(path, json.dumps(config).encode())
    if fault == "invalid-json":
        path.write_bytes(b"invalid-private-json")
    elif fault == "config-permissions":
        path.chmod(0o644)
    elif fault == "config-symlink":
        path.unlink()
        path.symlink_to(local.token_file)
    elif fault == "ancestor-symlink":
        real = local.plugin_dir.with_name("moved-private")
        local.plugin_dir.rename(real)
        local.plugin_dir.symlink_to(real, target_is_directory=True)
    elif fault == "token-permissions":
        local.token_file.chmod(0o644)
    elif fault == "token-symlink":
        local.token_file.unlink()
        local.token_file.symlink_to(path)
    elif fault == "token-absent":
        local.token_file.unlink()
    elif fault == "token-invalid":
        local.token_file.write_bytes(b"not a token\ninvalid")
    registry = ExtensionRegistry()
    report = registry.load_directory(extensions, {"openbao-local"})[0]
    assert report["status"] == "loaded"
    assert registry.get("secrets", "openbao-local") is None
    metadata = registry.list("secrets")[0]
    assert metadata["status"] == "unavailable" and metadata["available"] is False
    rendered = json.dumps(registry.list())
    assert "private" not in rendered and str(tmp_path) not in rendered


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, -1, True, "19200", "19200:8200"])
def test_port_validation_happens_before_writes(tmp_path, port):
    setup = load_setup()
    with pytest.raises(SecretError, match="port"):
        setup.LocalBao(tmp_path / "state", port=port)
    assert not (tmp_path / "state").exists()


def test_cli_has_safe_actions_defaults_and_redacts_unexpected_errors(tmp_path, monkeypatch, capsys):
    import subprocess
    setup = load_setup()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    help_result = subprocess.run([sys.executable, str(ROOT / "scripts/setup_local_bao.py"), "--help"],
                                 text=True, capture_output=True, env=env)
    assert help_result.returncode == 0 and "--state-dir" in help_result.stdout and "--port" in help_result.stdout
    assert "setup" in help_result.stdout and "verify" in help_result.stdout and "unseal" in help_result.stdout
    assert not (tmp_path / "state").exists()
    seen = []

    def run(local, action="setup", *, rotate_token=False):
        seen.append((local.state, local.port, action, rotate_token))
        return {"ok": True, "credentials_written": False}

    monkeypatch.setattr(setup.LocalBao, "run", run)
    assert setup.main(["verify", "--state-dir", str(tmp_path / "state"), "--port", "19201"]) == 0
    assert seen == [(tmp_path / "state", 19201, "verify", False)]
    assert json.loads(capsys.readouterr().out) == {"ok": True, "credentials_written": False}
    with pytest.raises(SystemExit) as caught:
        setup.main(["verify", "--rotate-token"])
    assert caught.value.code == 2
    capsys.readouterr()

    def broken(*args, **kwargs):
        raise RuntimeError("root-and-shares-private-upstream")

    monkeypatch.setattr(setup.LocalBao, "run", broken)
    assert setup.main(["setup", "--state-dir", str(tmp_path / "state")]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "private-upstream" not in output.err and "Traceback" not in output.err


def test_rotation_accepts_invalid_token_lookup_400_but_requires_denial_readback(tmp_path, http_api):
    setup = load_setup()
    fixture = BaoFixture()

    def dispatch(method, path, token, payload):
        status, body, headers = fixture.dispatch(method, path, token, payload)
        if path == "/v1/auth/token/lookup" and status == 403:
            return 400, {"errors": ["bad token private"]}, headers
        return status, body, headers

    with http_api(dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        root = local.bootstrap(allow_init=True, fresh=True)
        local.configure(root)
        old = local.token_file.read_text()
        fixture.tokens.pop(old)
        with pytest.raises(SecretError, match="explicit rotation"):
            local.configure(root)
        local.configure(root, rotate_token=True)
        assert old not in fixture.tokens and fixture.token_counter == 2


@pytest.mark.parametrize("action,rotate", [("restart", False), ("verify", True), ("unseal", True)])
def test_invalid_operation_is_rejected_before_state_or_docker(tmp_path, monkeypatch, action, rotate):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")

    def forbidden(*args, **kwargs):
        pytest.fail("invalid operation must not reach Docker")

    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    with pytest.raises(SecretError, match="action|rotation"):
        local.run(action, rotate_token=rotate)
    assert not local.state.exists()


@pytest.mark.parametrize("action", ["verify", "unseal"])
def test_non_setup_actions_do_not_create_missing_state(tmp_path, monkeypatch, action):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: pytest.fail("no Docker without owned state"))
    with pytest.raises(SecretError):
        local.run(action)
    assert not local.state.exists()


@pytest.mark.parametrize("fault", ["label", "image", "image-id", "port", "user", "config-rw", "raft-source",
    "extra-mount", "command", "entrypoint", "caps", "privileged", "security", "network", "environment", "devices"])
def test_unowned_or_incompatible_container_is_never_started(tmp_path, monkeypatch, fault):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    item = compatible_container(setup, local, running=False)
    mutations = {
        "label": lambda: item["Config"]["Labels"].update({setup.LABEL: "foreign"}),
        "image": lambda: item["Config"].update(Image="openbao/openbao:latest"),
        "image-id": lambda: item.update(Image="sha256:foreign"),
        "port": lambda: item["HostConfig"]["PortBindings"]["8200/tcp"][0].update(HostIp="0.0.0.0"),
        "user": lambda: item["Config"].update(User="root"),
        "config-rw": lambda: item["Mounts"][1].update(RW=True),
        "raft-source": lambda: item["Mounts"][0].update(Source=str(tmp_path / "foreign")),
        "extra-mount": lambda: item["Mounts"].append({"Type": "bind", "Source": "/", "Destination": "/host", "RW": True}),
        "command": lambda: item["Config"].update(Cmd=["server", "-dev"]),
        "entrypoint": lambda: item["Config"].update(Entrypoint=["sh", "-c"]),
        "caps": lambda: item["HostConfig"].update(CapAdd=["IPC_LOCK"]),
        "privileged": lambda: item["HostConfig"].update(Privileged=True),
        "security": lambda: item["HostConfig"].update(SecurityOpt=[]),
        "network": lambda: item["HostConfig"].update(NetworkMode="host"),
        "environment": lambda: item["Config"].update(Env=["BAO_LOCAL_CONFIG=foreign"]),
        "devices": lambda: item["HostConfig"].update(Devices=[{"PathOnHost": "/dev/sda"}]),
    }
    mutations[fault]()
    setup.private_write(local.config, local.hcl)
    docker = FakeDocker(setup, local, item)
    monkeypatch.setattr(setup.subprocess, "run", docker)
    with pytest.raises(SecretError):
        local.container(allow_start=True)
    assert not any(c[0] in ("run", "start", "stop", "restart", "rm") for c in docker.calls)
    assert item["State"]["Running"] is False


@pytest.mark.parametrize("name", ["config.hcl", "bootstrap.json", "app-token", "pending-revocation.json", "provider.json", "raft-link"])
def test_prepare_rejects_file_or_nested_raft_symlinks_before_docker(tmp_path, name):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched-private")
    if name == "provider.json":
        path = local.plugin_dir / name
    elif name == "raft-link":
        path = local.raft / "redirect"
    else:
        path = local.base / name
    path.symlink_to(outside)
    with pytest.raises(SecretError):
        local.prepare()
    assert outside.read_bytes() == b"untouched-private"


@pytest.mark.parametrize("name", ["state,readonly", "state\nline", "../redirect"])
def test_state_path_cannot_inject_mount_options_or_traverse(tmp_path, name):
    setup = load_setup()
    with pytest.raises(SecretError):
        setup.LocalBao(tmp_path / name)


@pytest.mark.parametrize("keys", ["abc", ["one", "one", "one"], ["one", "two"], ["one", "two", 3]])
def test_invalid_bootstrap_never_sends_unseal_material(tmp_path, keys):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    setup.private_write(local.bootstrap_file, json.dumps({"keys": keys, "root_token": "test-root"}).encode())
    calls = []

    def api(path, **kwargs):
        calls.append(path)
        assert path == "sys/init", "invalid bootstrap must not be used"
        return {"initialized": True}

    local.api = api
    with pytest.raises(SecretError, match="bootstrap"):
        local.bootstrap()
    assert calls == ["sys/init"]


def test_old_raft_without_bootstrap_is_never_started_or_reinitialized(tmp_path, monkeypatch):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    previous = local.raft / "raft.db"
    previous.write_bytes(b"fixture-old-raft-do-not-replace")
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: pytest.fail("old raft must not reach Docker"))
    with pytest.raises(SecretError, match="never reinitialize"):
        local.run("setup")
    assert previous.read_bytes() == b"fixture-old-raft-do-not-replace"


@pytest.mark.parametrize("initialized,fresh,existing", [(True, True, False), (False, False, False), (False, True, True)])
def test_inconsistent_recovery_never_initializes(tmp_path, initialized, fresh, existing):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    if existing:
        setup.private_write(local.bootstrap_file, b'{"root_token":"test-old","keys":["a","b","c"]}')
    calls = []

    def api(path, **kwargs):
        calls.append((path, kwargs))
        return {"initialized": initialized}

    local.api = api
    with pytest.raises(SecretError):
        local.bootstrap(allow_init=True, fresh=fresh)
    assert calls == [("sys/init", {})]


def test_only_owned_compatible_stopped_container_is_started_and_read_back(tmp_path, monkeypatch):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    setup.private_write(local.config, local.hcl)
    docker = FakeDocker(setup, local, compatible_container(setup, local, running=False))
    monkeypatch.setattr(setup.subprocess, "run", docker)
    assert local.container(allow_start=True)["State"]["Running"]
    assert [c for c in docker.calls if c[0] not in ("container", "image")] == [["start", "kw-openbao-v2"]]
    assert docker.calls[-1] == ["container", "inspect", "kw-openbao-v2"]


def test_setup_lock_prevents_any_docker_or_api_mutation(tmp_path, monkeypatch):
    import fcntl
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    lock_path = local.base / ".setup.lock"
    setup.private_write(lock_path, b"fixture")
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: pytest.fail("locked setup must not reach Docker"))
    with lock_path.open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SecretError, match="already running"):
            local.run("setup")


@pytest.mark.parametrize("status,body", [(200, b"not-private-json"), (200, b"[]"), (302, b"private-body"), (500, b"private-body")])
def test_bad_api_responses_never_include_upstream_body(tmp_path, http_api, status, body):
    setup = load_setup()
    with http_api(lambda *a: (status, body, {})) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        with pytest.raises(SecretError) as caught:
            local.api("sys/init")
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("field,value", [("policies", ["kuberweaver-v2-connections", "default"]),
    ("identity_policies", ["admin"]), ("orphan", False), ("renewable", True), ("ttl", 0),
    ("ttl", True), ("ttl", 604801), ("explicit_max_ttl", 0)])
def test_app_token_safety_checks_reject_added_privilege_or_ttl_drift(tmp_path, field, value):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    data = {"policies": [setup.POLICY_NAME], "identity_policies": [], "orphan": True,
            "renewable": False, "ttl": 604799, "explicit_max_ttl": 604800}
    data[field] = value
    local.api = lambda *a, **kw: {"data": data}
    assert not local.token_safe("test-root", "test-app")


@pytest.mark.parametrize("failure", ["kv1", "mount-readback", "policy-readback", "admin-allowed", "wrong-marker"])
def test_bad_server_contract_never_publishes_extension_config(tmp_path, monkeypatch, http_api, failure):
    setup = load_setup()
    fixture = BaoFixture()
    if failure == "kv1":
        fixture.mounts["secret/"] = {"type": "kv", "options": {"version": "1"}}

    def dispatch(method, path, token, payload):
        if failure == "admin-allowed" and path == "/v1/sys/mounts" and token in fixture.tokens:
            return 200, {}, {}
        status, body, headers = fixture.dispatch(method, path, token, payload)
        if failure == "mount-readback" and path == "/v1/sys/mounts":
            return 200, {"data": {}}, {}
        if failure == "policy-readback" and method == "GET" and path.startswith("/v1/sys/policies/"):
            return 200, {"data": {"policy": "different-private-policy"}}, {}
        if failure == "wrong-marker" and method == "GET" and path.endswith("/setup-check"):
            return 200, {"data": {"data": {"value": "different-private-marker"}}}, {}
        return status, body, headers

    with http_api(dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        setup.private_write(local.config, local.hcl)
        monkeypatch.setattr(setup.subprocess, "run", FakeDocker(setup, local, compatible_container(setup, local)))
        with pytest.raises(SecretError) as caught:
            local.run("setup")
        assert not (local.plugin_dir / "provider.json").exists()
        assert "private" not in str(caught.value)
    assert not any(c[0] == "DELETE" and c[2] == fixture.root for c in fixture.calls)
    if failure == "kv1":
        assert not any(c[1] == "/v1/sys/mounts/secret" for c in fixture.calls)


@pytest.mark.parametrize("failure", ["daemon", "timeout", "invalid-json"])
def test_docker_boundary_fails_closed_and_redacts_all_output(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()

    def runner(argv, **kwargs):
        if failure == "timeout":
            raise setup.subprocess.TimeoutExpired(argv, 1, output="private-stdout", stderr="private-stderr")
        return SimpleNamespace(returncode=1 if failure == "daemon" else 0,
                               stdout="invalid-private-json", stderr="private-stderr-daemon-failed")

    monkeypatch.setattr(setup.subprocess, "run", runner)
    with pytest.raises(SecretError) as caught:
        local.container(allow_start=True)
    import traceback
    assert "private" not in "".join(traceback.format_exception(caught.value))
    assert not local.bootstrap_file.exists()


def test_verify_never_unseals_but_unseal_restores_existing_marker_without_reseeding(tmp_path, monkeypatch, http_api):
    setup = load_setup()
    fixture = BaoFixture()
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        setup.private_write(local.config, local.hcl)
        monkeypatch.setattr(setup.subprocess, "run", FakeDocker(setup, local, compatible_container(setup, local)))
        local.run("setup")
        saved = dict(fixture.values)
        fixture.sealed, fixture.unseal_keys = True, []
        start = len(fixture.calls)
        with pytest.raises(SecretError, match="sealed"):
            local.run("verify")
        assert not any(c[1] == "/v1/sys/unseal" for c in fixture.calls[start:])
        evidence = local.run("unseal")
        assert evidence["sealed"] is False and evidence["setup_roundtrip"] is False
        assert fixture.values == saved and fixture.token_counter == 1
        assert all(call[0] == "GET" or call[1] in ("/v1/auth/token/lookup", "/v1/sys/unseal")
                   or call[2] in fixture.tokens for call in fixture.calls[start:])


def test_private_write_never_follows_parent_swapped_after_preflight(tmp_path, monkeypatch):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    safe_path, fdopen = setup.safe_path, setup.os.fdopen
    destinations = []
    swapped = False

    def race(path):
        nonlocal swapped
        checked = safe_path(path)
        if checked == local.token_file and not swapped:
            swapped = True
            local.base.rename(local.base.with_name("moved-owned"))
            local.base.symlink_to(outside, target_is_directory=True)
        return checked

    def observe_write(fd, *args, **kwargs):
        destinations.append(Path(os.readlink(f"/proc/self/fd/{fd}")).parent)
        return fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(setup, "safe_path", race)
    monkeypatch.setattr(setup.os, "fdopen", observe_write)
    with pytest.raises(SecretError):
        setup.private_write(local.token_file, b"fixture-only-private")
    assert outside not in destinations
    assert list(outside.iterdir()) == []


def test_private_write_collision_never_deletes_a_preexisting_file(tmp_path, monkeypatch):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    existing = local.base / ".local-bao-collision"
    existing.write_bytes(b"preexisting-not-owned-temporary")
    monkeypatch.setattr(setup.secrets, "token_hex", lambda size: "collision")
    with pytest.raises(SecretError):
        setup.private_write(local.token_file, b"fixture-only")
    assert existing.read_bytes() == b"preexisting-not-owned-temporary"
    assert not local.token_file.exists()


def test_recovery_rejects_nonprivate_parent_before_http(tmp_path):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    setup.private_write(local.token_file, b"fixture-new-private")
    setup.private_write(local.rotation_file, b'{"old":"fixture-old-private","new":"fixture-new-private"}')
    root = "fixture-root-private"
    local.base.chmod(0o755)
    local.api = lambda *a, **kw: pytest.fail("nonprivate recovery must not reach HTTP")
    with pytest.raises(SecretError):
        local.configure(root)


def test_rotation_retry_revokes_original_token_after_http_503(tmp_path, http_api, capsys):
    setup = load_setup()
    fixture = BaoFixture()
    refuse_revoke = False

    def dispatch(method, path, token, payload):
        if refuse_revoke and path == "/v1/auth/token/revoke":
            return 503, {"errors": ["private upstream refusal"]}, {}
        return fixture.dispatch(method, path, token, payload)

    with http_api(dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        root = local.bootstrap(allow_init=True, fresh=True)
        local.configure(root)
        original = local.token_file.read_text()
        refuse_revoke = True
        with pytest.raises(SecretError) as caught:
            local.configure(root, rotate_token=True)
        assert original not in str(caught.value)
        assert "private upstream refusal" not in str(caught.value)
        refuse_revoke = False
        start = len(fixture.calls)
        restarted = setup.LocalBao(local.state, port=port)
        restarted.configure(root, rotate_token=True)
        original_revoked = original not in fixture.tokens
        assert original_revoked, "successful retry must revoke the initial token, not only the intermediate token"
        events = ["create" if call[1] == "/v1/auth/token/create-orphan" else "revoke-original"
                  for call in fixture.calls[start:] if call[1] == "/v1/auth/token/create-orphan"
                  or (call[1] == "/v1/auth/token/revoke" and call[3] == {"token": original})]
        assert events[:2] == ["revoke-original", "create"]
        assert len(fixture.tokens) == 1
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("publication", ["before", "after"])
def test_setup_resumes_rotation_interrupted_at_token_publication(tmp_path, monkeypatch, http_api, capsys, publication):
    setup = load_setup()
    fixture = BaoFixture()
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        setup.private_write(local.config, local.hcl)
        docker = FakeDocker(setup, local, compatible_container(setup, local))
        monkeypatch.setattr(setup.subprocess, "run", docker)
        local.run("setup")
        original = local.token_file.read_text()
        write = setup.private_write

        def interrupt(path, value):
            if path == local.token_file:
                pending = json.loads(setup._protected_read(local.rotation_file))
                journal_matches = pending == {"old": original, "new": value.decode("ascii")}
                assert journal_matches, "both token references must be durable before publication"
                assert local.rotation_file.stat().st_mode & 0o777 == 0o600
                assert local.base.stat().st_mode & 0o777 == 0o700
                if publication == "before":
                    raise KeyboardInterrupt
                write(path, value)
                raise KeyboardInterrupt
            write(path, value)

        with monkeypatch.context() as interrupted:
            interrupted.setattr(setup, "private_write", interrupt)
            with pytest.raises(KeyboardInterrupt):
                local.run("setup", rotate_token=True)
        pending = json.loads(setup._protected_read(local.rotation_file))
        candidate = pending["new"]
        snapshot = {p.relative_to(local.state): p.read_bytes() for p in local.state.rglob("*") if p.is_file()}
        start = len(fixture.calls)
        with pytest.raises(SecretError, match="rotation"):
            setup.LocalBao(local.state, port=port).run("verify")
        assert snapshot == {p.relative_to(local.state): p.read_bytes() for p in local.state.rglob("*") if p.is_file()}
        assert all(call[0] == "GET" for call in fixture.calls[start:])
        restarted = setup.LocalBao(local.state, port=port)
        evidence = restarted.run("setup")
        resumed_candidate = restarted.token_file.read_text() == candidate
        assert resumed_candidate, "setup must resume the recorded rotation, not create another token"
        assert fixture.token_counter == 2
        original_revoked = original not in fixture.tokens
        assert original_revoked, "publication interruption must not lose the original revocation"
        assert json.loads(setup._protected_read(local.rotation_file)) == {}
        assert evidence["setup_roundtrip"] is True
        assert restarted.run("setup")["initialized"] is True
        assert restarted.token_file.read_text() == candidate
        assert fixture.token_counter == 2
        assert restarted.run("verify")["setup_roundtrip"] is False
        rendered = json.dumps(evidence)
        assert all(value not in rendered for value in (original, candidate, fixture.root))
        assert not any(call[0] in ("run", "start") for call in docker.calls)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("write", [False, True])
def test_verify_refuses_pending_rotation_without_mutations(tmp_path, http_api, write):
    setup = load_setup()
    fixture = BaoFixture()
    refuse_revoke = False

    def dispatch(method, path, token, payload):
        if refuse_revoke and path == "/v1/auth/token/revoke":
            return 503, b"private refusal", {}
        return fixture.dispatch(method, path, token, payload)

    with http_api(dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        root = local.bootstrap(allow_init=True, fresh=True)
        local.configure(root)
        local.verify(root, write=True)
        refuse_revoke = True
        with pytest.raises(SecretError):
            local.configure(root, rotate_token=True)
        snapshot = {p.name: p.read_bytes() for p in local.base.iterdir() if p.is_file()}
        calls, values = list(fixture.calls), dict(fixture.values)
        with pytest.raises(SecretError, match="rotation incomplete"):
            local.verify(root, write=write)
        assert calls == fixture.calls
        assert values == fixture.values
        assert snapshot == {p.name: p.read_bytes() for p in local.base.iterdir() if p.is_file()}


@pytest.mark.parametrize("fault", ["permissions", "hardlink", "directory", "base-permissions"])
def test_pending_revocation_protection_is_checked_before_docker(tmp_path, monkeypatch, fault):
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    setup.private_write(local.rotation_file, b'{"old":"fixture-old-private","new":"fixture-new-private"}')
    if fault == "permissions":
        local.rotation_file.chmod(0o644)
    elif fault == "hardlink":
        os.link(local.rotation_file, local.base / "other-link")
    elif fault == "directory":
        local.rotation_file.unlink()
        local.rotation_file.mkdir(mode=0o700)
    else:
        local.base.chmod(0o755)
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: pytest.fail("unprotected recovery must not reach Docker"))
    with pytest.raises(SecretError):
        local.run("setup")


@pytest.mark.parametrize("failure", ["revoke-503", "revoke-disconnect", "readback-503", "readback-disconnect", "readback-200"])
def test_setup_recovers_ambiguous_revocation_without_claiming_success(tmp_path, monkeypatch, http_api, capsys, failure):
    setup = load_setup()
    fixture = BaoFixture()
    inject, original = False, None

    def dispatch(method, path, token, payload):
        absent = path == "/v1/auth/token/revoke" and payload["token"] not in fixture.tokens
        reply = fixture.dispatch(method, path, token, payload)
        if absent:
            return 400, {"errors": ["private already revoked"]}, {}
        if inject and path == "/v1/auth/token/revoke" and failure.startswith("revoke-"):
            return None if failure.endswith("disconnect") else (503, b"private ambiguous refusal", {})
        if (inject and path == "/v1/auth/token/lookup" and payload["token"] == original
                and failure.startswith("readback-")):
            if failure.endswith("disconnect"):
                return None
            return (200, {"data": {"ttl": 604800}}, {}) if failure.endswith("200") else (503, b"private ambiguous readback", {})
        return reply

    with http_api(dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        setup.private_write(local.config, local.hcl)
        docker = FakeDocker(setup, local, compatible_container(setup, local))
        monkeypatch.setattr(setup.subprocess, "run", docker)
        local.run("setup")
        original = local.token_file.read_text()
        args = ["--state-dir", str(local.state), "--port", str(port)]
        inject = True
        assert setup.main(["setup", *args, "--rotate-token"]) == 1
        failed = capsys.readouterr()
        assert failed.out == "" and failed.err
        candidate = local.token_file.read_text()
        saved = setup._protected_read(local.rotation_file)
        pending_matches = json.loads(saved) == {"old": original, "new": candidate}
        assert pending_matches, "an ambiguous revoke/readback must retain the original token reference"
        revoked = original not in fixture.tokens
        assert revoked, "the fixture processes revocation before losing its acknowledgement"
        start = len(fixture.calls)
        assert setup.main(["verify", *args]) == 1
        refused = capsys.readouterr()
        assert refused.out == "" and "rotation incomplete" in refused.err
        assert local.rotation_file.read_bytes() == saved
        assert all(call[0] == "GET" for call in fixture.calls[start:])
        inject = False
        start = len(fixture.calls)
        assert setup.main(["setup", *args]) == 0
        completed = capsys.readouterr()
        evidence = json.loads(completed.out)
        assert completed.err == "" and evidence["setup_roundtrip"] is True
        assert fixture.token_counter == 2
        retained = local.token_file.read_text() == candidate
        assert retained, "recovery must retain the already published token"
        assert json.loads(setup._protected_read(local.rotation_file)) == {}
        assert not any(call[1] == "/v1/auth/token/create-orphan" for call in fixture.calls[start:])
        rendered = failed.err + refused.err + completed.out
        assert all(value not in rendered for value in (original, candidate, fixture.root, *fixture.shares,
                                                      "private ambiguous", "private already revoked"))
        assert not any(call[0] in ("run", "start") for call in docker.calls)


@pytest.mark.parametrize("interruption", ["before-revoke", "after-revoke", "before-settlement"])
def test_rotation_interrupted_during_revocation_resumes_from_disk(tmp_path, monkeypatch, http_api, capsys, interruption):
    setup = load_setup()
    fixture = BaoFixture()
    with http_api(fixture.dispatch) as port:
        local = setup.LocalBao(tmp_path / "state", port=port)
        local.prepare()
        root = local.bootstrap(allow_init=True, fresh=True)
        local.configure(root)
        original = local.token_file.read_text()
        api, write = local.api, setup.private_write

        def interrupt_api(path, **kwargs):
            if path == "auth/token/revoke" and interruption == "before-revoke":
                raise KeyboardInterrupt
            reply = api(path, **kwargs)
            if path == "auth/token/revoke" and interruption == "after-revoke":
                raise KeyboardInterrupt
            return reply

        def interrupt_write(path, value):
            if path == local.rotation_file and value == b"{}" and interruption == "before-settlement":
                raise KeyboardInterrupt
            write(path, value)

        with monkeypatch.context() as interrupted:
            interrupted.setattr(local, "api", interrupt_api)
            interrupted.setattr(setup, "private_write", interrupt_write)
            with pytest.raises(KeyboardInterrupt):
                local.configure(root, rotate_token=True)
        candidate = local.token_file.read_text()
        pending_matches = json.loads(setup._protected_read(local.rotation_file)) == {"old": original, "new": candidate}
        assert pending_matches, "process interruption must preserve both token references"
        restarted = load_setup().LocalBao(local.state, port=port)
        restarted.configure(root)
        retained = restarted.token_file.read_text() == candidate
        revoked = original not in fixture.tokens
        assert retained and revoked
        assert fixture.token_counter == 2
        assert json.loads(setup._protected_read(local.rotation_file)) == {}
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("fault", ["invalid-json", "shape", "partial", "same", "non-ascii", "whitespace", "type", "oversize", "foreign-current"])
def test_invalid_pending_revocation_fails_closed_without_http_or_secret_output(tmp_path, capsys, fault):
    import traceback
    setup = load_setup()
    local = setup.LocalBao(tmp_path / "state")
    local.prepare()
    old, new, root = "fixture-old-private", "fixture-new-private", "fixture-root-private"
    pending: dict[str, object] = {"old": old, "new": new}
    if fault == "partial":
        pending.pop("old")
    elif fault == "same":
        pending["old"] = new
    elif fault == "non-ascii":
        pending["old"] = old + "é"
    elif fault == "whitespace":
        pending["old"] = old + "\n"
    elif fault == "type":
        pending["old"] = 123
    elif fault == "oversize":
        pending["old"] = "x" * 16385
    raw = b"private invalid-json" if fault == "invalid-json" else b"[]" if fault == "shape" else json.dumps(pending).encode()
    setup.private_write(local.rotation_file, raw)
    setup.private_write(local.token_file, b"fixture-foreign-private" if fault == "foreign-current" else new.encode())
    local.api = lambda *a, **kw: pytest.fail("invalid recovery must not reach HTTP")
    with pytest.raises(SecretError, match="pending rotation unavailable or invalid") as caught:
        local.configure(root)
    assert local.rotation_file.read_bytes() == raw
    rendered = "".join(traceback.format_exception(caught.value))
    assert all(value not in rendered for value in (old, new, root, "private invalid-json"))
    assert capsys.readouterr() == ("", "")
