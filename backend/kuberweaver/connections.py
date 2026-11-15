"""Small hypervisor connection service; no vendor SDK or implicit remote reads."""
from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack, contextmanager
import hashlib
import inspect
import ipaddress
import json
import re
import secrets
import threading
from urllib.parse import urlsplit
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from .providers import provider_provenance, resolve_provider
from .secrets import SecretValue
from .storage import now


class ConnectionSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    label: str = Field(min_length=1, max_length=80)
    driver: str
    endpoint: str = Field(max_length=256)
    secret_ref: str = Field(max_length=128)
    secrets_provider: str
    tls_sha256: str | None = None
    enabled: bool = True

    @field_validator('driver', 'secrets_provider')
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

    @field_validator('tls_sha256')
    @classmethod
    def certificate_digest(cls, value):
        if value is not None and not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError('Invalid SHA-256 digest')
        return value

    @field_validator('endpoint')
    @classmethod
    def https_origin(cls, value):
        try:
            if (not value.startswith('https://') or any(ord(c) < 33 or ord(c) > 126 for c in value)
                    or any(c in value for c in ('?', '#', '%'))):
                raise ValueError
            parsed = urlsplit(value)
            address = ipaddress.ip_address(parsed.hostname)
            addresses = [address]
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                addresses.append(address.ipv4_mapped)
            if (parsed.username is not None or parsed.password is not None or parsed.path
                    or parsed.query or parsed.fragment or parsed.netloc.endswith(':')
                    or parsed.port is not None and not 1 <= parsed.port <= 65535
                    or any(a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified
                           for a in addresses)):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('Expected an HTTPS origin on an allowed explicit IP address') from None
        return value


class ConnectionPatch(ConnectionSpec):
    """Unset fields are absent changes; only tls_sha256 accepts explicit null."""
    revision: int = Field(ge=1)
    # Pydantic keeps these unset defaults out of model_dump(exclude_unset=True);
    # their non-null annotations deliberately reject explicitly supplied null.
    label: str = Field(default=None, min_length=1, max_length=80)  # type: ignore[assignment]
    driver: str = Field(default=None)  # type: ignore[assignment]
    endpoint: str = Field(default=None, max_length=256)  # type: ignore[assignment]
    secret_ref: str = Field(default=None, max_length=128)  # type: ignore[assignment]
    secrets_provider: str = Field(default=None)  # type: ignore[assignment]
    enabled: bool = Field(default=None)  # type: ignore[assignment]


_Text = Annotated[str, Field(min_length=1, max_length=256)]
_Nonnegative = Annotated[int, Field(ge=0, le=2**63 - 1)]
_Positive = Annotated[int, Field(ge=1, le=2**40)]


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class _Credentials(_Closed):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=4096)


class _Server(_Closed):
    name: _Text
    version: _Text
    api_type: _Text
    host_id: _Text | None = None
    write_api_supported: bool | None
    build: _Text | None = None
    api_version: _Text | None = None
    license_edition: _Text | None = None


class _Named(_Closed):
    id: _Text
    name: _Text


class _Datastore(_Named):
    capacity_bytes: _Nonnegative
    free_bytes: _Nonnegative
    accessible: bool


class _VirtualMachine(_Named):
    uuid: _Text | None
    power_state: _Text
    cpu: _Positive | None
    memory_mb: _Positive | None


class _Inventory(_Closed):
    server: _Server
    datastores: list[_Datastore] = Field(max_length=1000)
    networks: list[_Named] = Field(max_length=1000)
    resource_pools: list[_Named] = Field(max_length=1000)
    virtual_machines: list[_VirtualMachine] = Field(max_length=1000)


def _validated_inventory(payload, credentials):
    if inspect.isawaitable(payload):
        if inspect.iscoroutine(payload):
            payload.close()
        raise ValueError('Expected synchronous inventory')
    value = _Inventory.model_validate(payload).model_dump(exclude_unset=True)
    markers = []
    username = None
    try:
        raw = credentials.decode('utf-8')
        markers.append(raw)
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            username = decoded.get('username')
            password = decoded.get('password')
            if isinstance(password, str) and password:
                markers.append(password)
    except (UnicodeError, ValueError):
        pass

    def contains_secret(item):
        if isinstance(item, str):
            # Usernames may be substrings of ordinary resource IDs and names.
            return item == username or any(marker in item for marker in markers)
        if isinstance(item, dict):
            return any(contains_secret(child) for child in item.values())
        if isinstance(item, list):
            return any(contains_secret(child) for child in item)
        return False

    if contains_secret(value):
        raise ValueError('Inventory contains credential material')
    return value


