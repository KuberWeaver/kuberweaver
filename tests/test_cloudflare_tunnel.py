"""Cloudflare tunnel capability: named tunnels (create/configure/delete) with
ingress + DNS routing through vault-scoped tokens, behind the same durable
approval/audit model as DNS changes. The cloudflared connector credential is
shape-checked, stored in the vault by reference and staged only as a private
0600 handoff file; it never enters a request record, audit detail, log or
response. The execution result carries a pinned, secret-free cloudflared
deployment bundle for the supervision VM.

All Cloudflare traffic goes to an in-process httpx.MockTransport fake; all
credential values are disposable fixtures. No test contacts Cloudflare and no
real token exists here.
"""
import base64
import json
import os
import re
import stat
import sys
import traceback
import uuid
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from kuberweaver.secrets import SecretError, SecretValue  # noqa: E402

# Disposable fixtures (same shapes as test_cloudflare_secrets / test_cloudflare_dns).
_BODY = 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0'
SCANNABLE_USER = 'cfut_' + _BODY + 'a1b2c3d4'
SCANNABLE_ACCOUNT = 'cfat_' + _BODY + '0F1E2D3C'
ACCOUNT_ID = '0123456789abcdef0123456789abcdef'
ZONE_ID = 'd41d8cd98f00b204e9800998ecf8427e'
TUNNEL_ID = '5f3c9a2e-8b1d-4e6f-9a2b-7c4d5e6f8a9b'
HOSTNAME = 'zabbix.lab.example'
SERVICE = 'http://127.0.0.1:80'
INGRESS = [{'hostname': HOSTNAME, 'service': SERVICE}]


def tunnel_token_for(tunnel_id=TUNNEL_ID, account_id=ACCOUNT_ID, secret='tunnel-secret-fixture'):
    """A disposable connector credential shaped like a real cloudflared token:
    base64 of the closed {a, t, s} JSON object Cloudflare issues."""
    return base64.b64encode(json.dumps({'a': account_id, 't': tunnel_id, 's': secret},
                                       separators=(',', ':')).encode()).decode()


TUNNEL_TOKEN = tunnel_token_for()


def spec(**changes):
    return {'label': 'Cloudflare tunnel', 'purpose': 'tunnel',
            'secret_ref': 'cloudflare/tunnel-lab', 'secrets_provider': 'local-vault',
            'account_id': ACCOUNT_ID, 'enabled': True, **changes}


def change_spec(**changes):
    return {'connector_id': 'c' * 32, 'operation': 'create', 'name': 'supervision-web',
            'ingress': [dict(rule) for rule in INGRESS], **changes}


