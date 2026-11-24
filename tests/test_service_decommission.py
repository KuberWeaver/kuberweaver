"""Service retirement: administrator-only, pinned and provider-verified removal.

Retirement is deliberately separate from rollback:

* the request is an explicit administrator decision and only queues the job on
  the one serialized worker, so nothing runs inline in the HTTP request;
* the job's *pinned* execution provider must itself declare
  ``decommission(job, emit)`` and must verify ``{'status': 'decommissioned'}``;
* rollback is never substituted, before or after the fact, and rollback
  capability alone never implies retirement;
* a rejected, failed or interrupted retirement is persisted as
  ``decommission_failed`` and requires manual review.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from test_core_jobs import RecordingExecutor, install_executor, login
from test_core_graph import api, graph, manifest  # noqa: F401 - fixtures
from test_studio import publish, save_blueprint

VERIFIED = {'status': 'decommissioned'}


class RetiringExecutor(RecordingExecutor):
    """A provider that explicitly supports retirement, unlike plain rollback."""

    def __init__(self, result=None, error=None):
        super().__init__()
        self.result = dict(VERIFIED if result is None else result)
        self.error = error
        self.observed = []

    def decommission(self, job, emit):
        self.calls.append(('decommission', job['id']))
        self.observed.append(job['status'])
        emit('decommission', 'info', 'Test-provider removal readback')
        if self.error is not None:
            raise self.error
        return dict(self.result)


def wait(client, identity, statuses, timeout=10):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = client.get('/api/jobs/' + identity).json()
        if job['status'] in statuses:
            return job
        time.sleep(.01)
    raise AssertionError(job)


def deployed_service(client, app, executor):
    """Request, approve and deploy one offering; return the succeeded job."""
    install_executor(app, executor)
    record = save_blueprint(client)
    offering = publish(client, 'Web stack', record['id']).json()
    login(client, app.state.state_dir, 'operator')
    identity = client.post(f"/api/studio/offerings/{offering['id']}/request").json()['job_id']
    login(client, app.state.state_dir, 'admin')
    assert client.post(f'/api/jobs/{identity}/approve').status_code == 200
    job = wait(client, identity, {'succeeded'})
    assert job['id'] == identity
    return job


def test_decommission_requires_admin_and_a_completed_service_request(api):
    client, app = api
    install_executor(app, RetiringExecutor())
    # A plain job is not a service request, so retirement does not exist for it.
    plain = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/studio/services/{plain['id']}/decommission").status_code == 404
    assert client.post('/api/studio/services/' + '0' * 32 + '/decommission').status_code == 404
    record = save_blueprint(client)
    offering = publish(client, 'Web stack', record['id']).json()
    login(client, app.state.state_dir, 'operator')
    pending = client.post(f"/api/studio/offerings/{offering['id']}/request").json()['job_id']
    # A request that was never approved and applied has nothing to retire.
    assert client.post(f'/api/studio/services/{pending}/decommission').status_code == 403
    login(client, app.state.state_dir, 'admin')
    assert client.post(f'/api/studio/services/{pending}/decommission').status_code == 409
    assert app.state.store.get('jobs', pending)['status'] == 'pending_approval'
    for username in ('operator', 'viewer'):
        login(client, app.state.state_dir, username)
        assert client.post(f'/api/studio/services/{pending}/decommission').status_code == 403
    client.cookies.clear()
    assert client.post(f'/api/studio/services/{pending}/decommission').status_code == 401
    assert app.state.store.get('jobs', pending)['status'] == 'pending_approval'


def test_retirement_requires_an_explicit_provider_capability(api):
    client, app = api
    executor = RecordingExecutor()  # rollback only: retirement is not implied
    job = deployed_service(client, app, executor)
    assert job['can_rollback'] is True
    assert job['can_decommission'] is False
    assert client.get('/api/jobs').json()[0]['can_decommission'] is False
    response = client.post(f"/api/studio/services/{job['id']}/decommission")
    assert response.status_code == 409, response.text
    stored = app.state.store.get('jobs', job['id'])
    assert stored['status'] == 'succeeded'
    assert 'operation' not in stored or stored['operation'] == 'execute'
    # Nothing was invoked by the refused request and rollback never ran.
    assert executor.calls == [('execute', job['id'])]


def test_decommission_queues_on_the_one_worker_and_verifies_removal(api):
    client, app = api
    executor = RetiringExecutor()
    job = deployed_service(client, app, executor)
    assert job['can_decommission'] is True
    assert executor.calls == [('execute', job['id'])]
    # Pause the single consumer: a successful response must not have run anything.
    app.state.worker.pause.clear()
    try:
        response = client.post(f"/api/studio/services/{job['id']}/decommission")
        assert response.status_code == 200, response.text
        queued = response.json()
        assert queued['id'] == job['id']
        assert queued['status'] == 'decommission_queued'
        assert queued['operation'] == 'decommission'
        assert queued['can_decommission'] is False
        assert executor.calls == [('execute', job['id'])]
        assert app.state.store.get('jobs', job['id'])['status'] == 'decommission_queued'
    finally:
        app.state.worker.pause.set()
    done = wait(client, job['id'], {'decommissioned', 'decommission_failed'})
    assert done['status'] == 'decommissioned', done
    assert done['result']['status'] == 'decommissioned'
    assert done['finished_at']
    assert done['can_decommission'] is False
    assert done['can_rollback'] is False
    assert executor.calls == [('execute', job['id']), ('decommission', job['id'])]
    assert executor.observed == ['decommissioning']
    assert any(event['phase'] == 'decommission' for event in done['events'])
    audit = [row['action'] for row in client.get('/api/audit').json()]
    assert 'job.decommission_requested' in audit
    assert 'job.decommissioned' in audit
    login(client, app.state.state_dir, 'operator')
    assert client.get('/api/studio/services').json()[0]['status'] == 'decommissioned'


def test_unverified_retirement_is_persisted_and_needs_review(api):
    client, app = api
    executor = RetiringExecutor(result={'status': 'succeeded'})
    job = deployed_service(client, app, executor)
    assert client.post(f"/api/studio/services/{job['id']}/decommission").status_code == 200
    failed = wait(client, job['id'], {'decommissioned', 'decommission_failed'})
    assert failed['status'] == 'decommission_failed', failed
    assert failed['requires_manual_review'] is True
    assert 'retirement failed' in failed['error'].lower()
    assert failed['can_decommission'] is False
    assert executor.calls == [('execute', job['id']), ('decommission', job['id'])]
    stored = app.state.store.get('jobs', job['id'])
    assert stored['status'] == 'decommission_failed'
    assert stored['requires_manual_review'] is True
    assert stored['finished_at']
    # A failed retirement is never silently retried, and never becomes a rollback.
    assert client.post(f"/api/studio/services/{job['id']}/decommission").status_code == 409
    assert executor.calls == [('execute', job['id']), ('decommission', job['id'])]


def test_provider_exception_is_persisted_without_fallback_to_rollback(api):
    client, app = api
    executor = RetiringExecutor(error=RuntimeError('provider-detail-must-not-leak'))
    job = deployed_service(client, app, executor)
    assert client.post(f"/api/studio/services/{job['id']}/decommission").status_code == 200
    failed = wait(client, job['id'], {'decommissioned', 'decommission_failed'})
    assert failed['status'] == 'decommission_failed', failed
    assert failed['requires_manual_review'] is True
    assert 'provider-detail-must-not-leak' not in json.dumps(failed)
    assert executor.calls == [('execute', job['id']), ('decommission', job['id'])]


def test_retirement_uses_the_pinned_provider_not_the_current_binding(api):
    from backend.providers import provider_provenance

    client, app = api
    retiring, rebound = RetiringExecutor(), RetiringExecutor()
    app.state.registry.register('execution', 'retiring', retiring)
    app.state.provider_loaded_sources[('execution', 'retiring')] = provider_provenance(
        app, 'execution', 'retiring')
    app.state.registry.register('execution', 'rebound', rebound)
    app.state.provider_loaded_sources[('execution', 'rebound')] = provider_provenance(
        app, 'execution', 'rebound')
    body = manifest(app.state.plugins_dir, 'retiring-service')
    body['execution_provider'] = 'retiring'
    (app.state.plugins_dir / 'retiring-service' / 'manifest.yaml').write_text(json.dumps(body))
    candidate = graph()
    candidate['nodes'][0]['plugin_id'] = 'retiring-service'
    record = save_blueprint(client, 'Retiring stack', candidate)
    offering = publish(client, 'Retiring stack', record['id']).json()
    login(client, app.state.state_dir, 'operator')
    identity = client.post(f"/api/studio/offerings/{offering['id']}/request").json()['job_id']
    login(client, app.state.state_dir, 'admin')
    assert client.post(f'/api/jobs/{identity}/approve').status_code == 200
    assert wait(client, identity, {'succeeded'})['status'] == 'succeeded'
    assert retiring.calls == [('execute', identity)]
    assert client.get('/api/jobs/' + identity).json()['can_decommission'] is True
    # The global binding is irrelevant after approval: the pinned provider retires.
    app.state.provider_bindings['execution'] = 'rebound'
    assert client.post(f'/api/studio/services/{identity}/decommission').status_code == 200
    assert wait(client, identity, {'decommissioned'})['status'] == 'decommissioned'
    assert retiring.calls == [('execute', identity), ('decommission', identity)]
    assert rebound.calls == []


def test_retirement_refuses_a_changed_execution_pin(api):
    client, app = api
    executor = RetiringExecutor()
    job = deployed_service(client, app, executor)
    stored = app.state.store.get('jobs', job['id'])
    stored['approved_execution_provider'] = {'name': 'default', 'provenance': '0' * 64}
    app.state.store.put('jobs', job['id'], stored)
    assert client.get('/api/jobs/' + job['id']).json()['can_decommission'] is False
    assert client.post(f"/api/studio/services/{job['id']}/decommission").status_code == 409
    assert app.state.store.get('jobs', job['id'])['status'] == 'succeeded'
    assert executor.calls == [('execute', job['id'])]


@pytest.mark.parametrize('status', ['decommission_queued', 'decommissioning'])
def test_interrupted_retirement_requires_manual_review(api, status):
    from backend.app import create_app

    client, app = api
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    persisted = app.state.store.get('jobs', job['id'])
    persisted.update(status=status, operation='decommission',
                     service_request={'offering_id': 'a' * 32, 'title': 'Web stack'})
    app.state.store.put('jobs', job['id'], persisted)
    restarted = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir,
                           testing=True)
    with TestClient(restarted) as other:
        login(other, app.state.state_dir, 'admin')
        recovered = other.get('/api/jobs/' + job['id']).json()
        assert recovered['status'] == 'decommission_failed'
        assert recovered['requires_manual_review'] is True
        assert 'manual review' in recovered['error'].lower()
        assert recovered['can_decommission'] is False
        # Review is mandatory: an interrupted retirement is not silently re-run.
        assert other.post(f'/api/studio/services/{job["id"]}/decommission').status_code == 409
    assert restarted.state.store.get('jobs', job['id'])['status'] == 'decommission_failed'


def test_statuses_are_visible_to_the_existing_frontend_contract(api):
    client, app = api
    executor = RetiringExecutor()
    job = deployed_service(client, app, executor)
    app.state.worker.pause.clear()
    try:
        queued = client.post(f"/api/studio/services/{job['id']}/decommission").json()
    finally:
        app.state.worker.pause.set()
    assert queued['status'] == 'decommission_queued'
    assert wait(client, job['id'], {'decommissioned'})['status'] == 'decommissioned'
    overview = client.get('/api/overview').json()
    assert overview['counts']['decommissioned'] == 1
    assert overview['counts']['decommission_queued'] == 0
