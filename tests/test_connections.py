"""Connection consumers, real SQLite/auth, injected external boundaries only."""
import importlib.util
import json
import re
import sys
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient



def spec(**changes):
    return {'label': 'Lab hypervisor', 'driver': 'recording',
            'endpoint': 'https://192.0.2.10', 'secret_ref': 'hypervisors/lab',
            'secrets_provider': 'connection-secrets', 'tls_sha256': None,
            'enabled': True, **changes}


def make_app(path, monkeypatch):
    # app.py creates a module-level app: never let its first import touch data/.
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
    from backend.app import create_app
    app = create_app(state_dir=path / 'state', testing=True)
    if importlib.util.find_spec('backend.connections') is not None:
        from backend.connections import install_connections_api
        if not hasattr(app.state, 'connections'):
            install_connections_api(app)
    return app


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
        raise AssertionError('These connection tests must not open real network connections')

    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def platform(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


def test_create_read_list_and_restart_persist_references_only(platform, tmp_path, monkeypatch):
    app, client = platform
    response = client.post('/api/connections', json=spec())
    assert response.status_code == 201, response.text
    connection = response.json()
    assert re.fullmatch('[0-9a-f]{32}', connection['id'])
    assert connection == {**spec(), 'id': connection['id'], 'revision': 1, 'observation': None}
    assert client.get('/api/connections/' + connection['id']).json() == connection
    assert client.get('/api/connections').json() == [connection]
    row = app.state.store.one('SELECT body FROM connections WHERE id=?', (connection['id'],))
    assert json.loads(row['body']) == connection
    restarted = make_app(tmp_path, monkeypatch)
    with TestClient(restarted) as other:
        login(other, restarted)
        assert other.get('/api/connections/' + connection['id']).json() == connection
        assert other.get('/api/connections').json() == [connection]


@pytest.mark.parametrize('changes', [
    {'label': ''}, {'label': 'x' * 81}, {'label': 1},
    {'driver': '../driver'}, {'driver': 'driver\n'}, {'driver': 'x' * 65},
    {'secrets_provider': 'Bad'}, {'secret_ref': '../password'},
    {'secret_ref': 'name/../secret'}, {'secret_ref': 'x' * 129},
    {'secret_ref': 'secret\n'}, {'tls_sha256': 'A' * 64},
    {'tls_sha256': 'a' * 63}, {'tls_sha256': 'g' * 64}, {'enabled': 1},
    {'enabled': 'true'}, {'revision': 4}, {'id': 'a' * 32},
    {'password': 'DO-NOT-REFLECT-me'}, {'observation': {}},
])
def test_closed_strict_spec_rejects_invalid_values_without_echoing_body(platform, changes):
    app, client = platform
    response = client.post('/api/connections', json=spec(**changes))
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': 'Invalid connection specification'}
    assert app.state.connections.list() == []


@pytest.mark.parametrize('endpoint', [
    'http://192.0.2.10', 'https://example.test', 'https://127.0.0.1',
    'https://169.254.1.1', 'https://224.0.0.1', 'https://0.0.0.0',
    'https://[::1]', 'https://[::]', 'https://[fe80::1]', 'https://[ff02::1]',
    'https://[::ffff:127.0.0.1]', 'https://[fe80::1%25eth0]',
    'https://user:password@192.0.2.10', 'https://192.0.2.10/',
    'https://192.0.2.10/sdk', 'https://192.0.2.10?password=secret',
    'https://192.0.2.10?', 'https://192.0.2.10#', 'https://192.0.2.10:0',
    'https://192.0.2.10:65536', 'https://192.0.2.10:',
    ' https://192.0.2.10', 'https://192.0.2.10\n', 'https://192.0.\t2.10',
])
def test_endpoint_is_an_explicit_safe_https_ip_origin(platform, endpoint):
    app, client = platform
    response = client.post('/api/connections', json=spec(endpoint=endpoint))
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': 'Invalid connection specification'}


@pytest.mark.parametrize('endpoint', ['https://10.20.30.40:8443', 'https://[2001:db8::10]:443'])
def test_private_and_ipv6_origins_remain_literal(platform, endpoint):
    app, client = platform
    response = client.post('/api/connections', json=spec(endpoint=endpoint, tls_sha256='a' * 64))
    assert response.status_code == 201, response.text
    assert response.json()['endpoint'] == endpoint
    assert response.json()['tls_sha256'] == 'a' * 64


def test_patch_is_partial_optimistic_and_identity_stable(platform):
    app, client = platform
    original = client.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + original['id']
    response = client.patch(path, json={'revision': 1, 'label': 'Renamed', 'enabled': False})
    assert response.status_code == 200, response.text
    updated = {**original, 'label': 'Renamed', 'enabled': False, 'revision': 2}
    assert response.json() == updated
    assert client.get(path).json() == updated
    stale = client.patch(path, json={'revision': 1, 'label': 'Lost update'})
    assert stale.status_code == 409
    assert client.get(path).json() == updated
    assert [row['action'] for row in app.state.store.all(
        "SELECT action FROM audit WHERE action LIKE 'connection.%' ORDER BY id")] == [
            'connection.created', 'connection.updated']


@pytest.mark.parametrize('patch', [
    {}, {'revision': None}, {'revision': True}, {'revision': '1'}, {'revision': 0},
    {'revision': 1, 'id': 'b' * 32}, {'revision': 1, 'observation': {}},
    {'revision': 1, 'label': None}, {'revision': 1, 'endpoint': 'http://192.0.2.10'},
    {'revision': 1, 'password': 'not-a-spec-field'}, [],
])
def test_patch_requires_strict_revision_and_forbids_identity_or_invalid_spec(platform, patch):
    app, client = platform
    original = client.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + original['id']
    response = client.patch(path, json=patch)
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': 'Invalid connection specification'}
    assert client.get(path).json() == original


def test_unknown_and_malformed_ids_are_not_looked_up(platform):
    app, client = platform
    # Even a corrupt legacy row must not turn an invalid identifier into a hit.
    app.state.store.execute('INSERT INTO connections(id,body) VALUES(?,?)', ('not-an-id', '{}'))
    for identity in ('not-an-id', 'a' * 32):
        assert client.get('/api/connections/' + identity).status_code == 404
        assert client.patch('/api/connections/' + identity, json={'revision': 1}).status_code == 404


def test_installer_is_idempotent(platform):
    app, client = platform
    from backend.connections import install_connections_api
    service, routes = app.state.connections, len(app.routes)
    install_connections_api(app)
    assert app.state.connections is service
    assert len(app.routes) == routes


def test_patch_model_is_exported_for_typed_mcp_consumers():
    from backend import connections
    assert hasattr(connections, 'ConnectionPatch'), 'Missing typed patch contract'
    from pydantic import ValidationError
    patch = connections.ConnectionPatch.model_validate({'revision': 3, 'enabled': False, 'tls_sha256': None})
    assert patch.model_dump(exclude_unset=True) == {'revision': 3, 'enabled': False, 'tls_sha256': None}
    assert 'revision' in connections.ConnectionPatch.model_json_schema()['required']
    for payload in ({}, {'revision': True}, {'revision': 0}, {'revision': 1, 'id': 'a' * 32},
                    {'revision': 1, 'label': None}, {'revision': 1, 'driver': '../bad'}):
        with pytest.raises(ValidationError):
            connections.ConnectionPatch.model_validate(payload)


def inventory():
    """SDK-boundary fixture, not evidence of a remote hypervisor."""
    return {'server': {'name': 'Lab', 'version': '1.2', 'api_type': 'test',
                       'host_id': 'host-1', 'write_api_supported': None,
                       'build': '123', 'api_version': '1.0', 'license_edition': None},
            'datastores': [{'id': 'ds-1', 'name': 'Storage', 'capacity_bytes': 2048,
                            'free_bytes': 1024, 'accessible': True}],
            'networks': [{'id': 'network-1', 'name': 'Guests'}],
            'resource_pools': [{'id': 'pool-1', 'name': 'Resources'}],
            'virtual_machines': [{'id': 'vm-1', 'name': 'Existing', 'uuid': 'uuid-1',
                                  'power_state': 'poweredOff', 'cpu': 2, 'memory_mb': 1024}]}


class MemorySecrets:
    def __init__(self):
        self.reads = []
        self.values = []

    def describe(self):
        return {'can_store': False, 'approved_names': ['hypervisors/lab']}

    @contextmanager
    def resolve(self, name):
        from backend.secrets import SecretValue
        self.reads.append(name)
        value = SecretValue(b'{"username":"test-user","password":"boundary-test-only"}')
        self.values.append(value)
        try:
            yield value
        finally:
            value.close()


class RecordingDriver:
    def __init__(self):
        self.calls = []
        self.payload = inventory()

    def discover(self, connection, credentials):
        self.calls.append((deepcopy(connection), credentials))
        return deepcopy(self.payload)


def boundaries(app, driver=None, source=None, name='recording'):
    source = source if source is not None else MemorySecrets()
    driver = driver if driver is not None else RecordingDriver()
    app.state.registry.register('hypervisor', name, driver, {'label': 'Recording driver'})
    if not any(row['name'] == 'connection-secrets' for row in app.state.registry.list('secrets')):
        app.state.registry.register('secrets', 'connection-secrets', source)
    return driver, source


def test_explicit_discovery_resolves_selected_credentials_and_caches_observation(platform):
    app, client = platform
    driver, source = boundaries(app)
    connection = client.post('/api/connections', json=spec()).json()
    identity = connection['id']
    assert client.get('/api/connections').json() == [connection]
    assert client.get('/api/connections/' + identity).json() == connection
    assert not driver.calls and not source.reads
    for action in ('test', 'discover'):
        response = client.post(f'/api/connections/{identity}/{action}')
        assert response.status_code == 200, response.text
        observation = response.json()
        assert observation == {'connection_id': identity, 'revision': 1, 'status': 'reachable',
                               'observed_at': observation['observed_at'], 'inventory': inventory(), 'error': None}
        assert datetime.fromisoformat(observation['observed_at']).utcoffset().total_seconds() == 0
        assert client.get('/api/connections/' + identity).json()['observation'] == observation
        assert app.state.connections.get(identity)['observation'] == observation
    assert len(driver.calls) == 2
    assert source.reads == ['hypervisors/lab', 'hypervisors/lab']
    assert all(credentials == b'{"username":"test-user","password":"boundary-test-only"}'
               for _, credentials in driver.calls)
    from backend.secrets import SecretError
    for value in source.values:
        with pytest.raises(SecretError):
            value.bytes()
    persisted = json.dumps(app.state.store.all('SELECT * FROM connections') + app.state.store.all('SELECT * FROM audit'))
    assert 'boundary-test-only' not in persisted and 'test-user' not in persisted


def test_access_yields_connection_and_bytes_then_closes_secret(platform):
    app, client = platform
    driver, source = boundaries(app)
    connection = client.post('/api/connections', json=spec()).json()
    assert app.state.connections.driver(connection) is driver
    with app.state.connections.access(connection['id']) as (snapshot, credentials):
        assert snapshot == connection
        assert isinstance(credentials, bytes) and b'boundary-test-only' in credentials
    from backend.secrets import SecretError
    with pytest.raises(SecretError):
        source.values[0].bytes()
    assert not driver.calls


@pytest.mark.parametrize('failure', ['exception', 'http', 'system_exit'])
def test_external_failures_become_static_unavailable_observations(platform, failure):
    app, client = platform
    from fastapi import HTTPException

    class FailingDriver:
        def discover(self, connection, credentials):
            if failure == 'http':
                raise HTTPException(401, 'boundary-test-only', headers={'X-Secret': 'boundary-test-only'})
            if failure == 'system_exit':
                raise SystemExit('boundary-test-only')
            raise RuntimeError('boundary-test-only')

    _, source = boundaries(app, driver=FailingDriver())
    connection = client.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + connection['id']
    if failure == 'system_exit':
        try:
            observation = app.state.connections.discover(connection['id'], {'username': 'admin'})
        except SystemExit:
            pytest.fail('External process exit escaped the discovery boundary')
        assert observation['status'] == 'unavailable'
        assert observation['inventory'] is None
        return
    response = client.post(path + '/discover')
    assert response.status_code == 200, response.text
    observation = response.json()
    assert observation['status'] == 'unavailable'
    assert observation['inventory'] is None
    assert observation['error'] == 'Connection discovery unavailable'
    assert observation['connection_id'] == connection['id'] and observation['revision'] == 1
    assert response.headers.get('x-secret') is None
    assert client.get(path).json()['observation'] == observation
    assert 'boundary-test-only' not in response.text
    assert 'boundary-test-only' not in json.dumps(app.state.store.all('SELECT * FROM audit'))
    from backend.secrets import SecretError
    with pytest.raises(SecretError):
        source.values[0].bytes()


@pytest.mark.parametrize('bad_driver', ['missing', 'none', 'arity', 'async', 'async_callable'])
def test_driver_protocol_is_synchronous_and_has_exact_consumer_arguments(platform, bad_driver):
    app, client = platform
    from fastapi import HTTPException

    class WrongArity:
        def discover(self, connection):
            raise AssertionError('must not be called')

    class AsyncDriver:
        async def discover(self, connection, credentials):
            raise AssertionError('must not be called')

    class AsyncCallable:
        async def __call__(self, connection, credentials):
            raise AssertionError('must not be called')

    class Wrapped:
        discover = AsyncCallable()

    providers = {'none': None, 'arity': WrongArity(), 'async': AsyncDriver(), 'async_callable': Wrapped()}
    if bad_driver != 'missing':
        app.state.registry.register('hypervisor', 'bad', providers[bad_driver])
    connection = client.post('/api/connections', json=spec(driver='bad')).json()
    with pytest.raises(HTTPException) as error:
        app.state.connections.driver(connection)
    assert error.value.status_code == 503
    assert error.value.detail == 'Connection driver unavailable or incompatible'
    response = client.post('/api/connections/' + connection['id'] + '/test')
    assert response.status_code == 200
    assert response.json()['status'] == 'unavailable'


def test_selected_missing_secret_source_never_falls_back(platform):
    app, client = platform
    driver, source = boundaries(app)
    app.state.provider_bindings['secrets'] = 'connection-secrets'
    connection = client.post('/api/connections', json=spec(secrets_provider='missing')).json()
    response = client.post('/api/connections/' + connection['id'] + '/discover')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'unavailable' and response.json()['inventory'] is None
    assert not source.reads and not driver.calls


def test_secret_errors_are_sanitized_at_access_boundary(platform):
    app, client = platform
    from fastapi import HTTPException

    class FailingSource(MemorySecrets):
        @contextmanager
        def resolve(self, name):
            raise HTTPException(418, 'boundary-test-only', headers={'X-Secret': 'boundary-test-only'})
            yield

    boundaries(app, source=FailingSource())
    connection = client.post('/api/connections', json=spec()).json()
    with pytest.raises(HTTPException) as error:
        with app.state.connections.access(connection['id']):
            pytest.fail('credentials must not be yielded')
    assert error.value.status_code == 503 and error.value.detail == 'Connection credentials unavailable'
    assert error.value.headers is None


def test_disabled_connections_reject_remote_access_without_observation(platform):
    app, client = platform
    driver, source = boundaries(app)
    connection = client.post('/api/connections', json=spec(enabled=False)).json()
    response = client.post('/api/connections/' + connection['id'] + '/test')
    assert response.status_code == 409, response.text
    assert client.get('/api/connections/' + connection['id']).json()['observation'] is None
    assert not source.reads and not driver.calls
    assert client.post('/api/connections/' + 'a' * 32 + '/discover').status_code == 404


@pytest.mark.parametrize('change', [
    'not_object', 'missing_array', 'extra_root', 'extra_server', 'extra_vm',
    'bad_boolean', 'negative_capacity', 'boolean_cpu', 'missing_vm_name',
    'too_many', 'long_name', 'null_array', 'wrong_element', 'bad_server',
])
def test_discovery_inventory_is_closed_strict_and_bounded(platform, change):
    app, client = platform
    driver, _ = boundaries(app)
    payload = inventory()
    if change == 'not_object':
        payload = []
    elif change == 'missing_array':
        del payload['networks']
    elif change == 'extra_root':
        payload['credentials'] = 'not-for-storage'
    elif change == 'extra_server':
        payload['server']['password'] = 'not-for-storage'
    elif change == 'extra_vm':
        payload['virtual_machines'][0]['password'] = 'not-for-storage'
    elif change == 'bad_boolean':
        payload['server']['write_api_supported'] = 'true'
    elif change == 'negative_capacity':
        payload['datastores'][0]['capacity_bytes'] = -1
    elif change == 'boolean_cpu':
        payload['virtual_machines'][0]['cpu'] = True
    elif change == 'missing_vm_name':
        del payload['virtual_machines'][0]['name']
    elif change == 'too_many':
        payload['networks'] *= 1001
    elif change == 'long_name':
        payload['server']['name'] = 'x' * 257
    elif change == 'null_array':
        payload['networks'] = None
    elif change == 'wrong_element':
        payload['resource_pools'] = [None]
    elif change == 'bad_server':
        payload['server']['version'] = None
    driver.payload = payload
    connection = client.post('/api/connections', json=spec()).json()
    response = client.post('/api/connections/' + connection['id'] + '/discover')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'unavailable'
    assert response.json()['inventory'] is None
    assert response.json()['error'] == 'Connection discovery unavailable'
    stored = json.dumps(app.state.store.all('SELECT * FROM connections'))
    assert 'not-for-storage' not in stored


def test_discovery_accepts_required_empty_collections_and_nullable_unknowns(platform):
    app, client = platform
    driver, _ = boundaries(app)
    payload = inventory()
    payload['server'].pop('host_id')
    for key in ('build', 'api_version', 'license_edition'):
        payload['server'].pop(key)
    for key in ('datastores', 'networks', 'resource_pools'):
        payload[key] = []
    payload['virtual_machines'][0].update(uuid=None, cpu=None, memory_mb=None)
    driver.payload = payload
    connection = client.post('/api/connections', json=spec()).json()
    response = client.post('/api/connections/' + connection['id'] + '/discover')
    assert response.status_code == 200
    assert response.json()['status'] == 'reachable'
    assert response.json()['inventory'] == payload


def test_discovery_single_flight_is_per_connection_and_does_not_hold_store_lock(platform):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    app, client = platform
    started, release = Event(), Event()

    class PausedDriver(RecordingDriver):
        def discover(self, connection, credentials):
            if connection['label'] == 'Paused':
                started.set()
                assert release.wait(5), 'test did not release SDK boundary'
            return super().discover(connection, credentials)

    driver, _ = boundaries(app, driver=PausedDriver())
    connection = client.post('/api/connections', json=spec(label='Paused')).json()
    second = client.post('/api/connections', json=spec(label='Independent')).json()
    path = '/api/connections/' + connection['id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, path + '/discover')
        try:
            assert started.wait(5)
            assert app.state.store.lock.acquire(timeout=0.25), 'network call held SQLite lock'
            app.state.store.lock.release()
            duplicate = client.post(path + '/test')
            assert duplicate.status_code == 409, duplicate.text
            independent = client.post('/api/connections/' + second['id'] + '/test')
            assert independent.status_code == 200 and independent.json()['status'] == 'reachable'
        finally:
            release.set()
        assert pending.result(timeout=5).json()['status'] == 'reachable'
    assert len(driver.calls) == 2


def test_discovery_discards_observation_if_revision_changes_in_flight(platform):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    app, client = platform
    started, release = Event(), Event()

    class PausedDriver(RecordingDriver):
        def discover(self, connection, credentials):
            started.set()
            assert release.wait(5)
            # An SDK must not be able to mutate the host's connection snapshot.
            connection['revision'] = 99
            connection['endpoint'] = 'https://203.0.113.5'
            return super().discover(connection, credentials)

    boundaries(app, driver=PausedDriver())
    connection = client.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + connection['id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, path + '/discover')
        try:
            assert started.wait(5)
            patch = client.patch(path, json={'revision': 1, 'label': 'Changed during read'})
            assert patch.status_code == 200, patch.text
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 409, response.text
    current = client.get(path).json()
    assert current['revision'] == 2 and current['label'] == 'Changed during read'
    assert current['observation'] is None and current['endpoint'] == spec()['endpoint']
    # Finally released: a later explicit request can proceed.
    assert client.post(path + '/discover').json()['revision'] == 2


def test_driver_and_secret_source_listing_is_safe_local_metadata(platform):
    app, client = platform

    class Writable(MemorySecrets):
        def describe(self):
            return {'can_store': True, 'approved_names': ['hypervisors/lab'],
                    'names': ['hypervisors/other'], 'references': ['hypervisors/lab'],
                    'namespaced_references': True, 'private_token': 'not-public'}

        def store(self, name, value):
            raise AssertionError('GET must not store')

    driver, source = boundaries(app, source=Writable())
    app.state.registry.register('hypervisor', 'broken', None, {'label': 'Unavailable driver'})
    response = client.get('/api/connection-drivers')
    assert response.status_code == 200, response.text
    listing = response.json()
    assert set(listing) == {'drivers', 'secret_sources'}
    assert {'id': 'recording', 'label': 'Recording driver', 'available': True} in listing['drivers']
    assert {'id': 'broken', 'label': 'Unavailable driver', 'available': False} in listing['drivers']
    assert {'id': 'connection-secrets', 'available': True, 'can_store': True,
            'references': ['hypervisors/lab', 'hypervisors/other']} in listing['secret_sources']
    assert 'not-public' not in response.text
    assert not source.reads and not driver.calls


def test_listing_exposes_unavailable_selected_sources_without_fallback(platform):
    app, client = platform
    connection = client.post('/api/connections', json=spec(driver='not-installed', secrets_provider='not-installed')).json()
    response = client.get('/api/connection-drivers')
    assert response.status_code == 200
    assert {'id': 'not-installed', 'label': 'not-installed', 'available': False} in response.json()['drivers']
    assert {'id': 'not-installed', 'available': False, 'can_store': False,
            'references': []} in response.json()['secret_sources']
    assert client.get('/api/connections/' + connection['id']).json()['observation'] is None


def test_driver_replacement_is_consumed_without_global_binding_changes(platform):
    app, client = platform
    _, source = boundaries(app)

    class OtherDriver(RecordingDriver):
        def discover(self, connection, credentials):
            result = super().discover(connection, credentials)
            result['server']['name'] = 'Other hypervisor'
            return result

    alternate = OtherDriver()
    app.state.registry.register('hypervisor', 'alternate', alternate)
    original_bindings = dict(app.state.provider_bindings)
    connection = client.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + connection['id']
    assert client.post(path + '/discover').json()['inventory']['server']['name'] == 'Lab'
    updated = client.patch(path, json={'revision': 1, 'driver': 'alternate'})
    assert updated.status_code == 200
    assert updated.json()['observation'] is None
    assert client.post(path + '/discover').json()['inventory']['server']['name'] == 'Other hypervisor'
    assert len(alternate.calls) == 1
    assert app.state.provider_bindings == original_bindings


def test_fingerprint_covers_spec_revision_not_observation_or_resolved_secrets(platform):
    app, client = platform
    driver, source = boundaries(app)
    connection = client.post('/api/connections', json=spec()).json()
    service, identity = app.state.connections, connection['id']
    assert hasattr(service, 'fingerprint'), 'Missing approval fingerprint'
    initial = service.fingerprint(identity)
    assert re.fullmatch('[0-9a-f]{64}', initial)
    assert not source.reads and not driver.calls
    assert client.post('/api/connections/' + identity + '/discover').status_code == 200
    assert service.fingerprint(identity) == initial
    assert len(source.reads) == 1 and len(driver.calls) == 1
    response = client.patch('/api/connections/' + identity, json={'revision': 1, 'label': 'Changed'})
    assert response.status_code == 200
    assert service.fingerprint(identity) != initial
    # Even a revision-only accepted update invalidates previously approved input.
    before = service.fingerprint(identity)
    assert client.patch('/api/connections/' + identity, json={'revision': 2}).status_code == 200
    assert service.fingerprint(identity) != before
    assert len(source.reads) == 1


@pytest.mark.parametrize('kind', ['hypervisor', 'secrets'])
def test_fingerprint_pins_registered_provider_source_without_reading_credentials(platform, tmp_path, monkeypatch, kind):
    from types import ModuleType
    app, client = platform
    source_path = tmp_path / 'boundary_module.py'
    source_path.write_text('class Driver:\n    def discover(self, connection, credentials):\n        return {}\n'
                           'class Source:\n    def resolve(self, name):\n        raise AssertionError("must not resolve")\n')
    module = ModuleType('connection_boundary_source')
    module.__file__ = str(source_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source_path.read_bytes(), str(source_path), 'exec'), module.__dict__)
    if kind == 'hypervisor':
        boundaries(app, driver=module.Driver())
    else:
        boundaries(app, source=module.Source())
    connection = client.post('/api/connections', json=spec()).json()
    service = app.state.connections
    assert hasattr(service, 'fingerprint'), 'Missing approval fingerprint'
    initial = service.fingerprint(connection['id'])
    source_path.write_text(source_path.read_text() + '\n# provider source drift\n')
    assert service.fingerprint(connection['id']) != initial


class WritableSecrets(MemorySecrets):
    def __init__(self):
        super().__init__()
        self.saved = {}
        self.writes = []

    def describe(self):
        return {'can_store': True, 'approved_names': [], 'namespaced_references': True}

    def store(self, name, value):
        self.writes.append(value)
        self.saved[name] = value.bytes()
        with self.resolve(name) as readback:
            assert readback.bytes() == value.bytes()

    @contextmanager
    def resolve(self, name):
        from backend.secrets import SecretValue
        self.reads.append(name)
        value = SecretValue(self.saved.get(name, b'{"username":"test-user","password":"boundary-test-only"}'))
        self.values.append(value)
        try:
            yield value
        finally:
            value.close()


def test_human_credentials_write_is_memory_only_and_invalidates_revision_and_observation(platform):
    app, client = platform
    _, source = boundaries(app, source=WritableSecrets())
    connection = client.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + connection['id']
    assert client.post(path + '/discover').json()['status'] == 'reachable'
    before = app.state.connections.fingerprint(connection['id'])
    response = client.put(path + '/credentials', json={'username': 'ui-user', 'password': 'ui-only-password'})
    assert response.status_code == 200, response.text
    assert response.json() == {'stored': True, 'connection_id': connection['id']}
    assert json.loads(source.saved['hypervisors/lab']) == {'username': 'ui-user', 'password': 'ui-only-password'}
    from backend.secrets import SecretError
    with pytest.raises(SecretError):
        source.writes[0].bytes()
    current = client.get(path).json()
    assert current['revision'] == 2 and current['observation'] is None
    assert app.state.connections.fingerprint(connection['id']) != before
    persisted = json.dumps(app.state.store.all('SELECT * FROM connections') + app.state.store.all('SELECT * FROM audit'))
    assert 'ui-only-password' not in persisted and 'ui-user' not in persisted
    audit = app.state.store.one("SELECT * FROM audit WHERE action='connection.credentials_stored'")
    assert audit and json.loads(audit['detail']) == {} and audit['resource'] == connection['id']


@pytest.mark.parametrize('payload', [
    {}, {'username': 'u'}, {'password': 'hidden'}, {'username': '', 'password': 'hidden'},
    {'username': 'u', 'password': ''}, {'username': 1, 'password': 'hidden'},
    {'username': 'u', 'password': {'raw': 'hidden'}},
    {'username': 'u', 'password': 'hidden', 'extra': 'hidden'},
    {'username': 'x' * 257, 'password': 'hidden'}, {'username': 'u', 'password': 'x' * 4097},
    ['hidden'], None,
])
def test_credentials_validation_never_reflects_raw_password(platform, payload):
    app, client = platform
    _, source = boundaries(app, source=WritableSecrets())
    connection = client.post('/api/connections', json=spec()).json()
    response = client.put('/api/connections/' + connection['id'] + '/credentials', json=payload)
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': 'Invalid connection credentials'}
    assert not source.writes
    assert app.state.connections.get(connection['id']) == connection


def test_malformed_json_credentials_are_static_422(platform):
    app, client = platform
    _, source = boundaries(app, source=WritableSecrets())
    connection = client.post('/api/connections', json=spec()).json()
    response = client.put('/api/connections/' + connection['id'] + '/credentials',
                          content='{"password":"hidden",', headers={'Content-Type': 'application/json'})
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': 'Invalid connection credentials'}
    assert not source.writes


@pytest.mark.parametrize('mode', ['read_only', 'missing_store', 'async_store'])
def test_credentials_storage_requires_explicit_synchronous_write_capability(platform, mode):
    app, client = platform

    class ReadOnly(WritableSecrets):
        def describe(self):
            return {'can_store': False}

    class NoStore(MemorySecrets):
        def describe(self):
            return {'can_store': True}

    class AsyncStore(WritableSecrets):
        async def store(self, name, value):
            raise AssertionError('must not be called')

    source = {'read_only': ReadOnly(), 'missing_store': NoStore(), 'async_store': AsyncStore()}[mode]
    boundaries(app, source=source)
    connection = client.post('/api/connections', json=spec()).json()
    response = client.put('/api/connections/' + connection['id'] + '/credentials',
                          json={'username': 'ui-user', 'password': 'ui-only-password'})
    assert response.status_code == 409, response.text
    assert response.json() == {'detail': 'Selected secret source is read-only'}
    assert app.state.connections.get(connection['id']) == connection
    assert not getattr(source, 'writes', [])


def test_credentials_storage_sanitizes_provider_errors_and_closes_value(platform):
    from fastapi import HTTPException
    from backend.secrets import SecretError
    app, client = platform

    class FailingStore(WritableSecrets):
        def store(self, name, value):
            self.writes.append(value)
            raise HTTPException(401, 'ui-only-password', headers={'X-Secret': 'ui-only-password'})

    _, source = boundaries(app, source=FailingStore())
    connection = client.post('/api/connections', json=spec()).json()
    response = client.put('/api/connections/' + connection['id'] + '/credentials',
                          json={'username': 'ui-user', 'password': 'ui-only-password'})
    assert response.status_code == 503, response.text
    assert response.json() == {'detail': 'Connection credential storage unavailable'}
    assert 'x-secret' not in response.headers
    assert 'ui-only-password' not in json.dumps(app.state.store.all('SELECT * FROM audit'))
    with pytest.raises(SecretError):
        source.writes[0].bytes()
    assert app.state.connections.get(connection['id']) == connection


def test_credentials_reject_shared_reference_even_when_other_connection_disabled(platform):
    app, client = platform
    _, source = boundaries(app, source=WritableSecrets())
    first = client.post('/api/connections', json=spec()).json()
    assert client.post('/api/connections', json=spec(label='Other', enabled=False)).status_code == 201
    response = client.put('/api/connections/' + first['id'] + '/credentials',
                          json={'username': 'ui-user', 'password': 'ui-only-password'})
    assert response.status_code == 409, response.text
    assert response.json() == {'detail': 'Credential reference is shared by another connection'}
    assert not source.writes


def test_credentials_write_revalidates_revision_and_reserves_reference_outside_store_lock(platform):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    app, client = platform
    started, release = Event(), Event()

    class PausedStore(WritableSecrets):
        def store(self, name, value):
            started.set()
            assert release.wait(5)
            super().store(name, value)

    _, source = boundaries(app, source=PausedStore())
    connection = client.post('/api/connections', json=spec()).json()
    other = client.post('/api/connections', json=spec(secret_ref='hypervisors/other')).json()
    path = '/api/connections/' + connection['id']
    credentials = {'username': 'ui-user', 'password': 'ui-only-password'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.put, path + '/credentials', json=credentials)
        try:
            assert started.wait(5)
            assert app.state.store.lock.acquire(timeout=0.25), 'credential store held SQLite lock'
            app.state.store.lock.release()
            assert client.post(path + '/test').status_code == 409
            assert client.put(path + '/credentials', json=credentials).status_code == 409
            assert client.post('/api/connections', json=spec(label='New sharer')).status_code == 409
            assert client.patch('/api/connections/' + other['id'], json={'revision': 1,
                                'secret_ref': 'hypervisors/lab'}).status_code == 409
            assert client.patch(path, json={'revision': 1, 'label': 'Changed during storage'}).status_code == 200
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 409, response.text
    current = client.get(path).json()
    assert current['revision'] == 2 and current['label'] == 'Changed during storage'
    assert len(source.writes) == 1
    # No leaked reservation after optimistic conflict.
    assert client.put(path + '/credentials', json=credentials).status_code == 200
    assert client.get(path).json()['revision'] == 3


@pytest.mark.parametrize(('username', 'pool_field', 'pool_value'), [
    ('u', 'name', 'Resources'),
    ('root', 'id', 'ha-root-pool'),
])
def test_inventory_resource_containing_username_remains_reachable(platform, username, pool_field, pool_value):
    app, client = platform
    source = WritableSecrets()
    credentials = json.dumps({'username': username, 'password': 'boundary-test-only'}).encode()
    source.saved['hypervisors/lab'] = credentials
    driver, _ = boundaries(app, source=source)
    payload = inventory()
    payload['resource_pools'][0][pool_field] = pool_value
    driver.payload = deepcopy(payload)
    created = client.post('/api/connections', json=spec())
    assert created.status_code == 201
    path = '/api/connections/' + created.json()['id']

    for action in ('test', 'discover'):
        response = client.post(path + '/' + action)
        assert response.status_code == 200
        observation = response.json()
        assert observation['status'] == 'reachable'
        assert observation['error'] is None
        assert observation['inventory'] == payload
        assert observation['inventory']['resource_pools'][0][pool_field] == pool_value
        readback = client.get(path)
        assert readback.status_code == 200
        assert readback.json()['observation'] == observation
    assert source.reads == ['hypervisors/lab', 'hypervisors/lab']
    assert [raw for _, raw in driver.calls] == [credentials, credentials]


@pytest.mark.parametrize(('credentials', 'marker'), [
    (b'opaque-boundary-secret', 'opaque-boundary-secret'),
    (b'{"username":"test-user","password":"boundary-test-only"}', 'boundary-test-only'),
])
def test_inventory_cannot_embed_raw_credentials_or_password(platform, credentials, marker):
    app, client = platform
    source = WritableSecrets()
    source.saved['hypervisors/lab'] = credentials
    driver, _ = boundaries(app, source=source)
    driver.payload['server']['name'] = 'prefix-' + marker + '-suffix'
    connection = client.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + connection['id']
    response = client.post(path + '/discover')
    assert response.status_code == 200
    observation = response.json()
    assert observation['status'] == 'unavailable'
    assert observation['inventory'] is None
    assert observation['error'] == 'Connection discovery unavailable'
    assert client.get(path).json()['observation'] == observation
    assert source.reads == ['hypervisors/lab']
    assert [raw for _, raw in driver.calls] == [credentials]
    saved = json.dumps(app.state.store.all('SELECT * FROM connections') + app.state.store.all('SELECT * FROM audit'))
    assert marker not in response.text and marker not in saved


@pytest.mark.parametrize('field', ['username', 'password'])
def test_valid_inventory_cannot_echo_resolved_credentials_in_allowed_fields(platform, field):
    app, client = platform
    driver, _ = boundaries(app)
    driver.payload['server']['name'] = {'username': 'test-user', 'password': 'boundary-test-only'}[field]
    connection = client.post('/api/connections', json=spec()).json()
    response = client.post('/api/connections/' + connection['id'] + '/discover')
    assert response.status_code == 200
    assert response.json()['status'] == 'unavailable'
    assert response.json()['inventory'] is None
    saved = json.dumps(app.state.store.all('SELECT * FROM connections') + app.state.store.all('SELECT * FROM audit'))
    assert 'test-user' not in saved and 'boundary-test-only' not in saved


@pytest.mark.parametrize('raw', [b'', b'x' * 65537])
def test_access_refuses_empty_or_oversized_credentials(platform, raw):
    from fastapi import HTTPException
    from backend.secrets import SecretValue
    app, client = platform

    class BadSource(MemorySecrets):
        @contextmanager
        def resolve(self, name):
            value = SecretValue(raw)
            try:
                yield value
            finally:
                value.close()

    boundaries(app, source=BadSource())
    connection = client.post('/api/connections', json=spec()).json()
    with pytest.raises(HTTPException) as error:
        with app.state.connections.access(connection['id']):
            pytest.fail('invalid credentials must not be yielded')
    assert error.value.status_code == 503
    assert error.value.detail == 'Connection credentials unavailable'


def test_access_closes_secret_even_when_source_forgets_and_borrower_raises(platform):
    from backend.secrets import SecretError, SecretValue
    app, client = platform

    class ForgetfulSource(MemorySecrets):
        @contextmanager
        def resolve(self, name):
            value = SecretValue(b'boundary-test-only')
            self.values.append(value)
            yield value

    _, source = boundaries(app, source=ForgetfulSource())
    connection = client.post('/api/connections', json=spec()).json()
    with pytest.raises(ValueError, match='borrower error'):
        with app.state.connections.access(connection['id']):
            raise ValueError('borrower error')
    with pytest.raises(SecretError):
        source.values[0].bytes()


def test_sync_looking_driver_cannot_leave_an_unawaited_remote_operation(platform):
    app, client = platform

    class AwaitableDriver:
        pending = None

        async def remote(self):
            raise AssertionError('must not run asynchronous provider')

        def discover(self, connection, credentials):
            self.pending = self.remote()
            return self.pending

    driver = AwaitableDriver()
    boundaries(app, driver=driver)
    connection = client.post('/api/connections', json=spec()).json()
    response = client.post('/api/connections/' + connection['id'] + '/discover')
    try:
        assert response.status_code == 200 and response.json()['status'] == 'unavailable'
        assert driver.pending.cr_frame is None, 'rejected awaitable was not closed'
    finally:
        driver.pending.close()


def test_human_roles_csrf_and_origin_are_enforced_by_real_http_services(platform):
    app, client = platform
    driver, source = boundaries(app, source=WritableSecrets())
    connection = client.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + connection['id']
    for role in ('operator', 'viewer'):
        login(client, app, role)
        assert client.get('/api/connections').status_code == 200
        assert client.get(path).status_code == 200
        assert client.get('/api/connection-drivers').status_code == 200
        assert client.post('/api/connections', json=spec()).status_code == 403
        assert client.patch(path, json={'revision': 1, 'label': 'Denied'}).status_code == 403
        assert client.put(path + '/credentials', json={'username': 'u', 'password': 'hidden'}).status_code == 403
        response = client.post(path + '/test')
        assert response.status_code == (200 if role == 'operator' else 403)
    assert len(driver.calls) == 1 and not source.writes
    login(client, app)
    csrf = client.headers.pop('x-csrf-token')
    assert client.post('/api/connections', json=spec()).status_code == 403
    client.headers['x-csrf-token'] = csrf
    assert client.post(path + '/discover', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.put(path + '/credentials', json={}, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    with closing(TestClient(app)) as anonymous:
        assert anonymous.get('/api/connections').status_code == 401
        assert anonymous.get('/api/connection-drivers').status_code == 401
        assert anonymous.post(path + '/test').status_code == 401


def issue_agent(client, role, scopes):
    response = client.post('/api/agent-tokens', json={'label': 'connections-tests', 'role': role,
                                                   'scopes': scopes, 'expires_in_seconds': 3600})
    assert response.status_code == 201, response.text
    return response.json()['token']


def test_agents_intersect_role_and_scopes_and_never_ingest_credentials(platform):
    app, human = platform
    boundaries(app, source=WritableSecrets())
    connection = human.post('/api/connections', json=spec()).json()
    path = '/api/connections/' + connection['id']
    with closing(TestClient(app)) as agent:
        token = issue_agent(human, 'admin', ['configure'])
        agent.headers['Authorization'] = 'Bearer ' + token
        assert agent.post('/api/connections', json=spec(secret_ref='hypervisors/other')).status_code == 201
        assert agent.patch(path, json={'revision': 1, 'label': 'Configured by agent'}).status_code == 200
        assert agent.get(path).status_code == 403
        assert agent.post(path + '/test').status_code == 403
        assert agent.put(path + '/credentials', json={'username': 'u', 'password': 'hidden'}).status_code == 403
        token = issue_agent(human, 'operator', ['read:infrastructure'])
        agent.headers['Authorization'] = 'Bearer ' + token
        assert agent.get(path).status_code == 200
        assert agent.get('/api/connection-drivers').status_code == 200
        assert agent.post(path + '/discover').status_code == 200
        assert agent.post('/api/connections', json=spec()).status_code == 403
        assert agent.patch(path, json={'revision': 2}).status_code == 403
        token = issue_agent(human, 'viewer', ['read:infrastructure'])
        agent.headers['Authorization'] = 'Bearer ' + token
        assert agent.get(path).status_code == 200
        assert agent.post(path + '/discover').status_code == 403
        token = issue_agent(human, 'admin', ['read:finops'])
        agent.headers['Authorization'] = 'Bearer ' + token
        assert agent.get(path).status_code == 403
        assert agent.get('/api/connection-drivers').status_code == 403
        token = issue_agent(human, 'admin', ['configure', 'read:infrastructure'])
        agent.headers['Authorization'] = 'Bearer ' + token
        assert agent.put(path + '/credentials', json={'username': 'u', 'password': 'hidden'}).status_code == 403
        assert human.get(path, headers={'Authorization': 'Bearer ' + token}).status_code == 401


def test_sync_looking_secret_store_cannot_report_an_unexecuted_write(platform):
    app, client = platform

    class AwaitableStore(WritableSecrets):
        pending = None

        async def remote(self):
            raise AssertionError('must not execute asynchronous storage')

        def store(self, name, value):
            self.writes.append(value)
            self.pending = self.remote()
            return self.pending

    source = AwaitableStore()
    boundaries(app, source=source)
    connection = client.post('/api/connections', json=spec()).json()
    response = client.put('/api/connections/' + connection['id'] + '/credentials',
                          json={'username': 'ui-user', 'password': 'ui-only-password'})
    try:
        assert response.status_code == 503, response.text
        assert response.json() == {'detail': 'Connection credential storage unavailable'}
        assert source.pending.cr_frame is None
        assert app.state.connections.get(connection['id']) == connection
    finally:
        source.pending.close()