async def _request_json(request, message):
    try:
        return await request.json()
    except (ValueError, UnicodeError):
        raise HTTPException(422, message) from None


class Connections:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self._operation_locks = {}
        self._credential_writes = {}
        self.store.execute('CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def get(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise HTTPException(404, 'Unknown connection')
        row = self.store.one('SELECT body FROM connections WHERE id=?', (identity,))
        if row is None:
            raise HTTPException(404, 'Unknown connection')
        return json.loads(row['body'])

    def list(self):
        return [json.loads(row['body']) for row in self.store.all('SELECT body FROM connections ORDER BY rowid DESC')]

    def _check_reference_available(self, value, identity=None):
        owner = self._credential_writes.get((value['secrets_provider'], value['secret_ref']))
        if owner is not None and owner != identity:
            raise HTTPException(409, 'Credential reference is being updated')

    def create(self, spec, actor):
        value = ConnectionSpec.model_validate(spec).model_dump()
        record = {**value, 'id': secrets.token_hex(16), 'revision': 1, 'observation': None}
        with self.store.lock:
            self._check_reference_available(value)
            self.store.execute('INSERT INTO connections(id,body) VALUES(?,?)',
                               (record['id'], json.dumps(record)))
            self.store.audit(actor['username'], 'connection.created', record['id'])
            return self.get(record['id'])

    def update(self, identity, spec, actor):
        with self.store.lock:
            current = self.get(identity)
            try:
                changes = ConnectionPatch.model_validate(spec).model_dump(exclude_unset=True)
                patch = {key: value for key, value in changes.items() if key != 'revision'}
                value = ConnectionSpec.model_validate({
                    **{key: current[key] for key in ConnectionSpec.model_fields}, **patch}).model_dump()
            except ValidationError:
                raise HTTPException(422, 'Invalid connection specification') from None
            if changes['revision'] != current['revision']:
                raise HTTPException(409, 'Connection revision changed')
            self._check_reference_available(value, identity)
            record = {**value, 'id': identity, 'revision': current['revision'] + 1, 'observation': None}
            self.store.execute('UPDATE connections SET body=? WHERE id=?', (json.dumps(record), identity))
            self.store.audit(actor['username'], 'connection.updated', identity)
            return self.get(identity)

    def fingerprint(self, identity):
        connection = self.get(identity)
        try:
            driver = self.driver(connection)
            source = resolve_provider(self.app, 'secrets', connection['secrets_provider'])
            value = {key: connection[key] for key in ConnectionSpec.model_fields}
            value.update(revision=connection['revision'], driver_source=provider_provenance(
                self.app, 'hypervisor', connection['driver'], provider=driver),
                secrets_source=provider_provenance(self.app, 'secrets', connection['secrets_provider'], provider=source))
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        except (Exception, SystemExit):
            raise HTTPException(503, 'Connection provenance unavailable') from None

    def describe(self):
        registry = self.app.state.registry
        records = self.list()
        driver_rows = {row['name']: row for row in registry.list('hypervisor')}
        source_names = {row['name'] for row in registry.list('secrets')}
        for record in records:
            driver_rows.setdefault(record['driver'], {})
            source_names.add(record['secrets_provider'])
        drivers, sources = [], []
        for name, row in sorted(driver_rows.items()):
            try:
                self.driver({'driver': name})
                available = True
            except HTTPException:
                available = False
            label = row.get('label', name)
            if not isinstance(label, str) or not 1 <= len(label) <= 80:
                label = name
            drivers.append({'id': name, 'label': label, 'available': available})
        for name in sorted(source_names):
            descriptor = {'id': name, 'available': False, 'can_store': False, 'references': []}
            try:
                provider = resolve_provider(self.app, 'secrets', name)
                describe = getattr(provider, 'describe', None)
                info = describe() if callable(describe) else {}
                if not isinstance(info, dict):
                    raise ValueError
                names = []
                for key in ('approved_names', 'names', 'references'):
                    values = info.get(key, [])
                    if not isinstance(values, list) or len(values) > 1000:
                        raise ValueError
                    for value in values:
                        if not isinstance(value, str) or len(value) > 128:
                            raise ValueError
                        ConnectionSpec.logical_reference(value)
                    names.extend(values)
                descriptor.update(available=True, references=sorted(set(names)))
                consumer = getattr(provider, 'store', None)
                if (info.get('can_store') is True and callable(consumer)
                        and not inspect.iscoroutinefunction(consumer)
                        and not inspect.iscoroutinefunction(getattr(consumer, '__call__', None))):
                    inspect.signature(consumer).bind(None, None)
                    descriptor['can_store'] = True
            except (Exception, SystemExit):
                descriptor.update(available=False, can_store=False, references=[])
            sources.append(descriptor)
        return {'drivers': drivers, 'secret_sources': sources}

    def driver(self, connection):
        try:
            registry = self.app.state.registry
            provider = registry.get('hypervisor', connection['driver'])
            metadata = next(row for row in registry.list('hypervisor') if row['name'] == connection['driver'])
            if metadata.get('available', True) is not True:
                raise TypeError
            consumer = getattr(provider, 'discover', None)
            if (not callable(consumer) or inspect.iscoroutinefunction(consumer)
                    or inspect.iscoroutinefunction(getattr(consumer, '__call__', None))):
                raise TypeError
            inspect.signature(consumer).bind(None, None)
            return provider
        except (Exception, SystemExit):
            raise HTTPException(503, 'Connection driver unavailable or incompatible') from None

    @contextmanager
    def access(self, identity):
        connection = self.get(identity)
        if not connection['enabled']:
            raise HTTPException(409, 'Connection is disabled')
        stack = ExitStack()
        try:
            source = resolve_provider(self.app, 'secrets', connection['secrets_provider'])
            secret = stack.enter_context(source.resolve(connection['secret_ref']))
            if not isinstance(secret, SecretValue):
                raise TypeError('Expected SecretValue')
            stack.callback(secret.close)
            credentials = secret.bytes()
            if not isinstance(credentials, bytes) or not 0 < len(credentials) <= 65536:
                raise ValueError('Invalid credential size')
        except (Exception, SystemExit):
            try:
                stack.close()
            except (Exception, SystemExit):
                pass
            raise HTTPException(503, 'Connection credentials unavailable') from None
        try:
            yield connection, credentials
        finally:
            del credentials
            try:
                stack.close()
            except (Exception, SystemExit):
                raise HTTPException(503, 'Connection credentials unavailable') from None

    def store_credentials(self, identity, credentials, actor):
        with self._operation(identity) as connection:
            key = (connection['secrets_provider'], connection['secret_ref'])
            with self.store.lock:
                if any(record['id'] != identity and (record['secrets_provider'], record['secret_ref']) == key
                       for record in self.list()):
                    raise HTTPException(409, 'Credential reference is shared by another connection')
                self._credential_writes[key] = identity
            try:
                try:
                    source = resolve_provider(self.app, 'secrets', connection['secrets_provider'])
                    info = source.describe()
                except (Exception, SystemExit):
                    raise HTTPException(503, 'Connection credential storage unavailable') from None
                try:
                    consumer = getattr(source, 'store', None)
                    if (not isinstance(info, dict) or info.get('can_store') is not True
                            or not callable(consumer) or inspect.iscoroutinefunction(consumer)
                            or inspect.iscoroutinefunction(getattr(consumer, '__call__', None))):
                        raise TypeError('Expected an explicitly writable synchronous source')
                    inspect.signature(consumer).bind(None, None)
                except (Exception, SystemExit):
                    raise HTTPException(409, 'Selected secret source is read-only') from None
                value = SecretValue(json.dumps(credentials.model_dump(), ensure_ascii=False).encode())
                try:
                    # The selected source owns its write/readback protocol.
                    result = consumer(connection['secret_ref'], value)
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise TypeError('Expected synchronous credential storage')
                except (Exception, SystemExit):
                    raise HTTPException(503, 'Connection credential storage unavailable') from None
                finally:
                    value.close()
                with self.store.lock:
                    current = self.get(identity)
                    if current['revision'] != connection['revision']:
                        raise HTTPException(409, 'Connection revision changed during credential storage')
                    current.update(revision=current['revision'] + 1, observation=None)
                    self.store.execute('UPDATE connections SET body=? WHERE id=?', (json.dumps(current), identity))
                    self.store.audit(actor['username'], 'connection.credentials_stored', identity)
                    self.get(identity)  # Read back the exact updated target before reporting success.
                return {'stored': True, 'connection_id': identity}
            finally:
                with self.store.lock:
                    self._credential_writes.pop(key, None)

    @contextmanager
    def _operation(self, identity):
        with self.store.lock:
            connection = self.get(identity)
            operation_lock = self._operation_locks.setdefault(identity, threading.Lock())
            if not operation_lock.acquire(blocking=False):
                raise HTTPException(409, 'A connection operation is already running')
        try:
            yield connection
        finally:
            operation_lock.release()

    def discover(self, identity, actor):
        with self._operation(identity) as connection:
            if not connection['enabled']:
                raise HTTPException(409, 'Connection is disabled')
            observed_inventory, error = None, None
            try:
                provider = self.driver(connection)
                with self.access(identity) as (accessed, credentials):
                    if accessed['revision'] != connection['revision']:
                        raise ValueError('Connection revision changed before access')
                    observed_inventory = _validated_inventory(
                        provider.discover(deepcopy(connection), credentials), credentials)
            except (Exception, SystemExit):
                observed_inventory, error = None, 'Connection discovery unavailable'
            observation = {'connection_id': identity, 'revision': connection['revision'],
                           'status': 'unavailable' if error else 'reachable', 'observed_at': now(),
                           'inventory': observed_inventory, 'error': error}
            with self.store.lock:
                record = self.get(identity)
                if record['revision'] != connection['revision']:
                    raise HTTPException(409, 'Connection revision changed during discovery')
                record['observation'] = observation
                self.store.execute('UPDATE connections SET body=? WHERE id=?', (json.dumps(record), identity))
                self.store.audit(actor['username'], 'connection.discovered', identity, {'status': observation['status']})
                return self.get(identity)['observation']


def install_connections_api(app):
    if getattr(app.state, '_connections_api_installed', False):
        return
    connections = app.state.connections = Connections(app)
    original_count = len(app.router.routes)

    @app.get('/api/connection-drivers')
    def list_drivers(current=Depends(app.state.user_dependency)):
        return connections.describe()

    @app.get('/api/connections')
    def list_connections(current=Depends(app.state.user_dependency)):
        return connections.list()

    @app.post('/api/connections', status_code=201)
    async def create_connection(request: Request, current=Depends(app.state.role_dependency('admin'))):
        try:
            value = ConnectionSpec.model_validate(await _request_json(request, 'Invalid connection specification'))
        except ValidationError:
            raise HTTPException(422, 'Invalid connection specification') from None
        return connections.create(value, current)

    @app.get('/api/connections/{identity}')
    def get_connection(identity: str, current=Depends(app.state.user_dependency)):
        return connections.get(identity)

    @app.patch('/api/connections/{identity}')
    async def update_connection(identity: str, request: Request,
                                current=Depends(app.state.role_dependency('admin'))):
        return connections.update(identity, await _request_json(request, 'Invalid connection specification'), current)

    @app.post('/api/connections/{identity}/test')
    @app.post('/api/connections/{identity}/discover')
    def discover_connection(identity: str, current=Depends(app.state.role_dependency('admin', 'operator'))):
        return connections.discover(identity, current)

    @app.put('/api/connections/{identity}/credentials')
    async def store_credentials(identity: str, request: Request,
                                current=Depends(app.state.role_dependency('admin'))):
        if current.get('principal_type') == 'agent' or getattr(request.state, 'agent', None):
            raise HTTPException(403, 'Human administrator session required')
        try:
            credentials = _Credentials.model_validate(await _request_json(request, 'Invalid connection credentials'))
        except ValidationError:
            raise HTTPException(422, 'Invalid connection credentials') from None
        return await run_in_threadpool(connections.store_credentials, identity, credentials, current)

    # Tests and optional installers can run after the SPA catch-all exists.
    added = app.router.routes[original_count:]
    del app.router.routes[original_count:]
    before_spa = next((i for i, route in enumerate(app.router.routes)
                       if getattr(route, 'path', None) == '/{asset_path:path}'), len(app.router.routes))
    app.router.routes[before_spa:before_spa] = added
    app.state._connections_api_installed = True