class FakeCloudflareTunnel:
    """In-memory Cloudflare API fake for the tunnel surface. Asserts the fixed
    origin and the exact bearer API token on every call; the tunnel connector
    credential must never ride in a URL or header."""

    def __init__(self, token=SCANNABLE_ACCOUNT):
        self.token = token
        self.tunnels = {}
        self.configs = {}
        self.records = {ZONE_ID: {}}
        self.calls = []
        self.verify_status = 'active'
        self.fail = None
        self.verify_fail = None
        self.token_fail = None
        self.bad_token = None          # serve this instead of a shaped token
        self.echo_token_in_error = False
        self._next_id = 1
        self._config_version = 0

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

    def _new_id(self):
        if self._next_id == 1:
            self._next_id += 1
            return TUNNEL_ID
        value = str(uuid.UUID(int=self._next_id))
        self._next_id += 1
        return value

    def handler(self, request):
        assert request.url.host == 'api.cloudflare.com', 'origin must stay fixed'
        assert request.url.scheme == 'https'
        assert request.headers.get('Authorization') == 'Bearer ' + self.token
        assert self.token not in str(request.url), 'API token must never ride in a URL'
        assert TUNNEL_TOKEN not in str(request.url), 'tunnel token must never ride in a URL'
        assert TUNNEL_TOKEN not in request.headers.get('Authorization', '')
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
        match = re.fullmatch(r'/accounts/([0-9a-f]{32})/cfd_tunnel', route)
        if match:
            account_id = match[1]
            if account_id != ACCOUNT_ID:
                return self._error({'status': 403, 'errors': [{'code': 9109, 'message': 'Unauthorized'}]})
            if request.method == 'GET':
                deleted = request.url.params.get('is_deleted', 'false') == 'true'
                items = [t for t in self.tunnels.values()
                         if (t['deleted_at'] is not None) == deleted]
                page = int(request.url.params.get('page', '1'))
                per_page = int(request.url.params.get('per_page', '100'))
                chunk = items[(page - 1) * per_page:page * per_page]
                total_pages = max(1, -(-len(items) // per_page))
                return self._ok(chunk, {'page': page, 'per_page': per_page,
                                        'count': len(chunk), 'total_count': len(items),
                                        'total_pages': total_pages})
            if request.method == 'POST':
                body = json.loads(request.content)
                assert body.get('config_src') == 'cloudflare', 'tunnels must be remotely managed'
                tunnel_id = self._new_id()
                tunnel = {'id': tunnel_id, 'name': body['name'],
                          'created_at': '2026-11-20T00:00:00Z', 'deleted_at': None,
                          'status': 'down', 'tun_type': 'cfd_tunnel', 'remote_config': True,
                          'account_tag': ACCOUNT_ID, 'connections': []}
                self.tunnels[tunnel_id] = tunnel
                return self._ok(tunnel)
        match = re.fullmatch(r'/accounts/([0-9a-f]{32})/cfd_tunnel/([0-9a-f-]{36})', route)
        if match:
            tunnel_id = match[2]
            tunnel = self.tunnels.get(tunnel_id)
            if tunnel is None or tunnel['deleted_at'] is not None:
                return self._error({'status': 404, 'errors': [{'code': 1002, 'message': 'Tunnel not found'}]})
            if request.method == 'GET':
                return self._ok(tunnel)
            if request.method == 'DELETE':
                tunnel['deleted_at'] = '2026-11-20T02:00:00Z'
                return self._ok({'id': tunnel_id})
        match = re.fullmatch(r'/accounts/([0-9a-f]{32})/cfd_tunnel/([0-9a-f-]{36})/token', route)
        if match:
            tunnel_id = match[2]
            if tunnel_id not in self.tunnels:
                return self._error({'status': 404, 'errors': [{'code': 1002, 'message': 'Tunnel not found'}]})
            if self.token_fail:
                return self._error(self.token_fail)
            return self._ok(self.bad_token if self.bad_token is not None
                            else tunnel_token_for(tunnel_id))
        match = re.fullmatch(r'/accounts/([0-9a-f]{32})/cfd_tunnel/([0-9a-f-]{36})/configurations', route)
        if match:
            tunnel_id = match[2]
            if tunnel_id not in self.tunnels:
                return self._error({'status': 404, 'errors': [{'code': 1002, 'message': 'Tunnel not found'}]})
            if request.method == 'PUT':
                body = json.loads(request.content)
                self._config_version += 1
                self.configs[tunnel_id] = body.get('config')
                return self._ok({'id': tunnel_id, 'config': body.get('config'),
                                 'version': self._config_version,
                                 'source': 'cloudflare'})
            if request.method == 'GET':
                return self._ok({'id': tunnel_id, 'config': self.configs.get(tunnel_id),
                                 'version': self._config_version, 'source': 'cloudflare'})
        match = re.fullmatch(r'/zones/([0-9a-f]{32})/dns_records', route)
        if match:
            zone_id = match[1]
            if request.method == 'POST':
                body = json.loads(request.content)
                record_id = f'{len(self.records[zone_id]) + 1:032x}'
                record = {'id': record_id, 'zone_id': zone_id, 'zone_name': 'lab.example',
                          'created_on': '2026-11-20T00:00:00Z',
                          'modified_on': '2026-11-20T00:00:00Z', **body}
                self.records[zone_id][record_id] = record
                return self._ok(record)
            if request.method == 'GET':
                items = list(self.records[zone_id].values())
                return self._ok(items, {'page': 1, 'per_page': 100, 'count': len(items),
                                        'total_count': len(items), 'total_pages': 1})
        match = re.fullmatch(r'/zones/([0-9a-f]{32})/dns_records/([0-9a-f]{32})', route)
        if match:
            zone_id, record_id = match.groups()
            if record_id not in self.records.get(zone_id, {}):
                return self._error({'status': 404, 'errors': [{'code': 81044, 'message': 'Record does not exist'}]})
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
            raise AssertionError('Cloudflare tunnel tests must not open non-loopback connections')

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
    """A tunnel-purpose connector with a fixture API token in the real vault."""
    app, client = platform
    record = client.post('/api/cloudflare/connectors', json=spec()).json()
    response = client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
                          json={'api_token': SCANNABLE_ACCOUNT})
    assert response.status_code == 200, response.text
    return record


@pytest.fixture
def wired(platform, connector):
    app, client = platform
    fake = FakeCloudflareTunnel()
    app.state.cloudflare_tunnel.transport = httpx.MockTransport(fake.handler)
    return app, client, connector, fake


def request_body(connector, **changes):
    return change_spec(**{'connector_id': connector['id'], **changes})


def submit(client, connector, **changes):
    response = client.post('/api/cloudflare/tunnel/requests', json=request_body(connector, **changes))
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, identity):
    response = client.post('/api/cloudflare/tunnel/requests/' + identity + '/approve')
    assert response.status_code == 200, response.text
    return response.json()


def persisted_dump(app):
    return json.dumps(app.state.store.all('SELECT * FROM cloudflare_tunnel_requests')
                      + app.state.store.all('SELECT * FROM cloudflare_connectors')
                      + app.state.store.all('SELECT * FROM audit'))


def staging_path(app, tunnel_id=TUNNEL_ID):
    return app.state.state_dir / 'cloudflare-tunnels' / tunnel_id / 'token'


def vault_read(local_vault, reference):
    with local_vault.resolve(reference) as secret:
        return secret.bytes()


# ---------------------------------------------------------------------------
# Request shape and governance gates.
# ---------------------------------------------------------------------------

