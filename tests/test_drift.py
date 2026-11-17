"""Drift reports: per-field comparison of recorded state against live resources."""
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from kuberweaver.adoption import connection_inspector, marker_digest
from kuberweaver.drift import (DEFAULT_INTERVAL, DRIFT, EMPTY, IN_SYNC,
                               INTERVAL_VARIABLE, UNAVAILABLE, UNSUPPORTED,
                               UNVERIFIED, DriftLoop, DriftService,
                               declared_attributes, drift_schedule)
from test_postgresql_store import pg_file  # noqa: F401  fixture

UUID = '11111111-1111-4111-8111-111111111111'
OTHER_UUID = '22222222-2222-4222-8222-222222222222'
MARKER = 'kuberweaver-job:' + 'a' * 32
CONNECTION = 'c' * 32
JOB = 'd' * 32
DRIFT_RUN = '/api/environment-states/drift/run'
DRIFT_LIST = '/api/environment-states/drift'


class Clock:
    def __init__(self, value='2026-11-17T12:00:00+00:00'):
        self.value = value

    def __call__(self):
        return self.value


def make_app(path, monkeypatch, name='state'):
    # app.py creates a module-level app: never let its first import touch data/.
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
    monkeypatch.delenv(INTERVAL_VARIABLE, raising=False)
    from kuberweaver.app import create_app
    return create_app(state_dir=path / name, testing=True)


def login(client, app, role='admin'):
    credentials = dict(line.split(':', 1) for line in
                       (app.state.state_dir / 'bootstrap-credentials.txt').read_text().splitlines())
    response = client.post('/api/auth/login', json={'username': role, 'password': credentials[role]})
    assert response.status_code == 200, response.text
    token = response.json()['csrf_token']
    client.headers['x-csrf-token'] = token
    return token


def live(uuid=UUID, marker=MARKER, **overrides):
    row = {'uuid': uuid, 'name': 'vm-owned', 'power_state': 'poweredOff',
           'cpu': 2, 'memory_mb': 2048, 'ownership_sha256': marker_digest(marker)}
    row.update(overrides)
    return row


def inspector(**overrides):
    def read(connection_id, uuid):
        return live(**overrides)
    return read


def adopt(client, stack='vm-app', environment='dev', uuid=UUID, marker=MARKER):
    response = client.post('/api/environment-states/adoptions', json={
        'environment': environment, 'stack': stack, 'connection_id': CONNECTION,
        'uuid': uuid, 'marker': marker})
    assert response.status_code == 200, response.text
    return response.json()['state']


def run_report(client):
    response = client.post(DRIFT_RUN)
    assert response.status_code == 200, response.text
    return response.json()


def audit_actions(app):
    return [row['action'] for row in app.state.store.all('SELECT action FROM audit ORDER BY id')]


