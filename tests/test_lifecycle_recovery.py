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


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('restart', [False, True])
def test_real_executor_partial_rollback_can_be_retried_through_serialized_api(api, automatic, restart, tmp_path, monkeypatch):
    from kuberweaver import execution as execution_module
    from kuberweaver.execution import Executor
    from test_execution import SimulatedAnsible
    ssh_config = tmp_path / 'ssh_config'
    ssh_config.write_text('Host infra-app infra-branch-client\n  StrictHostKeyChecking yes\n')
    monkeypatch.setattr(execution_module, 'SSH_CONFIG', ssh_config)
    client, app = api
    shutil.copytree(ROOT / 'plugins/nginx', app.state.plugins_dir / 'nginx')

    class PartialRestore(SimulatedAnsible):
        fail_branch = True
        def __call__(self, argv, **kwargs):
            params = json.loads(Path(argv[argv.index('--extra-vars') + 1][1:]).read_text())
            self.fail_restore = self.fail_branch and params['kw_request']['target'] == 'branch-client'
            return super().__call__(argv, **kwargs)

    runner = PartialRestore()
    app.state.executor = Executor(app.state.state_dir, app.state.plugins_dir)
    app.state.executor.runner = runner
    candidate = graph()
    candidate['nodes'][0].update(id='web', plugin_id='nginx', config={'version': '2.0.0'})
    candidate['failure_mode'] = 'rolling' if automatic else 'none'
    job = client.post('/api/jobs', json={'graph': candidate}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    first = wait_job(client, job['id'])
    if not automatic:
        assert first['status'] == 'succeeded', first
        assert first['can_rollback'] is True
        assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 200
        first = wait_job(client, job['id'])
    assert first['status'] == 'rollback_failed', first
    assert first['can_rollback'] is True
    journal_path = app.state.state_dir / 'jobs' / job['id'] / 'journal.json'
    journal = json.loads(journal_path.read_text())
    assert journal['status'] == 'rollback_failed' and journal['restored'] == [0]
    assert [call[1] for call in runner.calls if call[0] == 'restore'].count('app') == 1
    runner.fail_branch = False
    from contextlib import ExitStack
    from fastapi.testclient import TestClient
    from kuberweaver.app import create_app
    with ExitStack() as stack:
        if restart:
            executor = Executor(app.state.state_dir, app.state.plugins_dir)
            executor.runner = runner
            app = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir,
                             executor=executor, testing=True)
            client = stack.enter_context(TestClient(app))
            login(client, app.state.state_dir, 'admin')
            assert client.get(f"/api/jobs/{job['id']}").json()['can_rollback'] is True
        app.state.worker.pause.clear()
        assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 200
        assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 409
        assert client.patch('/api/providers', json={'bindings': {'execution': 'default'}}).status_code == 409
        app.state.worker.pause.set()
        retried = wait_job(client, job['id'])
        assert retried['status'] == 'rolled_back', retried
        assert retried['can_rollback'] is False
        assert [call[1] for call in runner.calls if call[0] == 'restore'] == ['branch-client', 'app', 'branch-client']
        assert sorted(json.loads(journal_path.read_text())['restored']) == [0, 1]


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
