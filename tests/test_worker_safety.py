"""Real worker regressions; command boundaries are local simulations only."""
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_core_graph import api, graph
from test_core_jobs import login
from test_prepared_jobs import saved_engine
from test_source_dependencies import queued_api, approve_queued


@pytest.mark.parametrize('interruption', [SystemExit, KeyboardInterrupt])
def test_interrupted_apply_requires_durable_manual_review(queued_api, interruption):
    from kuberweaver.app import create_app

    client, app, runner, _ = queued_api
    private = 'private-interruption-token'

    def interrupt_after_apply(argv, **kwargs):
        result = runner(argv, **kwargs)
        if runner.calls[-1][0] == 'apply':
            raise interruption(private)
        return result

    app.state.executor.runner = interrupt_after_apply
    finished = []
    completed = threading.Event()

    def completion(payload):
        finished.append(payload)
        completed.set()

    app.state.registry.add_hook('job_finished', completion)
    identity = approve_queued(client)
    app.state.worker.pause.set()
    assert completed.wait(10), 'Worker did not complete interrupted operation'
    job = client.get('/api/jobs/' + identity).json()
    folder = app.state.state_dir / 'jobs' / identity
    journal_path = folder / 'journal.json'
    journal_bytes = journal_path.read_bytes()
    journal = json.loads(journal_bytes)
    snapshots = {str(path): path.read_bytes() for path in (folder / 'snapshots').iterdir()}
    assert journal['status'] == 'deploying'
    assert journal['modified'] == [0]
    assert not journal.get('restored')
    assert runner.services[('app', 'web')]['version'] == '2.0.0'
    assert [call[0] for call in runner.calls].count('apply') == 1
    assert not any(call[0] == 'restore' for call in runner.calls)
    assert job['status'] == 'failed' and job['result'] is None
    assert job['requires_manual_review'] is True
    assert private not in json.dumps(job)
    assert private not in json.dumps(finished)
    assert [item['status'] for item in finished] == ['failed']
    assert finished[0]['operation'] == 'execute'
    assert client.post(f'/api/jobs/{identity}/approve').status_code == 409
    assert client.post(f'/api/jobs/{identity}/rollback').status_code == 409
    calls = list(runner.calls)
    app.state.worker.close()
    restarted = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir,
                           executor=app.state.executor, testing=True)
    with TestClient(restarted) as other:
        login(other, app.state.state_dir, 'admin')
        recovered = other.get('/api/jobs/' + identity).json()
        assert recovered['status'] == 'failed'
        assert recovered['requires_manual_review'] is True
        assert other.post(f'/api/jobs/{identity}/approve').status_code == 409
        assert other.post(f'/api/jobs/{identity}/rollback').status_code == 409
        restarted.state.worker.process(identity)  # Stale queue admission cannot reapply.
        assert runner.calls == calls
        assert journal_path.read_bytes() == journal_bytes
        assert snapshots and all(Path(path).read_bytes() == data for path, data in snapshots.items())


@pytest.mark.parametrize('post_publication_error', [None, OSError, SystemExit, KeyboardInterrupt])
def test_preparation_completion_survives_deferred_http_approval(api, monkeypatch, post_publication_error):
    client, app = api
    provider, _ = saved_engine(api)
    published = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    finished = []
    lock_ownership = []
    original_emit = app.state.jobs.emit
    original_notify = app.state.jobs.notify

    def defer_completion(identity, phase, level, message):
        if message == 'Saved plan prepared; awaiting explicit administrator approval':
            assert not app.state.store.lock._is_owned()
            published.set()
            assert release.wait(10), 'Approval did not release preparation completion'
            if post_publication_error:
                raise post_publication_error('private-post-publication-token')
        return original_emit(identity, phase, level, message)

    def notify(event, payload):
        if event == 'job_finished':
            lock_ownership.append(app.state.store.lock._is_owned())
            assert app.state.worker.active_identity == payload['job_id']
        return original_notify(event, payload)

    def completion(payload):
        finished.append(payload)
        if len(finished) == 2:
            completed.set()

    monkeypatch.setattr(app.state.jobs, 'emit', defer_completion)
    monkeypatch.setattr(app.state.jobs, 'notify', notify)
    app.state.registry.add_hook('job_finished', completion)
    response = client.post('/api/jobs', json={'graph': graph()})
    assert response.status_code == 201, response.text
    identity = response.json()['id']
    try:
        assert published.wait(10), 'Preparation did not publish its result'
        ready = client.get('/api/jobs/' + identity).json()
        assert ready['status'] == 'pending_approval' and ready['result'] is None
        approved = client.post(f'/api/jobs/{identity}/approve')
        assert approved.status_code == 200, approved.text
        assert approved.json()['status'] == 'queued'
        commands = (provider.directory / 'commands.jsonl').read_text()
        assert 'apply' not in commands
        assert not finished
    finally:
        release.set()
    assert completed.wait(10), 'Execute did not finish after deferred approval'
    assert [item['status'] for item in finished] == ['pending_approval', 'succeeded']
    assert [item['operation'] for item in finished] == ['execute', 'execute']
    assert lock_ownership == [False, False]
    final = client.get('/api/jobs/' + identity).json()
    assert final['status'] == 'succeeded'
    app.state.worker.process(identity)  # Duplicate queue entries emit no completion.
    assert len(finished) == 2


