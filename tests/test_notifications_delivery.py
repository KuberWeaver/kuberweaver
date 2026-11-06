"""Real loopback HTTP only; never reads live configuration or credentials."""
import json
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from kuberweaver.extensions import ExtensionRegistry
from kuberweaver.secrets import RestrictedFileProvider


@pytest.fixture
def receiver():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            self.server.received.append((self.path, dict(self.headers), json.loads(body)))
            self.send_response(self.server.code)
            self.send_header('Location', '/must-not-follow')
            self.end_headers()
            self.wfile.write(b'RESPONSE_SECRET_DO_NOT_STORE')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.code = 204
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def setup_app(tmp_path, receiver):
    secret = tmp_path / 'endpoint'
    secret.write_text(f'http://127.0.0.1:{receiver.server_port}/TOKEN_SECRET')
    secret.chmod(0o600)
    registry = ExtensionRegistry()
    registry.register('secrets', 'restricted-file', RestrictedFileProvider({'webhook/url': secret}))
    app = SimpleNamespace(state=SimpleNamespace(state_dir=tmp_path / 'data', registry=registry,
                           provider_bindings={'secrets': 'restricted-file'}, provider_config_error=None))
    return app, {'enabled': True, 'transport': 'json', 'endpoint_secret_ref': 'webhook/url',
                 'allow_loopback_http': True, 'timeout': 1, 'max_attempts': 3}


def event(**changes):
    return dict({'event': 'job_finished', 'job_id': 'job-123', 'status': 'succeeded',
                 'operation': 'execute', 'environment': 'lab', 'time': '2026-01-01T00:00:00Z'}, **changes)


def deliver(provider, payload):
    queued = provider.send(payload)
    provider.retry_pending()
    return next(row for row in provider.read()['deliveries'] if row['id'] == queued['id'])


def test_real_delivery_deduplicates_across_restart_preserves_rollback(tmp_path, receiver):
    from kuberweaver.notifications import WebhookNotifications
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, config)
    first = deliver(provider, event())
    assert first['status'] == 'delivered'
    assert receiver.received[0][2] == event()
    assert receiver.received[0][1]['Idempotency-Key'] == first['id']
    restarted = WebhookNotifications(app, config)
    assert restarted.send(event())['id'] == first['id']
    assert len(receiver.received) == 1
    assert deliver(restarted, event(operation='rollback', status='rolled_back'))['status'] == 'delivered'
    assert len(receiver.received) == 2
    assert [row['status'] for row in restarted.read()['deliveries']] == ['delivered', 'delivered']


def test_failures_are_durable_bounded_retry_and_redacted(tmp_path, receiver, monkeypatch):
    from kuberweaver.notifications import WebhookNotifications
    app, config = setup_app(tmp_path, receiver)
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    receiver.code = 503
    provider = WebhookNotifications(app, config)
    result = deliver(provider, event())
    assert result['status'] == 'pending'
    assert result['attempts'] == 1
    restarted = WebhookNotifications(app, config)
    assert restarted.retry_pending()['deliveries'][0]['attempts'] == 2
    receiver.code = 204
    assert restarted.retry_pending()['deliveries'][0]['status'] == 'delivered'
    assert restarted.retry_pending()['deliveries'] == []
    assert len(receiver.received) == 3
    assert len({headers['Idempotency-Key'] for _, headers, _ in receiver.received}) == 1
    public = json.dumps([provider.read(), provider.describe()])
    for forbidden in ('TOKEN_SECRET', 'RESPONSE_SECRET', '127.0.0.1', 'webhook/url'):
        assert forbidden not in public
        assert forbidden.encode() not in provider.path.read_bytes()
    receiver.code = 500
    second = deliver(provider, event(job_id='job-other'))
    provider.retry_pending()
    assert provider.retry_pending()['deliveries'][0]['status'] == 'exhausted'
    assert provider.send(event(job_id='job-other'))['attempts'] == 3
    assert len(receiver.received) == 6


@pytest.mark.parametrize('changes', [
    {'enabled': 'yes'}, {'transport': 'smtp'}, {'endpoint_secret_ref': '../secret'},
    {'timeout': 0}, {'timeout': 61}, {'timeout': float('nan')}, {'max_attempts': 0},
    {'max_attempts': 6}, {'max_attempts': True}, {'allow_loopback_http': 'yes'},
    {'endpoint': 'https://SECRET'}, {'unknown': 1},
])
def test_invalid_configuration_is_sanitized(tmp_path, receiver, changes):
    from kuberweaver.notifications import WebhookNotifications, NotificationError
    app, config = setup_app(tmp_path, receiver)
    with pytest.raises(NotificationError, match='Invalid notification configuration'):
        WebhookNotifications(app, dict(config, **changes))
    assert not receiver.received


