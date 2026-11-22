import json
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from test_core_graph import api, graph


def test_observability_finops_blueprints_and_audit(api):
    client, app = api
    blueprint = client.post('/api/blueprints', json={'name': 'Reusable', 'graph': graph()})
    assert blueprint.status_code == 201
    identity = blueprint.json()['id']
    assert client.get('/api/blueprints/' + identity).json()['name'] == 'Reusable'
    assert len(client.get('/api/blueprints').json()) == 1
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    overview = client.get('/api/overview').json()
    assert overview['counts']['pending_approval'] == 1
    assert overview['recent_jobs'][0]['id'] == job['id']
    finops = client.get('/api/finops').json()
    # No active deployment carries a declared price, so no currency is invented.
    assert finops['by_currency'] == {} and finops['currency'] is None
    assert client.get('/api/diagnostics').json()['mode'] == 'rules'
    assert client.get('/api/diagnostics').json()['read_only'] is True
    assert any(row['action'] == 'job.submitted' for row in client.get('/api/audit').json())
    integrations = client.get('/api/integrations').json()
    assert any(row['id'] == 'gns3' and row['read_only'] for row in integrations)
    metrics = client.get('/metrics')
    assert metrics.status_code == 200
    assert 'kuberweaver_http_requests_total' in metrics.text
    assert 'kuberweaver_jobs{status="pending_approval"} 1' in metrics.text


def test_gns3_inventory_provider_only_gets_actual_upstream_data(api, monkeypatch):
    client, app = api
    calls = []
    monkeypatch.setenv('KUBERWEAVER_GNS3_URL', 'https://gns3.invalid:3080')
    monkeypatch.setenv('KUBERWEAVER_GNS3_PROJECT', 'project-uuid')

    def transport(request):
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith('/nodes'):
            return httpx.Response(200, json=[{'node_id': 'actual-node', 'name': 'Actual guest', 'status': 'started', 'node_type': 'qemu'}])
        return httpx.Response(200, json=[{'link_id': 'actual-link', 'nodes': []}])
    provider = app.state.registry.get('inventory', 'gns3')
    provider.client = httpx.Client(transport=httpx.MockTransport(transport))
    inventory = client.get('/api/infrastructure').json()
    assert inventory['nodes'][0]['id'] == 'actual-node'
    assert inventory['nodes'][0]['type'] == 'qemu'
    assert inventory['links'][0]['id'] == 'actual-link'
    assert inventory['source'] == 'gns3'
    assert inventory['errors'] == []
    assert len(calls) == 2 and all(method == 'GET' for method, _ in calls)


def test_optional_extensions_disabled_until_explicit_trust(api):
    client, app = api
    directory = app.state.state_dir / 'extensions' / 'test-addon'
    directory.mkdir(parents=True)
    (directory / 'plugin.yaml').write_text('id: test-addon\nversion: 1.0.0\nentrypoint: module.py\ncapabilities: ["providers:inventory"]\n')
    marker = app.state.state_dir / 'import-marker'
    (directory / 'module.py').write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\ndef register(ctx):\n    ctx.register('inventory', 'test-addon', object(), {{'status': 'configured'}})\n")
    report = client.get('/api/extensions').json()
    assert report['extensions'][0]['status'] == 'disabled'
    assert not marker.exists()
    assert client.post('/api/extensions/test-addon/enable', json={'trusted': False}).status_code == 422
    assert not marker.exists()
    result = client.post('/api/extensions/test-addon/enable', json={'trusted': True})
    assert result.status_code == 200, result.text
    assert marker.exists()
    report = client.get('/api/extensions').json()
    assert report['extensions'][0]['status'] == 'loaded'
    assert any(item['name'] == 'test-addon' for item in report['providers'])
    assert client.post('/api/extensions/test-addon/disable').status_code == 200
    assert not any(item['name'] == 'test-addon' for item in client.get('/api/extensions').json()['providers'])
