"""Readiness separates liveness from local dependency evidence.

The probe is unauthenticated, so these tests also prove it never leaks a path,
driver message or secret, and never mutates state.
"""
import pytest
from fastapi.testclient import TestClient

from test_core_graph import api, manifest
from test_postgresql_store import pg_file


def check(body, name):
    return body['checks'][name]


def test_readyz_reports_each_dependency(api):
    client, app = api
    response = client.get('/readyz')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert body['status'] == 'ready'
    assert body['service'] == 'kuberweaver'
    assert set(body) == {'status', 'service', 'checks'}
    assert check(body, 'database') == {'status': 'ok', 'backend': 'sqlite'}
    assert check(body, 'plugins') == {'status': 'ok', 'manifests': 1}
    assert check(body, 'vault') == {'status': 'unconfigured', 'provider': 'restricted-file'}


def test_readyz_fails_when_storage_is_unavailable(api, monkeypatch):
    from backend.storage import StorageError
    client, app = api

    def failed(*args, **kwargs):
        raise StorageError('private-storage-marker')

    monkeypatch.setattr(app.state.store, 'one', failed)
    response = client.get('/readyz')
    assert response.status_code == 503
    body = response.json()
    assert body['status'] == 'not_ready'
    assert check(body, 'database') == {'status': 'unavailable', 'backend': 'sqlite'}
    assert check(body, 'plugins')['status'] == 'ok'
    assert 'private-storage-marker' not in response.text


def test_readyz_fails_when_the_plugin_directory_is_missing(api, monkeypatch, tmp_path):
    client, app = api
    monkeypatch.setattr(app.state, 'plugins_dir', tmp_path / 'absent')
    response = client.get('/readyz')
    assert response.status_code == 503
    assert check(response.json(), 'plugins') == {'status': 'unavailable', 'manifests': 0}


def test_readyz_fails_when_the_plugin_directory_holds_no_manifest(api, monkeypatch, tmp_path):
    client, app = api
    empty = tmp_path / 'empty'
    empty.mkdir()
    monkeypatch.setattr(app.state, 'plugins_dir', empty)
    response = client.get('/readyz')
    assert response.status_code == 503
    assert check(response.json(), 'plugins') == {'status': 'unavailable', 'manifests': 0}


def test_readyz_fails_when_the_plugin_directory_is_a_file(api, monkeypatch, tmp_path):
    client, app = api
    target = tmp_path / 'plugins-file'
    target.write_text('not a directory')
    monkeypatch.setattr(app.state, 'plugins_dir', target)
    assert client.get('/readyz').status_code == 503


def test_readyz_fails_when_the_plugin_directory_is_not_configured(api, monkeypatch):
    client, app = api
    monkeypatch.setattr(app.state, 'plugins_dir', None)
    response = client.get('/readyz')
    assert response.status_code == 503
    assert check(response.json(), 'plugins') == {'status': 'unavailable', 'manifests': 0}


def test_readyz_fails_when_a_selected_vault_is_unavailable(api):
    from backend.secrets import SecretError

    class BrokenVault:
        def resolve(self, name):
            raise SecretError('vault unavailable')

        def verify(self):
            raise SecretError('private-vault-marker')

    client, app = api
    app.state.registry.register('secrets', 'fixture-vault', BrokenVault(), {'status': 'configured'})
    app.state.provider_bindings['secrets'] = 'fixture-vault'
    response = client.get('/readyz')
    assert response.status_code == 503
    assert check(response.json(), 'vault') == {'status': 'unavailable', 'provider': 'fixture-vault'}
    assert 'private-vault-marker' not in response.text


def test_readyz_reports_unexpected_vault_errors_without_details(api):
    class ExplodingVault:
        def resolve(self, name):
            raise RuntimeError('private-vault-marker')

        def verify(self):
            raise RuntimeError('private-vault-marker')

    client, app = api
    app.state.registry.register('secrets', 'exploding-vault', ExplodingVault(), {'status': 'configured'})
    app.state.provider_bindings['secrets'] = 'exploding-vault'
    response = client.get('/readyz')
    assert response.status_code == 503
    assert response.json()['status'] == 'not_ready'
    assert 'private-vault-marker' not in response.text


