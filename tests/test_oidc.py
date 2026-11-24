"""Optional OpenID Connect sign-in: disabled default, discovery, claims and links."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient

ISSUER = 'https://idp.example.test/realms/lab'
CLIENT_ID = 'kuberweaver'
CLIENT_SECRET = 'not-a-real-client-secret'
REDIRECT = 'https://testserver/api/auth/oidc/callback'
METADATA = {
    'issuer': ISSUER,
    'authorization_endpoint': ISSUER + '/authorize',
    'token_endpoint': ISSUER + '/token',
    'jwks_uri': ISSUER + '/jwks',
}
KID = 'test-key'


def bootstrap(path):
    return dict(line.split(':', 1) for line in (path / 'bootstrap-credentials.txt').read_text().splitlines())


class StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class StubHttp:
    """Minimal stand-in for httpx: discovery, JWKS and the token endpoint."""

    def __init__(self, *, metadata=None, jwks=None, discovery_status=200, token_status=200,
                 discovery_failure=False):
        self.metadata = metadata or dict(METADATA)
        self.jwks = jwks or {'keys': []}
        self.discovery_status = discovery_status
        self.token_status = token_status
        self.discovery_failure = discovery_failure
        self.token_payload = {}
        self.calls = []
        self.form = None

    def get(self, url, *, timeout=None, follow_redirects=None):
        self.calls.append(('GET', url))
        if self.discovery_failure:
            raise OSError('connection refused')
        if url.endswith('/.well-known/openid-configuration'):
            return StubResponse(self.discovery_status, self.metadata)
        if url == self.metadata.get('jwks_uri'):
            return StubResponse(200, self.jwks)
        raise AssertionError(url)

    def post(self, url, *, data=None, timeout=None, follow_redirects=None):
        self.calls.append(('POST', url))
        self.form = data
        return StubResponse(self.token_status, self.token_payload)


@pytest.fixture(scope='module')
def signing_key():
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope='module')
def stranger_key():
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_for(key):
    from jwt.algorithms import RSAAlgorithm
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({'kid': KID, 'use': 'sig', 'alg': 'RS256'})
    return {'keys': [jwk]}


def access_token_hash(access_token):
    digest = hashlib.sha256(access_token.encode()).digest()
    return base64.urlsafe_b64encode(digest[:16]).rstrip(b'=').decode()


def configure(monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_OIDC_ISSUER', ISSUER)
    monkeypatch.setenv('KUBERWEAVER_OIDC_CLIENT_ID', CLIENT_ID)
    monkeypatch.setenv('KUBERWEAVER_OIDC_CLIENT_SECRET', CLIENT_SECRET)
    monkeypatch.setenv('KUBERWEAVER_OIDC_REDIRECT_URL', REDIRECT)
    monkeypatch.setenv('KUBERWEAVER_OIDC_LABEL', 'Lab identity provider')


def build(tmp_path, monkeypatch, http):
    from backend.app import create_app
    configure(monkeypatch)
    return create_app(state_dir=tmp_path, testing=True, oidc_http=http)


def begin(client):
    response = client.get('/api/auth/oidc/start', follow_redirects=False)
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers['location']).query), response


def token_for(key, expected_nonce, *, username='operator', subject='subject-1', **claims):
    now = int(time.time())
    payload = {'iss': ISSUER, 'aud': CLIENT_ID, 'sub': subject, 'iat': now, 'exp': now + 300,
               'preferred_username': username, 'nonce': expected_nonce}
    payload.update(claims)
    return jwt.encode(payload, key, algorithm='RS256', headers={'kid': KID})


def sign_in(client, http, key, *, username='operator', subject='subject-1', **claims):
    query, _ = begin(client)
    http.token_payload = {'id_token': token_for(key, query['nonce'][0], username=username,
                                                subject=subject, **claims), 'token_type': 'Bearer'}
    return client.get(f"/api/auth/oidc/callback?code=authorization-code&state={query['state'][0]}",
                      follow_redirects=False)


def test_disabled_by_default_and_password_login_still_works(tmp_path, monkeypatch):
    from backend.app import create_app
    for name in ('KUBERWEAVER_OIDC_ISSUER', 'KUBERWEAVER_OIDC_CLIENT_ID', 'KUBERWEAVER_OIDC_CLIENT_SECRET'):
        monkeypatch.delenv(name, raising=False)
    app = create_app(state_dir=tmp_path, testing=True)
    assert app.state.oidc.enabled is False
    assert app.state.store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='oidc_identities'") is None
    with TestClient(app, base_url='https://testserver') as client:
        assert client.get('/api/auth/oidc/status').json() == {
            'enabled': False, 'label': None, 'reason': 'not_configured', 'detail': None}
        assert client.get('/api/auth/oidc/start', follow_redirects=False).status_code == 503
        assert client.get('/api/auth/oidc/callback?code=x&state=y', follow_redirects=False).status_code == 503
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': bootstrap(tmp_path)['admin']})
        assert login.status_code == 200


def test_incomplete_configuration_disables_only_the_provider(tmp_path, monkeypatch):
    from backend.app import create_app
    monkeypatch.setenv('KUBERWEAVER_OIDC_ISSUER', ISSUER)
    monkeypatch.delenv('KUBERWEAVER_OIDC_CLIENT_ID', raising=False)
    monkeypatch.delenv('KUBERWEAVER_OIDC_CLIENT_SECRET', raising=False)
    app = create_app(state_dir=tmp_path, testing=True)
    with TestClient(app, base_url='https://testserver') as client:
        status = client.get('/api/auth/oidc/status').json()
        assert status['enabled'] is False
        assert status['reason'] == 'configuration_error'
        assert 'KUBERWEAVER_OIDC_CLIENT_ID' in status['detail']
        assert CLIENT_SECRET not in json.dumps(status)
        assert client.get('/api/auth/oidc/start', follow_redirects=False).status_code == 503
        assert client.post('/api/auth/login', json={'username': 'admin',
                                                    'password': bootstrap(tmp_path)['admin']}).status_code == 200


def test_discovery_failure_and_issuer_mismatch_are_refused(tmp_path, monkeypatch):
    http = StubHttp(discovery_failure=True)
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        assert client.get('/api/auth/oidc/status').json()['enabled'] is True
        assert client.get('/api/auth/oidc/start', follow_redirects=False).status_code == 503

    mismatched = dict(METADATA, issuer='https://other.example.test')
    http = StubHttp(metadata=mismatched)
    app = build(tmp_path / 'second', monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        assert client.get('/api/auth/oidc/start', follow_redirects=False).status_code == 503


def test_successful_claim_mapping_links_the_subject_to_a_local_account(tmp_path, monkeypatch, signing_key):
    http = StubHttp(jwks=jwks_for(signing_key))
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        query, response = begin(client)
        location = urlsplit(response.headers['location'])
        assert location.scheme == 'https' and location.netloc == 'idp.example.test'
        state_header = response.headers['set-cookie']
        assert state_header.startswith('kw_oidc=')
        assert 'HttpOnly' in state_header and 'SameSite=lax' in state_header
        assert 'Path=/api/auth/oidc' in state_header and 'Max-Age=600' in state_header
        params = parse_qs(location.query)
        assert params['client_id'] == [CLIENT_ID]
        assert params['response_type'] == ['code']
        assert params['redirect_uri'] == [REDIRECT]
        assert 'openid' in params['scope'][0].split()
        assert params['code_challenge_method'] == ['S256']
        assert len(params['state'][0]) >= 32 and len(params['nonce'][0]) >= 32

        http.token_payload = {'id_token': token_for(signing_key, query['nonce'][0]),
                              'token_type': 'Bearer'}
        response = client.get(f"/api/auth/oidc/callback?code=authorization-code&state={query['state'][0]}",
                              follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == '/'
        assert 'kw_session=' in response.headers['set-cookie']
        assert 'HttpOnly' in response.headers['set-cookie']
        assert http.form['grant_type'] == 'authorization_code'
        assert http.form['code_verifier']
        assert http.form['client_secret'] == CLIENT_SECRET

        me = client.get('/api/auth/me').json()
        assert me['user']['username'] == 'operator' and me['user']['role'] == 'operator'
        row = app.state.store.one('SELECT * FROM oidc_identities')
        assert row['subject'] == 'subject-1' and row['issuer'] == ISSUER

        # A later token that names another local account still resolves to the
        # bound account: the identity provider proves identity, never a role.
        response = sign_in(client, http, signing_key, username='admin', subject='subject-1')
        assert response.status_code == 303
        assert client.get('/api/auth/me').json()['user']['username'] == 'operator'

        actions = [row['action'] for row in app.state.store.all('SELECT action FROM audit ORDER BY id')]
        assert actions.count('auth.oidc.login') == 2
        assert 'auth.oidc.linked' in actions
        assert CLIENT_SECRET not in json.dumps(app.state.store.all('SELECT * FROM audit'))


def test_unknown_and_already_linked_accounts_are_refused(tmp_path, monkeypatch, signing_key):
    http = StubHttp(jwks=jwks_for(signing_key))
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        unknown = sign_in(client, http, signing_key, username='ghost', subject='subject-ghost')
        assert unknown.status_code == 403
        assert 'set-cookie' not in unknown.headers
        assert client.get('/api/auth/me').status_code == 401
        assert app.state.store.one('SELECT * FROM oidc_identities') is None

        assert sign_in(client, http, signing_key, username='operator', subject='subject-1').status_code == 303
        conflict = sign_in(client, http, signing_key, username='operator', subject='subject-2')
        assert conflict.status_code == 403
        assert app.state.store.one('SELECT count(*) AS total FROM oidc_identities')['total'] == 1

        audit = [row for row in app.state.store.all('SELECT * FROM audit') if row['action'] == 'auth.oidc.login_failed']
        assert json.loads(audit[0]['detail'])['reason'] == 'account_refused'


def test_state_is_single_use_and_unknown_states_are_refused(tmp_path, monkeypatch, signing_key):
    http = StubHttp(jwks=jwks_for(signing_key))
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        query, _ = begin(client)
        http.token_payload = {'id_token': token_for(signing_key, query['nonce'][0])}
        url = f"/api/auth/oidc/callback?code=authorization-code&state={query['state'][0]}"
        assert client.get(url, follow_redirects=False).status_code == 303
        replay = client.get(url, follow_redirects=False)
        assert replay.status_code == 400
        before = app.state.store.one('SELECT count(*) AS total FROM audit')['total']
        assert client.get('/api/auth/oidc/callback?code=x&state=unknown', follow_redirects=False).status_code == 400
        assert client.get('/api/auth/oidc/callback?error=access_denied&state=unknown',
                          follow_redirects=False).status_code == 400
        # Random states never reach the audit trail: only a consumed attempt is evidence.
        assert app.state.store.one('SELECT count(*) AS total FROM audit')['total'] == before


def test_token_checks_refuse_wrong_nonce_audience_expiry_and_unsigned_tokens(tmp_path, monkeypatch, signing_key):
    http = StubHttp(jwks=jwks_for(signing_key))
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        assert sign_in(client, http, signing_key, nonce='another-nonce').status_code == 401
        assert sign_in(client, http, signing_key, aud='another-client').status_code == 401
        assert sign_in(client, http, signing_key, exp=int(time.time()) - 10).status_code == 401

        query, _ = begin(client)
        unsigned = jwt.encode({'iss': ISSUER, 'aud': CLIENT_ID, 'sub': 'subject-1',
                               'iat': int(time.time()), 'exp': int(time.time()) + 300,
                               'preferred_username': 'operator', 'nonce': query['nonce'][0]},
                              key='', algorithm='none')
        http.token_payload = {'id_token': unsigned}
        assert client.get(f"/api/auth/oidc/callback?code=code&state={query['state'][0]}",
                          follow_redirects=False).status_code == 401

        query, _ = begin(client)
        http.token_payload = {'id_token': token_for(signing_key, query['nonce'][0]), 'token_type': 'mac'}
        assert client.get(f"/api/auth/oidc/callback?code=code&state={query['state'][0]}",
                          follow_redirects=False).status_code == 401


def test_access_token_hash_must_match_when_present(tmp_path, monkeypatch, signing_key):
    http = StubHttp(jwks=jwks_for(signing_key))
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        access = 'opaque-access-token'
        query, _ = begin(client)
        http.token_payload = {'id_token': token_for(signing_key, query['nonce'][0],
                                                    at_hash=access_token_hash(access)),
                              'access_token': access, 'token_type': 'Bearer'}
        assert client.get(f"/api/auth/oidc/callback?code=code&state={query['state'][0]}",
                          follow_redirects=False).status_code == 303

        query, _ = begin(client)
        http.token_payload = {'id_token': token_for(signing_key, query['nonce'][0],
                                                    at_hash=access_token_hash('another-access-token')),
                              'access_token': access, 'token_type': 'Bearer'}
        assert client.get(f"/api/auth/oidc/callback?code=code&state={query['state'][0]}",
                          follow_redirects=False).status_code == 401


def test_issuer_is_compared_without_a_trailing_slash(tmp_path, monkeypatch, signing_key):
    http = StubHttp(jwks=jwks_for(signing_key))
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        assert sign_in(client, http, signing_key, iss=ISSUER + '/').status_code == 303


def test_callback_requires_the_state_cookie_from_the_same_browser(tmp_path, monkeypatch, signing_key):
    http = StubHttp(jwks=jwks_for(signing_key))
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        query, _ = begin(client)
        http.token_payload = {'id_token': token_for(signing_key, query['nonce'][0])}
        url = f"/api/auth/oidc/callback?code=code&state={query['state'][0]}"
        # A callback URL copied to another browser has no matching state cookie.
        client.cookies.clear()
        assert client.get(url, follow_redirects=False).status_code == 400
        assert client.get('/api/auth/me').status_code == 401


def test_token_endpoint_refusal_maps_to_401_without_leaking_the_body(tmp_path, monkeypatch, signing_key):
    http = StubHttp(jwks=jwks_for(signing_key), token_status=400)
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        query, _ = begin(client)
        response = client.get(f"/api/auth/oidc/callback?code=code&state={query['state'][0]}",
                              follow_redirects=False)
        assert response.status_code == 401
        assert CLIENT_SECRET not in response.text
        assert client.get('/api/auth/me').status_code == 401


def test_a_token_signed_by_an_unpublished_key_is_refused(tmp_path, monkeypatch, signing_key, stranger_key):
    http = StubHttp(jwks=jwks_for(signing_key))
    app = build(tmp_path, monkeypatch, http)
    with TestClient(app, base_url='https://testserver') as client:
        assert sign_in(client, http, stranger_key).status_code == 401
        assert app.state.store.one('SELECT * FROM oidc_identities') is None


def test_claim_mapping_normalises_and_refuses_unusable_claims(tmp_path, monkeypatch):
    http = StubHttp()
    app = build(tmp_path, monkeypatch, http)
    provider = app.state.oidc
    mapped = provider.map_claims({'sub': 'subject-1', 'preferred_username': '  Operator '})
    assert mapped == {'issuer': ISSUER, 'subject': 'subject-1', 'username': 'operator'}
    from backend.oidc import OidcAccountRefused, OidcInvalidToken
    with pytest.raises(OidcInvalidToken):
        provider.map_claims({'preferred_username': 'operator'})
    with pytest.raises(OidcAccountRefused):
        provider.map_claims({'sub': 'subject-1', 'preferred_username': 'not a username'})
    with pytest.raises(OidcAccountRefused):
        provider.map_claims({'sub': 'subject-1'})


def test_postgresql_translation_matches_the_binding_table():
    from backend.postgres import COLUMNS, DDL, translate_sql
    statement = translate_sql(
        'CREATE TABLE IF NOT EXISTS oidc_identities('
        'id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, user_id TEXT NOT NULL, '
        'created_at DOUBLE PRECISION NOT NULL, last_login DOUBLE PRECISION NOT NULL)')
    assert statement == f'CREATE TABLE IF NOT EXISTS oidc_identities ({DDL["oidc_identities"]})'
    assert COLUMNS['oidc_identities'] == ('id', 'issuer', 'subject', 'user_id', 'created_at', 'last_login')
