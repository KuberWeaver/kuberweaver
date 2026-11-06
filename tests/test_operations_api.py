"""Operator surface for optional integrations: explicit states, no secret output."""
from test_core_graph import api
from test_core_jobs import login


def test_operations_discovery_is_admin_only_and_never_exposes_config(api):
    client, app = api
    path = app.state.state_dir / 'config' / 'notifications.json'
    path.write_text('{"enabled": true, "private_token": "SHOULD-NOT-LEAK"}')
    response = client.get('/api/operations')
    assert response.status_code == 200
    assert response.json()['schema_version'] == 1
    assert 'SHOULD-NOT-LEAK' not in response.text
    assert any(row['id'] == 'notifications' for row in response.json()['capabilities'])
    login(client, app.state.state_dir, 'viewer')
    assert client.get('/api/operations').status_code == 403


def test_operations_missing_providers_are_honest_and_no_implicit_retry(api):
    client, app = api
    response = client.get('/api/operations')
    assert response.status_code == 200
    row = next(r for r in response.json()['capabilities'] if r['id'] == 'notifications')
    assert row['configured'] is False
    assert client.post('/api/operations/notifications/retry').status_code == 503


def test_operations_retries_only_explicitly_selected_provider_and_audits(api):
    client, app = api
    class Delivery:
        def send(self, payload):
            pass
        def retry_pending(self):
            return {'attempted': 1, 'delivered': 1, 'failed': 0}
    app.state.registry.register('notifications', 'test-delivery', Delivery())
    app.state.provider_bindings['notifications'] = 'test-delivery'
    response = client.post('/api/operations/notifications/retry')
    assert response.status_code == 200
    assert response.json()['result']['delivered'] == 1
    assert any(row['action'] == 'notifications.retry_requested' for row in client.get('/api/audit').json())
    login(client, app.state.state_dir, 'operator')
    assert client.post('/api/operations/notifications/retry').status_code == 403


def test_diagnostic_inference_is_explicit_authorized_and_audited(api):
    client, app = api
    class Diagnostic:
        calls = 0
        def read(self):
            return {'mode': 'llm', 'status': 'not_run', 'read_only': True, 'findings': []}
        def run(self):
            self.calls += 1
            return {'mode': 'llm', 'status': 'cached', 'read_only': True, 'findings': []}
    provider = Diagnostic()
    app.state.registry.register('diagnostics', 'explicit-test', provider)
    app.state.provider_bindings['diagnostics'] = 'explicit-test'
    assert client.get('/api/diagnostics').json()['status'] == 'not_run'
    assert provider.calls == 0
    response = client.post('/api/diagnostics/run')
    assert response.status_code == 200
    assert response.json()['status'] == 'cached'
    assert provider.calls == 1
    assert any(row['action'] == 'diagnostics.run_requested' for row in client.get('/api/audit').json())
    login(client, app.state.state_dir, 'viewer')
    assert client.post('/api/diagnostics/run').status_code == 403
    assert provider.calls == 1


def test_diagnostics_run_enforces_server_cooldown_and_redacts_provider_http_errors(api):
    client, app = api
    class Diagnostic:
        calls = 0
        def read(self):
            return {'mode': 'llm', 'findings': []}
        def run(self):
            from fastapi import HTTPException
            self.calls += 1
            raise HTTPException(503, 'UPSTREAM_PRIVATE_TOKEN=review-marker', headers={'X-Secret': 'review-marker'})
    provider = Diagnostic()
    app.state.registry.register('diagnostics', 'bounded-test', provider)
    app.state.provider_bindings['diagnostics'] = 'bounded-test'
    response = client.post('/api/diagnostics/run')
    assert response.status_code == 503
    assert 'review-marker' not in response.text
    assert 'x-secret' not in response.headers
    again = client.post('/api/diagnostics/run')
    assert again.status_code == 429
    assert provider.calls == 1