def test_create_request_lands_pending_without_any_cloudflare_call(wired):
    app, client, connector, fake = wired
    body = submit(client, connector, route={'zone_id': ZONE_ID})
    assert re.fullmatch('[0-9a-f]{32}', body['id'])
    assert body['status'] == 'pending_approval'
    assert body['operation'] == 'create' and body['name'] == 'supervision-web'
    assert body['ingress'] == [{'hostname': HOSTNAME, 'service': SERVICE},
                               {'hostname': None, 'service': 'http_status:404'}]
    assert body['route'] == {'zone_id': ZONE_ID, 'name': HOSTNAME}
    assert body['created_by'] == 'admin' and body['approved_by'] is None
    assert re.fullmatch('[0-9a-f]{64}', body['connector_fingerprint'])
    assert re.fullmatch('[0-9a-f]{64}', body['payload_digest'])
    assert fake.calls == []
    assert SCANNABLE_ACCOUNT not in persisted_dump(app)


def test_create_requires_tunnel_purpose_and_account(platform):
    app, client = platform
    fake = FakeCloudflareTunnel()
    app.state.cloudflare_tunnel.transport = httpx.MockTransport(fake.handler)
    dns_connector = client.post('/api/cloudflare/connectors',
                                json=spec(label='DNS only', purpose='dns',
                                          secret_ref='cloudflare/dns-only')).json()
    client.put('/api/cloudflare/connectors/' + dns_connector['id'] + '/credentials',
               json={'api_token': SCANNABLE_ACCOUNT})
    response = client.post('/api/cloudflare/tunnel/requests',
                           json=request_body(dns_connector))
    assert response.status_code == 409
    no_account = client.post('/api/cloudflare/connectors',
                             json=spec(label='No account', secret_ref='cloudflare/no-account',
                                       account_id=None)).json()
    client.put('/api/cloudflare/connectors/' + no_account['id'] + '/credentials',
               json={'api_token': SCANNABLE_ACCOUNT})
    response = client.post('/api/cloudflare/tunnel/requests', json=request_body(no_account))
    assert response.status_code == 409
    assert fake.calls == []


def test_spec_shape_is_closed_and_validated(wired):
    app, client, connector, fake = wired
    base = request_body(connector)
    # Unknown fields (including secret-shaped ones) fail closed.
    assert client.post('/api/cloudflare/tunnel/requests',
                       json={**base, 'api_token': SCANNABLE_ACCOUNT}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests',
                       json={**base, 'tunnel_token': TUNNEL_TOKEN}).status_code == 422
    # create: name and ingress required; tunnel_id forbidden.
    assert client.post('/api/cloudflare/tunnel/requests',
                       json={k: v for k, v in base.items() if k != 'name'}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests',
                       json={**base, 'ingress': []}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests',
                       json={**base, 'tunnel_id': TUNNEL_ID}).status_code == 422
    # ingress: at least one hostname rule; catch-all only last; services allowlisted.
    assert client.post('/api/cloudflare/tunnel/requests', json={
        **base, 'ingress': [{'service': 'http_status:404'}]}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests', json={
        **base, 'ingress': [{'hostname': HOSTNAME, 'service': 'file:///etc/passwd'}]}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests', json={
        **base, 'ingress': [{'hostname': HOSTNAME, 'service': 'gopher://x'}]}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests', json={
        **base, 'ingress': [{'hostname': None, 'service': 'http_status:404'},
                            {'hostname': HOSTNAME, 'service': SERVICE}]}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests', json={
        **base, 'ingress': [{'hostname': 'bad host', 'service': SERVICE}]}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests', json={
        **base, 'ingress': [{'hostname': HOSTNAME, 'service': 'http://127.0.0.1:99999'}]}).status_code == 422
    # route: name must match an ingress hostname; zone id shape enforced.
    assert client.post('/api/cloudflare/tunnel/requests',
                       json={**base, 'route': {'zone_id': ZONE_ID, 'name': 'other.lab.example'}}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests',
                       json={**base, 'route': {'zone_id': 'zz'}}).status_code == 422
    # configure/delete shapes.
    assert client.post('/api/cloudflare/tunnel/requests', json={
        'connector_id': connector['id'], 'operation': 'configure',
        'ingress': INGRESS}).status_code == 422  # missing tunnel_id
    assert client.post('/api/cloudflare/tunnel/requests', json={
        'connector_id': connector['id'], 'operation': 'delete',
        'tunnel_id': TUNNEL_ID, 'ingress': INGRESS}).status_code == 422
    assert client.post('/api/cloudflare/tunnel/requests', json={
        'connector_id': connector['id'], 'operation': 'delete',
        'tunnel_id': TUNNEL_ID, 'route': {'zone_id': ZONE_ID}}).status_code == 422
    assert fake.calls == []


def test_explicit_secret_reference_is_validated(wired):
    app, client, connector, fake = wired
    assert client.post('/api/cloudflare/tunnel/requests',
                       json=request_body(connector, tunnel_secret_ref='Not Valid')).status_code == 422
    body = submit(client, connector, tunnel_secret_ref='cloudflare/custom-tunnel')
    assert body['tunnel_secret_ref'] == 'cloudflare/custom-tunnel'


# ---------------------------------------------------------------------------
# Create lifecycle: tunnel + vault credential + ingress + DNS route + bundle.
# ---------------------------------------------------------------------------

