"""Destruction of an owned VM: proof, quarantine, cancellation and failures."""
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from kuberweaver.adoption import AdoptionService, marker_digest
from kuberweaver.destruction import (DestroyService, DestroyUnavailable,
                                     DestroyUnverified, connection_destroyer)
from test_postgresql_store import pg_file  # noqa: F401  fixture

UUID = '11111111-1111-4111-8111-111111111111'
OTHER_UUID = '22222222-2222-4222-8222-222222222222'
MARKER = 'kuberweaver-job:' + 'a' * 32
OTHER_MARKER = 'kuberweaver-job:' + 'b' * 32
CONNECTION = 'c' * 32
ADOPTIONS = '/api/environment-states/adoptions'
JOB = 'd' * 32


class Clock:
    def __init__(self, value='2026-11-17T12:00:00+00:00'):
        self.value = datetime.fromisoformat(value)

    def __call__(self):
        return self.value.isoformat()

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


def make_app(path, monkeypatch, name='state'):
    # app.py creates a module-level app: never let its first import touch data/.
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
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


def inspector(rows=None):
    table = rows if rows is not None else {UUID: live(), OTHER_UUID: live(OTHER_UUID, OTHER_MARKER)}

    def read(connection_id, uuid):
        return table[uuid]
    return read


def recorder():
    calls = []

    def destroy(connection_id, uuid, marker):
        calls.append((connection_id, uuid, marker))
        return True
    destroy.calls = calls
    return destroy


