import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from test_core_auth import bootstrap


def manifest(directory, plugin_id='sample-service'):
    path = directory / plugin_id
    path.mkdir(parents=True)
    body = {'id': plugin_id, 'name': 'Sample service', 'version': '1.0.0', 'capability': 'live',
            'engine': 'test', 'price_monthly': 3, 'currency': 'EUR',
            'executor': {'provider': 'test'},
            'defaults': {'targets': ['app'], 'port': 18090, 'cpu': 1, 'memory_mb': 256, 'disk_gb': 2},
            'schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                'targets': {'type': 'array', 'items': {'type': 'string'}},
                'port': {'type': 'integer'}, 'cpu': {'type': 'number'},
                'memory_mb': {'type': 'integer'}, 'disk_gb': {'type': 'number'},
                'public_cidr': {'type': 'string'}, 'version': {'type': 'string'}}}}
    (path / 'manifest.yaml').write_text(json.dumps(body))
    return body


def graph():
    return {'name': 'My graph', 'environment': 'dev', 'nodes': [
        {'id': 'node-1', 'plugin_id': 'sample-service', 'label': 'Service', 'position': {'x': 1, 'y': 1}, 'config': {}}],
        'edges': [], 'strategy': {'canary': 1, 'batch_size': 1}, 'failure_mode': 'none'}


@pytest.fixture
def api(tmp_path):
    from kuberweaver.app import create_app
    plugins = tmp_path / 'plugins'
    manifest(plugins)
    app = create_app(state_dir=tmp_path / 'data', plugins_dir=plugins, testing=True)
    # The product ships with no inventory provider; these suites exercise the
    # provider itself, so they bind it explicitly.
    app.state.provider_bindings['inventory'] = 'gns3'
    with TestClient(app) as client:
        credentials = bootstrap(tmp_path / 'data')
        response = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        yield client, app


def test_manifest_discovery_and_safe_validation(api):
    client, app = api
    assert client.get('/api/plugins').status_code == 200
    assert client.get('/api/plugins').json()[0]['id'] == 'sample-service'
    result = client.post('/api/plans/validate', json=graph()).json()
    assert result['allowed'], result
    assert result['cost']['monthly'] == 3
    assert result['cost']['currency'] == 'EUR'
    manifest(app.state.plugins_dir, 'another-service')
    assert len(client.get('/api/plugins').json()) == 2
    candidate = graph()
    candidate['nodes'][0]['plugin_id'] = 'another-service'
    assert client.post('/api/plans/validate', json=candidate).json()['allowed']


@pytest.mark.parametrize('config,code', [
    ({'targets': ['untrusted']}, 'target_not_allowed'),
    ({'port': 22, 'public_cidr': '0.0.0.0/0'}, 'port_not_allowed'),
    ({'command': 'rm -rf /'}, 'unsafe_field'),
    ({'password': 'never-store-this'}, 'unsafe_field'),
    ({'cpu': 10000}, 'quota_cpu'),
])
def test_invalid_graph_denied(api, config, code):
    client, _ = api
    candidate = graph()
    candidate['nodes'][0]['config'] = config
    result = client.post('/api/plans/validate', json=candidate).json()
    assert not result['allowed']
    assert code in {v['code'] for v in result['violations']}


def test_dag_unknown_edges_and_strict_model(api):
    client, _ = api
    candidate = graph()
    candidate['edges'] = [{'id': 'e1', 'source': 'node-1', 'target': 'node-1'}]
    assert 'cycle' in {v['code'] for v in client.post('/api/plans/validate', json=candidate).json()['violations']}
    candidate['edges'][0]['target'] = 'missing'
    assert 'unknown_edge' in {v['code'] for v in client.post('/api/plans/validate', json=candidate).json()['violations']}
    candidate['command'] = 'not accepted'
    assert client.post('/api/plans/validate', json=candidate).status_code == 422


def test_schema_defaults_are_applied_then_validated(api):
    client, app = api
    path = app.state.plugins_dir / 'sample-service' / 'manifest.yaml'
    value = json.loads(path.read_text())
    value['defaults'] = {}
    value['schema']['properties']['targets']['default'] = ['app']
    value['schema']['properties']['port']['default'] = 18090
    path.write_text(json.dumps(value))
    report = client.post('/api/plans/validate', json=graph()).json()
    assert report['allowed'], report
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert job['graph']['nodes'][0]['config']['targets'] == ['app']
    value['schema']['properties']['targets']['default'] = ['outside-lab']
    path.write_text(json.dumps(value))
    assert not client.post('/api/plans/validate', json=graph()).json()['allowed']


@pytest.mark.parametrize('canary', [0, 2])
def test_only_supported_canary_strategy_is_admitted(api, canary):
    client, _ = api
    candidate = graph()
    candidate['nodes'][0]['config']['targets'] = ['app', 'branch-client']
    candidate['strategy']['canary'] = canary
    assert not client.post('/api/plans/validate', json=candidate).json()['allowed']


def test_typed_policy_update_and_cost_enforcement(api):
    client, _ = api
    assert client.patch('/api/settings', json={'policies': {'max_nodes': 1, 'max_monthly_cost': 0}}).status_code == 200
    result = client.post('/api/plans/validate', json=graph()).json()
    assert 'quota_cost' in {v['code'] for v in result['violations']}
    assert client.patch('/api/settings', json={'policies': {'allowed_targets': ['attacker']}}).status_code == 422
    assert client.patch('/api/settings', json={'pricing': {'cpu': -1}}).status_code == 422  # retired rate table
    assert client.patch('/api/settings', json={'unknown_section': {'a': 1}}).status_code == 422


def test_validation_reports_provider_registration_separately_from_policy(api):
    client, app = api
    initial = client.post('/api/plans/validate', json=graph()).json()
    assert initial['allowed'] and initial['execution']['status'] == 'configured'
    assert initial['execution']['target_connectivity'] == 'not_checked'
    app.state.provider_bindings['execution'] = 'unregistered-fixture'
    unavailable = client.post('/api/plans/validate', json=graph()).json()
    assert unavailable['allowed'] and unavailable['execution']['status'] == 'unavailable'
    assert unavailable['execution']['provider'] == 'unregistered-fixture'
    assert unavailable['execution']['target_connectivity'] == 'not_checked'