@pytest.mark.parametrize('endpoint', [
    'http://example.com/TOKEN_SECRET', 'http://localhost/TOKEN_SECRET',
    'https://user:password@example.com/TOKEN_SECRET', 'ftp://127.0.0.1/TOKEN_SECRET',
    'https://example.com:bad/TOKEN_SECRET', 'https://example.com/TOKEN_SECRET#fragment',
])
def test_invalid_endpoints_never_contact_network(tmp_path, receiver, endpoint, monkeypatch):
    from kuberweaver.notifications import WebhookNotifications
    def forbidden_client(*args, **kwargs):
        pytest.fail('Invalid endpoint reached HTTP transport')
    monkeypatch.setattr(WebhookNotifications, '_post', forbidden_client)
    app, config = setup_app(tmp_path, receiver)
    (tmp_path / 'endpoint').write_text(endpoint)
    provider = WebhookNotifications(app, config)
    assert deliver(provider, event())['status'] == 'pending'
    assert not receiver.received
    assert 'TOKEN_SECRET' not in json.dumps(provider.read())


def test_explicit_loopback_exception_and_redirect_refusal(tmp_path, receiver):
    from kuberweaver.notifications import WebhookNotifications
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, dict(config, allow_loopback_http=False))
    assert deliver(provider, event())['status'] == 'pending'
    assert not receiver.received
    receiver.code = 302
    provider = WebhookNotifications(app, config)
    assert provider.retry_pending()['deliveries'][0]['status'] == 'pending'
    assert len(receiver.received) == 1


@pytest.mark.parametrize('transport,field', [('discord', 'content'), ('teams', 'text')])
def test_vendor_wire_formats(tmp_path, receiver, transport, field):
    from kuberweaver.notifications import WebhookNotifications
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, dict(config, transport=transport))
    assert deliver(provider, event())['status'] == 'delivered'
    body = receiver.received[0][2]
    assert json.loads(body[field]) == event()
    if transport == 'discord':
        assert body['allowed_mentions'] == {'parse': []}


def test_payload_allowlist_and_disabled_provider(tmp_path, receiver):
    from kuberweaver.notifications import WebhookNotifications, NotificationError
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, config)
    deliver(provider, event(secret='ARBITRARY_SECRET'))
    assert 'secret' not in receiver.received[0][2]
    for payload in [event(operation='delete'), event(job_id='https://secret'), event(event='unknown')]:
        with pytest.raises(NotificationError, match='Invalid notification payload'):
            provider.send(payload)
    disabled = WebhookNotifications(app, dict(config, enabled=False))
    assert disabled.send(event(job_id='disabled'))['status'] == 'disabled'
    assert disabled.retry_pending()['deliveries'] == []
    assert disabled.describe()['status'] == 'disabled'
    assert len(receiver.received) == 1


def test_crash_after_remote_acceptance_requires_explicit_uncertain_retry(tmp_path, receiver, monkeypatch):
    from kuberweaver.notifications import WebhookNotifications
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, config)
    original = WebhookNotifications._post

    @contextmanager
    def crash_after_acceptance(client, *args, **kwargs):
        with original(client, *args, **kwargs) as response:
            yield response
        raise SystemExit('simulated process death')

    provider.send(event())
    with monkeypatch.context() as patcher:
        patcher.setattr(WebhookNotifications, '_post', crash_after_acceptance)
        with pytest.raises(SystemExit):
            provider.retry_pending()
    restarted = WebhookNotifications(app, config)
    assert restarted.read()['deliveries'][0]['status'] == 'uncertain'
    assert restarted.send(event())['status'] == 'uncertain'
    assert restarted.retry_pending()['deliveries'] == []
    assert len(receiver.received) == 1
    assert restarted.retry_pending(include_uncertain=True)['deliveries'][0]['status'] == 'delivered'
    assert len(receiver.received) == 2  # Explicit duplicate risk, not exactly once.


def test_register_missing_invalid_disabled_and_valid_configuration(tmp_path, receiver):
    from kuberweaver.notifications import register_notifications
    app, config = setup_app(tmp_path, receiver)
    for body, expected in [(None, 'unavailable'), ('{"endpoint":"SECRET"}', 'unavailable'),
                           (json.dumps(dict(config, enabled=False)), 'disabled'),
                           (json.dumps(config), 'configured')]:
        target = app.state.state_dir / 'config' / 'notifications.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        if body is not None:
            target.write_text(body)
        registry = ExtensionRegistry()
        register_notifications(registry, app)
        descriptor = registry.list('notifications')[0]
        assert descriptor['name'] == 'webhook'
        assert descriptor['status'] == expected
        assert 'SECRET' not in json.dumps(descriptor)
        provider = registry.get('notifications', 'webhook')
        assert (provider is not None) == (expected == 'configured')
    assert deliver(provider, event())['status'] == 'delivered'