def test_approve_creates_tunnel_stores_credential_and_routes_dns(wired, local_vault):
    app, client, connector, fake = wired
    body = submit(client, connector, route={'zone_id': ZONE_ID})
    done = approve(client, body['id'])
    assert done['status'] == 'succeeded', done
    result = done['result']
    assert result['token_verified'] is True
    assert result['tunnel']['id'] == TUNNEL_ID
    assert result['tunnel']['name'] == 'supervision-web'
    assert set(result['tunnel']) <= {'id', 'name', 'created_at', 'deleted_at', 'status', 'tun_type'}
    assert result['ingress'] == [{'hostname': HOSTNAME, 'service': SERVICE},
                                 {'hostname': None, 'service': 'http_status:404'}]
    reference = 'cloudflare/tunnel-' + TUNNEL_ID
    assert result['tunnel_secret_ref'] == reference
    assert result['credential_stored'] is True
    # The connector credential is in the real vault, closed envelope, readback.
    envelope = json.loads(vault_read(local_vault, reference))
    assert envelope == {'tunnel_token': TUNNEL_TOKEN}
    # DNS CNAME route to the tunnel, proxied.
    record = result['route']['record']
    assert record['type'] == 'CNAME' and record['name'] == HOSTNAME
    assert record['content'] == TUNNEL_ID + '.cfargotunnel.com'
    assert record['proxied'] is True
    assert fake.records[ZONE_ID][record['id']]['content'] == TUNNEL_ID + '.cfargotunnel.com'
    # The upstream config carries the catch-all.
    assert fake.configs[TUNNEL_ID]['ingress'][-1] == {'service': 'http_status:404'}
    # Call order: verify, create, token, configurations, DNS record.
    assert fake.calls == [
        ('GET', f'/client/v4/accounts/{ACCOUNT_ID}/tokens/verify'),
        ('POST', f'/client/v4/accounts/{ACCOUNT_ID}/cfd_tunnel'),
        ('GET', f'/client/v4/accounts/{ACCOUNT_ID}/cfd_tunnel/{TUNNEL_ID}/token'),
        ('PUT', f'/client/v4/accounts/{ACCOUNT_ID}/cfd_tunnel/{TUNNEL_ID}/configurations'),
        ('POST', f'/client/v4/zones/{ZONE_ID}/dns_records'),
    ]
    # Private staging handoff: 0600 file, exact token, never in any response.
    staged = staging_path(app)
    assert staged.is_file()
    assert stat.S_IMODE(staged.stat().st_mode) == 0o600
    assert staged.read_text() == TUNNEL_TOKEN
    assert stat.S_IMODE(staged.parent.stat().st_mode) == 0o700
    assert result['staging_file'] == str(staged)
    # Audit trail exists and carries no credential material.
    for action in ('cloudflare_tunnel.requested', 'cloudflare_tunnel.approved',
                   'cloudflare_tunnel.succeeded'):
        assert app.state.store.one('SELECT * FROM audit WHERE action=?', (action,))
    rendered = json.dumps(done) + persisted_dump(app)
    assert SCANNABLE_ACCOUNT not in rendered
    assert TUNNEL_TOKEN not in rendered
    assert 'tunnel-secret-fixture' not in rendered


def test_bundle_is_pinned_and_secret_free(wired):
    app, client, connector, fake = wired
    done = approve(client, submit(client, connector)['id'])
    bundle = done['result']['cloudflared']
    from kuberweaver import cloudflare_tunnel as module
    assert bundle['version'] == module.CLOUDFLARED_VERSION
    assert bundle['deb_sha256'] == module.CLOUDFLARED_DEB_SHA256
    assert re.fullmatch('[0-9a-f]{64}', bundle['deb_sha256'])
    assert bundle['deb_url'] == (
        f'https://github.com/cloudflare/cloudflared/releases/download/'
        f'{module.CLOUDFLARED_VERSION}/cloudflared-linux-amd64.deb')
    assert bundle['env_file'] == '/etc/kuberweaver/cloudflared/tunnel.env'
    assert 'EnvironmentFile=/etc/kuberweaver/cloudflared/tunnel.env' in bundle['unit']
    assert 'tunnel run' in bundle['unit']
    assert TUNNEL_TOKEN not in json.dumps(bundle)
    assert SCANNABLE_ACCOUNT not in json.dumps(bundle)
    steps = json.dumps(bundle['steps'])
    assert bundle['deb_sha256'] in steps and 'sha256sum' in steps
    assert 'cloudflare/tunnel-' + TUNNEL_ID in steps  # vault reference, not value
    assert str(staging_path(app)) in steps
    # Deterministic: same inputs, same bundle.
    again = module.cloudflared_bundle('cloudflare/tunnel-' + TUNNEL_ID, str(staging_path(app)))
    assert again == bundle


def test_create_without_route_skips_dns(wired):
    app, client, connector, fake = wired
    done = approve(client, submit(client, connector)['id'])
    assert done['status'] == 'succeeded'
    assert done['result']['route'] is None
    assert not any(path.endswith('/dns_records') for _, path in fake.calls)