@pytest.mark.parametrize('post_publication_error', [None, OSError, SystemExit, KeyboardInterrupt])
def test_execution_completion_survives_deferred_http_rollback(queued_api, monkeypatch, post_publication_error):
    client, app, runner, _ = queued_api
    published = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    finished = []
    lock_ownership = []
    original_emit = app.state.jobs.emit
    original_notify = app.state.jobs.notify

    def defer_completion(identity, phase, level, message):
        if message == 'Execution finished: succeeded':
            assert not app.state.store.lock._is_owned()
            published.set()
            assert release.wait(10), 'Rollback did not release execution completion'
            if post_publication_error:
                raise post_publication_error('private-post-publication-token')
        return original_emit(identity, phase, level, message)

    def notify(event, payload):
        if event == 'job_finished':
            lock_ownership.append(app.state.store.lock._is_owned())
            assert app.state.worker.active_identity == payload['job_id']
        return original_notify(event, payload)

    def completion(payload):
        finished.append(payload)
        if len(finished) == 2:
            completed.set()

    monkeypatch.setattr(app.state.jobs, 'emit', defer_completion)
    monkeypatch.setattr(app.state.jobs, 'notify', notify)
    app.state.registry.add_hook('job_finished', completion)
    identity = approve_queued(client)
    app.state.worker.pause.set()
    try:
        assert published.wait(10), 'Execution did not publish its result'
        response = client.post(f'/api/jobs/{identity}/rollback')
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'queued'
        assert not any(call[0] == 'restore' for call in runner.calls)
        assert not finished
    finally:
        release.set()
    assert completed.wait(10), 'Rollback did not finish'
    assert [(item['status'], item['operation']) for item in finished] == [
        ('succeeded', 'execute'), ('rolled_back', 'rollback')]
    assert lock_ownership == [False, False]
    assert client.get('/api/jobs/' + identity).json()['status'] == 'rolled_back'
    app.state.worker.process(identity)
    assert len(finished) == 2


@pytest.mark.parametrize('preparing', [False, True])
@pytest.mark.parametrize('post_publication_error', [OSError, SystemExit, KeyboardInterrupt])
def test_published_completion_survives_audit_failure(api, monkeypatch, preparing, post_publication_error):
    from test_core_jobs import RecordingExecutor, install_executor

    client, app = api
    app.state.worker.close()
    if preparing:
        saved_engine(api)
    else:
        install_executor(app, RecordingExecutor())
    response = client.post('/api/jobs', json={'graph': graph()})
    assert response.status_code == 201, response.text
    identity = response.json()['id']
    if not preparing:
        assert client.post(f'/api/jobs/{identity}/approve').status_code == 200
    finished = []
    original_audit = app.state.store.audit
    expected_status = 'pending_approval' if preparing else 'succeeded'
    action = 'job.prepared' if preparing else 'job.succeeded'
    published = []

    def fail_audit(actor, event, target=None, *args, **kwargs):
        if event == action:
            published.append(app.state.jobs.get(identity))
            raise post_publication_error('private-audit-token')
        return original_audit(actor, event, target, *args, **kwargs)

    def completion(payload):
        assert not app.state.store.lock._is_owned()
        assert app.state.worker.active_identity == identity
        finished.append(payload)

    monkeypatch.setattr(app.state.store, 'audit', fail_audit)
    app.state.registry.add_hook('job_finished', completion)
    app.state.worker.process(identity)
    final = app.state.jobs.get(identity)
    assert client.get('/api/jobs/' + identity).json()['status'] == expected_status
    assert len(published) == 1
    assert final == published[0]
    assert final['status'] == expected_status
    assert not final['requires_manual_review']
    assert [(item['status'], item['operation']) for item in finished] == [(expected_status, 'execute')]
    assert 'private-audit-token' not in json.dumps(final) + json.dumps(finished)
    assert app.state.worker.active_identity is None
    app.state.worker.process(identity)
    assert len(finished) == 1
