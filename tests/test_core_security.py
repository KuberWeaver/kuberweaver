import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from test_core_auth import bootstrap
from test_core_graph import api, graph, manifest
from test_core_jobs import RecordingExecutor, wait_job, login, install_executor


def test_production_lifespan_allows_only_one_worker_per_state_dir(tmp_path):
    from backend.app import create_app
    state = tmp_path / 'data'
    with TestClient(create_app(state_dir=state), base_url='http://localhost') as first:
        assert first.get('/healthz').status_code == 200
        with pytest.raises(RuntimeError, match='already running'):
            with TestClient(create_app(state_dir=state), base_url='http://localhost'):
                pass


def test_inline_secrets_never_enter_persistent_artifacts(api):
    client, app = api
    candidate = graph()
    marker = 'test-inline-secret-must-not-persist'
    candidate['nodes'][0]['config']['password'] = marker
    response = client.post('/api/jobs', json={'graph': candidate})
    assert response.status_code == 422
    for path in app.state.state_dir.rglob('*'):
        if path.is_file():
            assert marker.encode() not in path.read_bytes(), path


def test_approved_graph_tamper_stops_before_executor(api):
    client, app = api
    executor = RecordingExecutor()
    install_executor(app, executor)
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    client.post(f"/api/jobs/{job['id']}/approve")
    modified = app.state.store.get('jobs', job['id'])
    modified['graph']['nodes'][0]['config']['targets'] = ['branch-client']
    app.state.store.put('jobs', job['id'], modified)
    app.state.worker.pause.set()
    assert wait_job(client, job['id'])['status'] == 'failed'
    assert not executor.calls


def test_approved_plugin_code_drift_denies_execution(api):
    client, app = api
    executor = RecordingExecutor()
    install_executor(app, executor)
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    client.post(f"/api/jobs/{job['id']}/approve")
    (app.state.plugins_dir / 'sample-service' / 'reviewed-playbook.yml').write_text('changed implementation')
    app.state.worker.pause.set()
    assert wait_job(client, job['id'])['status'] == 'failed'
    assert not executor.calls


def test_explicit_trust_is_a_strict_boolean(api):
    client, app = api
    directory = app.state.state_dir / 'extensions' / 'safe-addon'
    directory.mkdir(parents=True)
    (directory / 'plugin.yaml').write_text('id: safe-addon\nversion: 1.0.0\nentrypoint: module.py\ncapabilities: []\n')
    (directory / 'module.py').write_text('def register(ctx):\n    pass\n')
    for value in ['true', 1]:
        assert client.post('/api/extensions/safe-addon/enable', json={'trusted': value}).status_code == 422


def test_sensitive_key_variants_are_rejected_explicitly(api):
    client, _ = api
    for field in ['access_token', 'database_password', 'ansible_extra_args']:
        candidate = graph()
        candidate['nodes'][0]['config'][field] = 'must-not-store'
        response = client.post('/api/plans/validate', json=candidate)
        assert 'unsafe_field' in {row['code'] for row in response.json()['violations']}


def test_terminal_sse_replays_persisted_events(api):
    client, _ = api
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    client.post(f"/api/jobs/{job['id']}/reject")
    result = client.get(f"/api/jobs/{job['id']}/events")
    assert result.status_code == 200
    assert 'text/event-stream' in result.headers['content-type']
    assert 'event: log' in result.text
    assert 'event: done' in result.text
    assert 'rejected' in result.text
    replay = client.get(f"/api/jobs/{job['id']}/events", headers={'Last-Event-ID': '1'})
    assert 'id: 1\n' not in replay.text
