"""Credential providers deliberately use a tiny read-only byte contract."""
from pathlib import Path

import pytest

from kuberweaver.secrets import RestrictedFileProvider, SecretError, OpenBaoProvider


def protected(tmp_path: Path, content: bytes = b"private material") -> Path:
    path = tmp_path / "credential"
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def test_restricted_file_is_scoped_and_wiped(tmp_path):
    path = protected(tmp_path)
    provider = RestrictedFileProvider({"operations-ssh": path})
    with provider.resolve("operations-ssh") as secret:
        assert secret.bytes() == b"private material"
        assert "private material" not in repr(secret)
    with pytest.raises(SecretError):
        secret.bytes()
    with pytest.raises(SecretError):
        provider.resolve(str(path)).__enter__()
    assert provider.describe() == {
        "provider": "restricted-file", "capabilities": ["read"],
        "approved_names": ["operations-ssh"], "persistent_secret_cache": False,
    }


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o400, 0o660, 0o777])
def test_file_permissions_fail_closed(tmp_path, mode):
    path = protected(tmp_path)
    path.chmod(mode)
    with pytest.raises(SecretError):
        with RestrictedFileProvider({"key": path}).resolve("key"):
            pass


def test_symlinks_and_hardlinks_fail_closed(tmp_path):
    path = protected(tmp_path)
    link = tmp_path / "symlink"
    link.symlink_to(path)
    with pytest.raises(SecretError):
        with RestrictedFileProvider({"key": link}).resolve("key"):
            pass
    hardlink = tmp_path / "hardlink"
    hardlink.hardlink_to(path)
    with pytest.raises(SecretError):
        with RestrictedFileProvider({"key": hardlink}).resolve("key"):
            pass


def test_bao_kvv2_contract_and_redaction(tmp_path):
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    token = protected(tmp_path, b"test-token-do-not-disclose")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("X-Vault-Token")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": {"data": {"value": "demo-secret"}}}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(SecretError):
            OpenBaoProvider(endpoint, {"demo": "kuberweaver/demo"}, token_file=token)
        provider = OpenBaoProvider(endpoint, {"demo": "kuberweaver/demo"},
                                   token_file=token, allow_loopback_http=True)
        with provider.resolve("demo") as value:
            assert value.bytes() == b"demo-secret"
        assert requests == [("/v1/secret/data/kuberweaver/demo", "test-token-do-not-disclose")]
        with pytest.raises(SecretError):
            with provider.resolve("kuberweaver/arbitrary"):
                pass
        assert len(requests) == 1
        descriptor = json.dumps(provider.describe())
        assert "demo-secret" not in descriptor and "test-token" not in descriptor
        assert str(token) not in descriptor and endpoint not in descriptor
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("endpoint", ["http://192.168.122.10:18200", "http://localhost:18200",
                                     "https://user:secret@host", "https://host/prefix",
                                     "http://127.0.0.1:bad", "file:///etc/passwd"])
def test_bao_rejects_unsafe_endpoints(endpoint):
    with pytest.raises(SecretError):
        OpenBaoProvider(endpoint, {"demo": "kuberweaver/demo"}, token_env="BAO_TOKEN",
                        allow_loopback_http=True)


@pytest.mark.parametrize("reference", ["../key", "/root", "https://evil", "kuberweaver/../x",
                                      "kuberweaver/%2e%2e/x", "other/demo", "kuberweaver/x?y"])
def test_bao_rejects_broad_or_injected_references(reference):
    with pytest.raises(SecretError):
        OpenBaoProvider("https://bao.example", {"demo": reference}, token_env="BAO_TOKEN")


def test_tls_is_verified_by_default():
    import ssl
    from urllib.request import HTTPSHandler
    provider = OpenBaoProvider("https://bao.example", {"demo": "kuberweaver/demo"},
                               token_env="BAO_TOKEN")
    handler = next(h for h in provider._opener.handlers if isinstance(h, HTTPSHandler))
    assert handler._context.verify_mode == ssl.CERT_REQUIRED
    assert handler._context.check_hostname is True
    assert provider.describe()["transport"] == "tls-verified"


@pytest.mark.parametrize("status,body", [(403, b'upstream-secret'), (404, b'upstream-secret'),
    (500, b'upstream-secret'), (200, b'upstream-secret'),
    (200, b'{"data":{"data":{"value":123,"private":"upstream-secret"}}}'),
    (200, b'{"data":{"data":{"other":"upstream-secret"}}}'),
    (302, b'upstream-secret')])
