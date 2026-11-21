"""Optional OpenID Connect sign-in: configuration, discovery, claims and links.

The provider is an adapter beside password sign-in, never a replacement for it:
whether an identity provider is configured changes nothing in the local login
path. An external identity proves who the visitor is and nothing more. The local
account still owns the role, and an unknown or already-linked identity is
refused instead of being provisioned on the fly.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import NoReturn
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from .users import USERNAME_PATTERN

CALLBACK_PATH = '/api/auth/oidc/callback'
STATE_COOKIE = 'kw_oidc'
STATE_PATH = '/api/auth/oidc'
DISCOVERY_TTL = 300
PENDING_TTL = 600
PENDING_LIMIT = 1024
ALLOWED_ALGORITHMS = ('RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512')
ALGORITHM_HASHES = {'RS256': hashlib.sha256, 'RS384': hashlib.sha384, 'RS512': hashlib.sha512,
                    'ES256': hashlib.sha256, 'ES384': hashlib.sha384, 'ES512': hashlib.sha512}
CLAIM_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$')
LOOPBACK = {'localhost', '127.0.0.1', '::1'}


class OidcError(RuntimeError):
    """A refusal with a stable reason code and the HTTP status it maps to."""

    status = 400
    reason = 'invalid_request'


class OidcDisabled(OidcError):
    status = 503
    reason = 'disabled'


class OidcUnavailable(OidcError):
    status = 503
    reason = 'provider_unavailable'


class OidcInvalidState(OidcError):
    status = 400
    reason = 'state_invalid'


class OidcInvalidToken(OidcError):
    status = 401
    reason = 'token_invalid'


class OidcAccountRefused(OidcError):
    status = 403
    reason = 'account_refused'


class OidcConfigurationError(ValueError):
    """An explicit provider configuration is incomplete or unusable."""


def _url_problem(value, *, allow_query=False):
    if not isinstance(value, str) or not value.strip():
        return 'must be an absolute http(s) URL'
    try:
        parts = urlsplit(value.strip())
        parts.port
    except ValueError:
        return 'must be an absolute http(s) URL'
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return 'must be an absolute http(s) URL'
    if parts.username or parts.password or parts.fragment or (parts.query and not allow_query):
        return 'must not carry credentials, a query or a fragment'
    # Lab deployments may run an identity provider on the same host; anywhere
    # else a cleartext issuer would expose codes and tokens on the wire.
    if parts.scheme == 'http' and parts.hostname not in LOOPBACK:
        return 'must use https outside loopback'
    return None


def _text(environ, name):
    return (environ.get(name) or '').strip()


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    scopes: tuple
    username_claim: str
    label: str
    algorithms: tuple
    redirect_url: str | None
    timeout: float


def load_config(environ=None) -> OidcConfig | None:
    """Return the configured provider, or None when no issuer is set.

    A partially set provider is an operator error and is reported as such; it
    never silently turns into a working or a silently ignored configuration.
    """
    environ = os.environ if environ is None else environ
    issuer = _text(environ, 'KUBERWEAVER_OIDC_ISSUER')
    client_id = _text(environ, 'KUBERWEAVER_OIDC_CLIENT_ID')
    client_secret = _text(environ, 'KUBERWEAVER_OIDC_CLIENT_SECRET')
    if not any((issuer, client_id, client_secret)):
        return None
    if not issuer:
        raise OidcConfigurationError('KUBERWEAVER_OIDC_ISSUER is required when any OIDC value is set')
    if not client_id:
        raise OidcConfigurationError('KUBERWEAVER_OIDC_CLIENT_ID is required when KUBERWEAVER_OIDC_ISSUER is set')
    if not client_secret:
        raise OidcConfigurationError('KUBERWEAVER_OIDC_CLIENT_SECRET is required when KUBERWEAVER_OIDC_ISSUER is set')
    problem = _url_problem(issuer)
    if problem:
        raise OidcConfigurationError(f'KUBERWEAVER_OIDC_ISSUER {problem}')
    issuer = issuer.strip().rstrip('/')
    if len(client_id) > 255:
        raise OidcConfigurationError('KUBERWEAVER_OIDC_CLIENT_ID must be at most 255 characters')
    if len(client_secret) > 1024:
        raise OidcConfigurationError('KUBERWEAVER_OIDC_CLIENT_SECRET must be at most 1024 characters')
    scopes = tuple(dict.fromkeys(re.split(r'\s+', _text(environ, 'KUBERWEAVER_OIDC_SCOPES') or 'openid profile email')))
    if '' in scopes or 'openid' not in scopes:
        raise OidcConfigurationError('KUBERWEAVER_OIDC_SCOPES must be a space-separated list containing openid')
    username_claim = _text(environ, 'KUBERWEAVER_OIDC_USERNAME_CLAIM') or 'preferred_username'
    if not CLAIM_PATTERN.fullmatch(username_claim):
        raise OidcConfigurationError('KUBERWEAVER_OIDC_USERNAME_CLAIM must be a claim name')
    label = _text(environ, 'KUBERWEAVER_OIDC_LABEL') or 'Single sign-on'
    if len(label) > 80 or any(ord(character) < 32 for character in label):
        raise OidcConfigurationError('KUBERWEAVER_OIDC_LABEL must be 1-80 printable characters')
    algorithms = tuple(dict.fromkeys(
        item.upper() for item in re.split(r'[\s,]+', _text(environ, 'KUBERWEAVER_OIDC_ALGORITHMS') or 'RS256') if item))
    if not algorithms or any(item not in ALLOWED_ALGORITHMS for item in algorithms):
        raise OidcConfigurationError(
            'KUBERWEAVER_OIDC_ALGORITHMS must be a comma-separated list of ' + ', '.join(ALLOWED_ALGORITHMS))
    redirect_url = _text(environ, 'KUBERWEAVER_OIDC_REDIRECT_URL') or None
    if redirect_url:
        problem = _url_problem(redirect_url, allow_query=False)
        if problem:
            raise OidcConfigurationError(f'KUBERWEAVER_OIDC_REDIRECT_URL {problem}')
    raw_timeout = _text(environ, 'KUBERWEAVER_OIDC_TIMEOUT') or '5'
    try:
        timeout = float(raw_timeout)
    except ValueError:
        raise OidcConfigurationError('KUBERWEAVER_OIDC_TIMEOUT must be a number of seconds') from None
    if not 1 <= timeout <= 30:
        raise OidcConfigurationError('KUBERWEAVER_OIDC_TIMEOUT must be between 1 and 30 seconds')
    return OidcConfig(issuer=issuer, client_id=client_id, client_secret=client_secret, scopes=scopes,
                      username_claim=username_claim, label=label, algorithms=algorithms,
                      redirect_url=redirect_url, timeout=timeout)


@dataclass
class PendingLogin:
    nonce: str
    verifier: str
    redirect_uri: str
    expires: float


class OidcProvider:
    def __init__(self, config, store, users, *, http=None, clock=time.time, detail=None):
        self.config = config
        self.store = store
        self.users = users
        self.http = http or httpx
        self.clock = clock
        self.detail = detail
        self._pending = {}
        self._metadata = None
        self._metadata_expires = 0.0
        self._keys = None
        self._keys_expires = 0.0
        if config is not None:
            self.store.execute(
                'CREATE TABLE IF NOT EXISTS oidc_identities('
                'id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, user_id TEXT NOT NULL, '
                'created_at DOUBLE PRECISION NOT NULL, last_login DOUBLE PRECISION NOT NULL)')

    @classmethod
    def from_environment(cls, store, users, *, environ=None, http=None, clock=time.time):
        return cls(load_config(environ), store, users, http=http, clock=clock)

    @property
    def enabled(self):
        return self.config is not None

    def status(self):
        if self.config is None:
            return {'enabled': False, 'label': None,
                    'reason': 'configuration_error' if self.detail else 'not_configured',
                    'detail': self.detail}
        return {'enabled': True, 'label': self.config.label, 'reason': None, 'detail': None}

    def discover(self):
        if self.config is None:
            raise OidcDisabled('Single sign-on is not enabled')
        current = self.clock()
        if self._metadata is not None and current < self._metadata_expires:
            return self._metadata
        endpoint = self.config.issuer + '/.well-known/openid-configuration'
        payload = self._fetch(endpoint)
        issuer = payload.get('issuer')
        if not isinstance(issuer, str) or issuer.rstrip('/') != self.config.issuer:
            raise OidcUnavailable('Identity provider metadata does not match the configured issuer')
        metadata = {}
        for name in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            value = payload.get(name)
            if _url_problem(value):
                raise OidcUnavailable('Identity provider metadata is incomplete')
            metadata[name] = value
        self._metadata = metadata
        self._metadata_expires = current + DISCOVERY_TTL
        return metadata

    def _fetch(self, url):
        try:
            response = self.http.get(url, timeout=self.config.timeout, follow_redirects=False)
        except Exception:
            raise OidcUnavailable('Identity provider is unavailable') from None
        if getattr(response, 'status_code', 0) != 200:
            raise OidcUnavailable('Identity provider is unavailable')
        try:
            payload = response.json()
        except Exception:
            raise OidcUnavailable('Identity provider returned an unreadable response') from None
        if not isinstance(payload, dict):
            raise OidcUnavailable('Identity provider returned an unreadable response')
        return payload

    def start(self, redirect_uri=None):
        """Create a single-use sign-in attempt and return the authorization URL."""
        if self.config is None:
            raise OidcDisabled('Single sign-on is not enabled')
        metadata = self.discover()
        redirect = self.config.redirect_url or redirect_uri
        if not isinstance(redirect, str) or _url_problem(redirect):
            raise OidcUnavailable('No usable sign-in callback URL is configured')
        current = self.clock()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        self._prune_pending(current)
        self._pending[state] = PendingLogin(nonce, verifier, redirect, current + PENDING_TTL)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        query = urlencode({
            'response_type': 'code', 'client_id': self.config.client_id, 'redirect_uri': redirect,
            'scope': ' '.join(self.config.scopes), 'state': state, 'nonce': nonce,
            'code_challenge': challenge, 'code_challenge_method': 'S256'})
        return f"{metadata['authorization_endpoint']}?{query}", state

    def complete(self, state, code):
        """Consume a sign-in attempt and return the linked local account."""
        if self.config is None:
            raise OidcDisabled('Single sign-on is not enabled')
        current = self.clock()
        pending = self._pending.pop(state, None) if isinstance(state, str) else None
        if pending is None or pending.expires <= current:
            raise OidcInvalidState('Invalid or expired sign-in state')
        if not isinstance(code, str) or not code or len(code) > 4096:
            raise OidcInvalidState('Invalid or expired sign-in state')
        tokens = self._exchange(code, pending.verifier, pending.redirect_uri)
        claims = self.verify(tokens['id_token'], pending.nonce, tokens.get('access_token'))
        return self.resolve_account(self.map_claims(claims))

    def _prune_pending(self, current):
        for state in [state for state, pending in self._pending.items() if pending.expires <= current]:
            self._pending.pop(state, None)
        while len(self._pending) >= PENDING_LIMIT:
            self._pending.pop(next(iter(self._pending)))

    def _exchange(self, code, verifier, redirect_uri):
        metadata = self.discover()
        form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri,
                'client_id': self.config.client_id, 'client_secret': self.config.client_secret,
                'code_verifier': verifier}
        try:
            response = self.http.post(metadata['token_endpoint'], data=form,
                                      timeout=self.config.timeout, follow_redirects=False)
        except Exception:
            raise OidcUnavailable('Identity provider is unavailable') from None
        if getattr(response, 'status_code', 0) != 200:
            # The body may echo the code or a token; it never reaches a caller.
            raise OidcInvalidToken('The identity provider refused the authorization code')
        try:
            payload = response.json()
        except Exception:
            raise OidcInvalidToken('The identity provider returned an unreadable token response') from None
        if not isinstance(payload, dict):
            raise OidcInvalidToken('The identity provider returned an unreadable token response')
        id_token = payload.get('id_token')
        token_type = payload.get('token_type')
        if not isinstance(id_token, str) or not id_token or len(id_token) > 65536:
            raise OidcInvalidToken('The identity provider returned no identity token')
        if token_type is not None and str(token_type).lower() != 'bearer':
            raise OidcInvalidToken('The identity provider returned an unsupported token type')
        access_token = payload.get('access_token')
        return {'id_token': id_token, 'access_token': access_token if isinstance(access_token, str) else None}

    def verify(self, id_token, nonce, access_token=None):
        """Verify signature, issuer, audience, lifetime, nonce and access-token hash."""
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise OidcInvalidToken('The identity token is malformed') from None
        algorithm = header.get('alg')
        if not isinstance(algorithm, str) or algorithm not in self.config.algorithms:
            raise OidcInvalidToken('The identity token uses an unsupported algorithm')
        key = self._signing_key(header.get('kid'), algorithm)
        try:
            claims = jwt.decode(
                id_token, key=key, algorithms=[algorithm], audience=self.config.client_id,
                options={'require': ['exp', 'iat', 'iss', 'aud', 'sub'], 'verify_exp': True,
                         'verify_iss': False})
        except jwt.PyJWTError:
            raise OidcInvalidToken('The identity token is invalid') from None
        issuer = claims.get('iss')
        if not isinstance(issuer, str) or issuer.rstrip('/') != self.config.issuer:
            raise OidcInvalidToken('The identity token was issued by another provider')
        presented = claims.get('nonce')
        if not isinstance(presented, str) or not presented.isascii() or not hmac.compare_digest(presented, nonce):
            raise OidcInvalidToken('The identity token does not match the sign-in request')
        bound = claims.get('at_hash')
        if bound is not None:
            if not access_token:
                raise OidcInvalidToken('The identity token binds an access token that was not returned')
            digest = ALGORITHM_HASHES[algorithm](access_token.encode()).digest()
            expected = base64.urlsafe_b64encode(digest[:len(digest) // 2]).rstrip(b'=').decode()
            if not isinstance(bound, str) or not hmac.compare_digest(bound, expected):
                raise OidcInvalidToken('The identity token does not match the access token')
        return claims

    def _signing_key(self, kid, algorithm):
        key = self._select_key(self._jwks(), kid)
        if key is None and kid is not None:
            # Providers rotate keys without notice; one forced refresh turns a
            # stale cache into a successful login instead of a lockout.
            self._keys = None
            key = self._select_key(self._jwks(), kid)
        if key is None:
            raise OidcInvalidToken('No matching signing key was published')
        if key.get('alg') not in (None, algorithm):
            raise OidcInvalidToken('The signing key does not match the token algorithm')
        try:
            return jwt.PyJWK(key, algorithm=algorithm).key
        except jwt.PyJWTError:
            raise OidcInvalidToken('The signing key is unusable') from None

    @staticmethod
    def _select_key(keys, kid):
        for key in keys:
            if kid is None or key.get('kid') == kid:
                return key
        return None

    def _jwks(self):
        metadata = self.discover()
        current = self.clock()
        if self._keys is not None and current < self._keys_expires:
            return self._keys
        payload = self._fetch(metadata['jwks_uri'])
        keys = payload.get('keys')
        if not isinstance(keys, list) or not keys:
            raise OidcUnavailable('The identity provider published no signing keys')
        self._keys = [key for key in keys if isinstance(key, dict)]
        self._keys_expires = current + DISCOVERY_TTL
        if not self._keys:
            raise OidcUnavailable('The identity provider published no signing keys')
        return self._keys

    def map_claims(self, claims):
        """Map verified claims to a local username; roles never come from claims."""
        if not isinstance(claims, dict):
            raise OidcInvalidToken('The identity token carries no claims')
        subject = claims.get('sub')
        if not isinstance(subject, str) or not 0 < len(subject) <= 255 \
                or not subject.isprintable() or any(character.isspace() for character in subject):
            raise OidcInvalidToken('The identity token carries no usable subject')
        value = claims.get(self.config.username_claim)
        if not isinstance(value, str):
            raise OidcAccountRefused('This identity does not name a local account')
        username = value.strip().lower()
        if not USERNAME_PATTERN.fullmatch(username):
            raise OidcAccountRefused('This identity does not name a local account')
        return {'issuer': self.config.issuer, 'subject': subject, 'username': username}

    def resolve_account(self, mapped):
        """Bind a subject to an existing local account on first sign-in."""
        current = self.clock()
        identity = hashlib.sha256(f"{mapped['issuer']}\0{mapped['subject']}".encode()).hexdigest()
        with self.store.lock:
            binding = self.store.one('SELECT * FROM oidc_identities WHERE id=?', (identity,))
            if binding:
                account = self.users.by_id(binding['user_id'])
                if not account:
                    raise OidcAccountRefused('This identity is no longer linked to a local account')
                self.store.execute('UPDATE oidc_identities SET last_login=? WHERE id=?', (current, identity))
                return {'account': account, 'linked': False}
            account = self.users.find(mapped['username'])
            if not account:
                raise OidcAccountRefused('No local account matches this identity')
            if self.store.one('SELECT id FROM oidc_identities WHERE issuer=? AND user_id=?',
                              (mapped['issuer'], account['id'])):
                raise OidcAccountRefused('This local account is already linked to another identity')
            self.store.execute('INSERT INTO oidc_identities VALUES(?,?,?,?,?,?)',
                               (identity, mapped['issuer'], mapped['subject'], account['id'], current, current))
        self.store.audit(account['username'], 'auth.oidc.linked', account['id'], {'issuer': mapped['issuer']})
        return {'account': account, 'linked': True}


def callback_url(provider, request):
    if provider.config is not None and provider.config.redirect_url:
        return provider.config.redirect_url
    return str(request.base_url).rstrip('/') + CALLBACK_PATH


def install_oidc(app):
    provider = app.state.oidc
    store = app.state.store

    def refused(error) -> NoReturn:
        # Only a callback that consumed a sign-in attempt is evidence worth
        # keeping; random states must not be able to grow the audit table.
        if error.reason != 'state_invalid':
            store.audit('anonymous', 'auth.oidc.login_failed', detail={'reason': error.reason})
        raise HTTPException(error.status, str(error)) from None

    @app.get('/api/auth/oidc/status')
    def status():
        return provider.status()

    @app.get('/api/auth/oidc/start')
    def start(request: Request):
        try:
            url, state = provider.start(callback_url(provider, request))
        except OidcError as error:
            raise HTTPException(error.status, str(error)) from None
        response = RedirectResponse(url, status_code=303)
        # SameSite=Lax, unlike the session cookie: the callback is a top-level
        # navigation from the provider, but the value still proves that this
        # browser, not another one, created the attempt (login CSRF).
        response.set_cookie(STATE_COOKIE, state, max_age=PENDING_TTL, path=STATE_PATH, httponly=True,
                            samesite='lax', secure=request.url.scheme == 'https')
        return response

    @app.get('/api/auth/oidc/callback')
    def callback(request: Request, code: str = '', state: str = '', error: str = ''):
        if not provider.enabled:
            raise HTTPException(503, 'Single sign-on is not enabled')
        if error:
            refused(OidcInvalidState('Sign-in was not completed'))
        presented = request.cookies.get(STATE_COOKIE)
        if not isinstance(state, str) or not state or not isinstance(presented, str) \
                or not hmac.compare_digest(presented, state):
            refused(OidcInvalidState('Invalid or expired sign-in state'))
        try:
            result = provider.complete(state, code)
        except OidcError as failure:
            refused(failure)
        account = result['account']
        app.state.sessions.prune()
        # An identity provider redirect is still a login: a cookie presented
        # here is replaced rather than promoted, exactly like the password path.
        app.state.sessions.revoke(request.cookies.get(app.state.sessions.COOKIE))
        token, _ = app.state.sessions.create(account)
        response = RedirectResponse('/', status_code=303)
        response.delete_cookie(STATE_COOKIE, path=STATE_PATH, httponly=True, samesite='lax',
                               secure=request.url.scheme == 'https')
        app.state.sessions.set_cookie(response, token, secure=request.url.scheme == 'https')
        store.audit(account['username'], 'auth.oidc.login', account['id'],
                    {'linked': result['linked'], 'provider': 'oidc'})
        return response
