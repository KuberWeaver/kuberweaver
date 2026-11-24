"""Adoption of an existing VM: ownership proof, refusals and idempotency."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.adoption import AdoptionService, connection_inspector, marker_digest
from test_postgresql_store import pg_file  # noqa: F401  fixture

UUID = '11111111-1111-4111-8111-111111111111'
OTHER_UUID = '22222222-2222-4222-8222-222222222222'
MARKER = 'kuberweaver-job:' + 'a' * 32
OTHER_MARKER = 'kuberweaver-job:' + 'b' * 32
CONNECTION = 'c' * 32
ADOPTIONS = '/api/environment-states/adoptions'


def make_app(path, monkeypatch, name='state'):
    # app.py creates a module-level app: never let its first import touch data/.
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
    from backend.app import create_app
    return create_app(state_dir=path / name, testing=True)


def login(client, app, role='admin'):
    credentials = dict(line.split(':', 1) for line in
                       (app.state.state_dir / 'bootstrap-credentials.txt').read_text().splitlines())
    response = client.post('/api/auth/login', json={'username': role, 'password': credentials[role]})
    assert response.status_code == 200, response.text
    token = response.json()['csrf_token']
    client.headers['x-csrf-token'] = token
    return token


def live(**overrides):
    row = {'uuid': UUID, 'name': 'vm-adopted-guard', 'power_state': 'poweredOff',
           'cpu': 2, 'memory_mb': 2048, 'ownership_sha256': marker_digest(MARKER)}
    row.update(overrides)
    return row


def body(**overrides):
    value = {'environment': 'dev', 'stack': 'vm-adopted', 'connection_id': CONNECTION,
             'uuid': UUID, 'marker': MARKER}
    value.update(overrides)
    return value


def audit_actions(app):
    return [row['action'] for row in app.state.store.all('SELECT action FROM audit ORDER BY id')
            if row['action'].startswith('environment_state.')]


@pytest.fixture
def platform(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live()
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


def test_an_adoption_records_one_verified_generation(platform):
    app, client = platform
    calls = []

    def inspect(connection_id, uuid):
        calls.append((connection_id, uuid))
        return live()

    app.state.vm_adoption.inspector = inspect
    response = client.post(ADOPTIONS, json=body())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['adopted'] is True and payload['unchanged'] is False
    state = payload['state']
    assert state['generation'] == 1 and state['source'] == 'adoption'
    assert state['job_id'] is None and state['lease'] is None
    assert state['resources'] == [{'address': 'esxi_vm.' + UUID, 'type': 'esxi_vm',
                                   'name': UUID, 'mode': 'managed'}]
    assert state['adopted'] == {'uuid': UUID, 'name': 'vm-adopted-guard', 'marker': MARKER,
                                'connection_id': CONNECTION, 'by': 'admin'}
    assert calls == [(CONNECTION, UUID)]
    # The document is encrypted at rest and the record names no provider detail.
    stored = app.state.store.one('SELECT body FROM environment_state_documents WHERE id=?',
                                 (state['id'],))['body']
    assert 'vm-adopted-guard' not in stored and 'adopted-vm' not in stored
    document = app.state.environment_states.document('dev', 'vm-adopted')
    assert document['uuid'] == UUID and document['marker'] == MARKER
    assert document['name'] == 'vm-adopted-guard' and document['cpu'] == 2
    assert document['connection_id'] == CONNECTION and document['adopted_by'] == 'admin'
    assert audit_actions(app) == ['environment_state.adopted']
    index = app.state.environment_states.adoption(UUID)
    assert index['stack'] == 'vm-adopted' and index['marker'] == MARKER
    assert index['id'] == state['id']
    listing = client.get(ADOPTIONS)
    assert listing.status_code == 200
    assert listing.json()['adoptions'] == [{'state_id': state['id'], 'uuid': UUID,
                                            'environment': 'dev', 'stack': 'vm-adopted',
                                            'name': 'vm-adopted-guard', 'marker': MARKER,
                                            'adopted_at': document['adopted_at'],
                                            'adopted_by': 'admin'}]


def test_an_uppercase_uuid_is_recorded_in_canonical_form(platform):
    app, client = platform
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live(
        uuid=UUID.upper(), ownership_sha256=marker_digest(MARKER))
    response = client.post(ADOPTIONS, json=body(uuid=UUID.upper()))
    assert response.status_code == 200, response.text
    state = response.json()['state']
    assert state['resources'][0]['address'] == 'esxi_vm.' + UUID
    assert state['adopted']['uuid'] == UUID
    assert app.state.environment_states.adoption(UUID)['uuid'] == UUID


def test_a_foreign_marker_is_refused(platform):
    app, client = platform
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live(
        ownership_sha256=marker_digest(OTHER_MARKER))
    response = client.post(ADOPTIONS, json=body())
    assert response.status_code == 409
    assert 'marker' in response.json()['detail'].lower()
    assert client.get('/api/environment-states').json() == {'states': []}
    assert app.state.environment_states.adoptions() == []
    assert audit_actions(app) == []


def test_a_vm_without_a_marker_is_refused(platform):
    app, client = platform
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live(ownership_sha256=None)
    response = client.post(ADOPTIONS, json=body())
    assert response.status_code == 409
    assert 'no KuberWeaver ownership marker' in response.json()['detail']
    assert app.state.environment_states.adoptions() == []


def test_a_driver_without_ownership_evidence_fails_closed(platform):
    app, client = platform
    row = live()
    row.pop('ownership_sha256')
    app.state.vm_adoption.inspector = lambda connection_id, uuid: row
    assert client.post(ADOPTIONS, json=body()).status_code == 409
    assert app.state.environment_states.adoptions() == []


def test_a_mismatched_uuid_is_refused(platform):
    app, client = platform
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live(uuid=OTHER_UUID)
    response = client.post(ADOPTIONS, json=body())
    assert response.status_code == 409
    assert 'uuid' in response.json()['detail'].lower()


def test_an_unreadable_vm_is_refused_without_leaking_the_cause(platform):
    app, client = platform

    def failing(connection_id, uuid):
        raise RuntimeError('synthetic provider text')

    app.state.vm_adoption.inspector = failing
    response = client.post(ADOPTIONS, json=body())
    assert response.status_code == 409
    assert 'synthetic provider text' not in response.text
    app.state.vm_adoption.inspector = None
    assert client.post(ADOPTIONS, json=body()).status_code == 503


def test_an_inconsistent_live_row_is_refused(platform):
    app, client = platform
    for bad in ({'power_state': 'halting'}, {'cpu': True}, {'memory_mb': 0}, {'name': ''},
                {'name': 'bad\nname'}):
        app.state.vm_adoption.inspector = lambda connection_id, uuid, bad=bad: live(**bad)
        assert client.post(ADOPTIONS, json=body()).status_code == 409


def test_reimport_is_idempotent(platform):
    app, client = platform
    first = client.post(ADOPTIONS, json=body())
    assert first.status_code == 200 and first.json()['adopted'] is True
    second = client.post(ADOPTIONS, json=body())
    assert second.status_code == 200
    payload = second.json()
    assert payload['adopted'] is False and payload['unchanged'] is True
    assert payload['state']['generation'] == 1
    assert payload['state']['resources'] == first.json()['state']['resources']
    assert len(app.state.environment_states.adoptions()) == 1
    assert audit_actions(app) == ['environment_state.adopted', 'environment_state.adoption_repeated']


def test_reimport_is_refused_when_the_annotation_changed(platform):
    app, client = platform
    assert client.post(ADOPTIONS, json=body()).status_code == 200
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live(
        ownership_sha256=marker_digest(OTHER_MARKER))
    response = client.post(ADOPTIONS, json=body())
    assert response.status_code == 409
    state = app.state.environment_states.get('dev', 'vm-adopted')
    assert state['generation'] == 1 and len(state['resources']) == 1


def test_a_second_stack_cannot_adopt_the_same_vm(platform):
    app, client = platform
    assert client.post(ADOPTIONS, json=body()).status_code == 200
    response = client.post(ADOPTIONS, json=body(stack='other-stack'))
    assert response.status_code == 409
    assert 'another stack' in response.json()['detail']
    assert app.state.environment_states.get('dev', 'other-stack') is None


def test_adoption_never_replaces_applied_state(platform):
    app, client = platform
    states = app.state.environment_states
    states.commit(states.acquire('dev', 'vm-adopted', 'd' * 32), {'values': {}}, [])
    response = client.post(ADOPTIONS, json=body())
    assert response.status_code == 409
    assert 'already has state' in response.json()['detail']
    assert states.get('dev', 'vm-adopted')['generation'] == 1


def test_adoption_is_refused_while_an_apply_holds_the_lease(platform):
    app, client = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-adopted', 'd' * 32)
    response = client.post(ADOPTIONS, json=body())
    assert response.status_code == 409
    assert 'lease' in response.json()['detail']
    assert states.release(lease) is True
    assert client.post(ADOPTIONS, json=body()).json()['adopted'] is True


def test_adoption_requires_an_administrator_session(platform):
    app, client = platform
    for role in ('operator', 'viewer'):
        login(client, app, role)
        assert client.post(ADOPTIONS, json=body()).status_code == 403


def test_adoption_requires_a_session_and_csrf(platform):
    app, client = platform
    token = client.headers.pop('x-csrf-token')
    assert client.post(ADOPTIONS, json=body()).status_code == 403
    client.headers['x-csrf-token'] = token
    client.cookies.clear()
    assert client.post(ADOPTIONS, json=body()).status_code == 401
    assert client.get(ADOPTIONS).status_code == 401


def test_agent_tokens_cannot_adopt(platform):
    app, client = platform
    issued = client.post('/api/agent-tokens', json={
        'label': 'adopter', 'role': 'admin', 'scopes': ['submit', 'configure'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    headers = {'authorization': 'Bearer ' + issued.json()['token']}
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.post(ADOPTIONS, json=body(), headers=headers).status_code == 403


@pytest.mark.parametrize('field,value', [
    ('environment', 'prod'), ('environment', ''), ('stack', ''), ('stack', ' padded'),
    ('stack', 'line\nbreak'), ('uuid', 'not-a-uuid'), ('uuid', '../../etc/passwd'),
    ('uuid', '11111111-1111-4111-8111-11111111111'), ('marker', 'kuberweaver-job:XYZ'),
    ('marker', 'synthetic-marker'), ('marker', 'kuberweaver-job:' + 'A' * 32),
    ('connection_id', 'c' * 31), ('connection_id', 'C' * 32),
])
def test_invalid_adoption_requests_are_refused(platform, field, value):
    _, client = platform
    response = client.post(ADOPTIONS, json=body(**{field: value}))
    assert response.status_code == 422, response.text


def test_unknown_and_missing_fields_are_refused(platform):
    _, client = platform
    value = body()
    value['extra'] = 'synthetic'
    assert client.post(ADOPTIONS, json=value).status_code == 422
    value = body()
    value.pop('marker')
    assert client.post(ADOPTIONS, json=value).status_code == 422


def test_adoption_survives_a_restart(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live()
    first = app.state.vm_adoption.adopt('dev', 'vm-adopted', CONNECTION, UUID, MARKER,
                                        {'username': 'admin', 'role': 'admin'})
    assert first['adopted'] is True
    restarted = make_app(tmp_path, monkeypatch)
    restarted.state.vm_adoption.inspector = lambda connection_id, uuid: live()
    again = restarted.state.vm_adoption.adopt('dev', 'vm-adopted', CONNECTION, UUID, MARKER,
                                              {'username': 'admin', 'role': 'admin'})
    assert again['adopted'] is False and again['state']['generation'] == 1
    assert restarted.state.environment_states.get('dev', 'vm-adopted')['adopted']['uuid'] == UUID
    assert [row['uuid'] for row in restarted.state.environment_states.adoptions()] == [UUID]
    with pytest.raises(HTTPException) as refusal:
        restarted.state.vm_adoption.adopt('staging', 'vm-adopted', CONNECTION, UUID, MARKER,
                                          {'username': 'admin', 'role': 'admin'})
    assert refusal.value.status_code == 409 and 'another stack' in refusal.value.detail


def test_adoption_refuses_when_the_state_key_is_missing(platform):
    app, client = platform
    assert client.post(ADOPTIONS, json=body()).status_code == 200
    (app.state.environment_states.directory / 'state.key').unlink()
    other = body(uuid=OTHER_UUID, marker=OTHER_MARKER, stack='other-stack')
    app.state.vm_adoption.inspector = lambda connection_id, uuid: live(
        uuid=OTHER_UUID, ownership_sha256=marker_digest(OTHER_MARKER))
    response = client.post(ADOPTIONS, json=other)
    assert response.status_code == 503
    assert app.state.environment_states.get('dev', 'other-stack') is None
    assert not (app.state.environment_states.directory / 'state.key').exists()


def test_live_read_goes_through_the_connection_registry():
    calls = []

    class Driver:
        def read_resource(self, connection, credentials, identity):
            calls.append(('read', connection['id'], credentials, identity))
            return {'uuid': identity}

    class FakeConnections:
        def access(self, identity):
            calls.append(('access', identity))

            @contextmanager
            def granted():
                yield {'id': identity, 'driver': 'esxi'}, b'{"username":"u","password":"p"}'
            return granted()

        def driver(self, connection):
            calls.append(('driver', connection['driver']))
            return Driver()

    class FakeApp:
        pass

    app = FakeApp()
    app.state = FakeApp()
    app.state.connections = FakeConnections()
    inspect = connection_inspector(app)
    assert inspect(CONNECTION, UUID) == {'uuid': UUID}
    assert calls == [('access', CONNECTION), ('driver', 'esxi'),
                     ('read', CONNECTION, b'{"username":"u","password":"p"}', UUID)]


def test_the_kernel_wires_the_adoption_service_with_the_connection_inspector(platform):
    app, _ = platform
    assert isinstance(app.state.vm_adoption, AdoptionService)
    assert callable(app.state.vm_adoption.inspector)


def test_postgresql_registers_the_adoption_index(tmp_path, pg_file):
    from backend.storage import Store

    store = Store(tmp_path / 'pg-adoption')
    try:
        assert store.backend == 'postgresql'
        from backend.environment_state import EnvironmentStates
        states = EnvironmentStates(store)
        service = AdoptionService(states, lambda connection_id, uuid: live())
        first = service.adopt('staging', 'vm-adopted', CONNECTION, UUID, MARKER,
                              {'username': 'admin', 'role': 'admin'})
        assert first['state']['generation'] == 1
        assert states.adoption(UUID)['stack'] == 'vm-adopted'
        again = service.adopt('staging', 'vm-adopted', CONNECTION, UUID, MARKER,
                              {'username': 'admin', 'role': 'admin'})
        assert again['adopted'] is False and again['state']['generation'] == 1
        with pytest.raises(HTTPException) as refusal:
            service.adopt('dev', 'other', CONNECTION, UUID, MARKER,
                          {'username': 'admin', 'role': 'admin'})
        assert 'another stack' in refusal.value.detail
    finally:
        store.conn.close()