def test_kv_errors_and_redirects_are_redacted(tmp_path, status, body, monkeypatch):
    import traceback
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    monkeypatch.setenv("KW_TEST_TOKEN", "do-not-disclose-token")
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = OpenBaoProvider(f"http://127.0.0.1:{server.server_port}",
            {"demo": "kuberweaver/demo"}, token_env="KW_TEST_TOKEN", allow_loopback_http=True)
        with pytest.raises(SecretError) as caught:
            with provider.resolve("demo"):
                pass
        rendered = repr(caught.value) + "".join(traceback.format_exception(caught.value))
        assert "upstream-secret" not in rendered and "do-not-disclose-token" not in rendered
        assert calls == ["/v1/secret/data/kuberweaver/demo"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_token_file_requires_exact_permissions(tmp_path):
    path = protected(tmp_path, b"protected-token")
    path.chmod(0o644)
    provider = OpenBaoProvider("https://bao.example", {"demo": "kuberweaver/demo"}, token_file=path)
    with pytest.raises(SecretError):
        with provider.resolve("demo"):
            pass


def test_setup_script_private_write_and_scope(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("setup_bao", Path(__file__).parents[1] / "scripts/setup_bao.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    path = tmp_path / "bootstrap.json"
    setup.private_json(path, {"fake_test_only": "never-log"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert "kuberweaver/*" in setup.POLICY
    assert '["read"]' in setup.POLICY
    assert "create" not in setup.POLICY and "list" not in setup.POLICY
    assert "-dev" not in setup.REMOTE_SETUP
    assert '127.0.0.1:18200:8200' in setup.REMOTE_SETUP
    assert 'storage "raft"' in setup.REMOTE_SETUP
    assert 'cluster_addr' in setup.REMOTE_SETUP
    assert callable(setup.restart_check)
    assert callable(setup.backend_plaintext_check)
    import socket
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        with pytest.raises(SecretError):
            setup.check_loopback_port(port)
        with socket.create_connection(("127.0.0.1", port)) as client:
            peer, _ = listener.accept()
            peer.close()  # server-side TIME_WAIT must not look like a live tunnel
            assert client.recv(1) == b""
    setup.check_loopback_port(port)


def test_bootstrap_api_does_not_forward_root_token_on_redirect(tmp_path, monkeypatch):
    import importlib.util
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    spec = importlib.util.spec_from_file_location("setup_bao", Path(__file__).parents[1] / "scripts/setup_bao.py")
    assert spec is not None and spec.loader is not None
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            if self.path == "/v1/start":
                self.send_response(302)
                self.send_header("Location", "/v1/must-not-follow")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(setup, "ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    try:
        with pytest.raises(SecretError):
            setup.api("start", token="unit-test-root-token")
        assert calls == ["/v1/start"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_self_signed_tls_is_rejected(tmp_path, monkeypatch):
    import shutil
    import ssl
    import subprocess
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    if not shutil.which("openssl"):
        pytest.skip("openssl needed for real untrusted-TLS handshake test")
    certificate, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=localhost"],
                   check=True, capture_output=True)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("KW_TEST_TOKEN", "not-sent-on-untrusted-tls")
    try:
        provider = OpenBaoProvider(f"https://127.0.0.1:{server.server_port}",
            {"demo": "kuberweaver/demo"}, token_env="KW_TEST_TOKEN", timeout=2)
        with pytest.raises(SecretError):
            with provider.resolve("demo"):
                pass
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_intermediate_symlink_rejected_even_if_resolve_is_raced(tmp_path, monkeypatch):
    directory = tmp_path / "real"
    directory.mkdir()
    path = directory / "credential"
    path.write_bytes(b"key")
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(directory, target_is_directory=True)
    # Simulate an attacker swapping a parent after a preliminary path resolution.
    monkeypatch.setattr(Path, "resolve", lambda self, **kwargs: self.absolute())
    with pytest.raises(SecretError):
        with RestrictedFileProvider({"key": link / "credential"}).resolve("key"):
            pass


def test_wrong_owner_empty_file_and_directory_are_rejected(tmp_path, monkeypatch):
    import os
    path = protected(tmp_path)
    original_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: original_uid + 1)
    with pytest.raises(SecretError):
        with RestrictedFileProvider({"key": path}).resolve("key"):
            pass
    monkeypatch.undo()
    path.write_bytes(b"")
    with pytest.raises(SecretError):
        with RestrictedFileProvider({"key": path}).resolve("key"):
            pass
    with pytest.raises(SecretError):
        with RestrictedFileProvider({"key": tmp_path}).resolve("key"):
            pass