@pytest.mark.parametrize('kind', ['directory', 'database'])
def test_storage_rejects_symlink_redirects(tmp_path, receiver, kind):
    from kuberweaver.notifications import WebhookNotifications, NotificationError
    app, config = setup_app(tmp_path, receiver)
    directory = app.state.state_dir / 'notifications'
    outside = tmp_path / 'untouched'
    if kind == 'directory':
        outside.mkdir()
        directory.parent.mkdir(parents=True)
        directory.symlink_to(outside, target_is_directory=True)
    else:
        directory.mkdir(parents=True)
        outside.write_text('untouched')
        (directory / 'deliveries.sqlite3').symlink_to(outside)
    with pytest.raises(NotificationError, match='Notification storage unavailable'):
        WebhookNotifications(app, config)
    if kind == 'database':
        assert outside.read_text() == 'untouched'
    else:
        assert list(outside.iterdir()) == []


def test_send_only_enqueues_and_occurrence_time_distinguishes_repeated_operations(tmp_path, receiver):
    from kuberweaver.notifications import WebhookNotifications
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, config)
    first = provider.send(event())
    assert first['status'] == 'pending'
    assert first['attempts'] == 0
    assert not receiver.received
    assert provider.send(event())['id'] == first['id']
    second = provider.send(event(time='2026-01-01T00:01:00Z'))
    assert second['id'] != first['id']
    assert provider.retry_pending()['deliveries'][0]['status'] == 'delivered'
    assert len(receiver.received) == 2


def test_transport_failure_after_acceptance_is_uncertain_and_logs_no_urls(tmp_path, receiver, monkeypatch, caplog):
    from kuberweaver.notifications import WebhookNotifications
    import logging
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, config)
    original = WebhookNotifications._post

    @contextmanager
    def lost_ack(client, *args, **kwargs):
        with original(client, *args, **kwargs) as response:
            yield response
        raise TimeoutError('TOKEN_SECRET RESPONSE_SECRET')

    with monkeypatch.context() as patcher:
        patcher.setattr(WebhookNotifications, '_post', lost_ack)
        with caplog.at_level(logging.INFO):
            queued = provider.send(event())
            result = provider.retry_pending()['deliveries'][0]
    assert result['status'] == 'uncertain'
    assert provider.retry_pending()['deliveries'] == []
    assert len(receiver.received) == 1
    assert 'TOKEN_SECRET' not in caplog.text
    assert 'TOKEN_SECRET' not in json.dumps(provider.read())


