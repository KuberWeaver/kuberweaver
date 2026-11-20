"""Cloudflare secrets/connector contract: scoped tokens, reference-only records,
vault write path, resolution at execution, and no secret leakage.

All token values are disposable test fixtures shaped like Cloudflare credentials;
none is a real token and no test contacts Cloudflare.
"""
import importlib.util
import json
import re
import sys
import traceback
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from kuberweaver.secrets import SecretError, SecretValue

# Disposable fixtures. Scannable format: prefix + 40 alphanumerics + 8 hex checksum.
_BODY = 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0'
SCANNABLE_USER = 'cfut_' + _BODY + 'a1b2c3d4'
SCANNABLE_ACCOUNT = 'cfat_' + _BODY + '0F1E2D3C'
LEGACY_TOKEN = 'T' + 'aB3' * 13  # 40 alphanumerics, not pure lowercase hex
GLOBAL_KEY_SCANNABLE = 'cfk_' + _BODY + 'a1b2c3d4'
GLOBAL_KEY_LEGACY = '0123456789abcdef' * 2 + '0123456'  # 39 lowercase hex
AMBIGUOUS_HEX = 'a1' * 20  # 40 lowercase hex: legacy global-key shape
ORIGIN_CA_KEY = 'v1.0-' + 'a' * 40
OAUTH_TOKEN = 'cfoat_' + _BODY + 'a1b2c3d4'


def spec(**changes):
    return {'label': 'Cloudflare DNS', 'purpose': 'dns',
            'secret_ref': 'cloudflare/dns-lab', 'secrets_provider': 'local-vault',
            'account_id': None, 'enabled': True, **changes}


