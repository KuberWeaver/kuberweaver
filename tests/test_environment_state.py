"""Durable stack state: generations, claims, encryption and real auth."""
import hashlib
import json
import stat

import pytest
from fastapi.testclient import TestClient

from backend.environment_state import EnvironmentStates, StateConflict, StateError
from test_postgresql_store import pg_file  # noqa: F401  fixture

JOB_A = 'a' * 32
JOB_B = 'b' * 32

DOCUMENT = {'values': {'root_module': {'resources': [
    {'address': 'libvirt_domain.vm', 'type': 'libvirt_domain', 'name': 'vm',
     'values': {'uuid': 'owned-uuid'}}]}},
    'private_attribute': 'plaintext-marker'}
RESOURCES = [{'address': 'libvirt_domain.vm', 'type': 'libvirt_domain', 'name': 'vm'}]
DIGEST = hashlib.sha256(json.dumps(DOCUMENT, sort_keys=True,
                                   separators=(',', ':')).encode()).hexdigest()


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


@pytest.fixture
def platform(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


def committed(states, job_id=JOB_A, environment='dev', stack='vm-test'):
    lease = states.acquire(environment, stack, job_id)
    return states.commit(lease, DOCUMENT, RESOURCES)


def test_a_commit_records_one_encrypted_generation(platform):
    app, _ = platform
    states = app.state.environment_states
    view = committed(states)
    assert view['generation'] == 1
    assert view['job_id'] == JOB_A
    assert view['digest'] == DIGEST
    assert view['resources'] == [{'address': 'libvirt_domain.vm', 'type': 'libvirt_domain',
                                  'name': 'vm', 'mode': 'managed'}]
    assert view['resource_count'] == 1
    assert view['lease'] is None and view['encrypted'] is True
    assert states.document('dev', 'vm-test') == DOCUMENT
    stored = app.state.store.one('SELECT body FROM environment_state_documents WHERE id=?',
                                 (view['id'],))['body']
    assert 'plaintext-marker' not in stored
    assert 'libvirt_domain' not in stored
    record = json.loads(app.state.store.one('SELECT body FROM environment_state WHERE id=?',
                                            (view['id'],))['body'])
    assert record['generation'] == 1
    assert record['resources'][0]['address'] == 'libvirt_domain.vm'
    key = states.directory / 'state.key'
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(states.directory.stat().st_mode) == 0o700


def test_each_commit_increments_the_generation(platform):
    app, _ = platform
    states = app.state.environment_states
    committed(states)
    second = states.acquire('dev', 'vm-test', JOB_B)
    assert second['base_generation'] == 1
    view = states.commit(second, {'values': {}}, [])
    assert view['generation'] == 2
    assert view['job_id'] == JOB_B
    assert view['resources'] == []
    assert view['resource_count'] == 0
    assert states.document('dev', 'vm-test') == {'values': {}}


def test_a_concurrent_apply_is_refused_while_a_claim_is_held(platform):
    app, _ = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-test', JOB_A)
    with pytest.raises(StateConflict, match='lease'):
        states.acquire('dev', 'vm-test', JOB_B)
    view = states.get('dev', 'vm-test')
    assert view['generation'] == 0 and view['resources'] == []
    assert view['lease'] == {'job_id': JOB_A, 'acquired_at': lease['acquired_at']}
    assert states.acquire('dev', 'vm-test', JOB_A)['token'] == lease['token']
    assert states.release(lease) is True
    assert states.get('dev', 'vm-test')['lease'] is None
    assert states.acquire('dev', 'vm-test', JOB_B)['job_id'] == JOB_B


def test_a_stale_expected_generation_is_refused(platform):
    app, _ = platform
    states = app.state.environment_states
    committed(states)
    with pytest.raises(StateConflict, match='generation'):
        states.acquire('dev', 'vm-test', JOB_B, expected_generation=0)
    assert states.get('dev', 'vm-test')['lease'] is None


def test_a_commit_from_a_lost_claim_is_refused(platform):
    app, _ = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-test', JOB_A)
    assert states.release(lease) is True
    with pytest.raises(StateConflict, match='no longer held'):
        states.commit(lease, DOCUMENT, RESOURCES)
    assert states.get('dev', 'vm-test')['generation'] == 0


def test_a_commit_against_an_advanced_generation_is_refused(platform):
    app, _ = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-test', JOB_A)
    record = json.loads(app.state.store.one('SELECT body FROM environment_state WHERE id=?',
                                            (lease['id'],))['body'])
    # An administrator release plus a second apply is the only path that can
    # advance the generation while this claim row still exists.
    app.state.store.execute('INSERT OR REPLACE INTO environment_state(id,body) VALUES(?,?)',
                            (lease['id'], json.dumps({**record, 'generation': 1,
                                                      'job_id': JOB_B})))
    with pytest.raises(StateConflict, match='generation'):
        states.commit(lease, DOCUMENT, RESOURCES)
    assert states.get('dev', 'vm-test')['generation'] == 1
    assert states.get('dev', 'vm-test')['digest'] is None


def test_state_survives_a_restart(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    view = committed(app.state.environment_states)
    restarted = make_app(tmp_path, monkeypatch)
    reopened = restarted.state.environment_states
    again = reopened.get('dev', 'vm-test')
    assert again['id'] == view['id']
    assert again['generation'] == 1 and again['digest'] == DIGEST
    assert again['resources'] == view['resources']
    assert reopened.document('dev', 'vm-test') == DOCUMENT
    assert [row['stack'] for row in reopened.list()] == ['vm-test']
    assert reopened.list(environment='staging') == []


def test_a_missing_key_is_never_replaced(platform):
    app, _ = platform
    states = app.state.environment_states
    committed(states)
    (states.directory / 'state.key').unlink()
    with pytest.raises(StateError, match='missing'):
        states.document('dev', 'vm-test')
    lease = states.acquire('dev', 'vm-test', JOB_B)
    with pytest.raises(StateError, match='missing'):
        states.commit(lease, {'values': {}}, [])
    assert not (states.directory / 'state.key').exists()


@pytest.mark.parametrize('field', ['digest', 'token'])
def test_a_tampered_document_is_refused(platform, field):
    app, _ = platform
    states = app.state.environment_states
    view = committed(states)
    row = app.state.store.one('SELECT body FROM environment_state_documents WHERE id=?',
                              (view['id'],))
    envelope = json.loads(row['body'])
    envelope[field] = '0' * 64 if field == 'digest' else 'not-a-fernet-token'
    app.state.store.execute('INSERT OR REPLACE INTO environment_state_documents(id,body) '
                            'VALUES(?,?)', (view['id'], json.dumps(envelope)))
    with pytest.raises(StateError, match='changed'):
        states.document('dev', 'vm-test')


@pytest.mark.parametrize('environment,stack', [
    ('qa', 'vm-test'), ('dev', ''), ('dev', ' leading'), ('dev', 'trailing '),
    ('dev', 'x' * 161), ('dev', 'control\x01name'), ('dev', None),
])
def test_an_invalid_scope_is_refused(platform, environment, stack):
    app, _client = platform
    with pytest.raises(ValueError):
        app.state.environment_states.acquire(environment, stack, JOB_A)


@pytest.mark.parametrize('job_id', ['job-1', 'A' * 32, 'a' * 31, ''])
def test_an_invalid_job_identity_is_refused(platform, job_id):
    app, _ = platform
    with pytest.raises(ValueError):
        app.state.environment_states.acquire('dev', 'vm-test', job_id)


@pytest.mark.parametrize('resources', [
    [{'address': 'libvirt_domain.vm', 'extra': 'x'}],
    [{'address': 'libvirt_domain.vm'}, {'address': 'libvirt_domain.vm'}],
    [{'address': '../escape'}],
    [{'address': 'libvirt_domain.vm', 'mode': 'shadow'}],
    [{'address': 'libvirt_domain.vm', 'type': 'bad type'}],
    'not-a-list',
    [{}],
])
def test_an_invalid_resource_list_is_refused(platform, resources):
    app, _ = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-test', JOB_A)
    with pytest.raises(ValueError):
        states.commit(lease, DOCUMENT, resources)
    assert states.get('dev', 'vm-test')['generation'] == 0
    assert states.get('dev', 'vm-test')['lease']['job_id'] == JOB_A


@pytest.mark.parametrize('document', [[], 'text', {'value': float('inf')},
                                      {'value': 'x' * (4 * 1024 * 1024)}])
def test_an_invalid_document_is_refused(platform, document):
    app, _ = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-test', JOB_A)
    with pytest.raises(ValueError):
        states.commit(lease, document, [])
    assert states.get('dev', 'vm-test')['generation'] == 0


def test_the_api_exposes_generations_without_the_document(platform):
    app, client = platform
    view = committed(app.state.environment_states)
    listing = client.get('/api/environment-states')
    assert listing.status_code == 200, listing.text
    rows = listing.json()['states']
    assert len(rows) == 1 and rows[0]['id'] == view['id']
    assert rows[0]['generation'] == 1
    detail = client.get('/api/environment-states/' + view['id'])
    assert detail.status_code == 200
    assert detail.json() == rows[0]
    assert 'plaintext-marker' not in detail.text
    assert 'document' not in detail.json()
    assert client.get('/api/environment-states/' + 'f' * 32).status_code == 404


def test_reads_require_a_session_and_release_requires_an_administrator(platform):
    app, client = platform
    view = committed(app.state.environment_states)
    url = '/api/environment-states/' + view['id'] + '/lease/release'
    # A second lifespan would start the shared worker twice; the anonymous
    # client only needs the middleware and the routes.
    anonymous = TestClient(app)
    assert anonymous.get('/api/environment-states').status_code == 401
    assert anonymous.post(url, json={'job_id': JOB_A}).status_code == 401
    login(client, app, 'viewer')
    assert client.get('/api/environment-states').status_code == 200
    assert client.post(url, json={'job_id': JOB_A}).status_code == 403
    login(client, app, 'operator')
    assert client.post(url, json={'job_id': JOB_A}).status_code == 403


def test_an_administrator_clears_a_stuck_lease_explicitly(platform):
    app, client = platform
    states = app.state.environment_states
    lease = states.acquire('dev', 'vm-test', JOB_A)
    url = '/api/environment-states/' + lease['id'] + '/lease/release'
    assert client.post(url, json={'job_id': JOB_B}).status_code == 409
    csrf = client.headers.pop('x-csrf-token')
    assert client.post(url, json={'job_id': JOB_A}).status_code == 403
    client.headers['x-csrf-token'] = csrf
    released = client.post(url, json={'job_id': JOB_A})
    assert released.status_code == 200, released.text
    assert released.json()['lease'] is None
    assert states.get('dev', 'vm-test')['lease'] is None
    assert client.post(url, json={'job_id': JOB_A}).status_code == 409
    rows = [row for row in client.get('/api/audit').json()
            if row['action'] == 'environment_state.lease_released']
    assert rows and rows[0]['resource'] == lease['id']


def test_agent_tokens_use_the_infrastructure_scope(platform):
    app, client = platform
    view = committed(app.state.environment_states)
    issued = client.post('/api/agent-tokens', json={
        'label': 'state-reader', 'role': 'viewer', 'scopes': ['read:infrastructure'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    reader = {'authorization': 'Bearer ' + issued.json()['token']}
    # A presented session cookie would make the Bearer request ambiguous.
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.get('/api/environment-states', headers=reader).status_code == 200
    assert client.get('/api/environment-states/' + view['id'], headers=reader).status_code == 200
    login(client, app)
    issued = client.post('/api/agent-tokens', json={
        'label': 'job-reader', 'role': 'viewer', 'scopes': ['read:jobs'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    jobs = {'authorization': 'Bearer ' + issued.json()['token']}
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.get('/api/environment-states', headers=jobs).status_code == 403


def test_postgresql_registers_the_state_tables(tmp_path, pg_file):
    from backend.storage import Store

    store = Store(tmp_path / 'pg-state')
    try:
        assert store.backend == 'postgresql'
        states = EnvironmentStates(store)
        lease = states.acquire('staging', 'app', JOB_A)
        view = states.commit(lease, DOCUMENT, RESOURCES)
        assert view['generation'] == 1
        assert states.list()[0]['stack'] == 'app'
        assert states.document('staging', 'app') == DOCUMENT
        second = states.acquire('staging', 'app', JOB_B)
        assert states.commit(second, {'values': {}}, [])['generation'] == 2
    finally:
        store.conn.close()