@pytest.fixture
def platform(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live()
    app.state.vm_destruction.inspector = lambda connection_id, uuid: live()
    app.state.drift.inspector = inspector()
    app.state.drift.clock = Clock()
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


def test_a_matching_stack_is_in_sync(platform):
    app, client = platform
    state = adopt(client)
    payload = run_report(client)
    report = payload['reports'][0]
    assert report['id'] == state['id'] and report['generation'] == 1
    assert report['status'] == IN_SYNC and report['reason'] is None
    assert report['summary'] == {'resources': 1, 'in_sync': 1, 'drift': 0,
                                 'unverified': 0, 'unsupported': 0}
    resource = report['resources'][0]
    assert resource['address'] == 'esxi_vm.' + UUID and resource['type'] == 'esxi_vm'
    assert resource['status'] == IN_SYNC and resource['fields'] == []
    assert resource['checked'] == ['uuid', 'name', 'power_state', 'cpu', 'memory_mb']
    assert report['checked_at'] == '2026-11-17T12:00:00+00:00'
    assert payload['summary']['in_sync'] == 1


@pytest.mark.parametrize('field,live_value,declared', [
    ('name', 'vm-renamed', 'vm-owned'),
    ('power_state', 'poweredOn', 'poweredOff'),
    ('cpu', 8, 2),
    ('memory_mb', 4096, 2048),
])
def test_drift_is_detected_per_field(platform, field, live_value, declared):
    app, client = platform
    adopt(client)
    app.state.drift.inspector = inspector(**{field: live_value})
    report = run_report(client)['reports'][0]
    assert report['status'] == DRIFT
    resource = report['resources'][0]
    assert resource['status'] == DRIFT
    assert resource['fields'] == [{'field': field, 'declared': declared, 'live': live_value}]
    assert resource['checked'] == ['uuid', 'name', 'power_state', 'cpu', 'memory_mb']
    assert report['summary']['drift'] == 1


def test_a_foreign_uuid_is_drift(platform):
    app, client = platform
    adopt(client)
    app.state.drift.inspector = inspector(uuid=OTHER_UUID)
    resource = run_report(client)['reports'][0]['resources'][0]
    assert resource['status'] == DRIFT
    assert resource['fields'] == [{'field': 'uuid', 'declared': UUID, 'live': OTHER_UUID}]


def test_an_unreadable_resource_is_unverified_not_drift(platform):
    app, client = platform
    adopt(client)

    def refuse(connection_id, uuid):
        raise RuntimeError('synthetic provider failure')
    app.state.drift.inspector = refuse
    report = run_report(client)['reports'][0]
    assert report['status'] == UNVERIFIED
    resource = report['resources'][0]
    assert resource['status'] == UNVERIFIED and resource['fields'] == []
    assert resource['reason'] == 'the resource could not be read'
    assert report['summary']['drift'] == 0 and report['summary']['unverified'] == 1


def test_without_a_live_reader_the_resource_is_unverified(platform):
    app, client = platform
    adopt(client)
    app.state.drift.inspector = None
    resource = run_report(client)['reports'][0]['resources'][0]
    assert resource['status'] == UNVERIFIED
    assert resource['reason'] == 'no live reader is wired'


def test_a_resource_without_a_recorded_connection_is_unverified(platform):
    app, client = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-app', JOB)
    document = {'source': 'adopted-vm', 'uuid': UUID, 'marker': MARKER,
                'name': 'vm-owned', 'power_state': 'poweredOff',
                'cpu': 2, 'memory_mb': 2048}
    states.commit(lease, document, [{'address': 'esxi_vm.' + UUID, 'type': 'esxi_vm',
                                     'name': UUID}])
    resource = run_report(client)['reports'][0]['resources'][0]
    assert resource['status'] == UNVERIFIED
    assert resource['reason'] == 'no connection is recorded for this stack'


def test_a_resource_type_without_a_reader_is_unsupported(platform):
    app, client = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-libvirt', JOB)
    states.commit(lease, {'values': {}},
                  [{'address': 'libvirt_domain.vm', 'type': 'libvirt_domain', 'name': 'vm'}])
    report = run_report(client)['reports'][0]
    assert report['status'] == UNSUPPORTED
    resource = report['resources'][0]
    assert resource['status'] == UNSUPPORTED
    assert resource['reason'] == 'the resource type has no read-only reader'


def test_a_data_resource_is_not_compared(platform):
    app, client = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-data', JOB)
    states.commit(lease, {'values': {}},
                  [{'address': 'data.esxi_vm.host', 'type': 'data', 'name': 'host',
                    'mode': 'data'}])
    resource = run_report(client)['reports'][0]['resources'][0]
    assert resource['status'] == UNSUPPORTED
    assert resource['reason'] == 'data sources are not managed resources'


def test_an_apply_lease_makes_the_report_unverified(platform):
    app, client = platform
    state = adopt(client)
    app.state.environment_states.acquire('dev', 'vm-app', JOB)
    report = run_report(client)['reports'][0]
    assert report['id'] == state['id']
    assert report['status'] == UNVERIFIED
    assert report['reason'] == 'an apply holds the state lease'
    assert report['resources'] == []


def test_a_quarantined_destruction_makes_the_report_unverified(platform):
    app, client = platform
    state = adopt(client)
    response = client.post(f"/api/environment-states/{state['id']}/destroy", json={
        'connection_id': CONNECTION, 'uuid': UUID, 'marker': MARKER})
    assert response.status_code == 200, response.text
    report = run_report(client)['reports'][0]
    assert report['status'] == UNVERIFIED
    assert report['reason'] == 'a destruction is in progress'


def test_a_stack_without_resources_is_empty(platform):
    app, client = platform
    app.state.environment_states.acquire('dev', 'never-applied', JOB)
    report = run_report(client)['reports'][0]
    assert report['stack'] == 'never-applied'
    assert report['status'] == EMPTY and report['generation'] == 0
    assert report['summary']['resources'] == 0


def test_an_unreadable_document_is_unavailable(platform):
    app, client = platform
    adopt(client)
    (app.state.environment_states.directory / 'state.key').unlink()
    report = run_report(client)['reports'][0]
    assert report['status'] == UNAVAILABLE
    assert report['reason'] == 'the recorded document is unavailable'
    assert report['resources'] == []


def test_a_declaration_without_attributes_checks_only_the_identity(platform):
    app, client = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-applied', JOB)
    document = {'values': {'root_module': {'resources': [
        {'address': 'esxi_vm.' + UUID, 'type': 'esxi_vm', 'name': UUID, 'values': {}}]}},
        'connection_id': CONNECTION}
    states.commit(lease, document, [{'address': 'esxi_vm.' + UUID, 'type': 'esxi_vm',
                                     'name': UUID}])
    resource = run_report(client)['reports'][0]['resources'][0]
    assert resource['status'] == IN_SYNC and resource['checked'] == ['uuid']


def test_declared_attributes_reads_both_document_shapes():
    adopted = {'source': 'adopted-vm', 'uuid': UUID, 'name': 'vm-owned',
               'power_state': 'poweredOff', 'cpu': 2, 'memory_mb': 2048,
               'connection_id': CONNECTION}
    assert declared_attributes(adopted, 'esxi_vm.' + UUID) == {
        'name': 'vm-owned', 'power_state': 'poweredOff', 'cpu': 2, 'memory_mb': 2048}
    assert declared_attributes(adopted, 'esxi_vm.' + OTHER_UUID) == {}
    applied = {'values': {'root_module': {'resources': [
        {'address': 'esxi_vm.' + UUID, 'type': 'esxi_vm',
         'values': {'name': 'vm-applied', 'cpu': 4}}]}}}
    assert declared_attributes(applied, 'esxi_vm.' + UUID) == {'name': 'vm-applied', 'cpu': 4}
    assert declared_attributes({}, 'esxi_vm.' + UUID) == {}
    assert declared_attributes('synthetic', 'esxi_vm.' + UUID) == {}


def test_reports_are_persisted_and_survive_a_restart(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live()
    app.state.drift.inspector = inspector()
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        state = adopt(client)
        run_report(client)
    restarted = make_app(tmp_path, monkeypatch)
    with TestClient(restarted, raise_server_exceptions=False) as client:
        login(client, restarted)
        listing = client.get(DRIFT_LIST)
        assert listing.status_code == 200
        assert listing.json()['reports'][0]['id'] == state['id']
        assert listing.json()['reports'][0]['status'] == IN_SYNC
        detail = client.get(f"/api/environment-states/{state['id']}/drift")
        assert detail.status_code == 200 and detail.json()['generation'] == 1


def test_the_report_writes_nothing_but_its_own_evidence(platform):
    app, client = platform
    state = adopt(client)
    before = app.state.store.one('SELECT body FROM environment_state WHERE id=?',
                                 (state['id'],))['body']
    document_before = app.state.store.one(
        'SELECT body FROM environment_state_documents WHERE id=?', (state['id'],))['body']
    calls = []

    class Driver:
        def read_resource(self, connection, credentials, identity):
            calls.append(('read_resource', identity))
            return live()

        def __getattr__(self, name):
            raise AssertionError('drift must not call ' + name)

    class Connections:
        def access(self, identity):
            @contextmanager
            def granted():
                yield {'id': identity, 'driver': 'esxi'}, b'{"username":"u","password":"p"}'
            return granted()

        def driver(self, connection):
            return Driver()

    app.state.connections = Connections()
    app.state.drift.inspector = connection_inspector(app)
    run_report(client)
    assert calls == [('read_resource', UUID)]
    after = app.state.store.one('SELECT body FROM environment_state WHERE id=?',
                                (state['id'],))['body']
    document_after = app.state.store.one(
        'SELECT body FROM environment_state_documents WHERE id=?', (state['id'],))['body']
    assert after == before and document_after == document_before
    assert app.state.store.one('SELECT 1 AS present FROM environment_state_claim LIMIT 1') is None
    assert app.state.store.one('SELECT 1 AS present FROM environment_state_destroy LIMIT 1') is None
    reports = app.state.store.all('SELECT body FROM environment_state_drift')
    assert len(reports) == 1 and json.loads(reports[0]['body'])['id'] == state['id']
    assert audit_actions(app).count('environment_state.drift_checked') == 1


def test_the_loop_sweep_writes_no_audit_row(platform):
    app, client = platform
    adopt(client)
    app.state.drift.run()
    assert 'environment_state.drift_checked' not in audit_actions(app)
    assert client.get(DRIFT_LIST).json()['reports'][0]['status'] == IN_SYNC


def test_the_manual_run_requires_an_administrator(platform):
    app, client = platform
    adopt(client)
    for role in ('operator', 'viewer'):
        login(client, app, role)
        assert client.post(DRIFT_RUN).status_code == 403
    login(client, app, 'viewer')
    assert client.get(DRIFT_LIST).status_code == 200


def test_the_manual_run_requires_a_session_and_csrf(platform):
    app, client = platform
    token = client.headers.pop('x-csrf-token')
    assert client.post(DRIFT_RUN).status_code == 403
    client.headers['x-csrf-token'] = token
    client.cookies.clear()
    assert client.post(DRIFT_RUN).status_code == 401


def test_the_drift_routes_respect_agent_scopes(platform):
    app, client = platform
    state = adopt(client)
    run_report(client)
    issued = client.post('/api/agent-tokens', json={
        'label': 'drift-reader', 'role': 'viewer', 'scopes': ['read:infrastructure'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    reader = {'authorization': 'Bearer ' + issued.json()['token']}
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.get(DRIFT_LIST, headers=reader).status_code == 200
    assert client.get(f"/api/environment-states/{state['id']}/drift", headers=reader).status_code == 200
    assert client.post(DRIFT_RUN, headers=reader).status_code == 403
    login(client, app)
    issued = client.post('/api/agent-tokens', json={
        'label': 'job-reader', 'role': 'viewer', 'scopes': ['read:jobs'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    jobs = {'authorization': 'Bearer ' + issued.json()['token']}
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.get(DRIFT_LIST, headers=jobs).status_code == 403


def test_unknown_states_and_unchecked_states_are_reported(platform):
    app, client = platform
    assert client.get('/api/environment-states/' + 'e' * 32 + '/drift').status_code == 404
    assert client.get('/api/environment-states/not-an-identity/drift').status_code == 404
    state = adopt(client)
    assert client.get(f"/api/environment-states/{state['id']}/drift").status_code == 404
    assert client.get(DRIFT_LIST).json() == {
        'reports': [], 'summary': {'total': 0, 'in_sync': 0, 'drift': 0, 'unverified': 0,
                                   'unsupported': 0, 'empty': 0, 'unavailable': 0},
        'schedule': {'interval_seconds': DEFAULT_INTERVAL, 'error': None}}


@pytest.mark.parametrize('value,expected,error', [
    (None, DEFAULT_INTERVAL, None), ('', DEFAULT_INTERVAL, None),
    ('0', 0.0, None), ('3600', 3600.0, None), ('30', DEFAULT_INTERVAL, True),
    ('synthetic', DEFAULT_INTERVAL, True), ('604801', DEFAULT_INTERVAL, True),
    ('-1', DEFAULT_INTERVAL, True)])
def test_the_interval_setting_falls_back_and_reports(monkeypatch, value, expected, error):
    if value is None:
        monkeypatch.delenv(INTERVAL_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(INTERVAL_VARIABLE, value)
    schedule = drift_schedule()
    assert schedule.interval == expected
    assert (schedule.error is not None) is bool(error)


def test_the_loop_repeats_and_survives_a_failure():
    calls = []

    def sweep():
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise RuntimeError('synthetic sweep failure')

    loop = DriftLoop(sweep, interval=0.05, initial_delay=0)
    loop.start()
    deadline = time.monotonic() + 3
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    loop.close()
    assert len(calls) >= 2
    assert loop.thread is not None and not loop.thread.is_alive()


def test_a_failing_stack_does_not_stop_the_other_reports(platform):
    app, client = platform
    adopt(client, stack='vm-good')
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live(uuid=OTHER_UUID,
                                                                       marker=MARKER)
    adopt(client, stack='vm-bad', uuid=OTHER_UUID)

    def read(connection_id, uuid):
        if uuid == OTHER_UUID:
            raise RuntimeError('synthetic')
        return live()

    app.state.drift.inspector = read
    payload = run_report(client)
    assert len(payload['reports']) == 2
    assert {report['stack']: report['status'] for report in payload['reports']} == {
        'vm-good': IN_SYNC, 'vm-bad': UNVERIFIED}


def test_the_kernel_wires_the_drift_service(platform):
    app, _ = platform
    assert isinstance(app.state.drift, DriftService)
    assert callable(app.state.drift.inspector)
    assert isinstance(app.state.drift_loop, DriftLoop)
    assert app.state.drift_loop not in app.state.background_services


def test_postgresql_registers_the_drift_table(tmp_path, pg_file):
    from kuberweaver.environment_state import EnvironmentStates
    from kuberweaver.storage import Store

    store = Store(tmp_path / 'pg-drift')
    try:
        assert store.backend == 'postgresql'
        states = EnvironmentStates(store)
        lease = states.acquire('staging', 'vm-app', JOB)
        states.commit(lease, {'source': 'adopted-vm', 'uuid': UUID, 'marker': MARKER,
                              'name': 'vm-owned', 'power_state': 'poweredOff',
                              'cpu': 2, 'memory_mb': 2048, 'connection_id': CONNECTION},
                      [{'address': 'esxi_vm.' + UUID, 'type': 'esxi_vm', 'name': UUID}])
        service = DriftService(states, lambda connection_id, uuid: live())
        result = service.run()
        assert result['reports'][0]['status'] == IN_SYNC
        row = store.one('SELECT body FROM environment_state_drift')
        assert row is not None
        assert json.loads(row['body'])['generation'] == 1
        assert service.collection()['summary']['in_sync'] == 1
    finally:
        store.conn.close()
