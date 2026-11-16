"""Opt-in vault writes use real loopback HTTP, never production credentials."""
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from kuberweaver.secrets import OpenBaoProvider, SecretError, SecretValue


@contextmanager
def vault(monkeypatch, *, mismatched=False, status=204):
    monkeypatch.setenv('KW_TEST_WRITE_TOKEN', 'unit-write-token')
    requests, values = [], {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            requests.append((self.command, self.path, self.headers.get('X-Vault-Token')))
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['data']['value']
            values[self.path] = 'mismatched-test-value' if mismatched else value
            self.send_response(status)
            self.end_headers()
            if status != 204:
                self.wfile.write(b'upstream-test-secret-must-not-escape')

        def do_GET(self):
            requests.append((self.command, self.path, self.headers.get('X-Vault-Token')))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': {'data': {'value': values[self.path]}}}).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, values
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_optional_openbao_write_is_read_back(monkeypatch):
    with vault(monkeypatch) as (endpoint, requests, _):
        source = OpenBaoProvider(endpoint, {}, token_env='KW_TEST_WRITE_TOKEN',
            allow_loopback_http=True, namespace='kuberweaver/connections', allow_write=True)
        value = SecretValue(b'{"username":"test-user","password":"test-only-material"}')
        try:
            assert source.store('esxi-lab', value) is None
            assert source.describe()['can_store'] is True
            with source.resolve('esxi-lab') as readback:
                assert readback.bytes() == value.bytes()
        finally:
            value.close()
        path = '/v1/secret/data/kuberweaver/connections/esxi-lab'
        assert requests == [('POST', path, 'unit-write-token'), ('GET', path, 'unit-write-token'),
                            ('GET', path, 'unit-write-token')]
        assert 'test-only-material' not in repr(source) + json.dumps(source.describe())


def test_existing_openbao_sources_remain_read_only(monkeypatch):
    with vault(monkeypatch) as (endpoint, requests, _):
        source = OpenBaoProvider(endpoint, {'known': 'kuberweaver/known'},
            token_env='KW_TEST_WRITE_TOKEN', allow_loopback_http=True)
        value = SecretValue(b'test-only-material')
        try:
            with pytest.raises(SecretError, match='read-only'):
                source.store('known', value)
        finally:
            value.close()
        assert requests == []
        assert source.describe()['can_store'] is False


@pytest.mark.parametrize('namespace', ['outside/connections', 'kuberweaver/../other',
    'kuberweaver/%2e%2e', '/kuberweaver/connections', 'kuberweaver/connections?x'])
def test_namespace_cannot_escape_admin_scope(namespace):
    with pytest.raises(SecretError):
        OpenBaoProvider('https://bao.example', {}, token_env='KW_TEST_WRITE_TOKEN',
            namespace=namespace, allow_write=True)


@pytest.mark.parametrize('reference', ['../other', 'https://evil', '/other', 'other?x', 'a' * 128])
def test_dynamic_reference_rejected_before_network(monkeypatch, reference):
    with vault(monkeypatch) as (endpoint, requests, _):
        source = OpenBaoProvider(endpoint, {}, token_env='KW_TEST_WRITE_TOKEN',
            allow_loopback_http=True, namespace='kuberweaver/connections', allow_write=True)
        value = SecretValue(b'test-only-material')
        try:
            with pytest.raises(SecretError):
                source.store(reference, value)
        finally:
            value.close()
        assert requests == []


@pytest.mark.parametrize('mismatched,status', [(True, 204), (False, 403), (False, 302)])
def test_failed_write_or_readback_never_claims_success_or_leaks(monkeypatch, mismatched, status):
    import traceback
    with vault(monkeypatch, mismatched=mismatched, status=status) as (endpoint, requests, _):
        source = OpenBaoProvider(endpoint, {}, token_env='KW_TEST_WRITE_TOKEN',
            allow_loopback_http=True, namespace='kuberweaver/connections', allow_write=True)
        value = SecretValue(b'test-only-material')
        try:
            with pytest.raises(SecretError) as caught:
                source.store('esxi-lab', value)
        finally:
            value.close()
        rendered = ''.join(traceback.format_exception(caught.value))
        for forbidden in ('test-only-material', 'upstream-test-secret', 'unit-write-token'):
            assert forbidden not in rendered
        assert requests[0][0] == 'POST'


@pytest.mark.parametrize('raw', [b'x' * (1024 * 1024), b'\x00' * 200_000])
def test_unreadable_value_is_rejected_before_any_vault_write(monkeypatch, raw):
    with vault(monkeypatch) as (endpoint, requests, values):
        source = OpenBaoProvider(endpoint, {}, token_env='KW_TEST_WRITE_TOKEN',
            allow_loopback_http=True, namespace='kuberweaver/connections', allow_write=True)
        value = SecretValue(raw)
        try:
            with pytest.raises(SecretError):
                source.store('esxi-lab', value)
        finally:
            value.close()
        assert requests == [], 'An unreadable value must never replace an existing secret'
        assert values == {}


@pytest.mark.parametrize('size,accepted', [(128 * 1024, True), (128 * 1024 + 1, False)])
def test_store_boundary_accounts_for_worst_case_json_escaping(monkeypatch, size, accepted):
    with vault(monkeypatch) as (endpoint, requests, _):
        source = OpenBaoProvider(endpoint, {}, token_env='KW_TEST_WRITE_TOKEN',
            allow_loopback_http=True, namespace='kuberweaver/connections', allow_write=True)
        value = SecretValue(b'\x00' * size)
        try:
            if accepted:
                source.store('esxi-lab', value)
                assert [method for method, _, _ in requests] == ['POST', 'GET']
            else:
                with pytest.raises(SecretError):
                    source.store('esxi-lab', value)
                assert requests == []
        finally:
            value.close()