def test_readyz_treats_an_explicit_false_verification_as_a_refusal(api):
    class RefusingVault:
        def resolve(self, name):
            raise RuntimeError('unused')

        def verify(self):
            return False

    client, app = api
    app.state.registry.register('secrets', 'refusing-vault', RefusingVault(), {'status': 'configured'})
    app.state.provider_bindings['secrets'] = 'refusing-vault'
    response = client.get('/readyz')
    assert response.status_code == 503
    assert check(response.json(), 'vault') == {'status': 'unavailable', 'provider': 'refusing-vault'}


def test_readyz_verifies_existing_local_vault_material(api, tmp_path):
    from backend.local_vault import LocalVaultProvider, initialize

    client, app = api
    directory = tmp_path / 'vault'
    initialize(directory)
    app.state.registry.register('secrets', 'local-vault', LocalVaultProvider(directory),
                                {'status': 'configured', 'reference_only': True})
    app.state.provider_bindings['secrets'] = 'local-vault'
    response = client.get('/readyz')
    assert response.status_code == 200
    assert check(response.json(), 'vault') == {'status': 'ok', 'provider': 'local-vault',
                                               'verification': 'material'}


def test_readyz_fails_when_local_vault_material_is_corrupt(api, tmp_path):
    from backend.local_vault import LocalVaultProvider, initialize

    client, app = api
    directory = tmp_path / 'vault'
    initialize(directory)
    (directory / 'key').write_bytes(b'A' * 44)
    (directory / 'key').chmod(0o600)
    app.state.registry.register('secrets', 'local-vault', LocalVaultProvider(directory), {'status': 'configured'})
    app.state.provider_bindings['secrets'] = 'local-vault'
    response = client.get('/readyz')
    assert response.status_code == 503
    assert check(response.json(), 'vault') == {'status': 'unavailable', 'provider': 'local-vault'}


def test_readyz_fails_when_the_vault_directory_is_missing(api, tmp_path):
    from backend.local_vault import LocalVaultProvider

    client, app = api
    app.state.registry.register('secrets', 'local-vault', LocalVaultProvider(tmp_path / 'absent'),
                                {'status': 'configured'})
    app.state.provider_bindings['secrets'] = 'local-vault'
    response = client.get('/readyz')
    assert response.status_code == 503
    assert check(response.json(), 'vault')['status'] == 'unavailable'
    assert not (tmp_path / 'absent').exists()


def test_readyz_needs_no_session_and_writes_no_state(api):
    client, app = api
    before = sorted(path.name for path in app.state.state_dir.iterdir())
    client.cookies.clear()
    response = client.get('/readyz')
    assert response.status_code == 200
    assert set(response.json()['checks']) == {'database', 'vault', 'plugins'}
    assert sorted(path.name for path in app.state.state_dir.iterdir()) == before
    assert not (app.state.state_dir / 'plugin-data').exists()


def test_readyz_accepts_an_agent_token_without_extra_scope(api):
    client, app = api
    issued = client.post('/api/agent-tokens', json={'label': 'probe', 'role': 'viewer',
                                                    'scopes': ['read:jobs'], 'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    agent = TestClient(app)
    try:
        response = agent.get('/readyz', headers={'Authorization': 'Bearer ' + issued.json()['token']})
        assert response.status_code == 200
    finally:
        agent.close()


def test_readyz_scope_mapping_is_explicit():
    from backend.agent_auth import required_scope
    assert required_scope('GET', '/readyz') == ''
    assert required_scope('POST', '/readyz') is None


def test_readyz_reports_the_postgresql_backend(tmp_path, pg_file):
    from backend.app import create_app

    plugins = tmp_path / 'plugins'
    manifest(plugins)
    app = create_app(state_dir=tmp_path / 'data', plugins_dir=plugins, testing=True)
    with TestClient(app) as client:
        response = client.get('/readyz')
        assert response.status_code == 200
        assert response.json()['checks']['database'] == {'status': 'ok', 'backend': 'postgresql'}
