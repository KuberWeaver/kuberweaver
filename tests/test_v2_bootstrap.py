"""The normal application factory, not a private test harness, owns the API."""
from test_core_graph import api


def test_standard_application_installs_connection_routes(api):
    client, _ = api
    response = client.get('/api/connections')
    assert response.status_code == 200, response.text
    assert response.json() == []
    drivers = client.get('/api/connection-drivers')
    assert drivers.status_code == 200
    assert isinstance(drivers.json()['drivers'], list)
    assert isinstance(drivers.json()['secret_sources'], list)
