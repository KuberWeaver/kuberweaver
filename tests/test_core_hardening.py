import copy
import json
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from test_core_auth import bootstrap
from test_core_graph import api, graph, manifest
from test_core_jobs import RecordingExecutor, wait_job, login, install_executor


def test_single_worker_serializes_multiple_approved_jobs(api):
    client, app = api
    executor = RecordingExecutor()
    install_executor(app, executor)
    identities = []
    for index in range(4):
        candidate = graph()
        candidate['name'] = f'Serial {index}'
        job = client.post('/api/jobs', json={'graph': candidate}).json()
        identities.append(job['id'])
        assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    for identity in identities:
        assert wait_job(client, identity)['status'] == 'succeeded'
    assert executor.maximum == 1
    assert len(executor.calls) == 4
    outbox = app.state.state_dir / 'outbox' / 'notifications.jsonl'
    for _ in range(100):
        records = [json.loads(row) for row in outbox.read_text().splitlines()] if outbox.exists() else []
        if len(records) == 4:
            break
        time.sleep(.01)
    assert len(records) == 4
    assert all(not row['external_delivery'] for row in records)


def test_manifest_drift_denies_approved_job(api):
    client, app = api
    executor = RecordingExecutor()
    install_executor(app, executor)
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    client.post(f"/api/jobs/{job['id']}/approve")
    plugin = app.state.plugins_dir / 'sample-service' / 'manifest.yaml'
    value = json.loads(plugin.read_text())
    value['version'] = '2.0.0'
    plugin.write_text(json.dumps(value))
    app.state.worker.pause.set()
    assert wait_job(client, job['id'])['status'] == 'failed'
    assert not executor.calls


def test_colliding_target_ports_are_rejected(api):
    client, _ = api
    candidate = graph()
    other = copy.deepcopy(candidate['nodes'][0])
    other['id'] = 'node-2'
    candidate['nodes'].append(other)
    result = client.post('/api/plans/validate', json=candidate).json()
    assert not result['allowed']
    assert 'resource_conflict' in {row['code'] for row in result['violations']}


def test_yaml_policy_persists_api_updates_and_invalid_config_fails_closed(tmp_path):
    from backend.app import create_app
    state = tmp_path / 'data'
    config = state / 'config'
    config.mkdir(parents=True)
    (config / 'policies.yaml').write_text('max_monthly_cost: 12\n')
    plugins = tmp_path / 'plugins'
    manifest(plugins)
    app = create_app(state_dir=state, plugins_dir=plugins, testing=True)
    with TestClient(app) as client:
        login(client, state, 'admin')
        assert client.get('/api/settings').json()['policies']['max_monthly_cost'] == 12
        assert client.patch('/api/settings', json={'policies': {'max_monthly_cost': 7}}).status_code == 200
    with TestClient(create_app(state_dir=state, plugins_dir=plugins, testing=True)) as client:
        login(client, state, 'admin')
        assert client.get('/api/settings').json()['policies']['max_monthly_cost'] == 7
    (config / 'policies.yaml').write_text('arbitrary_command: forbidden\n')
    with TestClient(create_app(state_dir=state, plugins_dir=plugins, testing=True)) as client:
        login(client, state, 'admin')
        report = client.post('/api/plans/validate', json=graph()).json()
        assert not report['allowed']
        assert 'configuration_error' in {row['code'] for row in report['violations']}


def test_local_host_origin_and_payload_limits(api):
    client, _ = api
    for origin in ['null', 'https://evil.example', 'http://testserver.evil.example']:
        assert client.post('/api/jobs', json={'graph': graph()}, headers={'Origin': origin}).status_code == 403
    assert client.get('/healthz', headers={'Host': '['}).status_code == 400
    assert client.post('/api/jobs', content='x' * 1_100_000, headers={'Content-Type': 'application/json'}).status_code == 413
    assert client.patch('/api/settings', json={'policies': {'max_nodes': '3'}}).status_code == 422


def test_static_frontend_same_origin_and_api_fallback(tmp_path):
    from backend.app import create_app
    frontend = tmp_path / 'dist'
    frontend.mkdir()
    (frontend / 'index.html').write_text('<!doctype html><title>Real frontend fixture</title>')
    (frontend / 'app.js').write_text('console.log("fixture");')
    with TestClient(create_app(state_dir=tmp_path / 'data', frontend_dir=frontend, testing=True)) as client:
        assert 'Real frontend fixture' in client.get('/').text
        assert client.get('/app.js').status_code == 200
        assert client.get('/api/unknown').status_code == 404
        assert 'Real frontend fixture' in client.get('/canvas').text
