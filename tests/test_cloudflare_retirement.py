"""The legacy vendor API and CLI module are not part of the kernel."""
from importlib.util import find_spec

import pytest
from fastapi.testclient import TestClient


LEGACY_TABLES = {'cloudflare_connectors', 'cloudflare_dns_requests', 'cloudflare_tunnel_requests'}


def test_kernel_does_not_register_cloudflare(tmp_path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(tmp_path / 'module-app'))
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL_FILE', raising=False)
    from kuberweaver.app import create_app

    app = create_app(tmp_path / 'state', testing=True)
    with TestClient(app) as client:
        response = client.get('/openapi.json')
        assert response.status_code == 200
        paths = response.json()['paths']
        assert {'/api/connections', '/api/plugins', '/api/jobs'} <= paths.keys()
        assert not any(path.startswith('/api/cloudflare') for path in paths)
        for path in ('connectors', 'dns/zones', 'tunnel/tunnels'):
            response = client.get(f'/api/cloudflare/{path}')
            assert response.status_code == 404
        tables = app.state.store.all("SELECT name FROM sqlite_master WHERE type='table'")
        assert LEGACY_TABLES.isdisjoint(row['name'] for row in tables)


def test_postgres_does_not_register_cloudflare_tables():
    from kuberweaver import postgres

    for registry in (postgres.COLUMNS, postgres.DDL, postgres.ORDERED):
        assert LEGACY_TABLES.isdisjoint(registry)


@pytest.mark.parametrize(('method', 'path'), [
    ('GET', '/connectors'),
    ('PATCH', '/connectors/' + 'a' * 32),
    ('GET', '/dns/zones'),
    ('POST', '/dns/requests'),
    ('GET', '/tunnel/tunnels'),
    ('POST', '/tunnel/requests/' + 'a' * 32 + '/approve'),
])
def test_legacy_cloudflare_routes_have_no_agent_scope(method, path):
    from kuberweaver.agent_auth import required_scope

    assert required_scope(method, '/api/cloudflare' + path) is None


@pytest.mark.parametrize('module', [
    'kuberweaver.cloudflare',
    'kuberweaver.cloudflare_dns',
    'kuberweaver.cloudflare_tunnel',
    'kw.commands.cloudflare',
])
def test_legacy_cloudflare_modules_are_not_importable(module):
    assert find_spec(module) is None
