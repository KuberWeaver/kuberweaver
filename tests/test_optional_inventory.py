"""The inventory provider is opt-in.

A topology source is a lab convenience. The product must work, and read as
healthy, with no inventory provider selected at all.
"""
from pathlib import Path

from fastapi.testclient import TestClient


def bootstrap(path: Path) -> dict:
    return dict(line.split(':', 1) for line in (path / 'bootstrap-credentials.txt').read_text().splitlines())


def test_inventory_is_unbound_by_default_and_reported_as_such(tmp_path):
    from backend.app import create_app

    plugins = tmp_path / 'plugins'
    plugins.mkdir()
    app = create_app(state_dir=tmp_path / 'data', plugins_dir=plugins, testing=True)
    assert app.state.provider_bindings['inventory'] == ''
    assert app.state.provider_bindings['execution'] == ''

    with TestClient(app) as client:
        credentials = bootstrap(tmp_path / 'data')
        response = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']

        body = client.get('/api/infrastructure').json()
        assert body['bound'] is False
        assert body['provider'] is None and body['source'] is None
        assert body['nodes'] == [] and body['links'] == []
        assert 'No inventory provider' in body['message']

        metrics = client.get('/metrics').text
        assert 'kuberweaver_provider_configured{kind="inventory"} 0' in metrics
        assert 'kuberweaver_provider_probe_observed{kind="inventory"} 0' in metrics


def test_binding_an_inventory_provider_brings_the_topology_back(tmp_path):
    from backend.app import create_app

    plugins = tmp_path / 'plugins'
    plugins.mkdir()
    app = create_app(state_dir=tmp_path / 'data', plugins_dir=plugins, testing=True)
    with TestClient(app) as client:
        credentials = bootstrap(tmp_path / 'data')
        response = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']

        assert client.patch('/api/providers', json={'bindings': {'inventory': 'gns3'}}).status_code == 200
        body = client.get('/api/infrastructure').json()
        assert body.get('bound') is not False
        assert body['source'] == 'gns3'

        # Clearing it again is a supported decision, not a broken configuration.
        cleared = client.patch('/api/providers', json={'bindings': {'inventory': ''}})
        assert cleared.status_code == 200
        assert cleared.json()['bindings']['inventory'] == ''
        assert client.get('/api/infrastructure').json()['bound'] is False


def test_a_required_capability_cannot_be_cleared(tmp_path):
    from backend.app import create_app

    plugins = tmp_path / 'plugins'
    plugins.mkdir()
    app = create_app(state_dir=tmp_path / 'data', plugins_dir=plugins, testing=True)
    with TestClient(app) as client:
        credentials = bootstrap(tmp_path / 'data')
        response = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']

        # Execution is opt-in like inventory: clearing it is allowed and simply
        # leaves the deployment to each manifest's own declaration.
        cleared = client.patch('/api/providers', json={'bindings': {'execution': ''}})
        assert cleared.status_code == 200
        assert cleared.json()['bindings']['execution'] == ''
        refused = client.patch('/api/providers', json={'bindings': {'diagnostics': ''}})
        assert refused.status_code == 422
        assert 'diagnostics needs a provider' in refused.json()['detail']


def test_gns3_refuses_with_a_named_variable_when_it_is_not_configured(tmp_path, monkeypatch):
    """No lab address is compiled in: an unset variable is named, not defaulted."""
    from backend.app import create_app

    monkeypatch.delenv('KUBERWEAVER_GNS3_URL', raising=False)
    monkeypatch.delenv('KUBERWEAVER_GNS3_PROJECT', raising=False)

    plugins = tmp_path / 'plugins'
    plugins.mkdir()
    app = create_app(state_dir=tmp_path / 'data', plugins_dir=plugins, testing=True)
    with TestClient(app) as client:
        credentials = bootstrap(tmp_path / 'data')
        response = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']

        assert client.patch('/api/providers', json={'bindings': {'inventory': 'gns3'}}).status_code == 200
        body = client.get('/api/infrastructure').json()
        assert body['source'] == 'gns3'
        errors = body['errors']
        assert any(error['error_type'] == 'ConfigurationError' for error in errors), errors
        assert any('KUBERWEAVER_GNS3_URL' in str(error.get('detail')) for error in errors), errors
        assert body['nodes'] == [] and body['links'] == []
