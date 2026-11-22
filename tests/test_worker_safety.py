"""Real worker regressions; command boundaries are local simulations only."""
import json

import pytest

from test_core_graph import api, graph
from test_prepared_jobs import saved_engine


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