@pytest.fixture
def platform(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    clock = Clock()
    app.state.vm_adoption.inspector = inspector()
    app.state.vm_destruction.inspector = inspector()
    app.state.vm_destruction.destroyer = recorder()
    app.state.vm_destruction.clock = clock
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client, clock


def adopt(client, stack='vm-owned', uuid=UUID, marker=MARKER, environment='dev'):
    response = client.post(ADOPTIONS, json={'environment': environment, 'stack': stack,
                                            'connection_id': CONNECTION, 'uuid': uuid,
                                            'marker': marker})
    assert response.status_code == 200, response.text
    return response.json()['state']


def destroy_body(**overrides):
    value = {'connection_id': CONNECTION, 'uuid': UUID, 'marker': MARKER}
    value.update(overrides)
    return value


def request_destroy(client, identity, **overrides):
    return client.post(f'/api/environment-states/{identity}/destroy',
                       json=destroy_body(**overrides))


def quarantine(client, stack='vm-owned'):
    state = adopt(client, stack)
    response = request_destroy(client, state['id'])
    assert response.status_code == 200, response.text
    return state, response.json()['destroy']


def destroy_actions(app):
    return [row['action'] for row in app.state.store.all('SELECT action FROM audit ORDER BY id')
            if row['action'].startswith('environment_state.')]


def test_a_quarantine_records_the_proof_and_touches_nothing(platform):
    app, client, clock = platform
    state = adopt(client)
    app.state.vm_destruction.destroyer = recorder()
    response = request_destroy(client, state['id'])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['changed'] is True
    destroy = payload['destroy']
    assert destroy['status'] == 'quarantined'
    assert destroy['uuid'] == UUID and destroy['marker'] == MARKER
    assert destroy['connection_id'] == CONNECTION and destroy['name'] == 'vm-owned'
    assert destroy['requested_by'] == 'admin' and destroy['not_before'] == '2026-11-17T12:15:00+00:00'
    assert destroy['requires_manual_review'] is False and destroy['reason'] is None
    assert payload['state']['generation'] == 1 and payload['state']['destroy']['destroy_id'] == destroy['destroy_id']
    assert app.state.vm_destruction.destroyer.calls == []
    detail = client.get('/api/environment-states/' + state['id']).json()
    assert detail['destroy']['status'] == 'quarantined'
    assert destroy_actions(app) == ['environment_state.adopted',
                                    'environment_state.destroy_quarantined']


def test_a_repeat_request_keeps_the_same_window(platform):
    app, client, _ = platform
    state = adopt(client)
    first = request_destroy(client, state['id']).json()
    second = request_destroy(client, state['id']).json()
    assert second['changed'] is False
    assert second['destroy']['destroy_id'] == first['destroy']['destroy_id']
    assert second['destroy']['not_before'] == first['destroy']['not_before']
    assert destroy_actions(app).count('environment_state.destroy_quarantined') == 1


def test_a_foreign_vm_is_refused_before_the_host_is_read(platform):
    app, client, _ = platform
    state = adopt(client)
    reads = []
    app.state.vm_destruction.inspector = lambda connection_id, uuid: reads.append(uuid)
    response = request_destroy(client, state['id'], uuid=OTHER_UUID)
    assert response.status_code == 409
    assert 'not owned' in response.json()['detail']
    assert reads == []
    assert app.state.environment_states.destruction(state['id']) is None


def test_a_stack_cannot_destroy_a_vm_owned_by_another_stack(platform):
    app, client, _ = platform
    adopt(client, 'vm-one', UUID, MARKER)
    other = adopt(client, 'vm-two', OTHER_UUID, OTHER_MARKER)
    response = destroy_body(uuid=OTHER_UUID, marker=OTHER_MARKER)
    assert client.post(f"/api/environment-states/{other['id']}/destroy",
                       json=response).status_code == 200
    first = app.state.environment_states.get('dev', 'vm-one')
    refused = client.post(f"/api/environment-states/{first['id']}/destroy",
                          json=response)
    assert refused.status_code == 409
    assert 'not owned' in refused.json()['detail']


def test_a_changed_annotation_is_refused(platform):
    app, client, _ = platform
    state = adopt(client)
    app.state.vm_destruction.inspector = lambda connection_id, uuid: live(
        ownership_sha256=marker_digest(OTHER_MARKER))
    response = request_destroy(client, state['id'])
    assert response.status_code == 409
    assert 'marker' in response.json()['detail'].lower()
    assert app.state.environment_states.destruction(state['id']) is None
    assert destroy_actions(app) == ['environment_state.adopted']


def test_a_vm_without_a_marker_is_refused(platform):
    app, client, _ = platform
    state = adopt(client)
    app.state.vm_destruction.inspector = lambda connection_id, uuid: live(ownership_sha256=None)
    response = request_destroy(client, state['id'])
    assert response.status_code == 409
    assert 'no KuberWeaver ownership marker' in response.json()['detail']


def test_an_unreadable_vm_is_refused_without_leaking_the_cause(platform):
    app, client, _ = platform
    state = adopt(client)
    app.state.vm_destruction.inspector = lambda connection_id, uuid: (_ for _ in ()).throw(
        RuntimeError('synthetic provider text'))
    response = request_destroy(client, state['id'])
    assert response.status_code == 409
    assert 'synthetic provider text' not in response.text
    app.state.vm_destruction.inspector = None
    assert request_destroy(client, state['id']).status_code == 503


def test_an_unknown_state_is_a_not_found(platform):
    _, client, _ = platform
    response = request_destroy(client, 'f' * 32)
    assert response.status_code == 404


def test_a_held_apply_lease_blocks_the_request(platform):
    app, client, _ = platform
    state = adopt(client)
    lease = app.state.environment_states.acquire('dev', 'vm-owned', JOB)
    response = request_destroy(client, state['id'])
    assert response.status_code == 409
    assert 'lease' in response.json()['detail']
    assert app.state.environment_states.release(lease) is True
    assert request_destroy(client, state['id']).status_code == 200


def test_an_apply_is_refused_while_a_destroy_is_quarantined(platform):
    app, client, _ = platform
    state, _ = quarantine(client)
    with pytest.raises(Exception) as refusal:
        app.state.environment_states.acquire('dev', 'vm-owned', JOB)
    assert 'destruction' in str(refusal.value)
    destroy_id = app.state.environment_states.destruction(state['id'])['destroy_id']
    assert client.post(f"/api/environment-states/{state['id']}/destroy/cancel",
                       json={'destroy_id': destroy_id}).status_code == 200
    lease = app.state.environment_states.acquire('dev', 'vm-owned', JOB)
    assert app.state.environment_states.release(lease) is True


def test_cancel_ends_the_destruction_without_a_host_call(platform):
    app, client, _ = platform
    state, destroy = quarantine(client)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/cancel",
                           json={'destroy_id': destroy['destroy_id']})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['cancelled'] is True and payload['destroy']['status'] == 'cancelled'
    assert payload['state']['generation'] == 1 and len(payload['state']['resources']) == 1
    assert app.state.vm_destruction.destroyer.calls == []
    assert destroy_actions(app)[-1] == 'environment_state.destroy_cancelled'
    again = client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                        json={'destroy_id': destroy['destroy_id']})
    assert again.status_code == 409


