"""Cloudflare DNS capability: record CRUD through vault-scoped tokens, behind the
same durable approval/audit model as deployments (request -> approve -> execute).

All Cloudflare traffic goes to an in-process httpx.MockTransport fake; all token
values are disposable fixtures shaped like Cloudflare credentials. No test
contacts Cloudflare and no real token exists here.
"""
import json
import logging
import re
import sys
import traceback
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from kuberweaver.secrets import SecretError  # noqa: E402

# Disposable fixtures (same shapes as test_cloudflare_secrets).
_BODY = 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0'
SCANNABLE_USER = 'cfut_' + _BODY + 'a1b2c3d4'
SCANNABLE_ACCOUNT = 'cfat_' + _BODY + '0F1E2D3C'
ZONE_ID = 'd41d8cd98f00b204e9800998ecf8427e'
RECORD_ID = '9e107d9d372bb6826bd81d3542a419d6'
ACCOUNT_ID = '0123456789abcdef0123456789abcdef'
A_RECORD = {'type': 'A', 'name': 'www.lab.example', 'content': '192.0.2.10',
            'ttl': 300, 'proxied': False}


def spec(**changes):
    return {'label': 'Cloudflare DNS', 'purpose': 'dns',
            'secret_ref': 'cloudflare/dns-lab', 'secrets_provider': 'local-vault',
            'account_id': None, 'enabled': True, **changes}


def change_spec(**changes):
    return {'connector_id': 'c' * 32, 'zone_id': ZONE_ID, 'operation': 'create',
            'record': dict(A_RECORD), **changes}


