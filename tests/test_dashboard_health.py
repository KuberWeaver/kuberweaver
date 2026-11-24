"""Dashboard health observations: real SQLite and auth, no outbound request."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.dashboard_health import (DEFAULT_INTERVAL, DEFAULT_TIMEOUT, MAX_BODY,
                                          DashboardProbeLoop, ProbeResponse, ResponseTooLarge,
                                          fetch_once, probe_settings)

UNKNOWN_HEALTH = {'status': 'unknown', 'observed_at': None, 'http_status': None,
                  'latency_ms': None, 'detail': 'the record has not been probed yet',
                  'drift': False}


def spec(**changes):
    return {'label': 'Lab Grafana', 'kind': 'grafana', 'url': 'http://grafana.lab:3000/d/lab',
            'health_path': None, 'embed': False, 'enabled': True, **changes}


def make_app(path, monkeypatch, *, probe_seconds=None, probe_timeout=None, name='state'):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
    monkeypatch.delenv('KUBERWEAVER_DASHBOARD_PROBE_SECONDS', raising=False)
    monkeypatch.delenv('KUBERWEAVER_DASHBOARD_PROBE_TIMEOUT', raising=False)
    monkeypatch.delenv('KUBERWEAVER_DASHBOARD_EMBED_ORIGINS', raising=False)
    if probe_seconds is not None:
        monkeypatch.setenv('KUBERWEAVER_DASHBOARD_PROBE_SECONDS', probe_seconds)
    if probe_timeout is not None:
        monkeypatch.setenv('KUBERWEAVER_DASHBOARD_PROBE_TIMEOUT', probe_timeout)
    from backend.app import create_app
    return create_app(state_dir=path / name, testing=True)


def login(client, app, role='admin'):
    credentials = dict(line.split(':', 1) for line in
                       (app.state.state_dir / 'bootstrap-credentials.txt').read_text().splitlines())
    response = client.post('/api/auth/login', json={'username': role, 'password': credentials[role]})
    assert response.status_code == 200
    client.headers['x-csrf-token'] = response.json()['csrf_token']


@pytest.fixture(autouse=True)
def prohibit_real_network(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError('A dashboard probe test must not open network connections')

    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def platform(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


def serve(app, responder):
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        return responder()

    app.state.dashboards.fetch = fetch
    return calls


def answer(status=200, content_type='application/json', body=b'{}', headers=None):
    return lambda: ProbeResponse(status, {'content-type': content_type, **(headers or {})}, body)


def test_a_healthy_answer_is_stored_and_listed(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    assert record['health'] == UNKNOWN_HEALTH
    calls = serve(app, answer(body=b'{"commit":"abc","database":"ok","version":"11.2"}'))
    response = client.post('/api/dashboards/' + record['id'] + '/probe')
    assert response.status_code == 200, response.text
    result = response.json()
    health = result['health']
    assert health['status'] == 'healthy'
    assert health['http_status'] == 200
    assert health['detail'] is None
    assert health['drift'] is False
    assert health['observed_at'] and isinstance(health['latency_ms'], int)
    assert calls == [('http://grafana.lab:3000/api/health', DEFAULT_TIMEOUT)]
    assert client.get('/api/dashboards').json()['dashboards'][0]['health'] == health
    stored = json.loads(app.state.store.one(
        'SELECT body FROM dashboard_health WHERE dashboard_id=?', (record['id'],))['body'])
    assert stored['probe_url'] == 'http://grafana.lab:3000/api/health'
    assert stored['revision'] == 1
    assert stored['status'] == 'healthy'


def test_an_unreachable_endpoint_is_recorded_without_touching_the_record(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    before = app.state.store.one('SELECT body FROM dashboards WHERE id=?', (record['id'],))['body']

    def refused(url, timeout):
        raise ConnectionRefusedError('nothing listens')

    app.state.dashboards.fetch = refused
    health = client.post('/api/dashboards/' + record['id'] + '/probe').json()['health']
    assert health['status'] == 'unreachable'
    assert health['http_status'] is None
    assert health['detail'] == 'the endpoint could not be reached'
    assert health['drift'] is True
    after = app.state.store.one('SELECT body FROM dashboards WHERE id=?', (record['id'],))['body']
    assert after == before
    assert client.get('/api/dashboards/' + record['id']).json()['enabled'] is True
    assert client.get('/api/dashboards/' + record['id']).json()['revision'] == 1


def test_a_timeout_is_unreachable(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()

    def slow(url, timeout):
        raise TimeoutError('no answer')

    app.state.dashboards.fetch = slow
    health = client.post('/api/dashboards/' + record['id'] + '/probe').json()['health']
    assert health['status'] == 'unreachable'
    assert health['detail'] == 'the endpoint did not answer within 5 seconds'


@pytest.mark.parametrize('kind, content_type, body', [
    ('grafana', 'text/html', b'<html>not grafana</html>'),
    ('grafana', 'application/json', b'{not json'),
    ('netdata', 'application/json', b'[]'),
    ('prometheus', 'application/json', b'{"status":"ok"}'),
    ('prometheus', 'text/plain', b'Prometheus Server is starting'),
    ('uptime-kuma', 'application/json', b'{}'),
    ('uptime-kuma', 'text/html', b'   '),
])
def test_a_malformed_answer_is_recorded(platform, kind, content_type, body):
    app, client = platform
    record = client.post('/api/dashboards', json=spec(
        label=kind, kind=kind, url='http://' + kind + '.lab/')).json()
    serve(app, answer(content_type=content_type, body=body))
    response = client.post('/api/dashboards/' + record['id'] + '/probe')
    assert response.status_code == 200, response.text
    health = response.json()['health']
    assert health['status'] == 'malformed', (kind, health)
    assert health['http_status'] == 200
    assert health['drift'] is True
    assert health['detail']


def test_a_non_success_status_is_unhealthy(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    serve(app, answer(status=503, body=b'{"database":"not ok"}'))
    health = client.post('/api/dashboards/' + record['id'] + '/probe').json()['health']
    assert health['status'] == 'unhealthy'
    assert health['http_status'] == 503
    assert health['detail'] == 'the endpoint answered HTTP 503'
    assert health['drift'] is True


def test_a_grafana_database_failure_is_unhealthy(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    serve(app, answer(body=b'{"database":"not ready"}'))
    health = client.post('/api/dashboards/' + record['id'] + '/probe').json()['health']
    assert health['status'] == 'unhealthy'
    assert health['detail'] == 'the tool reports its database is not ready'


def test_a_redirect_is_not_followed(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    calls = serve(app, answer(status=302, headers={'location': 'http://elsewhere.lab/'}))
    health = client.post('/api/dashboards/' + record['id'] + '/probe').json()['health']
    assert health['status'] == 'malformed'
    assert health['detail'] == 'the endpoint answered a redirect, which the probe does not follow'
    assert len(calls) == 1


def test_a_disabled_record_is_never_probed(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec(enabled=False)).json()
    assert record['health'] == {'status': 'disabled', 'observed_at': None, 'http_status': None,
                                'latency_ms': None,
                                'detail': 'the record is disabled and is not probed', 'drift': False}

    def explode(url, timeout):
        raise AssertionError('a disabled record must not be probed')

    app.state.dashboards.fetch = explode
    assert client.post('/api/dashboards/' + record['id'] + '/probe').status_code == 409
    assert client.get('/api/dashboards').json()['dashboards'][0]['health']['status'] == 'disabled'


def test_a_sweep_records_each_enabled_record_and_leaves_disabled_ones_alone(platform):
    app, client = platform
    enabled = client.post('/api/dashboards', json=spec()).json()
    disabled = client.post('/api/dashboards', json=spec(label='Off', enabled=False)).json()
    calls = serve(app, answer(body=b'{"database":"ok"}'))
    assert app.state.dashboards.probe_enabled() == 1
    assert len(calls) == 1
    listed = {record['id']: record for record in client.get('/api/dashboards').json()['dashboards']}
    assert listed[enabled['id']]['health']['status'] == 'healthy'
    assert listed[disabled['id']]['health']['status'] == 'disabled'


def test_editing_the_probe_target_invalidates_the_observation(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    serve(app, answer(body=b'{"database":"ok"}'))
    healthy = client.post('/api/dashboards/' + record['id'] + '/probe').json()['health']
    renamed = client.patch('/api/dashboards/' + record['id'],
                           json={'revision': 1, 'label': 'Renamed'}).json()
    assert renamed['health'] == healthy
    moved = client.patch('/api/dashboards/' + record['id'],
                         json={'revision': 2, 'health_path': '/other'}).json()
    assert moved['health']['status'] == 'unknown'
    assert moved['health']['observed_at'] == healthy['observed_at']
    assert moved['health']['detail'] == 'the record changed since the last observation'
    other = client.patch('/api/dashboards/' + record['id'],
                         json={'revision': 3, 'url': 'http://other.lab/'}).json()
    assert other['health']['status'] == 'unknown'


def test_deleting_a_record_removes_its_observation(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    serve(app, answer(body=b'{"database":"ok"}'))
    client.post('/api/dashboards/' + record['id'] + '/probe')
    assert app.state.store.one('SELECT count(*) AS total FROM dashboard_health')['total'] == 1
    assert client.delete('/api/dashboards/' + record['id'] + '?revision=1').status_code == 200
    assert app.state.store.one('SELECT count(*) AS total FROM dashboard_health')['total'] == 0


def test_a_manual_probe_is_audited_and_a_sweep_is_not(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    serve(app, answer(body=b'{"database":"ok"}'))

    def probed_rows():
        return [row for row in client.get('/api/audit').json() if row['action'] == 'dashboard.probed']

    app.state.dashboards.probe_enabled()
    assert probed_rows() == []
    client.post('/api/dashboards/' + record['id'] + '/probe')
    rows = probed_rows()
    assert len(rows) == 1
    assert rows[0]['resource'] == record['id']
    assert rows[0]['detail'] == {'status': 'healthy', 'http_status': 200}


def test_an_observation_survives_a_restart(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client, app)
        record = client.post('/api/dashboards', json=spec()).json()
        app.state.dashboards.fetch = answer(body=b'{"database":"ok"}')
        health = client.post('/api/dashboards/' + record['id'] + '/probe').json()['health']
    restarted = make_app(tmp_path, monkeypatch)
    with TestClient(restarted) as client:
        login(client, restarted)
        assert client.get('/api/dashboards/' + record['id']).json()['health'] == health


def test_probe_is_administrator_only_and_uses_the_configure_scope(platform):
    app, client = platform
    record = client.post('/api/dashboards', json=spec()).json()
    serve(app, answer(body=b'{"database":"ok"}'))
    issued = client.post('/api/agent-tokens', json={
        'label': 'dash-reader', 'role': 'viewer', 'scopes': ['read:integrations'],
        'expires_in_seconds': 3600}).json()
    reader = {'authorization': 'Bearer ' + issued['token']}
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.post('/api/dashboards/' + record['id'] + '/probe',
                       headers=reader).status_code == 403
    for role in ('viewer', 'operator'):
        login(client, app, role)
        assert client.post('/api/dashboards/' + record['id'] + '/probe').status_code == 403
    login(client, app)
    assert client.post('/api/dashboards/' + record['id'] + '/probe').status_code == 200
    assert client.post('/api/dashboards/' + '0' * 32 + '/probe').status_code == 404
    assert client.post('/api/dashboards/not-an-identity/probe').status_code == 404


def test_probe_settings_report_an_unusable_value(monkeypatch):
    monkeypatch.delenv('KUBERWEAVER_DASHBOARD_PROBE_SECONDS', raising=False)
    monkeypatch.delenv('KUBERWEAVER_DASHBOARD_PROBE_TIMEOUT', raising=False)
    assert probe_settings().interval == DEFAULT_INTERVAL
    assert probe_settings().error is None
    monkeypatch.setenv('KUBERWEAVER_DASHBOARD_PROBE_SECONDS', '0')
    assert probe_settings().interval == 0
    assert probe_settings().error is None
    monkeypatch.setenv('KUBERWEAVER_DASHBOARD_PROBE_SECONDS', 'often')
    invalid = probe_settings()
    assert invalid.interval == DEFAULT_INTERVAL
    assert invalid.error and 'KUBERWEAVER_DASHBOARD_PROBE_SECONDS' in invalid.error
    monkeypatch.setenv('KUBERWEAVER_DASHBOARD_PROBE_TIMEOUT', '0.1')
    invalid_timeout = probe_settings()
    assert invalid_timeout.timeout == DEFAULT_TIMEOUT
    assert invalid_timeout.error and 'KUBERWEAVER_DASHBOARD_PROBE_TIMEOUT' in invalid_timeout.error


def test_the_collection_reports_the_probe_policy(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, probe_seconds='0', probe_timeout='2.5')
    with TestClient(app) as client:
        login(client, app)
        policy = client.get('/api/dashboards').json()['probe']
        assert policy == {'interval_seconds': 0.0, 'timeout_seconds': 2.5, 'error': None}
    assert app.state.dashboard_probe_loop.thread is None


def test_testing_apps_do_not_start_the_periodic_loop(platform):
    app, client = platform
    assert app.state.dashboard_probe_loop.thread is None
    assert app.state.dashboard_probe_loop not in app.state.background_services


def test_an_explicit_interval_starts_the_periodic_loop(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, probe_seconds='30')
    with TestClient(app):
        loop = app.state.dashboard_probe_loop
        assert loop in app.state.background_services
        assert loop.thread is not None and loop.thread.is_alive()


def test_the_loop_repeats_until_closed():
    seen = []
    second = threading.Event()

    def sweep():
        seen.append(1)
        if len(seen) >= 2:
            second.set()

    loop = DashboardProbeLoop(sweep, interval=0.05, initial_delay=0)
    loop.start()
    assert second.wait(2)
    loop.close()
    assert loop.thread is not None and not loop.thread.is_alive()
    count = len(seen)
    time.sleep(0.1)
    assert len(seen) == count


def test_a_failed_sweep_does_not_stop_the_loop():
    seen = []
    second = threading.Event()

    def sweep():
        seen.append(1)
        if len(seen) >= 2:
            second.set()
        raise RuntimeError('unreachable registry')

    loop = DashboardProbeLoop(sweep, interval=0.05, initial_delay=0)
    loop.start()
    assert second.wait(2)
    loop.close()
    assert len(seen) >= 2


def test_a_zero_interval_never_starts_a_thread():
    loop = DashboardProbeLoop(lambda: None, interval=0)
    loop.start()
    assert loop.thread is None


def test_fetch_once_sends_no_credentials_and_keeps_the_query(monkeypatch):
    import backend.dashboard_health as module

    calls = []

    class FakeResponse:
        status = 200

        def getheaders(self):
            return [('Content-Type', 'application/json')]

        def read(self, size):
            return b'{"database":"ok"}'

    class FakeConnection:
        def __init__(self, host, port, timeout=None):
            calls.append(['connect', host, port, timeout])

        def request(self, method, path, headers=None):
            calls.append(['request', method, path, headers])

        def getresponse(self):
            return FakeResponse()

        def close(self):
            calls.append(['close'])

    monkeypatch.setattr(module.http.client, 'HTTPConnection', FakeConnection)
    response = fetch_once('http://grafana.lab:3000/api/health?full=1', 4.5)
    assert response.status == 200
    assert calls[0] == ['connect', 'grafana.lab', 3000, 4.5]
    assert calls[1][1] == 'GET' and calls[1][2] == '/api/health?full=1'
    assert calls[1][3] == {'User-Agent': 'kuberweaver-dashboard-probe/1',
                           'Accept': '*/*', 'Connection': 'close'}
    assert calls[-1] == ['close']


def test_fetch_once_refuses_an_oversized_body(monkeypatch):
    import backend.dashboard_health as module

    class FakeResponse:
        status = 200

        def getheaders(self):
            return []

        def read(self, size):
            return b'x' * (MAX_BODY + 1)

    class FakeConnection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(module.http.client, 'HTTPConnection', FakeConnection)
    with pytest.raises(ResponseTooLarge):
        fetch_once('http://grafana.lab/', 1.0)


def test_postgresql_registers_the_health_table():
    from backend.postgres import COLUMNS, DDL, translate_sql
    assert COLUMNS['dashboard_health'] == ('dashboard_id', 'body')
    statement = translate_sql('CREATE TABLE IF NOT EXISTS dashboard_health('
                              'dashboard_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    assert statement == f'CREATE TABLE IF NOT EXISTS dashboard_health ({DDL["dashboard_health"]})'
    assert '_kw_rowid' not in DDL['dashboard_health']
    assert translate_sql('INSERT OR REPLACE INTO dashboard_health(dashboard_id, body) '
                         'VALUES(?,?)') == (
        'INSERT INTO dashboard_health(dashboard_id, body) VALUES(%s,%s) '
        'ON CONFLICT (dashboard_id) DO UPDATE SET body=EXCLUDED.body')