def test_partial_failure_records_upstream_tunnel_for_cleanup(wired, local_vault):
    app, client, connector, fake = wired
    fake.token_fail = {'status': 500, 'errors': [{'code': 10000, 'message': 'internal'}]}
    body = submit(client, connector)
    done = approve(client, body['id'])
    assert done['status'] == 'failed'
    assert done['upstream_tunnel_id'] == TUNNEL_ID  # durable trace of the created tunnel
    assert done['result']['errors'] == [{'code': 10000, 'message': 'internal'}]
    # Nothing staged, nothing stored: the credential never existed.
    assert not staging_path(app).exists()
    with pytest.raises(SecretError):
        vault_read(local_vault, 'cloudflare/tunnel-' + TUNNEL_ID)
    assert TUNNEL_TOKEN not in json.dumps(done) + persisted_dump(app)


def test_bad_token_shape_fails_closed(wired, local_vault):
    app, client, connector, fake = wired
    for bad in ('not-base64!!', base64.b64encode(b'{"a":"x"}').decode(),
                tunnel_token_for(tunnel_id='00000000-0000-4000-8000-000000000000'),
                tunnel_token_for(account_id='f' * 32), ''):
        fake.bad_token = bad
        body = submit(client, connector)
        done = approve(client, body['id'])
        assert done['status'] == 'failed', bad
        assert not staging_path(app).exists()
    with pytest.raises(SecretError):
        vault_read(local_vault, 'cloudflare/tunnel-' + TUNNEL_ID)


def test_vault_reference_conflict_fails_closed(wired, local_vault):
    app, client, connector, fake = wired
    reference = 'cloudflare/tunnel-' + TUNNEL_ID
    local_vault.store(reference, SecretValue(b'{"tunnel_token":"pre-existing"}'))
    done = approve(client, submit(client, connector)['id'])
    assert done['status'] == 'failed'
    assert 'reference' in (done['error'] or '').lower()
    # The pre-existing credential is untouched.
    assert vault_read(local_vault, reference) == b'{"tunnel_token":"pre-existing"}'
    assert not staging_path(app).exists()


def test_token_verify_gate_precedes_any_write(wired):
    app, client, connector, fake = wired
    fake.verify_status = 'disabled'
    done = approve(client, submit(client, connector)['id'])
    assert done['status'] == 'failed'
    assert fake.calls == [('GET', f'/client/v4/accounts/{ACCOUNT_ID}/tokens/verify')]
    assert fake.tunnels == {}


def test_pins_reverified_at_approval(wired, local_vault):
    app, client, connector, fake = wired
    body = submit(client, connector)
    # Credential rotation changes the connector fingerprint.
    response = client.put('/api/cloudflare/connectors/' + connector['id'] + '/credentials',
                          json={'api_token': SCANNABLE_USER})
    assert response.status_code == 200
    fake.token = SCANNABLE_USER
    response = client.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/approve')
    assert response.status_code == 409
    assert fake.tunnels == {}
    # Payload drift is detected too.
    other = submit(client, connector)
    row = app.state.store.one('SELECT body FROM cloudflare_tunnel_requests WHERE id=?', (other['id'],))
    tampered = json.loads(row['body'])
    tampered['name'] = 'evil'
    app.state.store.execute('UPDATE cloudflare_tunnel_requests SET body=? WHERE id=?',
                            (json.dumps(tampered), other['id']))
    response = client.post('/api/cloudflare/tunnel/requests/' + other['id'] + '/approve')
    assert response.status_code == 409
    assert fake.tunnels == {}


def test_credentials_unavailable_fails_closed_before_state_change(platform):
    app, client = platform
    bare = client.post('/api/cloudflare/connectors',
                       json=spec(label='Bare', secret_ref='cloudflare/bare-tunnel')).json()
    fake = FakeCloudflareTunnel()
    app.state.cloudflare_tunnel.transport = httpx.MockTransport(fake.handler)
    body = submit(client, bare)
    response = client.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/approve')
    assert response.status_code == 200
    done = response.json()
    assert done['status'] == 'failed'
    assert done['approved_by'] is None  # fail closed before the approval is recorded
    assert fake.calls == []


def test_reject_and_double_approve_are_conflicts(wired):
    app, client, connector, fake = wired
    body = submit(client, connector)
    response = client.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/reject')
    assert response.status_code == 200 and response.json()['status'] == 'rejected'
    assert client.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/approve').status_code == 409
    assert fake.calls == []
    done = approve(client, submit(client, connector)['id'])
    assert client.post('/api/cloudflare/tunnel/requests/' + done['id'] + '/approve').status_code == 409


# ---------------------------------------------------------------------------
# Configure and delete.
# ---------------------------------------------------------------------------

