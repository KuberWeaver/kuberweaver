import copy
import json
import subprocess
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
from test_core_auth import bootstrap
from test_core_graph import api, graph, manifest


class RecordingExecutor:
    def __init__(self):
        self.calls = []
        self.concurrent = 0
        self.maximum = 0

    def execute(self, job, emit):
        self.concurrent += 1
        self.maximum = max(self.maximum, self.concurrent)
        self.calls.append(('execute', job['id']))
        emit('verify', 'info', 'Actual test-provider readback')
        time.sleep(0.03)
        self.concurrent -= 1
        return {'status': 'succeeded', 'targets': [{'target': 'app', 'verified': True}]}

    def rollback(self, job, emit):
        self.calls.append(('rollback', job['id']))
        emit('restore', 'info', 'Test-provider restore readback')
        return {'status': 'rolled_back', 'targets': [{'target': 'app', 'restored': True}]}


def install_executor(app, executor, name='recording'):
    """Explicitly register test-injected code before approval, never after it."""
    from kuberweaver.providers import provider_provenance
    try:
        app.state.registry.register('execution', name, executor, {'status': 'configured'})
    except ValueError:
        # A fixture may already have installed this review implementation; the
        # suite replaces it rather than failing on the duplicate.
        app.state.registry._entries[('execution', name)] = (executor, {'status': 'configured'})
    app.state.provider_loaded_sources[('execution', name)] = provider_provenance(app, 'execution', name)
    app.state.provider_bindings['execution'] = name
    return executor


def wait_job(client, identity):
    for _ in range(200):
        result = client.get('/api/jobs/' + identity).json()
        if result['status'] in {'succeeded', 'failed', 'rolled_back', 'rollback_failed'}:
            return result
        time.sleep(.01)
    raise AssertionError(result)


def login(client, state_dir, username):
    result = client.post('/api/auth/login', json={'username': username, 'password': bootstrap(state_dir)[username]})
    assert result.status_code == 200
    client.headers['X-CSRF-Token'] = result.json()['csrf_token']


def test_approval_serial_worker_gitops_and_explicit_rollback(api):
    client, app = api
    executor = RecordingExecutor()
    install_executor(app, executor)
    submitted = client.post('/api/jobs', json={'graph': graph()})
    assert submitted.status_code == 201, submitted.text
    job = submitted.json()
    assert job['status'] == 'pending_approval'
    time.sleep(.04)
    assert not executor.calls
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 409
    result = wait_job(client, job['id'])
    assert result['status'] == 'succeeded', result
    assert result['result']['targets'][0]['verified']
    repo = app.state.state_dir / 'gitops'
    readback = subprocess.run(['git', '-C', str(repo), 'show', result['git_commit'] + ':jobs/' + job['id'] + '.json'], capture_output=True, text=True, check=True)
    assert json.loads(readback.stdout)['graph']['nodes'][0]['config']['targets'] == ['app']
    assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 409
    assert wait_job(client, job['id'])['status'] == 'rolled_back'
    assert executor.calls == [('execute', job['id']), ('rollback', job['id'])]


def test_roles_revalidation_and_rejection(api):
    client, app = api
    state = app.state.state_dir
    login(client, state, 'viewer')
    assert client.post('/api/jobs', json={'graph': graph()}).status_code == 403
    assert client.get('/api/jobs').status_code == 200
    login(client, state, 'operator')
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 403
    assert client.patch('/api/settings', json={'policies': {'max_nodes': 2}}).status_code == 403
    failure = graph()
    failure['failure_mode'] = 'canary'
    assert client.post('/api/jobs', json={'graph': failure}).status_code == 422
    login(client, state, 'admin')
    client.patch('/api/settings', json={'policies': {'max_monthly_cost': 0}})
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 422
    assert client.get(f"/api/jobs/{job['id']}").json()['status'] == 'pending_approval'
    assert client.post(f"/api/jobs/{job['id']}/reject").json()['status'] == 'rejected'


def test_restart_never_reapplies_interrupted_jobs(api):
    client, app = api
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    job['status'] = 'running'
    app.state.store.put('jobs', job['id'], job)
    from kuberweaver.app import create_app
    restarted = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir, testing=True)
    with TestClient(restarted) as other:
        login(other, app.state.state_dir, 'admin')
        recovered = other.get('/api/jobs/' + job['id']).json()
        assert recovered['status'] == 'failed'
        assert recovered['requires_manual_review'] is True
        assert 'manual review' in recovered['error'].lower()
        assert other.post(f"/api/jobs/{job['id']}/approve").status_code == 409


def test_worker_revalidates_after_approval_before_apply(api):
    client, app = api
    executor = RecordingExecutor()
    install_executor(app, executor)
    # Pause the real queue consumer to deterministically introduce policy drift.
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    client.post(f"/api/jobs/{job['id']}/approve")
    client.patch('/api/settings', json={'policies': {'max_monthly_cost': 0}})
    app.state.worker.pause.set()
    result = wait_job(client, job['id'])
    assert result['status'] == 'failed'
    assert not executor.calls
