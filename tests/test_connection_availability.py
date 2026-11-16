"""Declared driver unavailability is metadata, not a remote health probe."""
import pytest
from fastapi import HTTPException

from test_connections import MemorySecrets, RecordingDriver, prohibit_real_network, spec
from test_core_graph import api


class MissingDependencyDriver:
    def discover(self, connection, credentials):
        raise AssertionError('An explicitly unavailable driver must not be invoked')


def test_connection_catalogue_respects_explicit_driver_unavailability(api):
    client, app = api
    app.state.registry.register('hypervisor', 'missing-dependency', MissingDependencyDriver(),
                                {'label': 'Optional missing dependency', 'available': False})
    descriptors = client.get('/api/connection-drivers').json()['drivers']
    descriptor = next(row for row in descriptors if row['id'] == 'missing-dependency')
    assert descriptor['available'] is False


def test_explicit_driver_unavailability_blocks_dispatch_before_credentials(api):
    client, app = api
    driver, source = RecordingDriver(), MemorySecrets()
    app.state.registry.register('hypervisor', 'missing-dependency', driver, {'available': False})
    app.state.registry.register('secrets', 'connection-secrets', source)
    response = client.post('/api/connections', json=spec(driver='missing-dependency'))
    assert response.status_code == 201, response.text
    connection = response.json()

    with pytest.raises(HTTPException) as error:
        app.state.connections.driver(connection)
    assert error.value.status_code == 503
    assert error.value.detail == 'Connection driver unavailable or incompatible'

    for action in ('test', 'discover'):
        response = client.post(f'/api/connections/{connection["id"]}/{action}')
        assert response.status_code == 200, response.text
        observation = response.json()
        assert observation['status'] == 'unavailable'
        assert observation['inventory'] is None
        assert observation['error'] == 'Connection discovery unavailable'
        assert client.get('/api/connections/' + connection['id']).json()['observation'] == observation
    assert not driver.calls and not source.reads


@pytest.mark.parametrize('available', [None, 0, 1, 0.0, 1.0, '', 'false', 'true', [], {}],
                         ids=['null', 'zero', 'one', 'float-zero', 'float-one', 'empty-string',
                              'string-false', 'string-true', 'array', 'object'])
def test_invalid_driver_availability_fails_closed(api, available):
    client, app = api
    driver, source = RecordingDriver(), MemorySecrets()
    app.state.registry.register('hypervisor', 'invalid-availability', driver, {'available': available})
    app.state.registry.register('secrets', 'connection-secrets', source)
    response = client.post('/api/connections', json=spec(driver='invalid-availability'))
    assert response.status_code == 201, response.text
    connection = response.json()

    with pytest.raises(HTTPException) as error:
        app.state.connections.driver(connection)
    assert error.value.status_code == 503
    assert error.value.detail == 'Connection driver unavailable or incompatible'
    response = client.get('/api/connection-drivers')
    assert response.status_code == 200, response.text
    descriptor = next(row for row in response.json()['drivers'] if row['id'] == 'invalid-availability')
    assert descriptor['available'] is False
    response = client.post(f'/api/connections/{connection["id"]}/discover')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'unavailable'
    assert response.json()['inventory'] is None
    assert not driver.calls and not source.reads


@pytest.mark.parametrize('metadata', [{}, {'available': True}], ids=['legacy', 'explicit-true'])
def test_callable_drivers_remain_available_without_probing(api, metadata):
    client, app = api
    driver, source = RecordingDriver(), MemorySecrets()
    bindings = dict(app.state.provider_bindings)
    app.state.registry.register('hypervisor', 'callable-driver', driver, metadata)
    app.state.registry.register('secrets', 'connection-secrets', source)
    response = client.post('/api/connections', json=spec(driver='callable-driver'))
    assert response.status_code == 201, response.text
    connection = response.json()

    response = client.get('/api/connection-drivers')
    assert response.status_code == 200, response.text
    descriptor = next(row for row in response.json()['drivers'] if row['id'] == 'callable-driver')
    assert descriptor['available'] is True
    assert app.state.connections.driver(connection) is driver
    assert not driver.calls and not source.reads

    response = client.post(f'/api/connections/{connection["id"]}/discover')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'reachable'
    assert response.json()['inventory'] == driver.payload
    assert len(driver.calls) == 1 and source.reads == ['hypervisors/lab']
    assert app.state.provider_bindings == bindings