def test_cancel_requires_the_current_destroy_id(platform):
    app, client, _ = platform
    state, destroy = quarantine(client)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/cancel",
                           json={'destroy_id': 'e' * 32})
    assert response.status_code == 409
    assert 'changed' in response.json()['detail']
    assert app.state.environment_states.destruction(state['id'])['status'] == 'quarantined'
    assert destroy['destroy_id'] != 'e' * 32


def test_execute_before_the_window_is_refused(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                           json={'destroy_id': destroy['destroy_id']})
    assert response.status_code == 409
    assert 'window' in response.json()['detail']
    assert app.state.environment_states.destruction(state['id'])['status'] == 'quarantined'
    assert app.state.vm_destruction.destroyer.calls == []
    clock.advance(minutes=15)
    assert client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                       json={'destroy_id': destroy['destroy_id']}).status_code == 200


def test_execute_empties_the_generation_after_proof(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)
    clock.advance(minutes=16)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                           json={'destroy_id': destroy['destroy_id']})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['destroyed'] is True and payload['destroy']['status'] == 'destroyed'
    emptied = payload['state']
    assert emptied['generation'] == 2 and emptied['resources'] == []
    assert emptied['source'] == 'destroy' and emptied['job_id'] is None
    assert emptied['adopted'] is None
    assert emptied['destroyed'] == {'uuid': UUID, 'at': '2026-11-17T12:16:00+00:00', 'by': 'admin'}
    assert app.state.vm_destruction.destroyer.calls == [(CONNECTION, UUID, MARKER)]
    document = app.state.environment_states.document('dev', 'vm-owned')
    assert document['source'] == 'destroyed' and document['uuid'] == UUID
    assert document['marker'] == MARKER and document['destroyed_by'] == 'admin'
    stored = app.state.store.one('SELECT body FROM environment_state_documents WHERE id=?',
                                 (state['id'],))['body']
    assert 'vm-owned' not in stored
    assert destroy_actions(app) == ['environment_state.adopted',
                                    'environment_state.destroy_quarantined',
                                    'environment_state.destroyed']


def test_execute_re_proves_the_annotation(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)
    app.state.vm_destruction.inspector = lambda connection_id, uuid: live(
        ownership_sha256=marker_digest(OTHER_MARKER))
    clock.advance(minutes=16)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                           json={'destroy_id': destroy['destroy_id']})
    assert response.status_code == 409
    assert 'marker' in response.json()['detail'].lower()
    stored = app.state.environment_states.destruction(state['id'])
    assert stored['status'] == 'failed' and stored['requires_manual_review'] is True
    assert app.state.environment_states.get('dev', 'vm-owned')['generation'] == 1
    assert app.state.vm_destruction.destroyer.calls == []


def test_execute_refuses_an_unreadable_vm(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)
    app.state.vm_destruction.inspector = lambda connection_id, uuid: (_ for _ in ()).throw(
        RuntimeError('synthetic provider text'))
    clock.advance(minutes=16)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                           json={'destroy_id': destroy['destroy_id']})
    assert response.status_code == 409
    assert 'synthetic provider text' not in response.text
    assert app.state.environment_states.destruction(state['id'])['status'] == 'failed'


