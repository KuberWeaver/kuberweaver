"""Shared Ed25519 identities. Private material belongs exclusively to the selected vault.

Disabling blocks future use; it does not revoke keys already installed on guests.
Pending records deliberately survive uncertain vault/metadata failures for recovery.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
import hashlib
import inspect
import json
import os
import stat
import re
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from .providers import provider_provenance, resolve_provider
from .secrets import SecretValue
from .storage import now, check_password

_PUBLIC = ('id', 'name', 'algorithm', 'public_key', 'fingerprint', 'enabled', 'created_at', 'created_by')


def _public(record):
    return {field: record[field] for field in _PUBLIC}


def _fingerprint(public):
    parsed = serialization.load_ssh_public_key(public.encode('ascii'))
    canonical = parsed.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode('ascii')
    if not canonical.startswith('ssh-ed25519 ') or canonical != public:
        raise ValueError('Expected canonical Ed25519 public key')
    return 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(public.split()[1], validate=True)).digest()).decode().rstrip('=')


def _public_bytes(private):
    if not isinstance(private, Ed25519PrivateKey):
        raise ValueError('Expected Ed25519')
    return private.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)


async def _body(request):
    try:
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise HTTPException(422, 'Invalid SSH key request') from None


class SSHKeys:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self._export_attempts = {}
        self.store.execute('CREATE TABLE IF NOT EXISTS ssh_keys(id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def _record(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise HTTPException(404, 'Unknown SSH key')
        row = self.store.one('SELECT body FROM ssh_keys WHERE id=?', (identity,))
        if row is None:
            raise HTTPException(404, 'Unknown SSH key')
        record = json.loads(row['body'])
        if record.get('state') != 'ready':
            raise HTTPException(503, 'SSH key creation requires administrator review')
        try:
            if (record['id'] != identity or record['algorithm'] != 'ed25519'
                    or record['fingerprint'] != _fingerprint(record['public_key'])
                    or type(record['enabled']) is not bool
                    or type(record['revision']) is not int or record['revision'] < 1):
                raise ValueError('Invalid metadata')
        except (Exception, SystemExit):
            raise HTTPException(503, 'SSH key metadata is inconsistent') from None
        return record

    def get(self, identity):
        return _public(self._record(identity))

    def list(self):
        rows = [json.loads(row['body']) for row in self.store.all('SELECT body FROM ssh_keys ORDER BY id')]
        return [_public(row) for row in sorted(rows, key=lambda row: (row['created_at'], row['id']), reverse=True)
                if row.get('state') == 'ready']

    def usage(self, identity):
        self._record(identity)
        def references(value):
            if isinstance(value, dict):
                config = value.get('config')
                if isinstance(config, dict) and config.get('ssh_key_id') == identity:
                    return True
                return any(references(child) for child in value.values())
            if isinstance(value, list):
                return any(references(child) for child in value)
            return False
        result = {}
        for table in ('jobs', 'blueprints'):
            found = []
            for row in self.store.all(f'SELECT id,body FROM {table} ORDER BY id'):
                value = json.loads(row['body'])
                if references(value):
                    name = value.get('name') or value.get('graph', {}).get('name') or row['id']
                    found.append({'id': row['id'], 'name': name})
            result[table] = found
        return result

    def _source(self, record):
        try:
            provider = resolve_provider(self.app, 'secrets', record['secrets_provider'])
            source = provider_provenance(self.app, 'secrets', record['secrets_provider'], provider=provider)
            if source != record['secrets_source']:
                raise ValueError('Changed source')
            return provider
        except (Exception, SystemExit):
            raise HTTPException(503, 'SSH key secret source unavailable or changed') from None

    def _creation_provider(self):
        path = self.store.path / 'config' / 'ssh-keys.json'
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return self.app.state.provider_bindings['secrets']
        with os.fdopen(fd) as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid() or info.st_size > 4096):
                raise ValueError('Invalid key vault configuration')
            data = json.loads(source.read(4097))
        if (not isinstance(data, dict) or set(data) != {'secrets_provider'}
                or not isinstance(data['secrets_provider'], str)
                or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', data['secrets_provider'])):
            raise ValueError('Invalid key vault configuration')
        return data['secrets_provider']

    def create(self, name, actor):
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or not name.strip() or any(ord(c) < 32 for c in name):
            raise HTTPException(422, 'Invalid SSH key name')
        try:
            selected = self._creation_provider()
            provider = resolve_provider(self.app, 'secrets', selected)
            info = provider.describe()
            consumer = getattr(provider, 'store', None)
            if (not isinstance(info, dict) or info.get('can_store') is not True or not callable(consumer)
                    or inspect.iscoroutinefunction(consumer)
                    or inspect.iscoroutinefunction(getattr(consumer, '__call__', None))):
                raise ValueError('Expected writable synchronous vault')
            inspect.signature(consumer).bind(None, None)
            source = provider_provenance(self.app, 'secrets', selected, provider=provider)
        except (Exception, SystemExit):
            raise HTTPException(503, 'A writable SSH key secret source is required') from None
        private = Ed25519PrivateKey.generate()
        public = _public_bytes(private).decode('ascii')
        fingerprint = _fingerprint(public)
        record = {'id': secrets.token_hex(16), 'name': name, 'algorithm': 'ed25519',
                  'public_key': public, 'fingerprint': fingerprint, 'enabled': True,
                  'created_at': now(), 'created_by': actor['username'], 'revision': 1,
                  'secrets_provider': selected, 'secrets_source': source,
                  'secret_ref': 'ssh-keys/key-' + secrets.token_hex(32), 'state': 'pending'}
        value = SecretValue(private.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()))
        del private
        try:
            with self.store.lock:
                # Persist recovery identity BEFORE the irreversible vault write. Never delete
                # an uncertain publication: the secrets protocol intentionally has no delete.
                self.store.execute('INSERT INTO ssh_keys(id,body) VALUES(?,?)', (record['id'], json.dumps(record)))
                try:
                    result = consumer(record['secret_ref'], value)
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise TypeError('Expected synchronous storage')
                    self._source(record)
                    with provider.resolve(record['secret_ref']) as restored:
                        if not isinstance(restored, SecretValue):
                            raise TypeError('Expected SecretValue')
                        try:
                            if restored.bytes() != value.bytes():
                                raise ValueError('Readback mismatch')
                        finally:
                            restored.close()
                    record['state'] = 'ready'
                    self.store.execute('UPDATE ssh_keys SET body=? WHERE id=?', (json.dumps(record), record['id']))
                    self.store.audit(actor['username'], 'ssh_key.created', record['id'])
                    return self.get(record['id'])
                except (Exception, SystemExit):
                    raise HTTPException(503, 'SSH key storage could not be confirmed; administrator review required') from None
        finally:
            value.close()

    def binding(self, identity):
        """Local, deterministic approval binding; no vault reads or usage metadata."""
        with self.store.lock:
            record = self._record(identity)
            if not record['enabled']:
                raise HTTPException(409, 'SSH key is disabled')
            self._source(record)
            return {**_public(record), 'revision': record['revision'],
                    'secrets_provider': record['secrets_provider'], 'secrets_source': record['secrets_source']}

    def public_key(self, identity):
        return self.binding(identity)['public_key']

    def update(self, identity, enabled, actor):
        if type(enabled) is not bool:
            raise HTTPException(422, 'Invalid SSH key request')
        with self.store.lock:
            record = self._record(identity)
            if record['enabled'] != enabled:
                record.update(enabled=enabled, revision=record['revision'] + 1)
                self.store.execute('UPDATE ssh_keys SET body=? WHERE id=?', (json.dumps(record), identity))
                self.store.audit(actor['username'], 'ssh_key.enabled' if enabled else 'ssh_key.disabled', identity)
            return self.get(identity)

    def export(self, identity, password, actor):
        if not isinstance(password, str) or not 1 <= len(password) <= 4096:
            raise HTTPException(422, 'Invalid SSH key export request')
        with self.store.lock:
            timestamp = time.monotonic()
            attempts = self._export_attempts.setdefault(actor['id'], [])
            attempts[:] = [stamp for stamp in attempts if timestamp - stamp < 60]
            if len(attempts) >= 5:
                raise HTTPException(429, 'Too many SSH key export attempts; retry in one minute',
                                    headers={'Retry-After': '60'})
            account = self.store.one('SELECT * FROM users WHERE id=?', (actor['id'],))
            if not account or account['role'] != 'admin' or not check_password(password, account['password_hash']):
                attempts.append(timestamp)
                self.store.audit(actor['username'], 'ssh_key.export_denied')
                raise HTTPException(401, 'Administrator reauthentication failed')
            with self.private_key(identity) as secret:
                material = secret.bytes()
            self.store.audit(actor['username'], 'ssh_key.exported', identity)
            return Response(material, media_type='application/octet-stream', headers={
                'Content-Disposition': 'attachment; filename="ssh-key-' + identity + '"',
                'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'Expires': '0',
                'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                'Content-Security-Policy': "default-src 'none'; sandbox"})

    @contextmanager
    def private_key(self, identity):
        record = self._record(identity)
        if not record['enabled']:
            raise HTTPException(409, 'SSH key is disabled')
        stack = ExitStack()
        try:
            source = self._source(record)
            secret = stack.enter_context(source.resolve(record['secret_ref']))
            if not isinstance(secret, SecretValue):
                raise TypeError('Expected SecretValue')
            stack.callback(secret.close)
            raw = secret.bytes()
            if not 0 < len(raw) <= 16384:
                raise ValueError('Invalid key size')
            private = serialization.load_ssh_private_key(raw, password=None)
            if _public_bytes(private).decode() != record['public_key']:
                raise ValueError('Public/private mismatch')
            del private, raw
        except (Exception, SystemExit):
            try:
                stack.close()
            except (Exception, SystemExit):
                pass
            raise HTTPException(503, 'SSH key private material unavailable or inconsistent') from None
        try:
            yield secret
        finally:
            try:
                stack.close()
            except (Exception, SystemExit):
                raise HTTPException(503, 'SSH key private material unavailable') from None


def install_ssh_keys_api(app):
    if getattr(app.state, '_ssh_keys_api_installed', False):
        return
    service = app.state.ssh_keys = SSHKeys(app)
    original_count = len(app.router.routes)

    def human_admin(request: Request, current=Depends(app.state.role_dependency('admin'))):
        if current.get('principal_type') == 'agent' or getattr(request.state, 'agent', None):
            raise HTTPException(403, 'Human administrator session required')
        return current

    @app.get('/api/ssh-keys')
    def listing(current=Depends(app.state.user_dependency)):
        return service.list()

    @app.get('/api/ssh-keys/{identity}')
    def detail(identity: str, current=Depends(app.state.user_dependency)):
        return {**service.get(identity), 'usage': service.usage(identity)}

    @app.post('/api/ssh-keys', status_code=201)
    async def create(request: Request, current=Depends(human_admin)):
        value = await _body(request)
        if set(value) != {'name'}:
            raise HTTPException(422, 'Invalid SSH key request')
        return await run_in_threadpool(service.create, value['name'], current)

    @app.patch('/api/ssh-keys/{identity}')
    async def update(identity: str, request: Request, current=Depends(human_admin)):
        value = await _body(request)
        if set(value) != {'enabled'}:
            raise HTTPException(422, 'Invalid SSH key request')
        return await run_in_threadpool(service.update, identity, value['enabled'], current)

    @app.post('/api/ssh-keys/{identity}/export')
    async def export(identity: str, request: Request, current=Depends(human_admin)):
        value = await _body(request)
        if set(value) != {'password'}:
            raise HTTPException(422, 'Invalid SSH key export request')
        return await run_in_threadpool(service.export, identity, value['password'], current)

    added = app.router.routes[original_count:]
    del app.router.routes[original_count:]
    before_spa = next((i for i, route in enumerate(app.router.routes)
                       if getattr(route, 'path', None) == '/{asset_path:path}'), len(app.router.routes))
    app.router.routes[before_spa:before_spa] = added
    app.state._ssh_keys_api_installed = True
