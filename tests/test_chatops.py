"""Signed HTTP integration; only disposable local providers/targets."""
import hashlib
import hmac
import importlib.util
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_core_graph import api, graph
from test_core_jobs import RecordingExecutor, install_executor, wait_job

KEY = b'chatops-test-key-at-least-32-bytes!!'


def setup_chatops(app):
    from kuberweaver.chatops import install_chatops_api
    from kuberweaver.secrets import RestrictedFileProvider
    path = app.state.state_dir / 'signing.key'
    path.write_bytes(KEY)
    path.chmod(0o600)
    app.state.registry.register('secrets', 'chatops-test', RestrictedFileProvider({'chatops/key': path}), {})
    app.state.provider_bindings['secrets'] = 'chatops-test'
    config = app.state.state_dir / 'config' / 'chatops.json'
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({'schema_version': 1, 'enabled': True, 'secret_ref': 'chatops/key',
                                 'actors': {'gateway:user-123': 'admin'},
                                 'action_ttl_seconds': 300, 'max_age_seconds': 60}))
    # Main app installs issuance normally; isolate only the callback adapter.
    # Isolate the adapter routes from the parent's separate middleware wiring.
    adapter = FastAPI()
    adapter.state = app.state
    install_chatops_api(adapter)
    return config, TestClient(adapter)


def issue(client, job, decision='approve'):
    result = client.post('/api/chatops/actions', json={'job_id': job['id'], 'decision': decision})
    assert result.status_code == 201, result.text
    return result.json()


def signed(client, action, nonce='nonce-00000000000000001', actor='gateway:user-123', timestamp=None, key=KEY, **updates):
    data = {'schema_version': 1, 'action_id': action['action_id'], 'actor': actor, 'nonce': nonce, **updates}
    body = json.dumps(data, separators=(',', ':')).encode()
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = hmac.new(key, b'v1.' + stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    return client.post('/api/chatops/callback', content=body, headers={
        'content-type': 'application/json', 'x-kw-timestamp': stamp, 'x-kw-signature': 'v1=' + signature})


def test_signed_approval_runs_actual_serial_worker(api):
    assert importlib.util.find_spec('kuberweaver.chatops') is not None
    client, app = api
    _, callback = setup_chatops(app)
    executor = install_executor(app, RecordingExecutor())
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job)
    assert set(action) == {'schema_version', 'action_id', 'job_id', 'graph_digest', 'decision', 'expires_at', 'callback_path'}
    assert signed(callback, action).status_code == 200
    assert wait_job(client, job['id'])['status'] == 'succeeded'
    assert executor.calls == [('execute', job['id'])]
    assert signed(callback, action).status_code == 409
    evidence = json.dumps(app.state.store.list('jobs')) + json.dumps(app.state.store.all('SELECT * FROM audit')) + json.dumps(app.state.store.list('settings'))
    assert KEY.decode() not in evidence
    assert KEY.decode() not in json.dumps(action)


@pytest.mark.parametrize('case,code', [('signature', 401), ('stale', 401), ('future', 401),
    ('unmapped', 403), ('revoked', 403), ('extra', 400), ('version', 400), ('short_nonce', 400),
    ('disabled', 503), ('missing_config', 503), ('invalid_config', 503), ('secret_unavailable', 503)])
def test_callback_fails_closed(api, case, code):
    client, app = api
    install_executor(app, RecordingExecutor())
    config, callback = setup_chatops(app)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job)
    kwargs = {}
    if case == 'signature': kwargs['key'] = b'wrong'
    if case == 'stale': kwargs['timestamp'] = int(time.time()) - 90
    if case == 'future': kwargs['timestamp'] = int(time.time()) + 90
    if case == 'unmapped': kwargs['actor'] = 'someone-else'
    if case == 'revoked': app.state.store.execute("UPDATE users SET role='viewer' WHERE username='admin'")
    if case == 'extra': kwargs['unexpected'] = 'must-not-be-reflected'
    if case == 'version': kwargs['schema_version'] = True
    if case == 'short_nonce': kwargs['nonce'] = 'tiny'
    if case == 'disabled': config.write_text('{}')
    if case == 'missing_config': config.unlink()
    if case == 'invalid_config': config.write_text('{broken')
    if case == 'secret_unavailable': (app.state.state_dir / 'signing.key').unlink()
    response = signed(callback, action, **kwargs)
    assert response.status_code == code, response.text
    assert app.state.jobs.get(job['id'])['status'] == 'pending_approval'
    assert KEY.decode() not in response.text


def test_strict_raw_body_cap_and_duplicate_keys(api):
    client, app = api
    _, callback = setup_chatops(app)
    assert callback.post('/api/chatops/callback', content=b'x' * 4097).status_code == 413
    body = b'{"schema_version":1,"schema_version":1}'
    stamp = str(int(time.time()))
    signature = hmac.new(KEY, b'v1.' + stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    response = callback.post('/api/chatops/callback', content=body, headers={
        'content-type': 'application/json', 'x-kw-timestamp': stamp, 'x-kw-signature': 'v1=' + signature})
    assert response.status_code == 400


@pytest.mark.parametrize('decision', ['approve', 'reject'])
@pytest.mark.parametrize('case', ['drift', 'expired', 'inflight', 'unknown', 'consumed'])
def test_bound_action_conflicts(api, case, decision):
    client, app = api
    install_executor(app, RecordingExecutor())
    _, callback = setup_chatops(app)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job, decision)
    key = 'chatops.action.' + action['action_id']
    stored = app.state.store.get('settings', key)
    if case == 'drift':
        job['graph']['name'] = 'Changed after review'
        app.state.store.put('jobs', job['id'], job)
    if case == 'expired': stored['expires_at'] = int(time.time()) - 1
    if case == 'inflight': stored['state'] = 'inflight'
    if case == 'consumed': stored['state'] = 'consumed'
    app.state.store.put('settings', key, stored)
    if case == 'unknown': action['action_id'] = 'f' * 64
    assert signed(callback, action).status_code == 409
    assert app.state.jobs.get(job['id'])['status'] == 'pending_approval'