def test_a_failed_destroy_is_rollback_failed_and_requires_review(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)

    def failing(connection_id, uuid, marker):
        raise RuntimeError('synthetic hypervisor text')

    app.state.vm_destruction.destroyer = failing
    clock.advance(minutes=16)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                           json={'destroy_id': destroy['destroy_id']})
    assert response.status_code == 502
    assert 'synthetic hypervisor text' not in response.text
    stored = app.state.environment_states.destruction(state['id'])
    assert stored['status'] == 'rollback_failed' and stored['requires_manual_review'] is True
    assert stored['reason'] == 'provider_error'
    assert app.state.environment_states.get('dev', 'vm-owned')['generation'] == 1
    assert destroy_actions(app)[-1] == 'environment_state.destroy_rollback_failed'
    assert client.post(f"/api/environment-states/{state['id']}/destroy/cancel",
                       json={'destroy_id': destroy['destroy_id']}).status_code == 409
    assert request_destroy(client, state['id']).status_code == 409
    assert client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                       json={'destroy_id': destroy['destroy_id']}).status_code == 409


def test_a_destroy_whose_state_write_fails_is_rollback_failed(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)
    (app.state.environment_states.directory / 'state.key').unlink()
    clock.advance(minutes=16)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                           json={'destroy_id': destroy['destroy_id']})
    assert response.status_code == 503
    assert 'manual review' in response.json()['detail']
    stored = app.state.environment_states.destruction(state['id'])
    assert stored['status'] == 'rollback_failed' and stored['requires_manual_review'] is True
    assert stored['reason'] == 'state_unconfirmed'
    assert app.state.environment_states.get('dev', 'vm-owned')['generation'] == 1


