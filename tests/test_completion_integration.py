import json
from test_core_graph import api, graph
from test_core_jobs import RecordingExecutor, install_executor, wait_job
from test_notifications_delivery import receiver


def test_notification_consumer_api_delivers_real_http_and_returns_sanitized_readback(api, receiver, tmp_path):
    from kuberweaver.notifications import WebhookNotifications
    from kuberweaver.secrets import RestrictedFileProvider
    client, app = api
    for service in getattr(app.state, 'background_services', []):
        service.close()
    endpoint = tmp_path / 'endpoint'
    endpoint.write_text(f'http://127.0.0.1:{receiver.server_port}/TOKEN_SECRET')
    endpoint.chmod(0o600)
    app.state.registry.register('secrets', 'notification-test', RestrictedFileProvider({'webhook/url': endpoint}))
    app.state.provider_bindings['secrets'] = 'notification-test'
    provider = WebhookNotifications(app, {'enabled': True, 'transport': 'json', 'endpoint_secret_ref': 'webhook/url', 'allow_loopback_http': True})
    app.state.registry.register('notifications', 'webhook-test', provider)
    app.state.provider_bindings['notifications'] = 'webhook-test'
    provider.send({'event': 'job_finished', 'job_id': 'test-job', 'status': 'succeeded', 'operation': 'execute', 'environment': 'dev', 'time': '2026-11-06T00:00:00Z'})
    pending = client.get('/api/operations/notifications')
    assert pending.status_code == 200
    assert pending.json()['deliveries'][0]['status'] == 'pending'
    response = client.post('/api/operations/notifications/retry')
    assert response.status_code == 200
    assert response.json()['result']['delivered'] == 1
    assert len(receiver.received) == 1
    assert receiver.received[0][2]['job_id'] == 'test-job'
    exact = client.get('/api/operations/notifications')
    assert exact.json()['deliveries'][0]['status'] == 'delivered'
    assert 'TOKEN_SECRET' not in exact.text
    assert '127.0.0.1' not in exact.text


def test_observability_is_consumed_by_operations_and_metrics_without_provider_calls(api):
    client, app = api
    class ReadTrap:
        def read(self): raise AssertionError('Operational read must not query inventory')
    app.state.registry.register('inventory', 'read-trap', ReadTrap())
    app.state.provider_bindings['inventory'] = 'read-trap'
    response = client.get('/api/operations')
    assert response.status_code == 200
    assert response.json()['observability']['storage']['status'] == 'ok'
    metrics = client.get('/metrics')
    assert metrics.status_code == 200
    assert 'kuberweaver_storage_readable 1' in metrics.text
    assert 'kuberweaver_worker_alive 1' in metrics.text


def test_chatops_callback_on_main_app_keeps_signature_auth_not_cookie_auth(api):
    from test_chatops import setup_chatops, issue, signed
    client, app = api
    setup_chatops(app)
    executor = install_executor(app, RecordingExecutor())
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    action = issue(client, job)
    from fastapi.testclient import TestClient
    callback = TestClient(app)
    assert signed(callback, action, key=b'wrong').status_code == 401
    assert signed(callback, action).status_code == 200
    assert wait_job(client, job['id'])['status'] == 'succeeded'
    assert executor.calls == [('execute', job['id'])]
    assert signed(callback, action).status_code == 409


def test_scoped_bearer_can_read_terminal_job_event_stream(api):
    from fastapi.testclient import TestClient
    client, app = api
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post('/api/jobs/' + job['id'] + '/reject').status_code == 200
    issued = client.post('/api/agent-tokens', json={'label': 'event-reader', 'role': 'viewer', 'scopes': ['read'], 'expires_in_seconds': 600})
    assert issued.status_code == 201
    reader = TestClient(app)
    response = reader.get('/api/jobs/' + job['id'] + '/events', headers={'Authorization': 'Bearer ' + issued.json()['token']})
    assert response.status_code == 200
    assert 'event: done' in response.text
    assert 'rejected' in response.text


def test_finops_only_agent_cannot_read_jobs_or_infrastructure(api):
    from fastapi.testclient import TestClient
    client, app = api
    issued = client.post('/api/agent-tokens', json={'label': 'finops-only', 'role': 'viewer', 'scopes': ['read:finops'], 'expires_in_seconds': 600})
    assert issued.status_code == 201
    reader = TestClient(app)
    headers = {'Authorization': 'Bearer ' + issued.json()['token']}
    assert reader.get('/api/finops', headers=headers).status_code == 200
    assert reader.get('/api/auth/me', headers=headers).status_code == 200
    for path in ['/api/jobs', '/api/infrastructure', '/api/audit', '/api/overview', '/api/providers']:
        assert reader.get(path, headers=headers).status_code == 403
    assert reader.post('/api/plans/validate', headers=headers, json=graph()).status_code == 403


def test_notification_read_scope_is_distinct_from_retry_scope(api):
    from fastapi.testclient import TestClient
    client, app = api
    for scope, read_code in [('notify', 403), ('read:operations', 200)]:
        issued = client.post('/api/agent-tokens', json={'label': 'notification-scope', 'role': 'admin', 'scopes': [scope], 'expires_in_seconds': 600})
        assert issued.status_code == 201
        reader = TestClient(app)
        headers = {'Authorization': 'Bearer ' + issued.json()['token']}
        assert reader.get('/api/operations/notifications', headers=headers).status_code == read_code
        if scope == 'read:operations':
            assert reader.post('/api/operations/notifications/retry', headers=headers).status_code == 403