def test_failed_policy_approval_preserves_action_but_burns_nonce(api):
    client, app = api
    install_executor(app, RecordingExecutor())
    _, callback = setup_chatops(app)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job)
    client.patch('/api/settings', json={'policies': {'max_monthly_cost': 0}})
    response = signed(callback, action)
    assert response.status_code == 422
    assert response.json() == {'detail': 'ChatOps decision failed'}
    assert app.state.store.get('settings', 'chatops.action.' + action['action_id'])['state'] == 'ready'
    assert signed(callback, action).status_code == 409
    client.patch('/api/settings', json={'policies': {'max_monthly_cost': 10000}})
    assert signed(callback, action, nonce='retry-0000000000000000001').status_code == 200


def test_required_remote_failure_does_not_consume_action(api):
    client, app = api
    install_executor(app, RecordingExecutor())
    _, callback = setup_chatops(app)
    config = app.state.state_dir / 'config' / 'gitops.json'
    config.write_text(json.dumps({'enabled': True, 'remote_url': str(app.state.state_dir / 'nonexistent.git'),
                                 'allow_local_test_path': True, 'branch': 'approved', 'required_before_execution': True}))
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job)
    response = signed(callback, action)
    assert response.status_code == 503
    assert response.json() == {'detail': 'ChatOps decision failed'}
    assert app.state.jobs.get(job['id'])['status'] == 'pending_approval'
    assert app.state.store.get('settings', 'chatops.action.' + action['action_id'])['state'] == 'ready'
    assert signed(callback, action).status_code == 409
    config.unlink()
    assert signed(callback, action, nonce='retry-0000000000000000001').status_code == 200


def test_reject_and_nonce_survive_restart(api):
    client, app = api
    install_executor(app, RecordingExecutor())
    _, callback = setup_chatops(app)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job, 'reject')
    assert signed(callback, action).json()['status'] == 'rejected'
    from kuberweaver.app import create_app
    restarted = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir, testing=True)
    _, after_restart = setup_chatops(restarted)
    assert signed(after_restart, action).status_code == 409
    assert signed(after_restart, action, nonce='different-0000000000000001').status_code == 409


@pytest.mark.parametrize('data', [
    {'job_id': 'not-an-id', 'decision': 'approve'},
    {'job_id': 'a' * 32, 'decision': 'rollback'},
    {'job_id': 'a' * 32, 'decision': 'approve', 'secret': 'not-reflected'},
])
def test_issue_strict_schema(api, data):
    client, app = api
    install_executor(app, RecordingExecutor())
    setup_chatops(app)
    response = client.post('/api/chatops/actions', json=data)
    assert response.status_code == 400


def test_issue_admin_only_and_pending_only(api):
    from test_core_jobs import login, RecordingExecutor, install_executor
    client, app = api
    install_executor(app, RecordingExecutor())
    setup_chatops(app)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    client.post('/api/jobs/' + job['id'] + '/reject')
    assert client.post('/api/chatops/actions', json={'job_id': job['id'], 'decision': 'approve'}).status_code == 409
    login(client, app.state.state_dir, 'viewer')
    assert client.post('/api/chatops/actions', json={'job_id': job['id'], 'decision': 'approve'}).status_code == 403


def test_signature_covers_exact_body_and_not_bearer_action(api):
    client, app = api
    install_executor(app, RecordingExecutor())
    _, callback = setup_chatops(app)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job)
    data = {'schema_version': 1, 'action_id': action['action_id'], 'actor': 'gateway:user-123', 'nonce': 'random-nonce-00000000000001'}
    body = json.dumps(data).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(KEY, b'v1.' + stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    headers = {'x-kw-timestamp': stamp, 'x-kw-signature': 'v1=' + signature, 'content-type': 'application/json'}
    assert callback.post('/api/chatops/callback', content=body + b' ', headers=headers).status_code == 401
    assert callback.post('/api/chatops/callback', json=data, headers={'Authorization': 'Bearer ' + action['action_id']}).status_code == 401
    assert callback.post('/api/chatops/callback', content=body, headers={**headers, 'content-type': 'text/plain'}).status_code == 400
    assert callback.post('/api/chatops/callback', content=body, headers=headers).status_code == 200


def test_nonce_is_global_across_actions(api):
    client, app = api
    install_executor(app, RecordingExecutor())
    _, callback = setup_chatops(app)
    first = client.post('/api/jobs', json={'graph': graph()}).json()
    second = client.post('/api/jobs', json={'graph': graph()}).json()
    a = issue(client, first, 'reject')
    b = issue(client, second, 'reject')
    assert signed(callback, a).status_code == 200
    assert signed(callback, b).status_code == 409
    assert app.state.jobs.get(second['id'])['status'] == 'pending_approval'


def test_worker_failure_is_not_reported_as_successful_deployment(api):
    client, app = api
    _, callback = setup_chatops(app)
    executor = install_executor(app, RecordingExecutor())
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job)
    assert signed(callback, action).json()['status'] == 'queued'
    client.patch('/api/settings', json={'policies': {'max_monthly_cost': 0}})
    app.state.worker.pause.set()
    result = wait_job(client, job['id'])
    assert result['status'] == 'failed'
    assert not executor.calls
    assert signed(callback, action, nonce='new-nonce-00000000000000001').status_code == 409
