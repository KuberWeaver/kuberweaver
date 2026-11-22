"""Real queue lifecycle and application-level persisted rollback recovery."""
import json
import shutil
import time
import pytest
from pathlib import Path

from test_core_graph import api, graph
from test_core_jobs import RecordingExecutor, login, wait_job, install_executor


ROOT = Path(__file__).resolve().parents[1]


def test_example_observes_actual_worker_and_selected_diagnostics_reads_durable_evidence(api):
    client, app = api
    shutil.copytree(ROOT / 'examples/extensions/lab-notes', app.state.extensions_dir / 'lab-notes')
    assert client.post('/api/extensions/lab-notes/enable', json={'trusted': True}).status_code == 200
    response = client.patch('/api/providers', json={'bindings': {'diagnostics': 'lab-notes'}})
    assert response.status_code == 200, response.text
    install_executor(app, RecordingExecutor())
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    assert wait_job(client, job['id'])['status'] == 'succeeded'
    record = app.state.state_dir / 'plugin-data/lab-notes/last-job.json'
    for _ in range(100):
        if record.exists():
            break
        time.sleep(.01)
    evidence = json.loads(record.read_text())
    assert evidence['event'] == 'job_finished' and evidence['job_id'] == job['id']
    assert evidence['status'] == 'succeeded' and evidence['time']
    diagnostics = client.get('/api/diagnostics').json()
    assert diagnostics['read_only'] is True
    assert diagnostics['summary']['total'] == 1
    assert diagnostics['findings'][0]['provenance']['job_id'] == job['id']
    assert client.post('/api/extensions/lab-notes/disable').status_code == 200
    assert client.get('/api/diagnostics').status_code == 503
    assert client.get('/api/jobs').status_code == 200


def test_before_plan_runs_after_schema_auth_and_cannot_override_admission(api):
    client, app = api
    seen = []
    def observe(payload):
        seen.append(dict(payload))
        payload['status'] = 'succeeded'
        payload['environment'] = 'dev'
        raise SystemExit('sensitive observer failure')
    app.state.registry.add_hook('before_plan', observe)
    invalid = graph()
    invalid['nodes'][0]['config'] = {'cpu': 10000}
    response = client.post('/api/plans/validate', json=invalid)
    assert response.status_code == 200 and response.json()['allowed'] is False
    assert len(seen) == 1
    assert seen[0]['status'] == 'planning' and seen[0]['time']
    assert 'graph' not in seen[0]
    assert client.post('/api/jobs', json={'graph': invalid}).status_code == 422
    assert len(seen) == 2
    assert client.get('/api/jobs').json() == []
    assert client.post('/api/plans/validate', json={'invalid': 'schema'}).status_code == 422
    assert len(seen) == 2
    client.post('/api/auth/logout')
    assert client.post('/api/plans/validate', json=graph()).status_code == 401
    assert len(seen) == 2


@pytest.mark.parametrize('outcome', ['succeeded', 'failed', 'exception'])
def test_canonical_worker_lifecycle_is_sanitized_nonfatal_and_finishes_once(api, outcome):
    client, app = api
    seen = []
    def broken(payload):
        payload['status'] = 'spoofed'
        raise RuntimeError('observer-secret-must-not-leak')
    for event in ('before_plan', 'after_approval', 'job_started', 'job_finished'):
        app.state.registry.add_hook(event, broken)
        app.state.registry.add_hook(event, lambda payload: seen.append(payload))

    class Outcome(RecordingExecutor):
        def execute(self, job, emit):
            if outcome == 'exception':
                raise ValueError('executor-secret-must-not-leak')
            return {'status': outcome, 'private_output': 'not-in-hook-payload'}
    install_executor(app, Outcome())
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    terminal = wait_job(client, job['id'])
    for _ in range(100):
        if any(row['event'] == 'job_finished' for row in seen):
            break
        time.sleep(.01)
    assert [row['event'] for row in seen] == ['before_plan', 'after_approval', 'job_started', 'job_finished']
    assert terminal['status'] == ('failed' if outcome == 'exception' else outcome)
    assert seen[-1]['status'] == terminal['status']
    assert all(set(row) == {'job_id', 'status', 'time', 'operation', 'environment', 'event'} for row in seen)
    assert all(row['job_id'] == job['id'] and row['time'] for row in seen)
    assert 'secret-must-not-leak' not in json.dumps(client.get('/api/jobs').json())
    app.state.worker.process(job['id'])  # Duplicate queue item must not emit a second completion.
    assert len([row for row in seen if row['event'] == 'job_finished']) == 1
    if outcome == 'succeeded':
        assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 200
        assert wait_job(client, job['id'])['status'] == 'rolled_back'
        for _ in range(100):
            if len([row for row in seen if row['event'] == 'job_finished']) == 2:
                break
            time.sleep(.01)
        assert [row['status'] for row in seen if row['event'] == 'job_finished'] == ['succeeded', 'rolled_back']


@pytest.mark.parametrize('status,manual_review', [('failed', False), ('rollback_failed', False), ('rollback_failed', True)])
def test_rollback_rejects_failed_or_unproven_or_interrupted_jobs(api, status, manual_review):
    client, app = api
    executor = install_executor(app, RecordingExecutor())
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    assert wait_job(client, job['id'])['status'] == 'succeeded'
    persisted = app.state.jobs.get(job['id'])
    persisted.update(status=status, requires_manual_review=manual_review, result={'status': 'rollback_failed'})
    app.state.store.put('jobs', job['id'], persisted)
    assert client.get(f"/api/jobs/{job['id']}").json()['can_rollback'] is False
    assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 409
    login(client, app.state.state_dir, 'operator')
    assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 403
    assert executor.calls == [('execute', job['id'])]
