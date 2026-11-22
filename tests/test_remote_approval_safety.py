"""Approval reservation races, exercised against disposable local bare remotes."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import threading

import pytest
from test_core_graph import api, graph
from test_chatops import setup_chatops, issue, signed
from test_remote_gitops import git, configure


def paused_publication(api, tmp_path, monkeypatch):
    import kuberweaver.gitops as module
    client, app = api
    remote = tmp_path / 'reserved.git'
    git(tmp_path, 'init', '--bare', str(remote))
    configure(app.state.state_dir, remote)
    enqueued = []
    app.state.worker.enqueue = enqueued.append
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    entered, release = threading.Event(), threading.Event()
    original = module._run

    def boundary(args, env, deadline):
        if 'push' in args:
            entered.set()
            assert release.wait(10), 'test did not release publication'
        return original(args, env, deadline)

    monkeypatch.setattr(module, '_run', boundary)
    return client, app, remote, enqueued, job, entered, release


@pytest.mark.parametrize('via_chatops', [False, True])
def test_one_claim_excludes_concurrent_approval_reject_and_rollback(api, tmp_path, monkeypatch, via_chatops):
    client, app, remote, enqueued, job, entered, release = paused_publication(api, tmp_path, monkeypatch)
    if via_chatops:
        _, callback = setup_chatops(app)
        action = issue(client, job)
        approve = lambda: signed(callback, action)
    else:
        approve = lambda: client.post(f"/api/jobs/{job['id']}/approve")
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(approve)
        try:
            assert entered.wait(5)
            current = app.state.jobs.get(job['id'])
            assert current['status'] == 'approving'
            assert current['requires_manual_review'] is True
            assert current['approval_claim']['state'] == 'inflight'
            for decision in ('approve', 'reject', 'rollback'):
                assert client.post(f"/api/jobs/{job['id']}/{decision}").status_code == 409
            if via_chatops:
                assert signed(callback, action, nonce='another-nonce-000000000001').status_code == 409
            assert not enqueued
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result.status_code == 200, result.text
    stored = app.state.jobs.get(job['id'])
    assert stored['status'] == 'queued'
    assert enqueued == [job['id']]
    assert git(remote, 'rev-parse', 'refs/heads/approved') == stored['git_commit']


@pytest.mark.parametrize('drift', ['graph', 'reject', 'source', 'manifest', 'provider', 'policy', 'remote_policy'])
def test_publication_drift_never_queues_stale_approval(api, tmp_path, monkeypatch, drift):
    import kuberweaver.jobs as module
    client, app, remote, enqueued, job, entered, release = paused_publication(api, tmp_path, monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"/api/jobs/{job['id']}/approve")
        try:
            assert entered.wait(5)
            if drift in {'graph', 'reject'}:
                with app.state.store.lock:
                    current = app.state.jobs.get(job['id'])
                    if drift == 'graph':
                        current['graph']['name'] = 'Unreviewed change'
                    else:
                        # Simulate an independent durable writer; the normal
                        # reject API is separately proven to exclude the claim.
                        current['status'] = 'rejected'
                    app.state.store.put('jobs', job['id'], current)
            elif drift == 'source':
                original = app.state.catalog.fingerprint
                monkeypatch.setattr(app.state.catalog, 'fingerprint', lambda name: original(name) + '-changed')
            elif drift == 'manifest':
                original = app.state.catalog.discover
                def changed():
                    manifests = copy.deepcopy(original())
                    manifests[job['graph']['nodes'][0]['plugin_id']]['description'] = 'Changed'
                    return manifests
                monkeypatch.setattr(app.state.catalog, 'discover', changed)
            elif drift == 'provider':
                monkeypatch.setattr(module, 'execution_binding', lambda app, *args: {'changed': True})
            elif drift == 'remote_policy':
                configure(app.state.state_dir, remote, branch='different-approved-branch')
            else:
                settings = app.state.admission.settings()['policies']
                # Still-allowed policy drift must invalidate the approval too.
                settings['max_monthly_cost'] += 1
                app.state.store.put('settings', 'policies', settings)
        finally:
            release.set()
        response = future.result(timeout=10)
    assert response.status_code == 409, response.text
    stored = app.state.jobs.get(job['id'])
    assert stored['requires_manual_review'] is True
    assert stored['approval_claim']['state'] == 'conflict'
    assert stored['status'] != 'queued'
    assert not enqueued
    if drift == 'graph':
        assert stored['graph']['name'] == 'Unreviewed change'
    if drift == 'reject':
        assert stored['status'] == 'rejected'
    sha = stored['git_commit']
    assert git(remote, 'rev-parse', 'refs/heads/approved') == sha
    artifact = json.loads(git(remote, 'show', sha + ':jobs/' + job['id'] + '.json'))
    assert artifact['graph'] == job['graph']
    assert stored['remote_gitops']['verified_commit'] == sha


def test_callback_graph_is_rechecked_inside_jobs_claim(api, monkeypatch):
    client, app = api
    _, callback = setup_chatops(app)
    app.state.worker.enqueue = lambda identity: pytest.fail('stale callback queued')
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job)
    original = app.state.jobs.approve
    def interleave(identity, actor, **kwargs):
        with app.state.store.lock:
            current = app.state.jobs.get(identity)
            current['graph']['name'] = 'Changed between action reservation and job claim'
            app.state.store.put('jobs', identity, current)
        return original(identity, actor, **kwargs)
    monkeypatch.setattr(app.state.jobs, 'approve', interleave)
    response = signed(callback, action)
    assert response.status_code == 409
    assert app.state.jobs.get(job['id'])['status'] == 'pending_approval'
    assert signed(callback, action).status_code == 409