def test_configure_updates_ingress_without_touching_credentials(wired, local_vault):
    app, client, connector, fake = wired
    created = approve(client, submit(client, connector)['id'])
    reference = created['result']['tunnel_secret_ref']
    before = vault_read(local_vault, reference)
    new_ingress = [{'hostname': 'grafana.lab.example', 'service': 'http://127.0.0.1:3000'}]
    body = submit(client, connector, operation='configure', tunnel_id=TUNNEL_ID,
                  name=None, ingress=new_ingress)
    calls_before = len(fake.calls)
    done = approve(client, body['id'])
    assert done['status'] == 'succeeded'
    assert done['result']['ingress'] == [{'hostname': 'grafana.lab.example',
                                          'service': 'http://127.0.0.1:3000'},
                                         {'hostname': None, 'service': 'http_status:404'}]
    assert fake.configs[TUNNEL_ID]['ingress'][0]['hostname'] == 'grafana.lab.example'
    assert not any(path.endswith('/token') for _, path in fake.calls[calls_before:])
    assert vault_read(local_vault, reference) == before


def test_delete_removes_tunnel_route_and_staging(wired, local_vault):
    app, client, connector, fake = wired
    created = approve(client, submit(client, connector, route={'zone_id': ZONE_ID})['id'])
    record_id = created['result']['route']['record']['id']
    reference = created['result']['tunnel_secret_ref']
    assert staging_path(app).is_file()
    body = submit(client, connector, operation='delete', tunnel_id=TUNNEL_ID, name=None,
                  ingress=None, route={'zone_id': ZONE_ID, 'record_id': record_id})
    done = approve(client, body['id'])
    assert done['status'] == 'succeeded', done
    assert fake.tunnels[TUNNEL_ID]['deleted_at'] is not None
    assert fake.records[ZONE_ID] == {}
    assert not staging_path(app).exists()
    # local-vault has no delete capability: the entry is retained, honestly reported.
    assert done['result']['vault_credential_deleted'] is False
    assert vault_read(local_vault, reference) == json.dumps({'tunnel_token': TUNNEL_TOKEN}).encode() \
        or vault_read(local_vault, reference)  # still resolvable, never printed
    assert TUNNEL_TOKEN not in json.dumps(done) + persisted_dump(app)


def test_delete_wipes_vault_when_provider_supports_it(platform, local_vault, monkeypatch):
    app, client = platform
    deleted = []

    class DeletableVault:
        def describe(self):
            return {**local_vault.describe(), 'can_delete': True}

        def store(self, name, value):
            return local_vault.store(name, value)

        def resolve(self, name):
            return local_vault.resolve(name)

        def delete(self, name):
            deleted.append(name)

    app.state.registry.register('secrets', 'deletable-vault', DeletableVault(),
                                {'reference_only': True})
    connector = client.post('/api/cloudflare/connectors',
                            json=spec(secrets_provider='deletable-vault')).json()
    client.put('/api/cloudflare/connectors/' + connector['id'] + '/credentials',
               json={'api_token': SCANNABLE_ACCOUNT})
    fake = FakeCloudflareTunnel()
    app.state.cloudflare_tunnel.transport = httpx.MockTransport(fake.handler)
    created = approve(client, submit(client, connector)['id'])
    reference = created['result']['tunnel_secret_ref']
    body = submit(client, connector, operation='delete', tunnel_id=TUNNEL_ID,
                  name=None, ingress=None)
    done = approve(client, body['id'])
    assert done['status'] == 'succeeded'
    assert done['result']['vault_credential_deleted'] is True
    assert deleted == [reference]


# ---------------------------------------------------------------------------
# Reads: sanitized, audited, no approval needed.
# ---------------------------------------------------------------------------

def test_tunnel_reads_are_sanitized_and_audited(wired):
    app, client, connector, fake = wired
    approve(client, submit(client, connector)['id'])
    response = client.get('/api/cloudflare/tunnel/tunnels',
                          params={'connector_id': connector['id']})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['truncated'] is False
    assert body['items'] == [{'id': TUNNEL_ID, 'name': 'supervision-web',
                              'created_at': '2026-11-20T00:00:00Z', 'deleted_at': None,
                              'status': 'down', 'tun_type': 'cfd_tunnel'}]
    detail = client.get('/api/cloudflare/tunnel/tunnels/' + TUNNEL_ID,
                        params={'connector_id': connector['id']})
    assert detail.status_code == 200, detail.text
    assert detail.json()['tunnel']['id'] == TUNNEL_ID
    assert detail.json()['ingress'][0] == {'hostname': HOSTNAME, 'service': SERVICE}
    audit = app.state.store.one("SELECT * FROM audit WHERE action='cloudflare_tunnel.tunnels_listed'")
    assert audit and audit['resource'] == connector['id']
    assert SCANNABLE_ACCOUNT not in persisted_dump(app)
    assert TUNNEL_TOKEN not in detail.text


def test_reads_require_account_and_credentials(platform):
    app, client = platform
    fake = FakeCloudflareTunnel()
    app.state.cloudflare_tunnel.transport = httpx.MockTransport(fake.handler)
    assert client.get('/api/cloudflare/tunnel/tunnels',
                      params={'connector_id': 'f' * 32}).status_code == 404
    assert client.get('/api/cloudflare/tunnel/tunnels',
                      params={'connector_id': 'not-an-id'}).status_code == 422
    bare = client.post('/api/cloudflare/connectors',
                       json=spec(label='Bare read', secret_ref='cloudflare/bare-read')).json()
    assert client.get('/api/cloudflare/tunnel/tunnels',
                      params={'connector_id': bare['id']}).status_code == 503
    no_account = client.post('/api/cloudflare/connectors',
                             json=spec(label='No account read', secret_ref='cloudflare/no-acct-read',
                                       account_id=None)).json()
    client.put('/api/cloudflare/connectors/' + no_account['id'] + '/credentials',
               json={'api_token': SCANNABLE_ACCOUNT})
    assert client.get('/api/cloudflare/tunnel/tunnels',
                      params={'connector_id': no_account['id']}).status_code == 409
    assert fake.calls == []


