"""Shared SSH keys: actual HTTP/auth/storage and the real encrypted local vault."""
from contextlib import closing
import base64
import hashlib
import json
import re

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from test_postgresql_store import pg_file


def make_app(path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
    from backend.app import create_app
    from backend.local_vault import initialize, LocalVaultProvider
    app = create_app(state_dir=path / 'state', testing=True)
    directory = path / 'vault'
    if not directory.exists():
        initialize(directory)
    provider = LocalVaultProvider(directory, allow_write=True)
    app.state.registry.register('secrets', 'test-vault', provider, {})
    app.state.provider_bindings['secrets'] = 'test-vault'
    return app


def credentials(app):
    return dict(line.split(':', 1) for line in
                (app.state.state_dir / 'bootstrap-credentials.txt').read_text().splitlines())


def login(client, app, role='admin'):
    result = client.post('/api/auth/login', json={'username': role, 'password': credentials(app)[role]})
    assert result.status_code == 200
    client.headers['x-csrf-token'] = result.json()['csrf_token']


@pytest.fixture
def platform(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


def create(client):
    response = client.post('/api/ssh-keys', json={'name': 'Lab access'})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize('role', ['admin', 'operator', 'viewer'])
def test_read_roles_and_human_only_mutations(platform, role):
    app, admin = platform
    key = create(admin)
    path = '/api/ssh-keys/' + key['id']
    with closing(TestClient(app)) as client:
        assert client.get('/api/ssh-keys').status_code == 401
        login(client, app, role)
        assert client.get('/api/ssh-keys').status_code == 200
        assert client.get(path).status_code == 200
        expected = 201 if role == 'admin' else 403
        assert client.post('/api/ssh-keys', json={'name': 'Other'}).status_code == expected
        assert client.patch(path, json={'enabled': False}).status_code == (200 if role == 'admin' else 403)
        if role != 'admin':
            assert client.post(path + '/export', json={'password': credentials(app)[role]}).status_code == 403
        client.headers.pop('x-csrf-token')
        assert client.patch(path, json={'enabled': True}).status_code == 403
        assert client.post(path + '/export', json={'password': credentials(app)[role]}).status_code == 403


def test_agent_read_scope_only_never_private_or_mutations(platform):
    app, admin = platform
    key = create(admin)
    for scopes, can_read in [(['read:infrastructure'], True), (['read', 'configure'], True), (['read:finops'], False)]:
        result = admin.post('/api/agent-tokens', json={'label': 'SSH test', 'role': 'admin',
                            'scopes': scopes, 'expires_in_seconds': 3600})
        assert result.status_code == 201, result.text
        with closing(TestClient(app)) as agent:
            agent.headers['Authorization'] = 'Bearer ' + result.json()['token']
            expected = 200 if can_read else 403
            assert agent.get('/api/ssh-keys').status_code == expected
            assert agent.get('/api/ssh-keys/' + key['id']).status_code == expected
            assert agent.post('/api/ssh-keys', json={'name': 'Denied'}).status_code == 403
            assert agent.patch('/api/ssh-keys/' + key['id'], json={'enabled': False}).status_code == 403
            assert agent.post('/api/ssh-keys/' + key['id'] + '/export',
                              json={'password': credentials(app)['admin']}).status_code == 403


@pytest.mark.parametrize('body', [{}, [], {'name': ''}, {'name': 1}, {'name': 'x' * 81},
    {'name': 'bad\nname'}, {'name': 'Valid', 'password': 'DO-NOT-ECHO'}, {'private_key': 'DO-NOT-ECHO'}])
def test_create_validation_is_sanitized(platform, body):
    app, client = platform
    response = client.post('/api/ssh-keys', json=body)
    assert response.status_code == 422
    assert 'DO-NOT-ECHO' not in response.text
    assert client.get('/api/ssh-keys').json() == []


def test_csrf_origin_and_export_validation(platform):
    app, client = platform
    key = create(client)
    path = '/api/ssh-keys/' + key['id']
    assert client.post('/api/ssh-keys', json={'name': 'Denied'}, headers={'origin': 'https://evil.example'}).status_code == 403
    for body in [[], {}, {'password': ['DO-NOT-ECHO']}, {'password': None}, {'password': 'DO-NOT-ECHO', 'extra': True}]:
        response = client.post(path + '/export', json=body)
        assert response.status_code == 422
        assert 'DO-NOT-ECHO' not in response.text
    for body in [{}, {'enabled': 1}, {'enabled': 'false'}, {'enabled': False, 'extra': 'DO-NOT-ECHO'}]:
        response = client.patch(path, json=body)
        assert response.status_code == 422
        assert 'DO-NOT-ECHO' not in response.text
    assert client.get('/api/ssh-keys/' + 'f' * 32).status_code == 404
    assert client.get('/api/ssh-keys/bad').status_code == 404


def test_usage_includes_retained_jobs_and_blueprints_without_private_config(platform):
    app, client = platform
    key = create(client)
    graph = {'nodes': [{'id': 'vm', 'name': 'Lab VM', 'config': {'ssh_key_id': key['id'], 'password': 'DO-NOT-ECHO'}}]}
    app.state.store.put('jobs', 'old-job', {'id': 'old-job', 'name': 'Old lab job', 'status': 'failed', 'graph': graph})
    app.state.store.put('blueprints', 'blueprint', {'id': 'blueprint', 'name': 'Lab blueprint', 'graph': graph})
    result = client.get('/api/ssh-keys/' + key['id'])
    assert result.status_code == 200
    assert result.json()['usage'] == {'jobs': [{'id': 'old-job', 'name': 'Old lab job'}],
                                      'blueprints': [{'id': 'blueprint', 'name': 'Lab blueprint'}]}
    assert 'DO-NOT-ECHO' not in result.text and 'secret_ref' not in result.text
    assert 'usage' not in app.state.ssh_keys.binding(key['id'])


def test_fingerprint_tamper_blocks_binding(platform):
    app, client = platform
    key = create(client)
    record = json.loads(app.state.store.one('SELECT body FROM ssh_keys WHERE id=?', (key['id'],))['body'])
    record['fingerprint'] = 'SHA256:wrong'
    app.state.store.execute('UPDATE ssh_keys SET body=? WHERE id=?', (json.dumps(record), key['id']))
    with pytest.raises(HTTPException) as invalid:
        app.state.ssh_keys.binding(key['id'])
    assert invalid.value.status_code == 503


@pytest.mark.parametrize('attack', ['ciphertext', 'private-mismatch', 'missing-vault-key', 'missing-provider'])
def test_private_material_tamper_fails_sanitized(platform, tmp_path, attack):
    app, client = platform
    key = create(client)
    record = json.loads(app.state.store.one('SELECT body FROM ssh_keys WHERE id=?', (key['id'],))['body'])
    provider = app.state.registry.get('secrets', 'test-vault')
    if attack == 'ciphertext':
        path = tmp_path / 'vault' / (hashlib.sha256(record['secret_ref'].encode()).hexdigest() + '.fernet')
        path.write_bytes(b'tampered')
    if attack == 'private-mismatch':
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from backend.secrets import SecretValue
        private = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
        secret = SecretValue(private)
        try:
            provider.store(record['secret_ref'], secret)
        finally:
            secret.close()
    if attack == 'missing-vault-key':
        (tmp_path / 'vault' / 'key').unlink()
    if attack == 'missing-provider':
        app.state.registry._entries.pop(('secrets', 'test-vault'))
    response = client.post('/api/ssh-keys/' + key['id'] + '/export', json={'password': credentials(app)['admin']})
    assert response.status_code == 503
    assert 'PRIVATE KEY' not in response.text and record['secret_ref'] not in response.text
    assert 'Traceback' not in response.text


@pytest.mark.parametrize('failure', ['read-only', 'unavailable', 'write-raised-after-publication', 'readback'])
def test_creation_fails_closed_preserves_uncertain_material(platform, tmp_path, monkeypatch, failure):
    app, client = platform
    provider = app.state.registry.get('secrets', 'test-vault')
    before = {p.name for p in (tmp_path / 'vault').iterdir()}
    if failure == 'read-only':
        monkeypatch.setattr(provider, 'describe', lambda: {'can_store': False})
    if failure == 'unavailable':
        app.state.provider_bindings['secrets'] = 'missing'
    if failure == 'write-raised-after-publication':
        store = provider.store
        def uncertain(reference, value):
            store(reference, value)
            raise RuntimeError('DO-NOT-ECHO')
        monkeypatch.setattr(provider, 'store', uncertain)
    if failure == 'readback':
        def denied(*args):
            raise RuntimeError('DO-NOT-ECHO')
        monkeypatch.setattr(provider, 'resolve', denied)
    response = client.post('/api/ssh-keys', json={'name': 'Uncertain'})
    assert response.status_code == 503
    assert 'DO-NOT-ECHO' not in response.text
    assert client.get('/api/ssh-keys').json() == []
    rows = app.state.store.all('SELECT body FROM ssh_keys')
    if failure in {'readback', 'write-raised-after-publication'}:
        assert len(rows) == 1 and json.loads(rows[0]['body'])['state'] == 'pending'
        assert {p.name for p in (tmp_path / 'vault').iterdir()} > before
    else:
        assert rows == []


def test_explicit_key_vault_does_not_replace_global_binding(platform):
    app, client = platform
    app.state.provider_bindings['secrets'] = 'restricted-file'
    folder = app.state.state_dir / 'config'
    folder.mkdir(exist_ok=True)
    config = folder / 'ssh-keys.json'
    config.write_text(json.dumps({'secrets_provider': 'test-vault'}))
    config.chmod(0o600)
    key = create(client)
    assert app.state.provider_bindings['secrets'] == 'restricted-file'
    assert app.state.ssh_keys.binding(key['id'])['secrets_provider'] == 'test-vault'


def test_invalid_explicit_key_vault_configuration_never_falls_back(platform):
    app, client = platform
    folder = app.state.state_dir / 'config'
    folder.mkdir(exist_ok=True)
    config = folder / 'ssh-keys.json'
    config.write_text('{invalid')
    config.chmod(0o600)
    assert client.post('/api/ssh-keys', json={'name': 'Not created'}).status_code == 503
    assert app.state.ssh_keys.list() == []


def test_postgresql_api_persistence_uses_isolated_schema(tmp_path, monkeypatch, pg_file):
    app = make_app(tmp_path, monkeypatch)
    assert app.state.store.backend == 'postgresql'
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        key = create(client)
        assert client.get('/api/ssh-keys').json() == [key]
        app.state.store.put('jobs', 'retained', {'id': 'retained', 'status': 'rejected', 'graph': {'nodes': [{'config': {'ssh_key_id': key['id']}}]}})
        assert client.get('/api/ssh-keys/' + key['id']).json()['usage']['jobs'] == [{'id': 'retained', 'name': 'retained'}]
        assert client.patch('/api/ssh-keys/' + key['id'], json={'enabled': False}).status_code == 200
    restarted = make_app(tmp_path, monkeypatch)
    with TestClient(restarted, raise_server_exceptions=False) as client:
        login(client, restarted)
        assert client.get('/api/ssh-keys').json()[0]['enabled'] is False
        assert client.patch('/api/ssh-keys/' + key['id'], json={'enabled': True}).status_code == 200
        response = client.post('/api/ssh-keys/' + key['id'] + '/export', json={'password': credentials(restarted)['admin']})
        assert response.status_code == 200
        serialization.load_ssh_private_key(response.content, password=None)
    assert not (app.state.state_dir / 'platform.sqlite3').exists()


def test_export_requires_reauthentication_and_returns_no_store_attachment(platform):
    app, client = platform
    key = create(client)
    path = '/api/ssh-keys/' + key['id'] + '/export'
    # Rotate to a password that is not also an audit value (the actor name), so
    # the assertion below tests the secret rather than the word "admin".
    rotated = 'tidal harbour lantern 47'
    account = app.state.store.one('SELECT * FROM users WHERE username=?', ('admin',))
    turned = client.post(f"/api/users/{account['id']}/password",
                         json={'current_password': credentials(app)['admin'],
                               'new_password': rotated})
    assert turned.status_code == 200, turned.text
    client.headers['x-csrf-token'] = turned.json()['csrf_token']

    denied = client.post(path, json={'password': 'wrong-disposable-password'})
    assert denied.status_code == 401
    assert 'wrong-disposable-password' not in denied.text
    response = client.post(path, json={'password': rotated})
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-disposition'] == 'attachment; filename="ssh-key-' + key['id'] + '"'
    assert response.headers['x-content-type-options'] == 'nosniff'
    serialization.load_ssh_private_key(response.content, password=None)
    audit = json.dumps(app.state.store.all('SELECT * FROM audit'))
    assert response.content.decode() not in audit and rotated not in audit
    assert 'wrong-disposable-password' not in audit
    for _ in range(5):
        response = client.post(path, json={'password': 'wrong'})
    assert response.status_code == 429
    assert client.post(path, json={'password': rotated}).status_code == 429


def test_binding_is_public_only_stable_and_disable_reenable_invalidates(platform, monkeypatch):
    app, client = platform
    key = create(client)
    service = app.state.ssh_keys
    source = app.state.registry.get('secrets', 'test-vault')
    def denied(*args):
        raise AssertionError('Metadata must never resolve secrets')
    monkeypatch.setattr(source, 'resolve', denied)
    before = service.binding(key['id'])
    assert before == service.binding(key['id'])
    assert before['public_key'] == key['public_key'] and before['fingerprint'] == key['fingerprint']
    assert before['secrets_provider'] == 'test-vault' and before['revision'] == 1
    assert 'secret_ref' not in before
    path = '/api/ssh-keys/' + key['id']
    assert client.patch(path, json={'enabled': False}).json()['enabled'] is False
    assert client.get(path).json()['enabled'] is False
    with pytest.raises(HTTPException) as disabled:
        service.binding(key['id'])
    assert disabled.value.status_code == 409
    with pytest.raises(HTTPException):
        with service.private_key(key['id']):
            pass
    assert client.patch(path, json={'enabled': True}).json()['enabled'] is True
    assert service.binding(key['id'])['revision'] == 3
    assert service.binding(key['id']) != before
    app.state.provider_bindings['secrets'] = 'restricted-file'
    assert service.binding(key['id'])['secrets_provider'] == 'test-vault'
    monkeypatch.setattr('backend.ssh_keys.provider_provenance', lambda *a, **k: 'changed')
    with pytest.raises(HTTPException) as drift:
        service.binding(key['id'])
    assert drift.value.status_code == 503


def test_create_persists_public_metadata_and_encrypted_private_key(platform, tmp_path, monkeypatch):
    app, client = platform
    key = create(client)
    assert set(key) == {'id', 'name', 'algorithm', 'public_key', 'fingerprint', 'enabled', 'created_at', 'created_by'}
    assert re.fullmatch('[0-9a-f]{32}', key['id'])
    assert key['algorithm'] == 'ed25519' and key['enabled'] is True
    assert key['created_by'] == 'admin'
    public_blob = base64.b64decode(key['public_key'].split()[1])
    assert key['fingerprint'] == 'SHA256:' + base64.b64encode(hashlib.sha256(public_blob).digest()).decode().rstrip('=')
    assert client.get('/api/ssh-keys').json() == [key]
    assert client.get('/api/ssh-keys/' + key['id']).json().items() >= key.items()
    with app.state.ssh_keys.private_key(key['id']) as value:
        raw = value.bytes()
        private = serialization.load_ssh_private_key(raw, password=None)
        assert private.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode() == key['public_key']
    metadata = app.state.store.one('SELECT body FROM ssh_keys WHERE id=?', (key['id'],))['body']
    assert raw.decode() not in metadata
    assert all(raw not in p.read_bytes() for p in (tmp_path / 'vault').iterdir() if p.is_file())
    restarted = make_app(tmp_path, monkeypatch)
    with closing(TestClient(restarted)) as other:
        login(other, restarted)
        assert other.get('/api/ssh-keys').json() == [key]
        with restarted.state.ssh_keys.private_key(key['id']) as restored:
            assert restored.bytes() == raw