def test_resolve_intact_verifies_the_vm_and_restores_retry(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)

    def failing(connection_id, uuid, marker):
        raise RuntimeError('provider down')

    app.state.vm_destruction.destroyer = failing
    clock.advance(minutes=16)
    assert client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                       json={'destroy_id': destroy['destroy_id']}).status_code == 502
    app.state.vm_destruction.inspector = lambda connection_id, uuid: (_ for _ in ()).throw(
        RuntimeError('unreachable'))
    refused = client.post(f"/api/environment-states/{state['id']}/destroy/resolve",
                          json={'destroy_id': destroy['destroy_id'], 'outcome': 'intact'})
    assert refused.status_code == 409
    assert app.state.environment_states.destruction(state['id'])['status'] == 'rollback_failed'
    app.state.vm_destruction.inspector = inspector()
    response = client.post(f"/api/environment-states/{state['id']}/destroy/resolve",
                           json={'destroy_id': destroy['destroy_id'], 'outcome': 'intact'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['resolved'] == 'intact'
    assert payload['destroy']['status'] == 'failed'
    assert payload['destroy']['reason'] == 'resolved_intact'
    assert payload['state']['generation'] == 1 and len(payload['state']['resources']) == 1
    assert destroy_actions(app)[-1] == 'environment_state.destroy_resolved'
    app.state.vm_destruction.destroyer = recorder()
    retry = request_destroy(client, state['id'])
    assert retry.status_code == 200 and retry.json()['changed'] is True


def test_resolve_destroyed_commits_the_emptied_generation(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)

    def failing(connection_id, uuid, marker):
        raise RuntimeError('provider down')

    app.state.vm_destruction.destroyer = failing
    clock.advance(minutes=16)
    assert client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                       json={'destroy_id': destroy['destroy_id']}).status_code == 502
    clock.advance(minutes=1)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/resolve",
                           json={'destroy_id': destroy['destroy_id'], 'outcome': 'destroyed'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['resolved'] == 'destroyed'
    assert payload['destroy']['status'] == 'destroyed'
    assert payload['destroy']['reason'] == 'resolved_destroyed'
    assert payload['state']['generation'] == 2 and payload['state']['resources'] == []
    document = app.state.environment_states.document('dev', 'vm-owned')
    assert document['source'] == 'destroyed' and document['uuid'] == UUID
    assert client.post(f"/api/environment-states/{state['id']}/destroy/resolve",
                       json={'destroy_id': destroy['destroy_id'], 'outcome': 'destroyed'}).status_code == 409


def test_a_quarantined_destroy_cannot_be_resolved(platform):
    app, client, _ = platform
    state, destroy = quarantine(client)
    response = client.post(f"/api/environment-states/{state['id']}/destroy/resolve",
                           json={'destroy_id': destroy['destroy_id'], 'outcome': 'destroyed'})
    assert response.status_code == 409
    assert 'unverified' in response.json()['detail']


def test_a_state_with_several_resources_is_refused(platform):
    app, client, _ = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'many', JOB)
    states.commit(lease, {'values': {}},
                  [{'address': 'esxi_vm.' + UUID, 'type': 'esxi_vm', 'name': UUID},
                   {'address': 'null_resource.other', 'type': 'null_resource', 'name': 'other'}])
    state = states.get('dev', 'many')
    response = request_destroy(client, state['id'])
    assert response.status_code == 409
    assert 'not owned' in response.json()['detail']


def test_cancel_is_refused_once_execution_started(platform):
    app, client, clock = platform
    state, destroy = quarantine(client)
    clock.advance(minutes=16)
    caught = []

    def trying_to_cancel(connection_id, uuid, marker):
        try:
            app.state.vm_destruction.cancel(state['id'], destroy['destroy_id'],
                                            {'username': 'admin', 'role': 'admin'})
        except Exception as error:
            caught.append(error)
        return True

    app.state.vm_destruction.destroyer = trying_to_cancel
    response = client.post(f"/api/environment-states/{state['id']}/destroy/execute",
                           json={'destroy_id': destroy['destroy_id']})
    assert response.status_code == 200
    assert len(caught) == 1 and getattr(caught[0], 'status_code', None) == 409
    assert app.state.environment_states.destruction(state['id'])['status'] == 'destroyed'


def test_a_destroy_survives_a_restart(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.state.vm_adoption.inspector = inspector()
    state = app.state.vm_adoption.adopt('dev', 'vm-owned', CONNECTION, UUID, MARKER,
                                        {'username': 'admin', 'role': 'admin'})['state']
    clock = Clock()
    service = app.state.vm_destruction
    service.inspector = inspector()
    service.destroyer = recorder()
    service.clock = clock
    destroy = service.request(state['id'], CONNECTION, UUID, MARKER,
                              {'username': 'admin', 'role': 'admin'})['destroy']
    restarted = make_app(tmp_path, monkeypatch)
    restarted.state.vm_destruction.inspector = inspector()
    restarted.state.vm_destruction.destroyer = recorder()
    restarted.state.vm_destruction.clock = clock
    assert restarted.state.environment_states.destruction(state['id'])['destroy_id'] == destroy['destroy_id']
    clock.advance(minutes=16)
    result = restarted.state.vm_destruction.execute(state['id'], destroy['destroy_id'],
                                                    {'username': 'admin', 'role': 'admin'})
    assert result['state']['generation'] == 2 and result['destroy']['status'] == 'destroyed'


def test_the_request_requires_a_human_administrator(platform):
    app, client, _ = platform
    state = adopt(client)
    for role in ('operator', 'viewer'):
        login(client, app, role)
        assert request_destroy(client, state['id']).status_code == 403
    login(client, app)
    issued = client.post('/api/agent-tokens', json={
        'label': 'destroyer', 'role': 'admin', 'scopes': ['submit', 'configure'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    headers = {'authorization': 'Bearer ' + issued.json()['token']}
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.post(f"/api/environment-states/{state['id']}/destroy",
                       json=destroy_body(), headers=headers).status_code == 403


def test_the_request_requires_a_session_and_csrf(platform):
    app, client, _ = platform
    state = adopt(client)
    token = client.headers.pop('x-csrf-token')
    assert request_destroy(client, state['id']).status_code == 403
    client.headers['x-csrf-token'] = token
    client.cookies.clear()
    assert request_destroy(client, state['id']).status_code == 401


@pytest.mark.parametrize('field,value', [
    ('uuid', 'not-a-uuid'), ('uuid', '../../etc/passwd'), ('uuid', UUID + '0'),
    ('marker', 'kuberweaver-job:XYZ'), ('marker', 'synthetic-marker'),
    ('marker', 'kuberweaver-job:' + 'A' * 32),
    ('connection_id', 'c' * 31), ('connection_id', 'C' * 32),
])
def test_invalid_requests_are_refused(platform, field, value):
    _, client, _ = platform
    state = adopt(client)
    response = request_destroy(client, state['id'], **{field: value})
    assert response.status_code == 422, response.text


def test_unknown_and_missing_fields_are_refused(platform):
    _, client, _ = platform
    state = adopt(client)
    value = destroy_body()
    value['extra'] = 'synthetic'
    assert client.post(f"/api/environment-states/{state['id']}/destroy",
                       json=value).status_code == 422
    value = destroy_body()
    value.pop('marker')
    assert client.post(f"/api/environment-states/{state['id']}/destroy",
                       json=value).status_code == 422


def test_the_kernel_wires_the_destruction_service(platform):
    app, _, _ = platform
    assert isinstance(app.state.vm_destruction, DestroyService)
    assert callable(app.state.vm_destruction.inspector)
    assert callable(app.state.vm_destruction.destroyer)


def test_connection_destroyer_requires_a_confirmed_write():
    calls = []

    class Driver:
        def __init__(self, result):
            self.result = result

        def destroy_resource(self, connection, credentials, identity, marker):
            calls.append((connection['id'], credentials, identity, marker))
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    class FakeConnections:
        def __init__(self, driver):
            self.selected = driver

        def access(self, identity):
            @contextmanager
            def granted():
                yield {'id': identity, 'driver': 'esxi'}, b'{"username":"u","password":"p"}'
            return granted()

        def driver(self, connection):
            return self.selected

    class FakeApp:
        pass

    app = FakeApp()
    app.state = FakeApp()
    app.state.connections = FakeConnections(Driver(True))
    destroy = connection_destroyer(app)
    assert destroy(CONNECTION, UUID, MARKER) is True
    assert calls == [(CONNECTION, b'{"username":"u","password":"p"}', UUID, MARKER)]
    app.state.connections = FakeConnections(Driver(None))
    with pytest.raises(DestroyUnverified):
        destroy(CONNECTION, UUID, MARKER)
    app.state.connections = FakeConnections(Driver(RuntimeError('synthetic')))
    with pytest.raises(RuntimeError):
        destroy(CONNECTION, UUID, MARKER)

    class ReadOnly:
        pass

    app.state.connections = FakeConnections(ReadOnly())
    with pytest.raises(DestroyUnavailable):
        destroy(CONNECTION, UUID, MARKER)


def test_postgresql_registers_the_destroy_record(tmp_path, pg_file):
    from kuberweaver.environment_state import EnvironmentStates
    from kuberweaver.storage import Store

    store = Store(tmp_path / 'pg-destroy')
    try:
        assert store.backend == 'postgresql'
        states = EnvironmentStates(store)
        adopter = AdoptionService(states, lambda connection_id, uuid: live())
        actor = {'username': 'admin', 'role': 'admin'}
        adopter.adopt('staging', 'vm-owned', CONNECTION, UUID, MARKER, actor)
        identity = states.identity('staging', 'vm-owned')
        clock = Clock()
        service = DestroyService(states, lambda connection_id, uuid: live(),
                                 lambda connection_id, uuid, marker: True, clock=clock)
        first = service.request(identity, CONNECTION, UUID, MARKER, actor)
        assert first['destroy']['status'] == 'quarantined'
        clock.advance(minutes=16)
        result = service.execute(identity, first['destroy']['destroy_id'], actor)
        assert result['destroy']['status'] == 'destroyed'
        assert result['state']['generation'] == 2 and result['state']['resources'] == []
        row = store.one('SELECT body FROM environment_state_destroy WHERE id=?', (identity,))
        assert json.loads(row['body'])['status'] == 'destroyed'
    finally:
        store.conn.close()