def test_read_api_errors_are_sanitized(wired):
    app, client, connector, fake = wired
    fake.fail = {'status': 403, 'errors': [{'code': 9109, 'message': 'Unauthorized'}]}
    fake.echo_token_in_error = True
    response = client.get('/api/cloudflare/tunnel/tunnels',
                          params={'connector_id': connector['id']})
    assert response.status_code == 502
    assert SCANNABLE_ACCOUNT not in response.text
    assert TUNNEL_TOKEN not in response.text
    assert SCANNABLE_ACCOUNT not in persisted_dump(app)
    assert '9109' in response.text


# ---------------------------------------------------------------------------
# Roles, agent scopes, CSRF: the deployment governance model.
# ---------------------------------------------------------------------------

def test_roles_and_agent_scopes_follow_the_deployment_model(platform, connector):
    app, human = platform
    fake = FakeCloudflareTunnel()
    app.state.cloudflare_tunnel.transport = httpx.MockTransport(fake.handler)
    body = submit(human, connector)
    with closing(TestClient(app)) as operator:
        login(operator, app, 'operator')
        assert operator.post('/api/cloudflare/tunnel/requests',
                             json=request_body(connector)).status_code == 201
        assert operator.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/approve').status_code == 403
        assert operator.get('/api/cloudflare/tunnel/requests').status_code == 200
    with closing(TestClient(app)) as viewer:
        login(viewer, app, 'viewer')
        assert viewer.post('/api/cloudflare/tunnel/requests',
                           json=request_body(connector)).status_code == 403
        assert viewer.get('/api/cloudflare/tunnel/tunnels',
                          params={'connector_id': connector['id']}).status_code == 200
    with closing(TestClient(app)) as anonymous:
        assert anonymous.get('/api/cloudflare/tunnel/requests').status_code == 401
        assert anonymous.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/approve').status_code == 401
    issued = human.post('/api/agent-tokens', json={'label': 'tunnel-ci', 'role': 'operator',
                                                   'scopes': ['read', 'read:infrastructure', 'submit'],
                                                   'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    with closing(TestClient(app)) as agent:
        agent.headers['Authorization'] = 'Bearer ' + issued.json()['token']
        assert agent.get('/api/cloudflare/tunnel/requests').status_code == 200
        assert agent.get('/api/cloudflare/tunnel/tunnels',
                         params={'connector_id': connector['id']}).status_code == 200
        assert agent.post('/api/cloudflare/tunnel/requests', json=request_body(connector)).status_code == 201
        assert agent.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/approve').status_code == 403
        assert agent.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/reject').status_code == 403
    granted = human.post('/api/agent-tokens', json={'label': 'tunnel-approver', 'role': 'admin',
                                                    'scopes': ['read', 'read:infrastructure', 'approve'],
                                                    'expires_in_seconds': 3600})
    assert granted.status_code == 201, granted.text
    with closing(TestClient(app)) as approver:
        approver.headers['Authorization'] = 'Bearer ' + granted.json()['token']
        response = approver.post('/api/cloudflare/tunnel/requests/' + body['id'] + '/approve')
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'succeeded'
        assert response.json()['approved_by'].startswith('agent:')
    assert SCANNABLE_ACCOUNT not in persisted_dump(app)
    assert TUNNEL_TOKEN not in persisted_dump(app)


def test_csrf_and_origin_guards_apply(platform, connector):
    app, client = platform
    body = submit(client, connector)
    path = '/api/cloudflare/tunnel/requests/' + body['id'] + '/approve'
    assert client.post(path, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post(path, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.post(path, headers={'x-csrf-token': 'wrong'}).status_code == 403


# ---------------------------------------------------------------------------
# Durability: SQLite restart and the PostgreSQL production contract.
# ---------------------------------------------------------------------------

def test_requests_persist_across_restart(wired, tmp_path, monkeypatch):
    app, client, connector, fake = wired
    body = submit(client, connector, route={'zone_id': ZONE_ID})
    approve(client, body['id'])
    restarted = make_app(tmp_path, monkeypatch)
    with TestClient(restarted) as other:
        login(other, restarted)
        stored = other.get('/api/cloudflare/tunnel/requests/' + body['id']).json()
        assert stored['status'] == 'succeeded'
        assert stored['result']['tunnel']['id'] == TUNNEL_ID
        assert stored['result']['tunnel_secret_ref'] == 'cloudflare/tunnel-' + TUNNEL_ID


def test_table_is_registered_in_the_postgres_contract():
    from kuberweaver import postgres
    assert postgres.COLUMNS['cloudflare_tunnel_requests'] == ('id', 'body')
    assert 'cloudflare_tunnel_requests' in postgres.DDL
    assert 'cloudflare_tunnel_requests' in postgres.ORDERED
    translated = postgres.translate_sql(
        'CREATE TABLE IF NOT EXISTS cloudflare_tunnel_requests(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    assert translated == ('CREATE TABLE IF NOT EXISTS cloudflare_tunnel_requests '
                          f'({postgres.DDL["cloudflare_tunnel_requests"]})')


@pytest.fixture
def pg_schema(tmp_path, monkeypatch):
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
    fake = FakeCloudflareTunnel()
    app.state.cloudflare_tunnel.transport = httpx.MockTransport(fake.handler)
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, app)
        record = client.post('/api/cloudflare/connectors', json=spec()).json()
        client.put('/api/cloudflare/connectors/' + record['id'] + '/credentials',
                   json={'api_token': SCANNABLE_ACCOUNT})
        body = submit(client, record, route={'zone_id': ZONE_ID})
        done = approve(client, body['id'])
        assert done['status'] == 'succeeded'
        assert SCANNABLE_ACCOUNT not in persisted_dump(app)
        assert TUNNEL_TOKEN not in persisted_dump(app)
    reopened = make_app(tmp_path, monkeypatch)
    assert reopened.state.store.backend == 'postgresql'
    with TestClient(reopened) as other:
        login(other, reopened)
        stored = other.get('/api/cloudflare/tunnel/requests/' + body['id']).json()
        assert stored['status'] == 'succeeded'
        assert stored['result']['route']['record']['content'] == TUNNEL_ID + '.cfargotunnel.com'


# ---------------------------------------------------------------------------
# Installer hygiene and leakage.
# ---------------------------------------------------------------------------

def test_installer_is_idempotent_and_routes_precede_spa(platform):
    app, client = platform
    from kuberweaver.cloudflare import install_cloudflare_api
    service, routes = app.state.cloudflare_tunnel, len(app.routes)
    install_cloudflare_api(app)
    assert app.state.cloudflare_tunnel is service
    assert len(app.routes) == routes
    paths = [getattr(route, 'path', None) for route in app.routes]
    spa = paths.index('/{asset_path:path}') if '/{asset_path:path}' in paths else len(paths)
    for path in ('/api/cloudflare/tunnel/tunnels', '/api/cloudflare/tunnel/tunnels/{tunnel_id}',
                 '/api/cloudflare/tunnel/requests', '/api/cloudflare/tunnel/requests/{identity}',
                 '/api/cloudflare/tunnel/requests/{identity}/approve',
                 '/api/cloudflare/tunnel/requests/{identity}/reject'):
        assert path in paths and paths.index(path) < spa


def test_service_errors_never_leak_credentials(wired):
    app, client, connector, fake = wired
    fake.fail = {'status': 500, 'errors': [{'code': 10000, 'message': 'internal'}]}
    fake.echo_token_in_error = True
    body = submit(client, connector)
    done = approve(client, body['id'])
    rendered = json.dumps(done) + persisted_dump(app)
    assert SCANNABLE_ACCOUNT not in rendered
    assert TUNNEL_TOKEN not in rendered
    from kuberweaver.cloudflare_dns import CloudflareApiError
    try:
        raise CloudflareApiError('Cloudflare API request failed',
                                 errors=[{'code': 1, 'message': 'x ' + SCANNABLE_ACCOUNT
                                          + ' ' + TUNNEL_TOKEN}])
    except CloudflareApiError as caught:
        text = str(caught) + repr(caught) + ''.join(traceback.format_exception(caught))
        assert SCANNABLE_ACCOUNT not in text
        assert TUNNEL_TOKEN not in text


def test_token_shape_contract_is_fail_closed():
    from kuberweaver.cloudflare_tunnel import validate_tunnel_token, parse_tunnel_envelope
    good = tunnel_token_for()
    assert validate_tunnel_token(good, account_id=ACCOUNT_ID, tunnel_id=TUNNEL_ID) == good
    for bad in (None, 42, '', 'x' * 5000, '!!!!',
                base64.b64encode(b'not json').decode(),
                base64.b64encode(json.dumps({'a': ACCOUNT_ID, 't': TUNNEL_ID}).encode()).decode(),
                base64.b64encode(json.dumps({'a': ACCOUNT_ID, 't': TUNNEL_ID,
                                             's': 'x', 'extra': 1}).encode()).decode(),
                tunnel_token_for(tunnel_id='00000000-0000-4000-8000-000000000000'),
                tunnel_token_for(account_id='f' * 32)):
        with pytest.raises(SecretError):
            validate_tunnel_token(bad, account_id=ACCOUNT_ID, tunnel_id=TUNNEL_ID)
    envelope = json.dumps({'tunnel_token': good}).encode()
    assert parse_tunnel_envelope(envelope, account_id=ACCOUNT_ID, tunnel_id=TUNNEL_ID) == good
    for bad in (b'', b'{}', json.dumps({'api_token': good}).encode(),
                json.dumps({'tunnel_token': good, 'extra': 1}).encode(),
                json.dumps({'tunnel_token': '!!!!'}).encode()):
        with pytest.raises(SecretError):
            parse_tunnel_envelope(bad, account_id=ACCOUNT_ID, tunnel_id=TUNNEL_ID)