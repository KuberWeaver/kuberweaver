"""Cloudflare secrets/connector contract: scoped API tokens only, reference-only
records, vault storage through the provider write path, resolution at execution.

No Cloudflare network call happens here. The API origin is a fixed constant, never
client-controlled; live capabilities (DNS, tunnel) consume this contract later.
Credential material never enters a record, plan, audit detail, log or response.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import inspect
import json
import re
import secrets
import threading

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from .providers import provider_provenance, resolve_provider
from .secrets import SecretError, SecretValue
from .storage import now

# Fixed trusted origin; a connector record carries no endpoint field.
API_ORIGIN = 'https://api.cloudflare.com/client/v4'

# Least-privilege permission contracts per purpose, as (policy scope, resource,
# access) tuples matching Cloudflare token permission groups. The platform cannot
# inspect a token's grants without a live verify call; operators must create the
# token with exactly these permissions, scoped to the specific zone/account.
SCOPE_PROFILES = {
    'dns': (('zone', 'zone', 'read'), ('zone', 'dns', 'edit')),
    'tunnel': (('account', 'cloudflare_tunnel', 'edit'),
               ('zone', 'zone', 'read'), ('zone', 'dns', 'edit')),
}

# Scannable format (2026): cfut_/cfat_ prefix + 40 alphanumerics + 8 hex checksum.
# Legacy scoped tokens: 40 alphanumerics. A pure lowercase-hex 40-char string is
# ambiguous with the legacy Global API Key shape (37-45 lowercase hex) and is
# rejected fail-closed. Global keys (cfk_, hex), Origin CA keys and Wrangler OAuth
# tokens are never accepted: they are not least-privilege scoped API tokens.
_SCANNABLE = re.compile(r'(?:cfut_|cfat_)[A-Za-z0-9]{40}[0-9a-fA-F]{8}')
_LEGACY = re.compile(r'[A-Za-z0-9]{40}')
_LEGACY_GLOBAL = re.compile(r'[0-9a-f]{40}')
_MAX_ENVELOPE = 4096


def validate_api_token(token):
    """Accept only scoped API token shapes; the error never echoes the value."""
    if isinstance(token, str) and (_SCANNABLE.fullmatch(token)
            or (_LEGACY.fullmatch(token) and not _LEGACY_GLOBAL.fullmatch(token))):
        return token
    raise SecretError('Cloudflare credentials must be a scoped API token, not a global key')


def envelope_bytes(token):
    """Closed vault envelope: exactly one scoped token, no other fields."""
    return json.dumps({'api_token': validate_api_token(token)},
                      separators=(',', ':')).encode('utf-8')


def parse_envelope(raw):
    """Resolve-side validation: anything but one scoped token fails closed."""
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_ENVELOPE:
            raise ValueError
        def closed_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=closed_object)
        if not isinstance(payload, dict) or set(payload) != {'api_token'}:
            raise ValueError
        return validate_api_token(payload['api_token'])
    except Exception:
        raise SecretError('Cloudflare credential envelope is invalid') from None


def _writable_consumer(provider):
    info = provider.describe()
    consumer = getattr(provider, 'store', None)
    if (not isinstance(info, dict) or info.get('can_store') is not True
            or not callable(consumer) or inspect.iscoroutinefunction(consumer)
            or inspect.iscoroutinefunction(getattr(consumer, '__call__', None))):
        raise TypeError('Expected an explicitly writable synchronous source')
    inspect.signature(consumer).bind(None, None)
    return consumer


def store_credentials(provider, reference, token):
    """Vault write path: shape-check before any write, envelope, provider-owned
    readback. ``token`` is a SecretValue holding the raw scoped token string."""
    try:
        consumer = _writable_consumer(provider)
    except (Exception, SystemExit):
        raise SecretError('Cloudflare secret source is read-only or incompatible') from None
    try:
        raw = token.bytes()
        value = validate_api_token(raw.decode('utf-8'))
        wrapped = SecretValue(envelope_bytes(value))
        try:
            result = consumer(reference, wrapped)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError('Expected synchronous credential storage')
        finally:
            wrapped.close()
    except Exception:
        raise SecretError('Cloudflare credential storage unavailable') from None


@contextmanager
def access(provider, connector):
    """Execution-time resolution: yield (connector, token bytes), then wipe."""
    if not isinstance(connector, dict) or connector.get('enabled') is not True:
        raise SecretError('Cloudflare connector is disabled')
    stack = ExitStack()
    try:
        secret = stack.enter_context(provider.resolve(connector['secret_ref']))
        if not isinstance(secret, SecretValue):
            raise TypeError('Expected SecretValue')
        stack.callback(secret.close)
        raw = secret.bytes()
        if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_ENVELOPE:
            raise ValueError('Invalid credential size')
        token = parse_envelope(raw).encode('utf-8')
    except (Exception, SystemExit):
        try:
            stack.close()
        except (Exception, SystemExit):
            pass
        raise SecretError('Cloudflare connector credentials unavailable') from None
    try:
        yield connector, token
    finally:
        del token
        stack.close()


class CloudflareConnectorSpec(BaseModel):
    """Reference-only connector record; there is no endpoint or secret field."""
    model_config = ConfigDict(extra='forbid', strict=True)
    label: str = Field(min_length=1, max_length=80)
    purpose: str
    secret_ref: str = Field(max_length=128)
    secrets_provider: str
    account_id: str | None = None
    enabled: bool = True

    @field_validator('purpose')
    @classmethod
    def known_purpose(cls, value):
        if value not in SCOPE_PROFILES:
            raise ValueError('Unknown Cloudflare purpose')
        return value

    @field_validator('secrets_provider')
    @classmethod
    def registered_name(cls, value):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value):
            raise ValueError('Invalid registry identifier')
        return value

    @field_validator('secret_ref')
    @classmethod
    def logical_reference(cls, value):
        if not re.fullmatch(r'[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)*', value):
            raise ValueError('Invalid logical reference')
        return value

    @field_validator('account_id')
    @classmethod
    def account_identifier(cls, value):
        if value is not None and not re.fullmatch(r'[0-9a-f]{32}', value):
            raise ValueError('Invalid account identifier')
        return value


class CloudflareConnectorPatch(CloudflareConnectorSpec):
    """Unset fields are absent changes; no field accepts explicit null."""
    revision: int = Field(ge=1)
    label: str = Field(default=None, min_length=1, max_length=80)  # type: ignore[assignment]
    purpose: str = Field(default=None)  # type: ignore[assignment]
    secret_ref: str = Field(default=None, max_length=128)  # type: ignore[assignment]
    secrets_provider: str = Field(default=None)  # type: ignore[assignment]
    account_id: str = Field(default=None)  # type: ignore[assignment]
    enabled: bool = Field(default=None)  # type: ignore[assignment]


class _Credential(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    api_token: str = Field(min_length=1, max_length=128)


async def _request_json(request, message):
    try:
        return await request.json()
    except (ValueError, UnicodeError):
        raise HTTPException(422, message) from None


class CloudflareConnectors:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self._operation_locks = {}
        self._credential_writes = {}
        self.store.execute('CREATE TABLE IF NOT EXISTS cloudflare_connectors(id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def get(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise HTTPException(404, 'Unknown Cloudflare connector')
        row = self.store.one('SELECT body FROM cloudflare_connectors WHERE id=?', (identity,))
        if row is None:
            raise HTTPException(404, 'Unknown Cloudflare connector')
        return json.loads(row['body'])

    def list(self):
        return [json.loads(row['body']) for row in self.store.all(
            'SELECT body FROM cloudflare_connectors ORDER BY rowid DESC')]

    def _check_reference_available(self, value, identity=None):
        owner = self._credential_writes.get((value['secrets_provider'], value['secret_ref']))
        if owner is not None and owner != identity:
            raise HTTPException(409, 'Credential reference is being updated')
        if any(record['id'] != identity
               and (record['secrets_provider'], record['secret_ref'])
               == (value['secrets_provider'], value['secret_ref'])
               for record in self.list()):
            raise HTTPException(409, 'Credential reference is shared by another Cloudflare connector')

    def create(self, spec, actor):
        value = CloudflareConnectorSpec.model_validate(spec).model_dump()
        record = {**value, 'id': secrets.token_hex(16), 'revision': 1}
        with self.store.lock:
            self._check_reference_available(value)
            self.store.execute('INSERT INTO cloudflare_connectors(id,body) VALUES(?,?)',
                               (record['id'], json.dumps(record)))
            self.store.audit(actor['username'], 'cloudflare_connector.created', record['id'])
            return self.get(record['id'])

    def update(self, identity, spec, actor):
        with self.store.lock:
            current = self.get(identity)
            try:
                changes = CloudflareConnectorPatch.model_validate(spec).model_dump(exclude_unset=True)
                patch = {key: value for key, value in changes.items() if key != 'revision'}
                value = CloudflareConnectorSpec.model_validate({
                    **{key: current[key] for key in CloudflareConnectorSpec.model_fields}, **patch}).model_dump()
            except ValidationError:
                raise HTTPException(422, 'Invalid Cloudflare connector specification') from None
            if changes['revision'] != current['revision']:
                raise HTTPException(409, 'Cloudflare connector revision changed')
            self._check_reference_available(value, identity)
            record = {**value, 'id': identity, 'revision': current['revision'] + 1}
            self.store.execute('UPDATE cloudflare_connectors SET body=? WHERE id=?', (json.dumps(record), identity))
            self.store.audit(actor['username'], 'cloudflare_connector.updated', identity)
            return self.get(identity)

    def fingerprint(self, identity):
        """Pin references and provider provenance; never resolve or hash values."""
        connector = self.get(identity)
        try:
            source = resolve_provider(self.app, 'secrets', connector['secrets_provider'])
            value = {key: connector[key] for key in CloudflareConnectorSpec.model_fields}
            value.update(revision=connector['revision'], secrets_source=provider_provenance(
                self.app, 'secrets', connector['secrets_provider'], provider=source))
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        except (Exception, SystemExit):
            raise HTTPException(503, 'Cloudflare connector provenance unavailable') from None

    @contextmanager
    def access(self, identity):
        connector = self.get(identity)
        if not connector['enabled']:
            raise HTTPException(409, 'Cloudflare connector is disabled')
        stack = ExitStack()
        try:
            source = resolve_provider(self.app, 'secrets', connector['secrets_provider'])
            record, token = stack.enter_context(access(source, connector))
        except (Exception, SystemExit):
            try:
                stack.close()
            except (Exception, SystemExit):
                pass
            raise HTTPException(503, 'Cloudflare connector credentials unavailable') from None
        try:
            yield record, token
        finally:
            del token
            try:
                stack.close()
            except (Exception, SystemExit):
                raise HTTPException(503, 'Cloudflare connector credentials unavailable') from None

    def store_credentials(self, identity, credentials, actor):
        with self._operation(identity) as connector:
            key = (connector['secrets_provider'], connector['secret_ref'])
            with self.store.lock:
                self._check_reference_available(connector, identity)
                self._credential_writes[key] = identity
            try:
                try:
                    source = resolve_provider(self.app, 'secrets', connector['secrets_provider'])
                    _writable_consumer(source)
                except HTTPException:
                    raise
                except (Exception, SystemExit):
                    raise HTTPException(409, 'Selected secret source is read-only') from None
                try:
                    validate_api_token(credentials.api_token)
                except SecretError:
                    raise HTTPException(422, 'Invalid Cloudflare credentials') from None
                value = SecretValue(credentials.api_token.encode('utf-8'))
                try:
                    store_credentials(source, connector['secret_ref'], value)
                except (Exception, SystemExit):
                    raise HTTPException(503, 'Cloudflare credential storage unavailable') from None
                finally:
                    value.close()
                with self.store.lock:
                    current = self.get(identity)
                    if current['revision'] != connector['revision']:
                        raise HTTPException(409, 'Cloudflare connector revision changed during credential storage')
                    current.update(revision=current['revision'] + 1)
                    self.store.execute('UPDATE cloudflare_connectors SET body=? WHERE id=?',
                                       (json.dumps(current), identity))
                    self.store.audit(actor['username'], 'cloudflare_connector.credentials_stored', identity)
                    self.get(identity)  # Read back the exact updated target before reporting success.
                return {'stored': True, 'connector_id': identity}
            finally:
                with self.store.lock:
                    self._credential_writes.pop(key, None)

    @contextmanager
    def _operation(self, identity):
        with self.store.lock:
            connector = self.get(identity)
            operation_lock = self._operation_locks.setdefault(identity, threading.Lock())
            if not operation_lock.acquire(blocking=False):
                raise HTTPException(409, 'A Cloudflare connector operation is already running')
        try:
            yield connector
        finally:
            operation_lock.release()


def install_cloudflare_api(app):
    if getattr(app.state, '_cloudflare_api_installed', False):
        return
    connectors = app.state.cloudflare_connectors = CloudflareConnectors(app)
    from .cloudflare_dns import install_cloudflare_dns_api
    install_cloudflare_dns_api(app)
    from .cloudflare_tunnel import install_cloudflare_tunnel_api
    install_cloudflare_tunnel_api(app)
    original_count = len(app.router.routes)

    @app.get('/api/cloudflare/connectors')
    def list_connectors(current=Depends(app.state.user_dependency)):
        return connectors.list()

    @app.post('/api/cloudflare/connectors', status_code=201)
    async def create_connector(request: Request, current=Depends(app.state.role_dependency('admin'))):
        try:
            value = CloudflareConnectorSpec.model_validate(
                await _request_json(request, 'Invalid Cloudflare connector specification'))
        except ValidationError:
            raise HTTPException(422, 'Invalid Cloudflare connector specification') from None
        return connectors.create(value, current)

    @app.get('/api/cloudflare/connectors/{identity}')
    def get_connector(identity: str, current=Depends(app.state.user_dependency)):
        return connectors.get(identity)

    @app.patch('/api/cloudflare/connectors/{identity}')
    async def update_connector(identity: str, request: Request,
                               current=Depends(app.state.role_dependency('admin'))):
        return connectors.update(identity, await _request_json(request, 'Invalid Cloudflare connector specification'), current)

    @app.put('/api/cloudflare/connectors/{identity}/credentials')
    async def store_credentials(identity: str, request: Request,
                                current=Depends(app.state.role_dependency('admin'))):
        if current.get('principal_type') == 'agent' or getattr(request.state, 'agent', None):
            raise HTTPException(403, 'Human administrator session required')
        try:
            credentials = _Credential.model_validate(await _request_json(request, 'Invalid Cloudflare credentials'))
        except ValidationError:
            raise HTTPException(422, 'Invalid Cloudflare credentials') from None
        return await run_in_threadpool(connectors.store_credentials, identity, credentials, current)

    # Tests and optional installers can run after the SPA catch-all exists.
    added = app.router.routes[original_count:]
    del app.router.routes[original_count:]
    before_spa = next((i for i, route in enumerate(app.router.routes)
                       if getattr(route, 'path', None) == '/{asset_path:path}'), len(app.router.routes))
    app.router.routes[before_spa:before_spa] = added
    app.state._cloudflare_api_installed = True