def make_app(path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
    from kuberweaver.app import create_app
    app = create_app(state_dir=path / 'state', testing=True)
    from kuberweaver.cloudflare import install_cloudflare_api
    if not hasattr(app.state, 'cloudflare_connectors'):
        install_cloudflare_api(app)
    return app


def login(client, app, role='admin'):
    credentials = dict(line.split(':', 1) for line in
                       (app.state.state_dir / 'bootstrap-credentials.txt').read_text().splitlines())
    response = client.post('/api/auth/login', json={'username': role, 'password': credentials[role]})
    assert response.status_code == 200
    client.headers['x-csrf-token'] = response.json()['csrf_token']


@pytest.fixture(autouse=True)
def prohibit_real_network(monkeypatch):
    import socket
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def loopback_only(address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ('127.0.0.1', '::1'):
            raise AssertionError('Cloudflare contract tests must not open non-loopback connections')

    def connect(self, address):
        loopback_only(address)
        return real_connect(self, address)

    def create_connection(address, *args, **kwargs):
        loopback_only(address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(socket, 'create_connection', create_connection)


@pytest.fixture
def local_vault(tmp_path):
    from kuberweaver.local_vault import LocalVaultProvider, initialize
    directory = tmp_path / 'vault'
    initialize(directory)
    return LocalVaultProvider(directory, allow_write=True)


@pytest.fixture
def platform(tmp_path, monkeypatch, local_vault):
    app = make_app(tmp_path, monkeypatch)
    app.state.registry.register('secrets', 'local-vault', local_vault,
                                {'label': 'Test local vault', 'reference_only': True})
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


def stored_token(provider, reference):
    with provider.resolve(reference) as secret:
        return json.loads(secret.bytes().decode('utf-8'))['api_token']


# ---------------------------------------------------------------------------
# Token shape contract: scoped API tokens only, global keys fail closed.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('token', [SCANNABLE_USER, SCANNABLE_ACCOUNT, LEGACY_TOKEN])
def test_scoped_token_shapes_are_accepted(token):
    from kuberweaver.cloudflare import validate_api_token
    assert validate_api_token(token) == token


@pytest.mark.parametrize('token', [
    GLOBAL_KEY_SCANNABLE, GLOBAL_KEY_LEGACY, AMBIGUOUS_HEX, ORIGIN_CA_KEY, OAUTH_TOKEN,
    '', 'x' * 39, 'x' * 41, 'x' * 53, SCANNABLE_USER[:-1], SCANNABLE_USER + 'a',
    SCANNABLE_USER + '\n', ' ' + SCANNABLE_USER, SCANNABLE_USER.replace('A', 'é', 1),
    'Bearer ' + SCANNABLE_USER, 'https://api.cloudflare.com/' + SCANNABLE_USER,
    'cfut_' + _BODY + 'gggggggg', 'cfut_' + '_' * 40 + 'a1b2c3d4',
    '0123456789abcdef0123456789abcdef01234567',  # 40 lowercase hex
])
def test_global_keys_and_malformed_tokens_fail_closed(token):
    from kuberweaver.cloudflare import validate_api_token
    with pytest.raises(SecretError):
        validate_api_token(token)


def test_token_validation_errors_never_echo_the_credential():
    from kuberweaver.cloudflare import validate_api_token
    for token in (GLOBAL_KEY_SCANNABLE, GLOBAL_KEY_LEGACY, SCANNABLE_USER + 'x'):
        with pytest.raises(SecretError) as caught:
            validate_api_token(token)
        rendered = str(caught.value) + repr(caught.value) + ''.join(
            traceback.format_exception(caught.value))
        assert token not in rendered
        assert 'cfk_' not in rendered and 'cfut_' not in rendered


def test_non_string_tokens_fail_closed():
    from kuberweaver.cloudflare import validate_api_token
    for token in (None, 42, b'cfut_' + _BODY.encode() + b'a1b2c3d4', ['x'], {'api_token': 'x'}):
        with pytest.raises(SecretError):
            validate_api_token(token)


# ---------------------------------------------------------------------------
# Vault envelope: closed JSON shape, legacy username/password material refused.
# ---------------------------------------------------------------------------

def test_envelope_roundtrip_is_exact_and_closed():
    from kuberweaver.cloudflare import envelope_bytes, parse_envelope
    raw = envelope_bytes(SCANNABLE_USER)
    assert json.loads(raw.decode('utf-8')) == {'api_token': SCANNABLE_USER}
    assert parse_envelope(raw) == SCANNABLE_USER


@pytest.mark.parametrize('payload', [
    b'', b'not json', b'[]', b'null', b'"token"', b'{}',
    b'{"api_token": null}', b'{"api_token": 42}', b'{"api_token": []}',
    b'{"api_token": "' + GLOBAL_KEY_SCANNABLE.encode() + b'"}',
    b'{"api_token": "' + SCANNABLE_USER.encode() + b'", "extra": "x"}',
    b'{"auth_email": "user@example.test", "auth_key": "' + GLOBAL_KEY_LEGACY.encode() + b'"}',
    b'{"username": "u", "password": "p"}',
    b'{"api_token": "' + SCANNABLE_USER.encode() + b'", "api_token": "' + LEGACY_TOKEN.encode() + b'"}',
    b'{"api_token": "' + b'x' * 200 + b'"}',
])
def test_envelope_rejects_anything_but_one_scoped_token(payload):
    from kuberweaver.cloudflare import parse_envelope
    with pytest.raises(SecretError):
        parse_envelope(payload)


def test_envelope_errors_are_sanitized():
    from kuberweaver.cloudflare import parse_envelope
    payload = b'{"api_token": "' + SCANNABLE_USER.encode() + b'", "extra": "leak-marker"}'
    with pytest.raises(SecretError) as caught:
        parse_envelope(payload)
    rendered = ''.join(traceback.format_exception(caught.value))
    assert SCANNABLE_USER not in rendered and 'leak-marker' not in rendered


# ---------------------------------------------------------------------------
# Connector record contract: reference-only, closed, no secret fields.
# ---------------------------------------------------------------------------

def test_connector_spec_is_closed_and_reference_only():
    from kuberweaver.cloudflare import CloudflareConnectorSpec
    value = CloudflareConnectorSpec.model_validate(spec())
    assert value.model_dump() == spec()
    assert 'api.cloudflare.com' not in json.dumps(value.model_dump())
    schema = CloudflareConnectorSpec.model_json_schema()
    assert schema['additionalProperties'] is False
    assert not {'api_token', 'token', 'password', 'endpoint', 'secret'} & set(schema['properties'])


@pytest.mark.parametrize('changes', [
    {'label': ''}, {'label': 'x' * 81}, {'label': 1},
    {'purpose': 'workers'}, {'purpose': 'DNS'}, {'purpose': None},
    {'secret_ref': '../password'}, {'secret_ref': 'name/../secret'},
    {'secret_ref': 'x' * 129}, {'secret_ref': 'secret\n'}, {'secret_ref': 'https://evil'},
    {'secrets_provider': 'Bad'}, {'secrets_provider': '../provider'},
    {'account_id': 'not-hex'}, {'account_id': 'A' * 32}, {'account_id': 'a' * 31},
    {'account_id': 'a' * 33}, {'enabled': 'true'}, {'enabled': 1},
    {'api_token': SCANNABLE_USER}, {'token': SCANNABLE_USER},
    {'password': 'DO-NOT-REFLECT'}, {'endpoint': 'https://api.cloudflare.com'},
    {'revision': 4}, {'id': 'a' * 32}, {'observation': {}},
])
def test_spec_rejects_invalid_or_secret_bearing_fields(changes):
    from pydantic import ValidationError
    from kuberweaver.cloudflare import CloudflareConnectorSpec
    with pytest.raises(ValidationError):
        CloudflareConnectorSpec.model_validate(spec(**changes))


def test_patch_contract_is_partial_strict_and_revision_gated():
    from pydantic import ValidationError
    from kuberweaver.cloudflare import CloudflareConnectorPatch
    patch = CloudflareConnectorPatch.model_validate({'revision': 3, 'enabled': False})
    assert patch.model_dump(exclude_unset=True) == {'revision': 3, 'enabled': False}
    assert 'revision' in CloudflareConnectorPatch.model_json_schema()['required']
    for payload in ({}, {'revision': True}, {'revision': 0}, {'revision': '1'},
                    {'revision': 1, 'id': 'a' * 32}, {'revision': 1, 'label': None},
                    {'revision': 1, 'enabled': None}, {'revision': 1, 'api_token': SCANNABLE_USER},
                    {'revision': 1, 'purpose': 'workers'}):
        with pytest.raises(ValidationError):
            CloudflareConnectorPatch.model_validate(payload)


def test_scope_profiles_are_least_privilege_contracts():
    from kuberweaver.cloudflare import API_ORIGIN, SCOPE_PROFILES
    assert API_ORIGIN == 'https://api.cloudflare.com/client/v4'
    assert set(SCOPE_PROFILES) == {'dns', 'tunnel'}
    assert SCOPE_PROFILES['dns'] == (('zone', 'zone', 'read'), ('zone', 'dns', 'edit'))
    assert ('account', 'cloudflare_tunnel', 'edit') in SCOPE_PROFILES['tunnel']
    for profile in SCOPE_PROFILES.values():
        for scope, resource, access in profile:
            assert scope in {'zone', 'account'} and access in {'read', 'edit'}
            assert resource != '*'


# ---------------------------------------------------------------------------
# Vault write path + resolution by reference (real providers, fake tokens).
# ---------------------------------------------------------------------------

def test_store_and_resolve_by_reference_local_vault(local_vault):
    from kuberweaver.cloudflare import access, store_credentials
    value = SecretValue(SCANNABLE_USER.encode())
    try:
        assert store_credentials(local_vault, 'cloudflare/dns-lab', value) is None
    finally:
        value.close()
    assert stored_token(local_vault, 'cloudflare/dns-lab') == SCANNABLE_USER
    connector = spec()
    with access(local_vault, connector) as (record, token):
        assert record == connector
        assert token == SCANNABLE_USER.encode()
    assert SCANNABLE_USER not in repr(local_vault) + json.dumps(local_vault.describe())


def test_store_and_resolve_by_reference_openbao(monkeypatch, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from kuberweaver.cloudflare import access, store_credentials
    from kuberweaver.secrets import OpenBaoProvider

    monkeypatch.setenv('KW_TEST_CF_TOKEN', 'unit-write-token')
    requests, values = [], {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            requests.append((self.command, self.path))
            values[self.path] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['data']['value']
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            requests.append((self.command, self.path))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': {'data': {'value': values[self.path]}}}).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f'http://127.0.0.1:{server.server_port}'
        source = OpenBaoProvider(endpoint, {}, token_env='KW_TEST_CF_TOKEN',
                                 allow_loopback_http=True, namespace='kuberweaver/cloudflare',
                                 allow_write=True)
        value = SecretValue(SCANNABLE_ACCOUNT.encode())
        try:
            store_credentials(source, 'tunnel-lab', value)
        finally:
            value.close()
        path = '/v1/secret/data/kuberweaver/cloudflare/tunnel-lab'
        assert requests == [('POST', path), ('GET', path)]
        with access(source, spec(purpose='tunnel', secret_ref='tunnel-lab')) as (_, token):
            assert token == SCANNABLE_ACCOUNT.encode()
        assert requests == [('POST', path), ('GET', path), ('GET', path)]
        assert SCANNABLE_ACCOUNT not in repr(source) + json.dumps(source.describe())
        assert 'unit-write-token' not in json.dumps(values)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('token', [GLOBAL_KEY_SCANNABLE, GLOBAL_KEY_LEGACY, '', 'x' * 41, 'not a token'])
def test_invalid_token_never_reaches_the_vault(local_vault, token):
    from kuberweaver.cloudflare import store_credentials
    directory = Path(local_vault._directory)
    before = set(directory.iterdir())
    value = SecretValue(token.encode())
    try:
        with pytest.raises(SecretError):
            store_credentials(local_vault, 'cloudflare/dns-lab', value)
    finally:
        value.close()
    assert set(directory.iterdir()) == before, 'An invalid token must never create or replace vault material'


def test_readonly_providers_are_refused_before_write(tmp_path):
    from kuberweaver.cloudflare import store_credentials
    from kuberweaver.local_vault import LocalVaultProvider, initialize
    directory = tmp_path / 'readonly-vault'
    initialize(directory)
    provider = LocalVaultProvider(directory)
    value = SecretValue(SCANNABLE_USER.encode())
    try:
        with pytest.raises(SecretError):
            store_credentials(provider, 'cloudflare/dns-lab', value)
    finally:
        value.close()
    assert not list(directory.glob('*.fernet'))


def test_rotation_replaces_the_stored_token(local_vault):
    from kuberweaver.cloudflare import access, store_credentials
    for token in (SCANNABLE_USER, SCANNABLE_ACCOUNT):
        value = SecretValue(token.encode())
        try:
            store_credentials(local_vault, 'cloudflare/dns-lab', value)
        finally:
            value.close()
        with access(local_vault, spec()) as (_, resolved):
            assert resolved == token.encode()
    assert stored_token(local_vault, 'cloudflare/dns-lab') == SCANNABLE_ACCOUNT


def test_access_reresolves_every_time_and_closes_the_secret(local_vault):
    from kuberweaver.cloudflare import access, store_credentials
    reads = []
    original = local_vault.resolve

    @contextmanager
    def counting(name):
        reads.append(name)
        with original(name) as secret:
            yield secret

    local_vault.resolve = counting
    value = SecretValue(LEGACY_TOKEN.encode())
    try:
        store_credentials(local_vault, 'cloudflare/dns-lab', value)
    finally:
        value.close()
    with access(local_vault, spec()) as (_, first):
        assert first == LEGACY_TOKEN.encode()
    with access(local_vault, spec()) as (_, second):
        assert second == LEGACY_TOKEN.encode()
    assert reads == ['cloudflare/dns-lab', 'cloudflare/dns-lab'], 'No persistent secret cache'


def test_access_fails_closed_on_disabled_corrupt_or_missing(local_vault):
    from kuberweaver.cloudflare import access, store_credentials
    with pytest.raises(SecretError):
        with access(local_vault, spec(enabled=False)):
            pass
    with pytest.raises(SecretError):
        with access(local_vault, spec()):  # nothing stored yet
            pass
    local_vault.store('cloudflare/dns-lab', SecretValue(b'{"username":"u","password":"p"}'))
    with pytest.raises(SecretError) as caught:
        with access(local_vault, spec()):
            pass
    rendered = ''.join(traceback.format_exception(caught.value))
    assert 'password' not in rendered and '"p"' not in rendered
    value = SecretValue(SCANNABLE_USER.encode())
    try:
        store_credentials(local_vault, 'cloudflare/dns-lab', value)
    finally:
        value.close()
    with access(local_vault, spec()) as (_, token):
        assert token == SCANNABLE_USER.encode()


def test_access_errors_never_leak_the_stored_token(local_vault):
    from kuberweaver.cloudflare import access, store_credentials
    value = SecretValue(SCANNABLE_USER.encode())
    try:
        store_credentials(local_vault, 'cloudflare/dns-lab', value)
    finally:
        value.close()
    # Tamper the ciphertext so decryption fails with the token still on disk.
    directory = Path(local_vault._directory)
    import hashlib
    target = directory / (hashlib.sha256(b'cloudflare/dns-lab').hexdigest() + '.fernet')
    target.write_bytes(b'corrupted-ciphertext')
    with pytest.raises(SecretError) as caught:
        with access(local_vault, spec()):
            pass
    rendered = ''.join(traceback.format_exception(caught.value))
    assert SCANNABLE_USER not in rendered


# ---------------------------------------------------------------------------
# API surface: reference-only records, human-only vault writes, no leakage.
# ---------------------------------------------------------------------------

def test_api_create_get_list_and_restart_persist_references_only(platform, tmp_path, monkeypatch):
    app, client = platform
    response = client.post('/api/cloudflare/connectors', json=spec())
    assert response.status_code == 201, response.text
    record = response.json()
    assert re.fullmatch('[0-9a-f]{32}', record['id'])
    assert record == {**spec(), 'id': record['id'], 'revision': 1}
    path = '/api/cloudflare/connectors/' + record['id']
    assert client.get(path).json() == record
    assert client.get('/api/cloudflare/connectors').json() == [record]
    row = app.state.store.one('SELECT body FROM cloudflare_connectors WHERE id=?', (record['id'],))
    assert json.loads(row['body']) == record
    restarted = make_app(tmp_path, monkeypatch)
    with TestClient(restarted) as other:
        login(other, restarted)
        assert other.get(path).json() == record


@pytest.mark.parametrize('changes', [
    {'label': ''}, {'purpose': 'workers'}, {'secret_ref': '../password'},
    {'secrets_provider': 'Bad'}, {'account_id': 'zz'}, {'enabled': 'true'},
    {'api_token': SCANNABLE_USER}, {'password': 'DO-NOT-REFLECT'},
    {'endpoint': 'https://api.cloudflare.com'}, {'revision': 1}, {'id': 'a' * 32},
])
def test_api_create_rejects_invalid_specs_without_echoing(platform, changes):
    app, client = platform
    response = client.post('/api/cloudflare/connectors', json=spec(**changes))
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': 'Invalid Cloudflare connector specification'}
    assert app.state.cloudflare_connectors.list() == []


def test_api_credentials_write_stores_via_real_vault_without_leakage(platform):
    app, client = platform
    record = client.post('/api/cloudflare/connectors', json=spec()).json()
    path = '/api/cloudflare/connectors/' + record['id']
    response = client.put(path + '/credentials', json={'api_token': SCANNABLE_USER})
    assert response.status_code == 200, response.text
    assert response.json() == {'stored': True, 'connector_id': record['id']}
    provider = app.state.registry.get('secrets', 'local-vault')
    assert stored_token(provider, 'cloudflare/dns-lab') == SCANNABLE_USER
    current = client.get(path).json()
    assert current['revision'] == 2 and current == {**record, 'revision': 2}
    audit = app.state.store.one(
        "SELECT * FROM audit WHERE action='cloudflare_connector.credentials_stored'")
    assert audit and json.loads(audit['detail']) == {} and audit['resource'] == record['id']
    persisted = json.dumps(app.state.store.all('SELECT * FROM cloudflare_connectors')
                           + app.state.store.all('SELECT * FROM audit'))
    assert SCANNABLE_USER not in persisted
    for response in (client.get(path), client.get('/api/cloudflare/connectors')):
        assert SCANNABLE_USER not in response.text
    fingerprint = app.state.cloudflare_connectors.fingerprint(record['id'])
    assert re.fullmatch('[0-9a-f]{64}', fingerprint)
    client.put(path + '/credentials', json={'api_token': SCANNABLE_ACCOUNT})
    assert app.state.cloudflare_connectors.fingerprint(record['id']) != fingerprint


@pytest.mark.parametrize('payload', [
    {}, {'token': SCANNABLE_USER}, {'api_token': ''}, {'api_token': GLOBAL_KEY_SCANNABLE},
    {'api_token': GLOBAL_KEY_LEGACY}, {'api_token': 'x' * 41}, {'api_token': 42},
    {'api_token': SCANNABLE_USER, 'extra': 'x'},
    {'api_token': SCANNABLE_USER, 'auth_email': 'user@example.test'},
    {'auth_email': 'user@example.test', 'auth_key': GLOBAL_KEY_LEGACY},
    ['x'], None,
])
def test_api_credentials_validation_never_reflects_or_stores(platform, payload):
    app, client = platform
    record = client.post('/api/cloudflare/connectors', json=spec()).json()
    response = client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials', json=payload)
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': 'Invalid Cloudflare credentials'}
    assert client.get('/api/cloudflare/connectors/' + record['id']).json() == record
    provider = app.state.registry.get('secrets', 'local-vault')
    with pytest.raises(SecretError):
        with provider.resolve('cloudflare/dns-lab'):
            pass


def test_api_credentials_are_human_admin_only(platform):
    app, human = platform
    record = human.post('/api/cloudflare/connectors', json=spec()).json()
    path = '/api/cloudflare/connectors/' + record['id']
    response = human.post('/api/agent-tokens', json={'label': 'cf-tests', 'role': 'admin',
                                                    'scopes': ['configure', 'read:infrastructure'],
                                                    'expires_in_seconds': 3600})
    assert response.status_code == 201, response.text
    with closing(TestClient(app)) as agent:
        agent.headers['Authorization'] = 'Bearer ' + response.json()['token']
        assert agent.get(path).status_code == 200
        assert agent.get('/api/cloudflare/connectors').status_code == 200
        assert agent.post('/api/cloudflare/connectors', json=spec(secret_ref='cloudflare/other')).status_code == 201
        assert agent.patch(path, json={'revision': 1, 'label': 'Agent configured'}).status_code == 200
        assert agent.put(path + '/credentials', json={'api_token': SCANNABLE_USER}).status_code == 403
    for role in ('operator', 'viewer'):
        with closing(TestClient(app)) as other:
            login(other, app, role)
            assert other.put(path + '/credentials', json={'api_token': SCANNABLE_USER}).status_code == 403
    with closing(TestClient(app)) as anonymous:
        assert anonymous.put(path + '/credentials', json={'api_token': SCANNABLE_USER}).status_code == 401
        assert anonymous.get(path).status_code == 401
    provider = app.state.registry.get('secrets', 'local-vault')
    with pytest.raises(SecretError):
        with provider.resolve('cloudflare/dns-lab'):
            pass


def test_api_readonly_source_is_refused(platform):
    app, client = platform
    from kuberweaver.local_vault import LocalVaultProvider
    readonly = LocalVaultProvider(Path(app.state.registry.get('secrets', 'local-vault')._directory))
    app.state.registry.register('secrets', 'readonly-vault', readonly, {'reference_only': True})
    record = client.post('/api/cloudflare/connectors',
                         json=spec(secrets_provider='readonly-vault')).json()
    response = client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
                          json={'api_token': SCANNABLE_USER})
    assert response.status_code == 409, response.text
    assert response.json() == {'detail': 'Selected secret source is read-only'}
    assert client.get('/api/cloudflare/connectors/' + record['id']).json() == record


def test_api_async_store_cannot_report_an_unexecuted_write(platform):
    app, client = platform

    class AwaitableStore:
        pending = None

        def describe(self):
            return {'can_store': True}

        def resolve(self, name):
            raise AssertionError('must not resolve during a failed write')

        async def remote(self):
            raise AssertionError('must not execute asynchronous storage')

        def store(self, name, value):
            self.pending = self.remote()
            return self.pending

    source = AwaitableStore()
    app.state.registry.register('secrets', 'async-vault', source, {'reference_only': True})
    record = client.post('/api/cloudflare/connectors',
                         json=spec(secrets_provider='async-vault')).json()
    response = client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
                          json={'api_token': SCANNABLE_USER})
    try:
        assert response.status_code == 503, response.text
        assert response.json() == {'detail': 'Cloudflare credential storage unavailable'}
        assert source.pending.cr_frame is None
        assert client.get('/api/cloudflare/connectors/' + record['id']).json() == record
    finally:
        source.pending.close()


def test_api_patch_is_revision_gated_and_partial(platform):
    app, client = platform
    record = client.post('/api/cloudflare/connectors', json=spec()).json()
    path = '/api/cloudflare/connectors/' + record['id']
    response = client.patch(path, json={'revision': 1, 'label': 'Renamed', 'enabled': False})
    assert response.status_code == 200, response.text
    updated = {**record, 'label': 'Renamed', 'enabled': False, 'revision': 2}
    assert response.json() == updated
    assert client.patch(path, json={'revision': 1, 'label': 'Lost update'}).status_code == 409
    assert client.get(path).json() == updated
    assert [row['action'] for row in app.state.store.all(
        "SELECT action FROM audit WHERE action LIKE 'cloudflare_connector.%' ORDER BY id")] == [
            'cloudflare_connector.created', 'cloudflare_connector.updated']


def test_api_duplicate_reference_is_refused(platform):
    app, client = platform
    assert client.post('/api/cloudflare/connectors', json=spec()).status_code == 201
    duplicate = client.post('/api/cloudflare/connectors',
                            json=spec(label='Second', purpose='tunnel'))
    assert duplicate.status_code == 409
    assert duplicate.json() == {'detail': 'Credential reference is shared by another Cloudflare connector'}
    assert client.post('/api/cloudflare/connectors',
                       json=spec(secret_ref='cloudflare/tunnel-lab')).status_code == 201


def test_api_unknown_and_malformed_ids_are_not_looked_up(platform):
    app, client = platform
    app.state.store.execute('INSERT INTO cloudflare_connectors(id,body) VALUES(?,?)', ('not-an-id', '{}'))
    for identity in ('not-an-id', 'a' * 32):
        assert client.get('/api/cloudflare/connectors/' + identity).status_code == 404
        assert client.patch('/api/cloudflare/connectors/' + identity,
                            json={'revision': 1}).status_code == 404
        assert client.put('/api/cloudflare/connectors/' + identity + '/credentials',
                          json={'api_token': SCANNABLE_USER}).status_code == 404


def test_api_csrf_and_origin_guards(platform):
    app, client = platform
    record = client.post('/api/cloudflare/connectors', json=spec()).json()
    path = '/api/cloudflare/connectors/' + record['id']
    assert client.put(path + '/credentials', json={'api_token': SCANNABLE_USER},
                      headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.put(path + '/credentials', json={'api_token': SCANNABLE_USER},
                      headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_api_installer_is_idempotent(platform):
    app, client = platform
    from kuberweaver.cloudflare import install_cloudflare_api
    service, routes = app.state.cloudflare_connectors, len(app.routes)
    install_cloudflare_api(app)
    assert app.state.cloudflare_connectors is service
    assert len(app.routes) == routes


def test_fingerprint_pins_references_and_provenance_without_resolving(platform, tmp_path):
    app, client = platform

    class NoResolve:
        def describe(self):
            return {'can_store': True}

        def resolve(self, name):
            raise AssertionError('fingerprinting must never resolve a secret')

        def store(self, name, value):
            raise AssertionError('fingerprinting must never write a secret')

    source = NoResolve()
    app.state.registry.register('secrets', 'noresolve-vault', source, {'reference_only': True})
    record = client.post('/api/cloudflare/connectors',
                         json=spec(secrets_provider='noresolve-vault')).json()
    service = app.state.cloudflare_connectors
    before = service.fingerprint(record['id'])
    assert re.fullmatch('[0-9a-f]{64}', before)
    client.patch('/api/cloudflare/connectors/' + record['id'], json={'revision': 1, 'label': 'Renamed'})
    assert service.fingerprint(record['id']) != before
    source_path = Path(sys.modules['kuberweaver.cloudflare'].__file__)
    original = source_path.read_text()
    try:
        source_path.write_text(original + '\n# provider source drift\n')
        assert service.fingerprint(record['id']) != before
    finally:
        source_path.write_text(original)


def test_service_access_resolves_at_execution_and_fails_closed(platform):
    app, client = platform
    record = client.post('/api/cloudflare/connectors', json=spec()).json()
    service = app.state.cloudflare_connectors
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        with service.access(record['id']):
            pass
    assert caught.value.status_code == 503
    client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
               json={'api_token': SCANNABLE_USER})
    with service.access(record['id']) as (connector, token):
        assert connector['id'] == record['id']
        assert token == SCANNABLE_USER.encode()
    client.patch('/api/cloudflare/connectors/' + record['id'], json={'revision': 2, 'enabled': False})
    with pytest.raises(HTTPException) as caught:
        with service.access(record['id']):
            pass
    assert caught.value.status_code == 409


# ---------------------------------------------------------------------------
# PostgreSQL production contract: the new table is registered, not implicit.
# ---------------------------------------------------------------------------

def test_table_is_registered_in_the_postgres_contract():
    from kuberweaver import postgres
    assert postgres.COLUMNS['cloudflare_connectors'] == ('id', 'body')
    assert 'cloudflare_connectors' in postgres.DDL
    assert 'cloudflare_connectors' in postgres.ORDERED
    translated = postgres.translate_sql(
        'CREATE TABLE IF NOT EXISTS cloudflare_connectors(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    assert translated == f'CREATE TABLE IF NOT EXISTS cloudflare_connectors ({postgres.DDL["cloudflare_connectors"]})'


@pytest.fixture
def pg_schema(tmp_path, monkeypatch):
    import os
    import uuid
    import psycopg
    from psycopg.conninfo import make_conninfo
    from psycopg import sql
    from kuberweaver.postgres import read_dsn_file
    source = Path(os.environ.get('KUBERWEAVER_TEST_DATABASE_URL_FILE',
                                 Path(__file__).parents[1] / 'data/postgresql/test-url'))
    if not source.exists():
        pytest.skip('Dedicated PostgreSQL test DSN file not configured')
    dsn = read_dsn_file(source)
    schema = 'kw_test_' + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    target = tmp_path / 'pg-url'
    target.write_text(make_conninfo(dsn, options='-csearch_path=' + schema))
    target.chmod(0o600)
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', str(target))
    try:
        yield target
    finally:
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        admin.close()


def test_postgresql_creation_and_controller_reopen(tmp_path, monkeypatch, pg_schema, local_vault):
    app = make_app(tmp_path, monkeypatch)
    assert app.state.store.backend == 'postgresql'
    app.state.registry.register('secrets', 'local-vault', local_vault, {'reference_only': True})
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        record = client.post('/api/cloudflare/connectors', json=spec()).json()
        response = client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
                              json={'api_token': SCANNABLE_USER})
        assert response.status_code == 200, response.text
        persisted = json.dumps(app.state.store.all('SELECT * FROM cloudflare_connectors')
                               + app.state.store.all('SELECT * FROM audit'))
        assert SCANNABLE_USER not in persisted
    reopened = make_app(tmp_path, monkeypatch)
    assert reopened.state.store.backend == 'postgresql'
    with TestClient(reopened) as other:
        login(other, reopened)
        assert other.get('/api/cloudflare/connectors/' + record['id']).json() == {**record, 'revision': 2}