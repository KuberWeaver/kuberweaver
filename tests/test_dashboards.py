"""External dashboard registry: real SQLite and auth, no outbound request."""
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def spec(**changes):
    return {'label': 'Lab Grafana', 'kind': 'grafana', 'url': 'http://grafana.lab:3000/d/lab',
            'health_path': None, 'embed': False, 'enabled': True, **changes}


def make_app(path, monkeypatch, origins=None, name='state'):
    # app.py creates a module-level app: never let its first import touch data/.
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
    if origins is None:
        monkeypatch.delenv('KUBERWEAVER_DASHBOARD_EMBED_ORIGINS', raising=False)
    else:
        monkeypatch.setenv('KUBERWEAVER_DASHBOARD_EMBED_ORIGINS', origins)
    from kuberweaver.app import create_app
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
        raise AssertionError('The dashboard registry must not open network connections')

    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def platform(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


def test_create_read_list_and_restart_persist_the_record(platform, tmp_path, monkeypatch):
    app, client = platform
    response = client.post('/api/dashboards', json=spec())
    assert response.status_code == 201, response.text
    record = response.json()
    assert re.fullmatch('[0-9a-f]{32}', record['id'])
    assert record == {**spec(), 'id': record['id'], 'revision': 1,
                      'created_at': record['created_at'], 'created_by': 'admin',
                      'origin': 'http://grafana.lab:3000', 'probe_path': '/api/health',
                      'embeddable': False}
    assert client.get('/api/dashboards/' + record['id']).json() == record
    assert client.get('/api/dashboards').json() == {'dashboards': [record], 'embed_origins': [],
                                                    'embed_error': None}
    stored = json.loads(app.state.store.one('SELECT body FROM dashboards WHERE id=?',
                                            (record['id'],))['body'])
    assert stored == {**spec(), 'id': record['id'], 'revision': 1,
                      'created_at': record['created_at'], 'created_by': 'admin'}
    restarted = make_app(tmp_path, monkeypatch)
    with TestClient(restarted) as other:
        login(other, restarted)
        assert other.get('/api/dashboards/' + record['id']).json() == record
        assert other.get('/api/dashboards').json()['dashboards'] == [record]


@pytest.mark.parametrize('kind, probe', [
    ('grafana', '/api/health'), ('uptime-kuma', '/'), ('prometheus', '/-/healthy'),
    ('netdata', '/api/v1/info'), ('custom', '/')])
def test_each_kind_has_a_default_probe_path(platform, kind, probe):
    app, client = platform
    payload = {'label': kind, 'kind': kind, 'url': f'http://{kind}.lab/', 'embed': False, 'enabled': True}
    response = client.post('/api/dashboards', json=payload)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record['health_path'] is None
    assert record['probe_path'] == probe
    explicit = client.post('/api/dashboards', json={**payload, 'label': kind + ' custom',
                                                    'health_path': '/healthz'})
    assert explicit.status_code == 201
    assert explicit.json()['health_path'] == '/healthz'
    assert explicit.json()['probe_path'] == '/healthz'


@pytest.mark.parametrize('changes', [
    {'label': ''},
    {'label': ' Lab'},
    {'label': 'Lab\nGrafana'},
    {'label': 'x' * 81},
    {'kind': 'zabbix'},
    {'kind': 'Grafana'},
    {'url': 'ftp://grafana.lab/d/lab'},
    {'url': 'javascript:alert(1)'},
    {'url': 'grafana.lab/d/lab'},
    {'url': 'http://user:pass@grafana.lab/'},
    {'url': 'http://grafana.lab/#panel'},
    {'url': 'http://grafana.lab:0/'},
    {'url': 'http://grafana.lab:99999/'},
    {'url': 'http://graf ana.lab/'},
    {'url': 'http://grafana.lab/' + 'x' * 2048},
    {'health_path': 'api/health'},
    {'health_path': '//grafana.lab/health'},
    {'health_path': '/api/../admin'},
    {'health_path': '/api/health?full=1'},
    {'health_path': '/api/health#x'},
    {'health_path': '/api/health\n'},
    {'embed': 'yes'},
    {'enabled': 1},
    {'unknown': True},
])
def test_invalid_specifications_are_refused_without_a_record(platform, changes):
    app, client = platform
    response = client.post('/api/dashboards', json=spec(**changes))
    assert response.status_code == 422, changes
    assert client.get('/api/dashboards').json()['dashboards'] == []
    assert app.state.store.one('SELECT count(*) AS total FROM dashboards')['total'] == 0


def test_a_body_that_is_not_an_object_is_refused(platform):
    app, client = platform
    assert client.post('/api/dashboards', json=['Lab Grafana']).status_code == 422
    assert client.post('/api/dashboards', json='Lab Grafana').status_code == 422
    assert client.post('/api/dashboards', content=b'{', headers={'content-type': 'application/json'}).status_code == 422


def test_embedding_is_refused_without_an_allowlist(platform):
    app, client = platform
    response = client.post('/api/dashboards', json=spec(embed=True))
    assert response.status_code == 422
    assert response.json()['detail'] == 'Embedding requires an allowlisted origin'
    assert client.get('/api/dashboards').json()['dashboards'] == []
    assert client.post('/api/dashboards', json=spec(label='Linked only')).status_code == 201


def test_embedding_accepts_an_allowlisted_origin(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, origins='https://grafana.lab')
    with TestClient(app) as client:
        login(client, app)
        response = client.post('/api/dashboards', json=spec(url='https://grafana.lab/d/abc', embed=True))
        assert response.status_code == 201, response.text
        record = response.json()
        assert record['origin'] == 'https://grafana.lab'
        assert record['embeddable'] is True
        assert client.get('/api/dashboards').json() == {
            'dashboards': [record], 'embed_origins': ['https://grafana.lab'], 'embed_error': None}
        for url in ('http://grafana.lab/d/abc', 'https://grafana.lab:8443/d/abc',
                    'https://other.lab/d/abc'):
            refused = client.post('/api/dashboards', json=spec(url=url, embed=True))
            assert refused.status_code == 422, url
        assert len(client.get('/api/dashboards').json()['dashboards']) == 1
        linked = client.post('/api/dashboards', json=spec(label='Not embedded',
                                                          url='https://other.lab/d/x'))
        assert linked.status_code == 201
        assert linked.json()['embeddable'] is False


def test_allowlist_normalizes_default_ports_and_trailing_slashes(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch,
                   origins='https://grafana.lab:443/, http://prometheus.lab:9090')
    with TestClient(app) as client:
        login(client, app)
        collection = client.get('/api/dashboards').json()
        assert collection['embed_origins'] == ['https://grafana.lab', 'http://prometheus.lab:9090']
        response = client.post('/api/dashboards', json=spec(url='https://grafana.lab:443/d/x', embed=True))
        assert response.status_code == 201
        assert response.json()['embeddable'] is True
        assert client.post('/api/dashboards', json=spec(
            url='http://prometheus.lab:9091/', embed=True)).status_code == 422


@pytest.mark.parametrize('origins', [
    'grafana.lab', 'https://grafana.lab/dash', '*', 'ftp://grafana.lab',
    'https://a.lab,https://a.lab', 'https://a.lab,,https://b.lab'])
def test_an_unusable_allowlist_is_reported_and_disables_embedding(tmp_path, monkeypatch, origins):
    app = make_app(tmp_path, monkeypatch, origins=origins)
    with TestClient(app) as client:
        login(client, app)
        collection = client.get('/api/dashboards').json()
        assert collection['embed_origins'] == []
        assert 'KUBERWEAVER_DASHBOARD_EMBED_ORIGINS' in collection['embed_error']
        assert client.post('/api/dashboards', json=spec(embed=True)).status_code == 422


def test_update_is_partial_and_revisioned(platform):
    app, client = platform
    created = client.post('/api/dashboards', json=spec()).json()
    identity = created['id']
    response = client.patch('/api/dashboards/' + identity, json={'revision': 1, 'label': 'Renamed'})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated['label'] == 'Renamed'
    assert updated['url'] == created['url']
    assert updated['revision'] == 2
    assert updated['created_at'] == created['created_at']
    assert updated['created_by'] == 'admin'
    assert client.patch('/api/dashboards/' + identity,
                        json={'revision': 1, 'label': 'Again'}).status_code == 409
    assert client.get('/api/dashboards/' + identity).json() == updated
    assert client.patch('/api/dashboards/' + identity, json={'label': 'No revision'}).status_code == 422
    assert client.patch('/api/dashboards/' + identity,
                        json={'revision': 0, 'label': 'Zero'}).status_code == 422
    assert client.patch('/api/dashboards/' + identity,
                        json={'revision': 1.5, 'label': 'Fraction'}).status_code == 422
    assert client.patch('/api/dashboards/not-an-identity',
                        json={'revision': 1, 'label': 'Ghost'}).status_code == 404
    assert client.patch('/api/dashboards/' + '0' * 32,
                        json={'revision': 1, 'label': 'Ghost'}).status_code == 404
    assert client.patch('/api/dashboards/' + identity,
                        json={'revision': 2, 'health_path': '/custom/health'}).json()['probe_path'] == '/custom/health'
    cleared = client.patch('/api/dashboards/' + identity, json={'revision': 3, 'health_path': None}).json()
    assert cleared['health_path'] is None and cleared['probe_path'] == '/api/health'
    changed = client.patch('/api/dashboards/' + identity, json={'revision': 4, 'kind': 'prometheus'}).json()
    assert changed['kind'] == 'prometheus' and changed['probe_path'] == '/-/healthy'
    refused = client.patch('/api/dashboards/' + identity, json={'revision': 5, 'embed': True})
    assert refused.status_code == 422
    assert client.get('/api/dashboards/' + identity).json()['revision'] == 5


def test_update_cannot_move_an_embedded_dashboard_off_the_allowlist(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, origins='https://grafana.lab')
    with TestClient(app) as client:
        login(client, app)
        created = client.post('/api/dashboards', json=spec(url='https://grafana.lab/d/a', embed=True)).json()
        identity = created['id']
        assert created['embeddable'] is True
        refused = client.patch('/api/dashboards/' + identity,
                               json={'revision': 1, 'url': 'https://other.lab/d/a'})
        assert refused.status_code == 422
        current = client.get('/api/dashboards/' + identity).json()
        assert current['url'] == 'https://grafana.lab/d/a' and current['revision'] == 1
        detached = client.patch('/api/dashboards/' + identity,
                                json={'revision': 1, 'url': 'https://other.lab/d/a', 'embed': False})
        assert detached.status_code == 200
        assert detached.json()['embeddable'] is False
        assert detached.json()['origin'] == 'https://other.lab'


def test_delete_requires_the_current_revision(platform):
    app, client = platform
    created = client.post('/api/dashboards', json=spec()).json()
    identity = created['id']
    assert client.delete('/api/dashboards/' + identity + '?revision=2').status_code == 409
    assert client.get('/api/dashboards/' + identity).status_code == 200
    response = client.delete('/api/dashboards/' + identity + '?revision=1')
    assert response.status_code == 200
    assert response.json() == {'deleted': True, 'id': identity}
    assert client.get('/api/dashboards/' + identity).status_code == 404
    assert client.get('/api/dashboards').json()['dashboards'] == []
    assert client.delete('/api/dashboards/' + identity + '?revision=1').status_code == 404
    assert client.delete('/api/dashboards/' + 'a' * 32).status_code == 422
    assert client.delete('/api/dashboards/' + 'a' * 32 + '?revision=abc').status_code == 422


def test_every_change_is_audited(platform):
    app, client = platform
    identity = client.post('/api/dashboards', json=spec()).json()['id']
    client.patch('/api/dashboards/' + identity, json={'revision': 1, 'enabled': False})
    client.delete('/api/dashboards/' + identity + '?revision=2')
    rows = client.get('/api/audit').json()
    by_action = {}
    for row in rows:
        by_action.setdefault(row['action'], []).append(row)
    for action in ('dashboard.created', 'dashboard.updated', 'dashboard.deleted'):
        assert by_action[action][0]['resource'] == identity
        assert by_action[action][0]['actor'] == 'admin'
    assert by_action['dashboard.created'][0]['detail'] == {
        'label': 'Lab Grafana', 'kind': 'grafana', 'embed': False}
    assert by_action['dashboard.deleted'][0]['detail'] == {'label': 'Lab Grafana', 'kind': 'grafana'}


def test_roles_and_csrf(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as anonymous:
        assert anonymous.get('/api/dashboards').status_code == 401
    for role in ('viewer', 'operator'):
        with TestClient(app) as client:
            login(client, app, role)
            assert client.get('/api/dashboards').status_code == 200
            assert client.post('/api/dashboards', json=spec()).status_code == 403
    with TestClient(app) as client:
        login(client, app)
        identity = client.post('/api/dashboards', json=spec()).json()['id']
        del client.headers['x-csrf-token']
        assert client.post('/api/dashboards', json=spec(label='No CSRF')).status_code == 403
        assert client.patch('/api/dashboards/' + identity,
                            json={'revision': 1, 'label': 'No CSRF'}).status_code == 403
        assert client.delete('/api/dashboards/' + identity + '?revision=1').status_code == 403
        assert client.get('/api/dashboards/' + identity).json()['label'] == 'Lab Grafana'


def test_agent_tokens_use_the_integration_scopes(platform):
    app, client = platform
    issued = client.post('/api/agent-tokens', json={
        'label': 'dash-reader', 'role': 'viewer', 'scopes': ['read:integrations'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    reader = {'authorization': 'Bearer ' + issued.json()['token']}
    # A presented session cookie would make the Bearer request ambiguous, so the
    # machine calls use the same client with the browser credentials removed.
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.get('/api/dashboards', headers=reader).status_code == 200
    assert client.post('/api/dashboards', json=spec(), headers=reader).status_code == 403
    login(client, app)
    issued = client.post('/api/agent-tokens', json={
        'label': 'dash-writer', 'role': 'admin', 'scopes': ['read:integrations', 'configure'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    writer = {'authorization': 'Bearer ' + issued.json()['token']}
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    created = client.post('/api/dashboards', json=spec(label='Agent dashboard'), headers=writer)
    assert created.status_code == 201, created.text
    identity = created.json()['id']
    assert created.json()['created_by'].startswith('agent:')
    patched = client.patch('/api/dashboards/' + identity,
                           json={'revision': 1, 'enabled': False}, headers=writer)
    assert patched.status_code == 200
    assert client.delete('/api/dashboards/' + identity + '?revision=2',
                         headers=writer).status_code == 200
    login(client, app)
    rows = client.get('/api/audit').json()
    created_rows = [row for row in rows if row['action'] == 'dashboard.created']
    assert created_rows[0]['actor'].startswith('agent:')


def test_deep_links_keep_the_probe_on_the_same_origin(platform):
    app, client = platform
    response = client.post('/api/dashboards', json=spec(
        url='HTTP://GRAFANA.LAB:3000/d/abc?orgId=1&kiosk', health_path='/status'))
    assert response.status_code == 201, response.text
    record = response.json()
    assert record['url'] == 'HTTP://GRAFANA.LAB:3000/d/abc?orgId=1&kiosk'
    assert record['origin'] == 'http://grafana.lab:3000'
    assert record['probe_path'] == '/status'


def test_postgresql_registers_the_dashboard_table():
    from kuberweaver.postgres import COLUMNS, DDL, translate_sql
    assert COLUMNS['dashboards'] == ('id', 'body')
    statement = translate_sql('CREATE TABLE IF NOT EXISTS dashboards(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    assert statement == f'CREATE TABLE IF NOT EXISTS dashboards ({DDL["dashboards"]})'
    assert '_kw_rowid BIGSERIAL UNIQUE NOT NULL' in DDL['dashboards']
    assert translate_sql('SELECT body FROM dashboards ORDER BY rowid DESC').endswith('ORDER BY _kw_rowid DESC')