def test_main_api_validation_enqueues_and_delivers_sanitized_summary(tmp_path, receiver, monkeypatch, caplog):
    from fastapi.testclient import TestClient
    from test_core_auth import bootstrap
    from test_core_graph import graph, manifest

    # Isolate even the module-level default app; never use live state/config.
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(tmp_path / 'import-state'))
    from kuberweaver.app import create_app
    from kuberweaver.providers import resolve_provider

    _, config = setup_app(tmp_path, receiver)
    state = tmp_path / 'main-state'
    (state / 'config').mkdir(parents=True)
    (state / 'config' / 'notifications.json').write_text(json.dumps(config))
    plugins = tmp_path / 'plugins'
    manifest(plugins)
    app = create_app(state_dir=state, plugins_dir=plugins, testing=True)
    app.state.registry.register('secrets', 'validation-secrets',
                                RestrictedFileProvider({'webhook/url': tmp_path / 'endpoint'}))
    # No lifespan dispatcher: prove enqueue-only before explicit API delivery.
    client = TestClient(app)
    try:
        credentials = bootstrap(state)
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        assert login.status_code == 200
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        selected = client.patch('/api/providers', json={'bindings': {
            'notifications': 'webhook', 'secrets': 'validation-secrets'}})
        assert selected.status_code == 200, selected.text
        provider = resolve_provider(app, 'notifications')
        candidate = graph()
        candidate['name'] = 'GRAPH_SECRET_DO_NOT_NOTIFY'
        candidate['nodes'][0]['config']['password'] = 'CONFIG_SECRET_DO_NOT_NOTIFY'
        response = client.post('/api/plans/validate', json=candidate)
        assert response.status_code == 200
        assert not response.json()['allowed']  # Admission guards remain intact.
        queued = provider.read()['deliveries']
        assert len(queued) == 1
        assert queued[0]['status'] == 'pending'
        assert queued[0]['attempts'] == 0
        assert not receiver.received
        status = client.get('/api/operations/notifications')
        assert status.status_code == 200
        receiver.code = 503
        retry = client.post('/api/operations/notifications/retry')
        assert retry.status_code == 200, retry.text
        assert provider.read()['deliveries'][0]['status'] == 'pending'
        original = receiver.received[0][2]
        assert original == event(event='before_plan', operation='validate', job_id=None,
                                 status='planning', environment='dev', time=original['time'])
        # A second host validation is a separate occurrence; retry keeps original time.
        assert client.post('/api/plans/validate', json=candidate).status_code == 200
        assert len(provider.read()['deliveries']) == 2
        from kuberweaver.notifications import WebhookNotifications
        restarted = WebhookNotifications(app, config)
        assert restarted.send(original)['id'] == queued[0]['id']
        receiver.code = 204
        assert client.post('/api/operations/notifications/retry').status_code == 200
        deliveries = provider.read()['deliveries']
        assert [row['status'] for row in deliveries] == ['delivered', 'delivered']
        assert len(receiver.received) == 3
        assert receiver.received[1][2] == original
        assert receiver.received[0][1]['Idempotency-Key'] == receiver.received[1][1]['Idempotency-Key']
        assert receiver.received[2][1]['Idempotency-Key'] != queued[0]['id']
        audit = client.get('/api/audit')
        assert not any(row['action'] == 'lifecycle.observer_failed' for row in audit.json())
        public = json.dumps([status.json(), provider.read(), audit.json()]) + caplog.text
        wire = json.dumps([body for _, _, body in receiver.received])
        for secret in ('TOKEN_SECRET', 'RESPONSE_SECRET', 'GRAPH_SECRET_DO_NOT_NOTIFY',
                       'CONFIG_SECRET_DO_NOT_NOTIFY'):
            assert secret not in public
            assert secret not in wire
            assert secret.encode() not in provider.path.read_bytes()
    finally:
        client.close()
        app.state.store.conn.close()


@pytest.mark.parametrize('changes', [
    {'event': 'after_approval'}, {'event': 'job_started'}, {'event': 'job_finished'},
    {'job_id': 'job-123'}, {'job_id': ''}, {'status': 'succeeded'},
    {'status': None}, {'operation': 'validate-other'}, {'environment': 'https://SECRET'},
    {'time': 'https://SECRET'}, {'time': None},
])
def test_validation_payload_accepts_only_host_summary_shape(tmp_path, receiver, changes):
    from kuberweaver.notifications import WebhookNotifications, NotificationError
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, config)
    payload = event(event='before_plan', operation='validate', job_id=None, status='planning')
    payload.update(changes)
    with pytest.raises(NotificationError, match='Invalid notification payload'):
        provider.send(payload)
    assert provider.read()['deliveries'] == []
    assert not receiver.received


def test_validation_replay_preserves_time_and_default_retry_leaves_uncertain(tmp_path, receiver, monkeypatch):
    from kuberweaver.notifications import WebhookNotifications
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, config)
    payload = event(event='before_plan', operation='validate', job_id=None, status='planning',
                    plugin_output={'secret': 'ARBITRARY_SECRET'})
    queued = provider.send(payload)
    original = WebhookNotifications._post

    @contextmanager
    def lost_ack(client, *args, **kwargs):
        with original(client, *args, **kwargs) as response:
            yield response
        raise TimeoutError('TOKEN_SECRET')

    with monkeypatch.context() as patcher:
        patcher.setattr(WebhookNotifications, '_post', lost_ack)
        assert provider.retry_pending()['deliveries'][0]['status'] == 'uncertain'
    restarted = WebhookNotifications(app, config)
    assert restarted.send(payload)['id'] == queued['id']
    assert restarted.send(payload)['status'] == 'uncertain'
    assert restarted.retry_pending()['deliveries'] == []
    assert len(receiver.received) == 1
    assert receiver.received[0][2]['time'] == payload['time']
    assert 'plugin_output' not in receiver.received[0][2]
    assert b'ARBITRARY_SECRET' not in restarted.path.read_bytes()


def test_retry_limit_is_bounded(tmp_path, receiver):
    from kuberweaver.notifications import WebhookNotifications, NotificationError
    app, config = setup_app(tmp_path, receiver)
    provider = WebhookNotifications(app, config)
    for limit in (0, -1, 101, True, '1'):
        with pytest.raises(NotificationError, match='Invalid retry request'):
            provider.retry_pending(limit=limit)