class FakeCloudflare:
    """In-memory Cloudflare API fake. Asserts the fixed origin and the exact
    bearer token on every call; records the call log for assertions."""

    def __init__(self, token=SCANNABLE_USER, *, page_size=100):
        self.token = token
        self.page_size = page_size
        self.zones = {ZONE_ID: {'id': ZONE_ID, 'name': 'lab.example',
                                'status': 'active', 'paused': False}}
        self.records = {ZONE_ID: {}}
        self.calls = []
        self.auth_headers = []
        self.verify_status = 'active'
        self.fail = None            # {'status': int, 'errors': [...]} for data calls
        self.verify_fail = None     # same, for the token verify call
        self.echo_token_in_error = False
        self._next_id = 1

    def _ok(self, result, result_info=None):
        body = {'success': True, 'errors': [], 'messages': [], 'result': result}
        if result_info is not None:
            body['result_info'] = result_info
        return httpx.Response(200, json=body)

    def _error(self, failure):
        errors = failure['errors']
        if self.echo_token_in_error:
            errors = [{**error, 'message': error.get('message', '') + ' ' + self.token}
                      for error in errors]
        return httpx.Response(failure['status'],
                              json={'success': False, 'errors': errors,
                                    'messages': [], 'result': None})

    def handler(self, request):
        assert request.url.host == 'api.cloudflare.com', 'origin must stay fixed'
        assert request.url.scheme == 'https'
        self.auth_headers.append(request.headers.get('Authorization'))
        assert request.headers.get('Authorization') == 'Bearer ' + self.token
        assert self.token not in str(request.url), 'token must never ride in a URL'
        path = request.url.path
        self.calls.append((request.method, path))
        assert path.startswith('/client/v4/')
        route = path[len('/client/v4'):]
        if route in ('/user/tokens/verify', f'/accounts/{ACCOUNT_ID}/tokens/verify'):
            if self.verify_fail:
                return self._error(self.verify_fail)
            return self._ok({'id': 'token-id', 'status': self.verify_status})
        if self.fail:
            return self._error(self.fail)
        if route == '/zones':
            items = list(self.zones.values())
            return self._ok(items, {'page': 1, 'per_page': len(items) or 1,
                                    'count': len(items), 'total_count': len(items),
                                    'total_pages': 1})
        match = re.fullmatch(r'/zones/([0-9a-f]{32})/dns_records', route)
        if match:
            zone_id = match[1]
            if zone_id not in self.zones:
                return self._error({'status': 404, 'errors': [{'code': 7003, 'message': 'Could not route to zone'}]})
            if request.method == 'GET':
                items = list(self.records[zone_id].values())
                qtype = request.url.params.get('type')
                qname = request.url.params.get('name')
                if qtype:
                    items = [item for item in items if item['type'] == qtype]
                if qname:
                    items = [item for item in items if item['name'] == qname]
                page = int(request.url.params.get('page', '1'))
                per_page = min(int(request.url.params.get('per_page', '100')), self.page_size)
                chunk = items[(page - 1) * per_page:page * per_page]
                total_pages = max(1, -(-len(items) // per_page))
                return self._ok(chunk, {'page': page, 'per_page': per_page,
                                        'count': len(chunk), 'total_count': len(items),
                                        'total_pages': total_pages})
            if request.method == 'POST':
                body = json.loads(request.content)
                record = {'id': f'{self._next_id:032x}', 'zone_id': zone_id,
                          'zone_name': self.zones[zone_id]['name'],
                          'created_on': '2026-11-20T00:00:00Z',
                          'modified_on': '2026-11-20T00:00:00Z',
                          'proxied': False, 'ttl': 1, **body}
                self._next_id += 1
                self.records[zone_id][record['id']] = record
                return self._ok(record)
        match = re.fullmatch(r'/zones/([0-9a-f]{32})/dns_records/([0-9a-f]{32})', route)
        if match:
            zone_id, record_id = match.groups()
            existing = self.records.get(zone_id, {}).get(record_id)
            if existing is None:
                return self._error({'status': 404, 'errors': [{'code': 81044, 'message': 'Record does not exist'}]})
            if request.method == 'PATCH':
                existing.update(json.loads(request.content))
                existing['modified_on'] = '2026-11-20T01:00:00Z'
                return self._ok(existing)
            if request.method == 'DELETE':
                del self.records[zone_id][record_id]
                return self._ok({'id': record_id})
        raise AssertionError(f'unexpected Cloudflare call: {request.method} {route}')


def make_app(path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(path / 'module-app'))
    from kuberweaver.app import create_app
    app = create_app(state_dir=path / 'state', testing=True)
    from kuberweaver.cloudflare import install_cloudflare_api
    if not hasattr(app.state, 'cloudflare_connectors'):
        install_cloudflare_api(app)
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
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def loopback_only(address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ('127.0.0.1', '::1'):
            raise AssertionError('Cloudflare DNS tests must not open non-loopback connections')

    def connect(self, address):
        loopback_only(address)
        return real_connect(self, address)

    def create_connection(address, *args, **kwargs):
        loopback_only(address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(socket, 'create_connection', create_connection)


@pytest.fixture
def local_vault(tmp_path):
    from kuberweaver.local_vault import LocalVaultProvider, initialize
    directory = tmp_path / 'vault'
    initialize(directory)
    return LocalVaultProvider(directory, allow_write=True)


@pytest.fixture
def platform(tmp_path, monkeypatch, local_vault):
    app = make_app(tmp_path, monkeypatch)
    app.state.registry.register('secrets', 'local-vault', local_vault,
                                {'label': 'Test local vault', 'reference_only': True})
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        yield app, client


@pytest.fixture
def connector(platform):
    """A dns-purpose connector with a fixture token stored in the real vault."""
    app, client = platform
    record = client.post('/api/cloudflare/connectors', json=spec()).json()
    response = client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
                          json={'api_token': SCANNABLE_USER})
    assert response.status_code == 200, response.text
    return record


@pytest.fixture
def wired(platform, connector):
    """platform + connector + mock transport bound to the DNS service."""
    app, client = platform
    fake = FakeCloudflare()
    app.state.cloudflare_dns.transport = httpx.MockTransport(fake.handler)
    return app, client, connector, fake


def request_body(connector, **changes):
    return change_spec(**{'connector_id': connector['id'], **changes})


def submit(client, connector, **changes):
    response = client.post('/api/cloudflare/dns/requests', json=request_body(connector, **changes))
    assert response.status_code == 201, response.text
    return response.json()


def persisted_dump(app):
    return json.dumps(app.state.store.all('SELECT * FROM cloudflare_dns_requests')
                      + app.state.store.all('SELECT * FROM cloudflare_connectors')
                      + app.state.store.all('SELECT * FROM audit'))


# ---------------------------------------------------------------------------
# Read path: zones and records, sanitized, audited, no approval needed.
# ---------------------------------------------------------------------------

def test_zones_list_is_sanitized_and_audited(wired):
    app, client, connector, fake = wired
    response = client.get('/api/cloudflare/dns/zones', params={'connector_id': connector['id']})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {'items': [{'id': ZONE_ID, 'name': 'lab.example', 'status': 'active'}],
                    'truncated': False}
    assert fake.calls == [('GET', '/client/v4/zones')]
    audit = app.state.store.one("SELECT * FROM audit WHERE action='cloudflare_dns.zones_listed'")
    assert audit and audit['resource'] == connector['id']
    assert SCANNABLE_USER not in persisted_dump(app)
    assert SCANNABLE_USER not in response.text


def test_records_list_paginates_and_filters(wired):
    app, client, connector, fake = wired
    for index in range(5):
        fake.records[ZONE_ID][f'{index:032x}'] = {
            'id': f'{index:032x}', 'zone_id': ZONE_ID, 'type': 'A' if index < 4 else 'TXT',
            'name': f'host{index}.lab.example', 'content': f'192.0.2.{index}',
            'ttl': 300, 'proxied': False, 'created_on': '2026-11-20T00:00:00Z',
            'modified_on': '2026-11-20T00:00:00Z', 'comment': 'private-ish note',
            'tags': ['internal'], 'meta': {'secret-looking': 'value'}}
    fake.page_size = 2
    response = client.get('/api/cloudflare/dns/records',
                          params={'connector_id': connector['id'], 'zone_id': ZONE_ID})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body['items']) == 5 and body['truncated'] is False
    assert [item['name'] for item in body['items']] == [f'host{i}.lab.example' for i in range(5)]
    for item in body['items']:
        assert set(item) <= {'id', 'zone_id', 'zone_name', 'type', 'name', 'content',
                             'ttl', 'proxied', 'priority', 'created_on', 'modified_on'}
    assert sum(1 for method, path in fake.calls if path.endswith('/dns_records')) == 3
    filtered = client.get('/api/cloudflare/dns/records',
                          params={'connector_id': connector['id'], 'zone_id': ZONE_ID,
                                  'type': 'TXT', 'name': 'host4.lab.example'})
    assert filtered.status_code == 200
    assert [item['type'] for item in filtered.json()['items']] == ['TXT']
    audit = app.state.store.one("SELECT * FROM audit WHERE action='cloudflare_dns.records_listed'")
    assert audit and json.loads(audit['detail'])['zone_id'] == ZONE_ID
    assert SCANNABLE_USER not in persisted_dump(app)


def test_reads_require_a_valid_connector_and_credentials(platform, connector):
    app, client = platform
    fake = FakeCloudflare()
    app.state.cloudflare_dns.transport = httpx.MockTransport(fake.handler)
    assert client.get('/api/cloudflare/dns/zones',
                      params={'connector_id': 'f' * 32}).status_code == 404
    assert client.get('/api/cloudflare/dns/zones',
                      params={'connector_id': 'not-an-id'}).status_code == 422
    assert client.get('/api/cloudflare/dns/records',
                      params={'connector_id': connector['id'], 'zone_id': 'zz'}).status_code == 422
    # A connector without stored credentials fails closed before any call.
    bare = client.post('/api/cloudflare/connectors',
                       json=spec(label='Bare', secret_ref='cloudflare/bare')).json()
    response = client.get('/api/cloudflare/dns/zones', params={'connector_id': bare['id']})
    assert response.status_code == 503
    assert fake.calls == []


def test_read_api_errors_are_sanitized(wired):
    app, client, connector, fake = wired
    fake.fail = {'status': 403, 'errors': [{'code': 9109, 'message': 'Unauthorized to access requested resource'}]}
    fake.echo_token_in_error = True
    response = client.get('/api/cloudflare/dns/zones', params={'connector_id': connector['id']})
    assert response.status_code == 502
    assert SCANNABLE_USER not in response.text
    assert SCANNABLE_USER not in persisted_dump(app)
    assert '9109' in response.text


# ---------------------------------------------------------------------------
# Mutation lifecycle: request -> pending_approval -> approve -> executed.
# ---------------------------------------------------------------------------

def test_create_request_lands_pending_without_any_cloudflare_call(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)
    assert re.fullmatch('[0-9a-f]{32}', body['id'])
    assert body['status'] == 'pending_approval'
    assert body['operation'] == 'create' and body['zone_id'] == ZONE_ID
    assert body['record'] == {**A_RECORD, 'priority': None}
    assert body['record_id'] is None
    assert body['created_by'] == 'admin' and body['approved_by'] is None
    assert re.fullmatch('[0-9a-f]{64}', body['connector_fingerprint'])
    assert re.fullmatch('[0-9a-f]{64}', body['payload_digest'])
    assert body['connector_fingerprint'] == app.state.cloudflare_connectors.fingerprint(connector['id'])
    assert fake.calls == [], 'a request must not touch Cloudflare before approval'
    audit = app.state.store.one("SELECT * FROM audit WHERE action='cloudflare_dns.requested'")
    assert audit and audit['resource'] == body['id']
    assert client.get('/api/cloudflare/dns/requests').json() == [body]
    assert client.get('/api/cloudflare/dns/requests/' + body['id']).json() == body


def test_approve_executes_create_with_the_vault_token(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 200, response.text
    done = response.json()
    assert done['status'] == 'succeeded'
    assert done['approved_by'] == 'admin' and done['approved_at'] and done['executed_at']
    assert done['result']['token_verified'] is True
    record = done['result']['record']
    assert record['type'] == 'A' and record['name'] == 'www.lab.example'
    assert record['content'] == '192.0.2.10' and record['ttl'] == 300
    assert record['zone_id'] == ZONE_ID and re.fullmatch('[0-9a-f]{32}', record['id'])
    assert fake.calls == [('GET', '/client/v4/user/tokens/verify'),
                          ('POST', f'/client/v4/zones/{ZONE_ID}/dns_records')]
    assert fake.records[ZONE_ID][record['id']]['content'] == '192.0.2.10'
    actions = [row['action'] for row in app.state.store.all(
        "SELECT action FROM audit WHERE action LIKE 'cloudflare_dns.%' ORDER BY id")]
    assert actions == ['cloudflare_dns.requested', 'cloudflare_dns.approved', 'cloudflare_dns.succeeded']
    assert SCANNABLE_USER not in persisted_dump(app)
    assert SCANNABLE_USER not in response.text
    assert client.get('/api/cloudflare/dns/requests/' + body['id']).json() == done


def test_approve_executes_update_as_patch(wired):
    app, client, connector, fake = wired
    fake.records[ZONE_ID][RECORD_ID] = {'id': RECORD_ID, 'zone_id': ZONE_ID, 'type': 'A',
                                        'name': 'www.lab.example', 'content': '192.0.2.10',
                                        'ttl': 300, 'proxied': False}
    body = submit(client, connector, operation='update', record_id=RECORD_ID,
                  record={'content': '192.0.2.20', 'ttl': 600})
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 200, response.text
    done = response.json()
    assert done['status'] == 'succeeded'
    assert done['result']['record']['content'] == '192.0.2.20'
    assert done['result']['record']['ttl'] == 600
    assert done['result']['record']['name'] == 'www.lab.example'
    assert fake.calls[-1] == ('PATCH', f'/client/v4/zones/{ZONE_ID}/dns_records/{RECORD_ID}')


def test_approve_executes_delete(wired):
    app, client, connector, fake = wired
    fake.records[ZONE_ID][RECORD_ID] = {'id': RECORD_ID, 'zone_id': ZONE_ID, 'type': 'A',
                                        'name': 'www.lab.example', 'content': '192.0.2.10',
                                        'ttl': 300, 'proxied': False}
    body = submit(client, connector, operation='delete', record_id=RECORD_ID, record=None)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 200, response.text
    done = response.json()
    assert done['status'] == 'succeeded'
    assert done['result']['record'] == {'id': RECORD_ID}
    assert fake.calls[-1] == ('DELETE', f'/client/v4/zones/{ZONE_ID}/dns_records/{RECORD_ID}')
    assert fake.records[ZONE_ID] == {}


def test_reject_never_calls_cloudflare(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/reject')
    assert response.status_code == 200
    assert response.json()['status'] == 'rejected'
    assert fake.calls == []
    assert client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve').status_code == 409


def test_terminal_requests_cannot_be_reapproved(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)
    assert client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve').status_code == 200
    again = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert again.status_code == 409
    assert client.post('/api/cloudflare/dns/requests/' + body['id'] + '/reject').status_code == 409
    assert sum(1 for method, _ in fake.calls if method == 'POST') == 1


def test_unknown_request_ids_are_404(wired):
    app, client, connector, fake = wired
    assert client.get('/api/cloudflare/dns/requests/' + 'a' * 32).status_code == 404
    assert client.post('/api/cloudflare/dns/requests/' + 'a' * 32 + '/approve').status_code == 404
    assert client.post('/api/cloudflare/dns/requests/not-an-id/reject').status_code == 404


# ---------------------------------------------------------------------------
# Pins and fail-closed gates.
# ---------------------------------------------------------------------------

def test_connector_drift_between_request_and_approval_is_refused(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)
    client.patch('/api/cloudflare/connectors/' + connector['id'],
                 json={'revision': 2, 'label': 'Renamed'})
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 409
    assert 'changed' in response.json()['detail']
    assert fake.calls == []
    assert client.get('/api/cloudflare/dns/requests/' + body['id']).json()['status'] == 'pending_approval'


def test_credential_rotation_invalidates_pending_requests(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)
    client.put('/api/cloudflare/connectors/' + connector['id'] + '/credentials',
               json={'api_token': SCANNABLE_ACCOUNT})
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 409
    assert fake.calls == []


def test_disabled_connector_is_refused(wired):
    app, client, connector, fake = wired
    client.patch('/api/cloudflare/connectors/' + connector['id'],
                 json={'revision': 2, 'enabled': False})
    response = client.post('/api/cloudflare/dns/requests', json=request_body(connector))
    assert response.status_code == 409
    assert fake.calls == []


def test_missing_credentials_leave_the_request_pending(platform, connector):
    app, client = platform
    fake = FakeCloudflare()
    app.state.cloudflare_dns.transport = httpx.MockTransport(fake.handler)
    bare = client.post('/api/cloudflare/connectors',
                       json=spec(label='Bare', secret_ref='cloudflare/bare')).json()
    body = submit(client, bare)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 503
    assert fake.calls == []
    assert client.get('/api/cloudflare/dns/requests/' + body['id']).json()['status'] == 'pending_approval'
    # Storing the credential bumps the connector revision, so the pinned
    # request is refused fail-closed; a fresh request executes cleanly.
    client.put('/api/cloudflare/connectors/' + bare['id'] + '/credentials',
               json={'api_token': SCANNABLE_USER})
    assert client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve').status_code == 409
    fresh = submit(client, bare)
    response = client.post('/api/cloudflare/dns/requests/' + fresh['id'] + '/approve')
    assert response.status_code == 200
    assert response.json()['status'] == 'succeeded'


def test_account_connector_verifies_through_the_account_endpoint(platform):
    app, client = platform
    fake = FakeCloudflare(SCANNABLE_ACCOUNT)
    app.state.cloudflare_dns.transport = httpx.MockTransport(fake.handler)
    record = client.post('/api/cloudflare/connectors',
                         json=spec(account_id=ACCOUNT_ID, secret_ref='cloudflare/dns-account')).json()
    client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
               json={'api_token': SCANNABLE_ACCOUNT})
    body = submit(client, record)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'succeeded'
    assert fake.calls[0] == ('GET', f'/client/v4/accounts/{ACCOUNT_ID}/tokens/verify')


def test_inactive_or_unverifiable_token_fails_closed(wired):
    app, client, connector, fake = wired
    fake.verify_status = 'disabled'
    body = submit(client, connector)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 200
    done = response.json()
    assert done['status'] == 'failed'
    assert fake.calls == [('GET', '/client/v4/user/tokens/verify')]
    assert SCANNABLE_USER not in persisted_dump(app)
    fake.verify_status = 'active'
    fake.verify_fail = {'status': 401, 'errors': [{'code': 9109, 'message': 'Invalid token'}]}
    body = submit(client, connector)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.json()['status'] == 'failed'
    assert fake.calls[-1] == ('GET', '/client/v4/user/tokens/verify')


# ---------------------------------------------------------------------------
# Failure evidence is bounded, sanitized, and never carries the token.
# ---------------------------------------------------------------------------

def test_cloudflare_error_is_recorded_without_the_token(wired):
    app, client, connector, fake = wired
    fake.fail = {'status': 400, 'errors': [{'code': 81044, 'message': 'Record already exists'}]}
    fake.echo_token_in_error = True
    body = submit(client, connector)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 200
    done = response.json()
    assert done['status'] == 'failed'
    assert done['error'] == 'Cloudflare API request failed'
    assert done['result']['errors'][0]['code'] == 81044
    assert SCANNABLE_USER not in json.dumps(done)
    assert SCANNABLE_USER not in persisted_dump(app)
    audit = app.state.store.one("SELECT * FROM audit WHERE action='cloudflare_dns.failed'")
    assert audit and SCANNABLE_USER not in json.dumps(dict(audit))


def test_transport_failure_is_sanitized(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)

    def broken(request):
        raise httpx.ConnectError('connection refused with ' + SCANNABLE_USER)

    app.state.cloudflare_dns.transport = httpx.MockTransport(broken)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.status_code == 200
    done = response.json()
    assert done['status'] == 'failed'
    assert done['error'] == 'Cloudflare API unreachable'
    assert SCANNABLE_USER not in json.dumps(done)
    assert SCANNABLE_USER not in persisted_dump(app)


def test_malformed_or_oversized_responses_fail_closed(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)

    def garbage(request):
        if request.url.path.endswith('/tokens/verify'):
            return httpx.Response(200, text='<html>not json</html>')
        raise AssertionError('must stop at the verify gate')

    app.state.cloudflare_dns.transport = httpx.MockTransport(garbage)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.json()['status'] == 'failed'
    assert SCANNABLE_USER not in persisted_dump(app)

    def huge(request):
        return httpx.Response(200, content=b'{"success": true, "result": []}' + b' ' * (2 * 1024 * 1024))

    app.state.cloudflare_dns.transport = httpx.MockTransport(huge)
    response = client.get('/api/cloudflare/dns/zones', params={'connector_id': connector['id']})
    assert response.status_code == 502


def test_no_token_in_logs_during_execution(wired, caplog):
    app, client, connector, fake = wired
    body = submit(client, connector)
    with caplog.at_level(logging.DEBUG):
        response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    assert response.json()['status'] == 'succeeded'
    rendered = caplog.text + ''.join(
        str(record.__dict__) for record in caplog.records)
    assert SCANNABLE_USER not in rendered
    assert 'Authorization' not in rendered


# ---------------------------------------------------------------------------
# Input validation: closed schemas, no secret fields, no echo.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('changes', [
    {'operation': 'upsert'},
    {'operation': 'create', 'record': None},
    {'operation': 'create', 'record_id': RECORD_ID},
    {'operation': 'update', 'record_id': RECORD_ID, 'record': None},
    {'operation': 'update', 'record_id': RECORD_ID, 'record': {}},
    {'operation': 'delete', 'record': dict(A_RECORD)},
    {'operation': 'delete'},
    {'zone_id': 'zz'},
    {'connector_id': 'not-an-id'},
    {'record': {**A_RECORD, 'type': 'ANY'}},
    {'record': {**A_RECORD, 'ttl': 30}},
    {'record': {**A_RECORD, 'ttl': 90000}},
    {'record': {**A_RECORD, 'proxied': True, 'type': 'TXT'}},
    {'record': {'type': 'MX', 'name': 'lab.example', 'content': 'mail.lab.example'}},
    {'record': {**A_RECORD, 'name': 'bad name'}},
    {'record': {**A_RECORD, 'content': 'line\nbreak'}},
    {'record': {**A_RECORD, 'api_token': SCANNABLE_USER}},
    {'record': {**A_RECORD, 'comment': 'x'}},
    {'api_token': SCANNABLE_USER},
    {'endpoint': 'https://evil.example'},
])
def test_invalid_change_requests_are_refused_without_echo(wired, changes):
    app, client, connector, fake = wired
    payload = request_body(connector, **changes)
    response = client.post('/api/cloudflare/dns/requests', json=payload)
    assert response.status_code == 422, response.text
    assert SCANNABLE_USER not in response.text
    assert app.state.cloudflare_dns.list_requests() == []
    assert fake.calls == []


def test_valid_record_shapes_are_accepted(wired):
    app, client, connector, fake = wired
    for record in (
        {'type': 'A', 'name': 'lab.example', 'content': '192.0.2.1'},
        {'type': 'AAAA', 'name': 'ipv6.lab.example', 'content': '2001:db8::1', 'proxied': True},
        {'type': 'CNAME', 'name': '*.lab.example', 'content': 'lab.example', 'proxied': False},
        {'type': 'TXT', 'name': '_dmarc.lab.example', 'content': 'v=DMARC1; p=none'},
        {'type': 'MX', 'name': 'lab.example', 'content': 'mail.lab.example', 'priority': 10},
        {'type': 'SRV', 'name': '_sip._tcp.lab.example', 'content': '0 5 5060 sip.lab.example', 'priority': 0},
        {'type': 'CAA', 'name': 'lab.example', 'content': '0 issue "letsencrypt.org"'},
        {'type': 'A', 'name': 'auto.lab.example', 'content': '192.0.2.2', 'ttl': 1},
    ):
        body = submit(client, connector, record=record)
        assert body['status'] == 'pending_approval'


# ---------------------------------------------------------------------------
# Roles, agent scopes, CSRF: the same governance surface as deployments.
# ---------------------------------------------------------------------------

def test_roles_and_agent_scopes_follow_the_deployment_model(platform, connector):
    app, human = platform
    fake = FakeCloudflare()
    app.state.cloudflare_dns.transport = httpx.MockTransport(fake.handler)
    body = submit(human, connector)
    # Humans: operator may request but not approve; viewer may only read.
    with closing(TestClient(app)) as operator:
        login(operator, app, 'operator')
        assert operator.post('/api/cloudflare/dns/requests',
                             json=request_body(connector)).status_code == 201
        assert operator.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve').status_code == 403
        assert operator.get('/api/cloudflare/dns/requests').status_code == 200
    with closing(TestClient(app)) as viewer:
        login(viewer, app, 'viewer')
        assert viewer.post('/api/cloudflare/dns/requests',
                           json=request_body(connector)).status_code == 403
        assert viewer.get('/api/cloudflare/dns/records',
                          params={'connector_id': connector['id'], 'zone_id': ZONE_ID}).status_code == 200
    with closing(TestClient(app)) as anonymous:
        assert anonymous.get('/api/cloudflare/dns/requests').status_code == 401
        assert anonymous.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve').status_code == 401
    # Agents: submit needs the submit scope; approve needs an explicit human grant.
    issued = human.post('/api/agent-tokens', json={'label': 'dns-ci', 'role': 'operator',
                                                   'scopes': ['read', 'read:infrastructure', 'submit'],
                                                   'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    with closing(TestClient(app)) as agent:
        agent.headers['Authorization'] = 'Bearer ' + issued.json()['token']
        assert agent.get('/api/cloudflare/dns/requests').status_code == 200
        assert agent.post('/api/cloudflare/dns/requests', json=request_body(connector)).status_code == 201
        assert agent.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve').status_code == 403
        assert agent.post('/api/cloudflare/dns/requests/' + body['id'] + '/reject').status_code == 403
    granted = human.post('/api/agent-tokens', json={'label': 'dns-approver', 'role': 'admin',
                                                    'scopes': ['read', 'read:infrastructure', 'approve'],
                                                    'expires_in_seconds': 3600})
    assert granted.status_code == 201, granted.text
    with closing(TestClient(app)) as approver:
        approver.headers['Authorization'] = 'Bearer ' + granted.json()['token']
        response = approver.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'succeeded'
        assert response.json()['approved_by'].startswith('agent:')
    assert SCANNABLE_USER not in persisted_dump(app)


def test_csrf_and_origin_guards_apply(platform, connector):
    app, client = platform
    body = submit(client, connector)
    path = '/api/cloudflare/dns/requests/' + body['id'] + '/approve'
    assert client.post(path, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post(path, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    without_csrf = client.post(path, headers={'x-csrf-token': 'wrong'})
    assert without_csrf.status_code == 403


# ---------------------------------------------------------------------------
# Durability: SQLite restart and the PostgreSQL production contract.
# ---------------------------------------------------------------------------

def test_requests_persist_across_restart(wired, tmp_path, monkeypatch):
    app, client, connector, fake = wired
    body = submit(client, connector)
    client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    restarted = make_app(tmp_path, monkeypatch)
    with TestClient(restarted) as other:
        login(other, restarted)
        stored = other.get('/api/cloudflare/dns/requests/' + body['id']).json()
        assert stored['status'] == 'succeeded'
        assert stored['result']['record']['name'] == 'www.lab.example'


def test_table_is_registered_in_the_postgres_contract():
    from kuberweaver import postgres
    assert postgres.COLUMNS['cloudflare_dns_requests'] == ('id', 'body')
    assert 'cloudflare_dns_requests' in postgres.DDL
    assert 'cloudflare_dns_requests' in postgres.ORDERED
    translated = postgres.translate_sql(
        'CREATE TABLE IF NOT EXISTS cloudflare_dns_requests(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    assert translated == ('CREATE TABLE IF NOT EXISTS cloudflare_dns_requests '
                          f'({postgres.DDL["cloudflare_dns_requests"]})')


@pytest.fixture
def pg_schema(tmp_path, monkeypatch):
    import os
    import uuid
    import psycopg
    from psycopg.conninfo import make_conninfo
    from psycopg import sql
    from kuberweaver.postgres import read_dsn_file
    source = Path(os.environ.get('KUBERWEAVER_TEST_DATABASE_URL_FILE',
                                 Path(__file__).parents[1] / 'data/postgresql/test-url'))
    if not source.exists():
        pytest.skip('Dedicated PostgreSQL test DSN file not configured')
    dsn = read_dsn_file(source)
    schema = 'kw_test_' + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    target = tmp_path / 'pg-url'
    target.write_text(make_conninfo(dsn, options='-csearch_path=' + schema))
    target.chmod(0o600)
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', str(target))
    try:
        yield target
    finally:
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        admin.close()


def test_postgresql_lifecycle_and_reopen(tmp_path, monkeypatch, pg_schema, local_vault):
    app = make_app(tmp_path, monkeypatch)
    assert app.state.store.backend == 'postgresql'
    app.state.registry.register('secrets', 'local-vault', local_vault, {'reference_only': True})
    fake = FakeCloudflare()
    app.state.cloudflare_dns.transport = httpx.MockTransport(fake.handler)
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        record = client.post('/api/cloudflare/connectors', json=spec()).json()
        client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
                   json={'api_token': SCANNABLE_USER})
        body = submit(client, record)
        response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'succeeded'
        assert SCANNABLE_USER not in persisted_dump(app)
    reopened = make_app(tmp_path, monkeypatch)
    assert reopened.state.store.backend == 'postgresql'
    with TestClient(reopened) as other:
        login(other, reopened)
        stored = other.get('/api/cloudflare/dns/requests/' + body['id']).json()
        assert stored['status'] == 'succeeded'
        assert stored['result']['record']['content'] == '192.0.2.10'


# ---------------------------------------------------------------------------
# Installer hygiene.
# ---------------------------------------------------------------------------

def test_installer_is_idempotent_and_routes_precede_spa(platform):
    app, client = platform
    from kuberweaver.cloudflare import install_cloudflare_api
    service, routes = app.state.cloudflare_dns, len(app.routes)
    install_cloudflare_api(app)
    assert app.state.cloudflare_dns is service
    assert len(app.routes) == routes
    paths = [getattr(route, 'path', None) for route in app.routes]
    spa = paths.index('/{asset_path:path}') if '/{asset_path:path}' in paths else len(paths)
    for path in ('/api/cloudflare/dns/zones', '/api/cloudflare/dns/records',
                 '/api/cloudflare/dns/requests', '/api/cloudflare/dns/requests/{identity}',
                 '/api/cloudflare/dns/requests/{identity}/approve',
                 '/api/cloudflare/dns/requests/{identity}/reject'):
        assert path in paths and paths.index(path) < spa


def test_service_errors_never_leak_the_token(wired):
    app, client, connector, fake = wired
    fake.fail = {'status': 500, 'errors': [{'code': 10000, 'message': 'internal'}]}
    fake.echo_token_in_error = True
    body = submit(client, connector)
    response = client.post('/api/cloudflare/dns/requests/' + body['id'] + '/approve')
    done = response.json()
    rendered = json.dumps(done) + persisted_dump(app)
    assert SCANNABLE_USER not in rendered
    try:
        from kuberweaver.cloudflare_dns import CloudflareApiError
        raise CloudflareApiError('Cloudflare API request failed',
                                 errors=[{'code': 1, 'message': 'x ' + SCANNABLE_USER}])
    except CloudflareApiError as caught:
        text = str(caught) + repr(caught) + ''.join(traceback.format_exception(caught))
        assert SCANNABLE_USER not in text
