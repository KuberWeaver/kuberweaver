"""Cloudflare named tunnels: create/configure/delete through vault-scoped tokens,
behind the same durable approval/audit model as DNS changes and deployments.

A create request exposes a website end to end on the Cloudflare side: a remotely
managed named tunnel, its ingress rules, an optional proxied CNAME route, and the
cloudflared connector credential. That credential is a secret: it is shape-checked
fail-closed, stored in the vault by reference (closed envelope, provider readback)
and staged only as a private 0600 handoff file for the guest install. It never
enters a request record, audit detail, log or response. The execution result
carries a pinned, secret-free cloudflared deployment bundle (version, SHA256,
systemd unit, install steps) so the supervision VM can run the connector once a
human approves; live use requires a real tunnel-purpose token and account.

The API origin is the fixed constant from the secrets contract. Upstream evidence
is allowlisted, bounded and redacted before it is persisted.
"""
from __future__ import annotations

import base64
import hmac
import inspect
import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from .cloudflare import API_ORIGIN, SCOPE_PROFILES, _request_json, _writable_consumer
from .cloudflare_dns import CloudflareApiError, _bounded_errors
from .jobs import digest
from .providers import resolve_provider
from .secrets import SecretError, SecretValue
from .storage import now

_ID = r'^[0-9a-f]{32}$'
_UUID = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
_REFERENCE = r'[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)*'
_LABEL = r'(?:[A-Za-z0-9_]|[A-Za-z0-9_][A-Za-z0-9_-]{0,61}[A-Za-z0-9_])'
_HOSTNAME = re.compile(r'(?:\*\.)?' + _LABEL + r'(?:\.' + _LABEL + r')+')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
_B64 = re.compile(r'[A-Za-z0-9+/]+={0,2}')
_SERVICE_HTTP = re.compile(r'https?://[A-Za-z0-9._-]+(?::([0-9]{1,5}))?(?:/[A-Za-z0-9._~/-]*)?')
_SERVICE_TCP = re.compile(r'tcp://[A-Za-z0-9._-]+:([0-9]{1,5})')
_SERVICE_UNIX = re.compile(r'unix:(/[A-Za-z0-9._/-]+)')
_SERVICE_STATUS = re.compile(r'http_status:[1-5][0-9]{2}')
_MAX_INGRESS = 100
_MAX_TOKEN = 4096
_MAX_ENVELOPE = 4096

# Bounded upstream evidence: allowlisted fields only, never raw responses.
_TUNNEL_FIELDS = {'id': 36, 'name': 64, 'created_at': 64, 'deleted_at': 64,
                  'status': 32, 'tun_type': 32}
_RECORD_FIELDS = {'id': 64, 'zone_id': 64, 'zone_name': 253, 'type': 16, 'name': 253,
                  'content': 2048, 'created_on': 64, 'modified_on': 64,
                  'ttl': 0, 'proxied': 0}
_MAX_RESPONSE = 1024 * 1024
_PER_PAGE = 100
_MAX_PAGES = 50
_PAYLOAD_KEYS = ('connector_id', 'operation', 'tunnel_id', 'name', 'ingress',
                 'route', 'tunnel_secret_ref')

# Pinned cloudflared release for the guest connector bundle. The SHA256 is the
# official checksum of the linux-amd64 .deb published with this release; the
# install steps verify it before dpkg touches the guest.
CLOUDFLARED_VERSION = '2026.11.1'
CLOUDFLARED_DEB_URL = ('https://github.com/cloudflare/cloudflared/releases/download/'
                       f'{CLOUDFLARED_VERSION}/cloudflared-linux-amd64.deb')
CLOUDFLARED_DEB_SHA256 = '744ce90be92adb361103d51c85f78646314c47e4a68bbaadfe12ab5acc16c9b6'
_ENV_FILE = '/etc/kuberweaver/cloudflared/tunnel.env'
_UNIT_FILE = '/etc/systemd/system/kw-cloudflared.service'
_UNIT = """[Unit]
Description=KuberWeaver Cloudflare tunnel connector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/kuberweaver/cloudflared/tunnel.env
ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run
Restart=on-failure
RestartSec=5s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
"""


class TunnelError(Exception):
    """A sanitized tunnel capability failure; the message carries no secret."""


def default_secret_ref(tunnel_id):
    return 'cloudflare/tunnel-' + tunnel_id


def validate_tunnel_token(token, *, account_id, tunnel_id):
    """Accept only the cloudflared connector credential shape: base64 of the
    closed {a, t, s} JSON object bound to this account and tunnel. The error
    never echoes the value."""
    try:
        if not isinstance(token, str) or not 32 <= len(token) <= _MAX_TOKEN:
            raise ValueError
        if not _B64.fullmatch(token):
            raise ValueError
        raw = base64.b64decode(token, validate=True)
        if not 0 < len(raw) <= _MAX_TOKEN:
            raise ValueError

        def closed_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=closed_object)
        if not isinstance(payload, dict) or set(payload) != {'a', 't', 's'}:
            raise ValueError
        if payload['a'] != account_id or payload['t'] != tunnel_id:
            raise ValueError
        secret = payload['s']
        if not isinstance(secret, str) or not 0 < len(secret) <= 512 or _CONTROL.search(secret):
            raise ValueError
        return token
    except Exception:
        raise SecretError('Cloudflare tunnel credential shape is invalid') from None


def tunnel_envelope_bytes(token, *, account_id, tunnel_id):
    """Closed vault envelope: exactly one connector credential, no other fields."""
    value = validate_tunnel_token(token, account_id=account_id, tunnel_id=tunnel_id)
    return json.dumps({'tunnel_token': value}, separators=(',', ':')).encode('utf-8')


def parse_tunnel_envelope(raw, *, account_id, tunnel_id):
    """Resolve-side validation: anything but one shaped credential fails closed."""
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
        if not isinstance(payload, dict) or set(payload) != {'tunnel_token'}:
            raise ValueError
        return validate_tunnel_token(payload['tunnel_token'],
                                     account_id=account_id, tunnel_id=tunnel_id)
    except Exception:
        raise SecretError('Cloudflare tunnel credential envelope is invalid') from None


def store_tunnel_token(provider, reference, token, *, account_id, tunnel_id):
    """Vault write path: shape-check before any write, closed envelope,
    provider-owned readback. ``token`` is a SecretValue holding the raw token."""
    try:
        consumer = _writable_consumer(provider)
    except (Exception, SystemExit):
        raise SecretError('Cloudflare secret source is read-only or incompatible') from None
    try:
        raw = token.bytes()
        envelope = tunnel_envelope_bytes(raw.decode('utf-8'),
                                         account_id=account_id, tunnel_id=tunnel_id)
        wrapped = SecretValue(envelope)
        try:
            result = consumer(reference, wrapped)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError('Expected synchronous credential storage')
        finally:
            wrapped.close()
        with provider.resolve(reference) as readback:
            if not hmac.compare_digest(readback.bytes(), envelope):
                raise ValueError('readback mismatch')
    except SecretError:
        raise
    except Exception:
        raise SecretError('Cloudflare tunnel credential storage unavailable') from None


def cloudflared_bundle(tunnel_secret_ref, staging_file):
    """Deterministic, secret-free deployment bundle for the guest connector.
    The credential travels only as the vault reference and the private staging
    file path; the token value itself is never part of the bundle."""
    steps = [
        f'Credential source: vault reference {tunnel_secret_ref}; the approved create '
        f'staged it 0600 at {staging_file} on the platform host.',
        f'scp -O {staging_file} ubuntu@<guest>:/tmp/kw-tunnel-token',
        '# On the guest, as root:',
        'install -d -m 0700 /etc/kuberweaver/cloudflared',
        "sed 's/^/TUNNEL_TOKEN=/' /tmp/kw-tunnel-token > " + _ENV_FILE,
        'chmod 0600 ' + _ENV_FILE + ' && shred -u /tmp/kw-tunnel-token',
        'curl -fsSL -o /tmp/cloudflared.deb ' + CLOUDFLARED_DEB_URL,
        f"echo '{CLOUDFLARED_DEB_SHA256}  /tmp/cloudflared.deb' | sha256sum -c -",
        'dpkg -i /tmp/cloudflared.deb && rm /tmp/cloudflared.deb',
        f'Write the unit text below to {_UNIT_FILE} (mode 0644).',
        'systemctl daemon-reload && systemctl enable --now kw-cloudflared.service',
        'systemctl is-active kw-cloudflared.service && cloudflared --version',
    ]
    return {'version': CLOUDFLARED_VERSION, 'deb_url': CLOUDFLARED_DEB_URL,
            'deb_sha256': CLOUDFLARED_DEB_SHA256, 'env_file': _ENV_FILE,
            'unit_file': _UNIT_FILE, 'unit': _UNIT, 'steps': steps}


def _check_port(value):
    if value is not None and not 1 <= int(value) <= 65535:
        raise ValueError('Service port is out of range')
    return value


def _check_service(value):
    if not isinstance(value, str) or not 0 < len(value) <= 2048 or _CONTROL.search(value):
        raise ValueError('Invalid ingress service')
    if value == 'hello_world' or _SERVICE_STATUS.fullmatch(value):
        return value
    match = _SERVICE_HTTP.fullmatch(value)
    if match:
        _check_port(match.group(1))
        return value
    match = _SERVICE_TCP.fullmatch(value)
    if match:
        _check_port(match.group(1))
        return value
    match = _SERVICE_UNIX.fullmatch(value)
    if match:
        if '..' in match.group(1).split('/'):
            raise ValueError('Invalid ingress service')
        return value
    raise ValueError('Unsupported ingress service scheme')


def _check_hostname(value):
    if value is not None and (not isinstance(value, str) or len(value) > 253
                              or not _HOSTNAME.fullmatch(value)):
        raise ValueError('Invalid ingress hostname')
    return value


class IngressRule(BaseModel):
    """One closed ingress rule; a null hostname is the catch-all (last only)."""
    model_config = ConfigDict(extra='forbid', strict=True)
    hostname: str | None = None
    service: str = Field(min_length=1, max_length=2048)

    _hostname = field_validator('hostname')(_check_hostname)
    _service = field_validator('service')(_check_service)


class TunnelRoute(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    zone_id: str = Field(pattern=_ID)
    name: str | None = Field(default=None, max_length=253)
    record_id: str | None = Field(default=None, pattern=_ID)

    _name = field_validator('name')(_check_hostname)


def _normalize_ingress(rules):
    if not isinstance(rules, list) or not 0 < len(rules) <= _MAX_INGRESS:
        raise ValueError('Ingress requires between 1 and 100 rules')
    normalized = [IngressRule.model_validate(rule).model_dump() for rule in rules]
    if not any(rule['hostname'] is not None for rule in normalized):
        raise ValueError('Ingress requires at least one hostname rule')
    for rule in normalized[:-1]:
        if rule['hostname'] is None:
            raise ValueError('The catch-all rule must be last')
    if normalized[-1]['hostname'] is not None:
        normalized.append({'hostname': None, 'service': 'http_status:404'})
    return normalized


class TunnelChangeSpec(BaseModel):
    """One proposed tunnel mutation, normalized per operation before pinning."""
    model_config = ConfigDict(extra='forbid', strict=True)
    connector_id: str = Field(pattern=_ID)
    operation: Literal['create', 'configure', 'delete']
    tunnel_id: str | None = Field(default=None, pattern=_UUID)
    name: str | None = Field(default=None, min_length=1, max_length=64,
                             pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    ingress: list | None = None
    route: dict | None = None
    tunnel_secret_ref: str | None = Field(default=None, max_length=128)

    @field_validator('tunnel_secret_ref')
    @classmethod
    def logical_reference(cls, value):
        if value is not None and not re.fullmatch(_REFERENCE, value):
            raise ValueError('Invalid logical reference')
        return value

    @model_validator(mode='after')
    def operation_shape(self):
        if self.operation == 'create':
            if self.tunnel_id is not None or not self.name or self.ingress is None:
                raise ValueError('create requires a name and ingress, no tunnel_id')
            self.ingress = _normalize_ingress(self.ingress)
            if self.route is not None:
                route = TunnelRoute.model_validate(self.route)
                if route.record_id is not None:
                    raise ValueError('create route carries no record_id')
                hostnames = [rule['hostname'] for rule in self.ingress if rule['hostname']]
                name = route.name if route.name is not None else hostnames[0]
                if name not in hostnames:
                    raise ValueError('Route name must match an ingress hostname')
                self.route = {'zone_id': route.zone_id, 'name': name}
        elif self.operation == 'configure':
            if self.tunnel_id is None or self.ingress is None:
                raise ValueError('configure requires a tunnel_id and ingress')
            if self.name is not None or self.route is not None or self.tunnel_secret_ref is not None:
                raise ValueError('configure carries only tunnel_id and ingress')
            self.ingress = _normalize_ingress(self.ingress)
        else:
            if self.tunnel_id is None or self.ingress is not None or self.name is not None:
                raise ValueError('delete requires only a tunnel_id and an optional route')
            if self.route is not None:
                route = TunnelRoute.model_validate(self.route)
                if route.record_id is None or route.name is not None:
                    raise ValueError('delete route requires zone_id and record_id')
                self.route = {'zone_id': route.zone_id, 'record_id': route.record_id}
        return self


def _ingress_config(rules):
    """Cloudflare configuration body: the catch-all omits the hostname key."""
    return [{'service': rule['service']} if rule['hostname'] is None
            else {'hostname': rule['hostname'], 'service': rule['service']}
            for rule in rules]


class CloudflareTunnel:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self.connectors = app.state.cloudflare_connectors
        # Injectable httpx transport: tests bind a mock; production uses the
        # default TLS transport against the fixed origin only.
        self.transport = None
        self._execution_locks = {}
        self.store.execute('CREATE TABLE IF NOT EXISTS cloudflare_tunnel_requests(id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    # -- durable requests ---------------------------------------------------

    def get_request(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise HTTPException(404, 'Unknown Cloudflare tunnel request')
        row = self.store.one('SELECT body FROM cloudflare_tunnel_requests WHERE id=?', (identity,))
        if row is None:
            raise HTTPException(404, 'Unknown Cloudflare tunnel request')
        return json.loads(row['body'])

    def list_requests(self):
        return [json.loads(row['body']) for row in self.store.all(
            'SELECT body FROM cloudflare_tunnel_requests ORDER BY rowid DESC')]

    def _put(self, request):
        self.store.execute('UPDATE cloudflare_tunnel_requests SET body=? WHERE id=?',
                           (json.dumps(request), request['id']))

    def _connector_for_change(self, connector_id):
        connector = self.connectors.get(connector_id)
        if not connector['enabled']:
            raise HTTPException(409, 'Cloudflare connector is disabled')
        if ('account', 'cloudflare_tunnel', 'edit') not in SCOPE_PROFILES.get(connector['purpose'], ()):
            raise HTTPException(409, 'Cloudflare connector purpose does not allow tunnel edits')
        if not connector.get('account_id'):
            raise HTTPException(409, 'Cloudflare tunnel operations require a connector account_id')
        return connector

    def create_request(self, value, actor):
        self._connector_for_change(value.connector_id)
        normalized = value.model_dump()
        request = {
            **normalized,
            'id': secrets.token_hex(16),
            'status': 'pending_approval',
            'created_at': now(),
            'created_by': actor['username'],
            'created_by_role': actor['role'],
            'connector_fingerprint': self.connectors.fingerprint(value.connector_id),
            'payload_digest': digest({key: normalized[key] for key in _PAYLOAD_KEYS}),
            'approved_by': None, 'approved_at': None, 'executed_at': None,
            'upstream_tunnel_id': None, 'result': None, 'error': None,
        }
        with self.store.lock:
            self.store.execute('INSERT INTO cloudflare_tunnel_requests(id,body) VALUES(?,?)',
                               (request['id'], json.dumps(request)))
            self.store.audit(actor['username'], 'cloudflare_tunnel.requested', request['id'],
                             {'operation': request['operation']})
            return self.get_request(request['id'])

    def reject(self, identity, actor):
        with self.store.lock:
            request = self.get_request(identity)
            if request['status'] != 'pending_approval':
                raise HTTPException(409, 'Tunnel request is not awaiting approval')
            request['status'] = 'rejected'
            self._put(request)
            self.store.audit(actor['username'], 'cloudflare_tunnel.rejected', identity)
            return self.get_request(identity)

    def approve(self, identity, actor):
        with self.store.lock:
            request = self.get_request(identity)
            if request['status'] != 'pending_approval':
                raise HTTPException(409, 'Tunnel request is not awaiting approval')
            connector_id = request['connector_id']
            lock = self._execution_locks.setdefault(connector_id, threading.Lock())
            if not lock.acquire(blocking=False):
                raise HTTPException(409, 'A Cloudflare tunnel operation is already running for this connector')
        try:
            return self._approve(identity, actor, connector_id)
        finally:
            lock.release()

    def _approve(self, identity, actor, connector_id):
        with self.store.lock:
            request = self.get_request(identity)
            if request['status'] != 'pending_approval':
                raise HTTPException(409, 'Tunnel request is not awaiting approval')
            connector = self.connectors.get(connector_id)
            if not connector['enabled']:
                raise HTTPException(409, 'Cloudflare connector is disabled')
            if self.connectors.fingerprint(connector_id) != request['connector_fingerprint']:
                raise HTTPException(409, 'Cloudflare connector changed; submit a new request')
            if digest({key: request[key] for key in _PAYLOAD_KEYS}) != request['payload_digest']:
                raise HTTPException(409, 'Tunnel request payload changed; submit a new request')
            # Fail closed before any state change: credentials must resolve now.
            try:
                with self.connectors.access(connector_id):
                    pass
            except HTTPException:
                request.update(status='failed', executed_at=now(),
                               error='Cloudflare connector credentials unavailable')
                self._put(request)
                self.store.audit(actor['username'], 'cloudflare_tunnel.failed', identity,
                                 {'operation': request['operation']})
                return self.get_request(identity)
            request.update(approved_by=actor['username'], approved_at=now(), status='executing')
            self._put(request)
            self.store.audit(actor['username'], 'cloudflare_tunnel.approved', identity,
                             {'operation': request['operation']})
        # Execution happens outside the store lock; the durable 'executing' row
        # means an interrupted process never silently re-runs the mutation.
        result, status, error = None, 'failed', None
        progress, markers = {}, []
        try:
            with self.connectors.access(connector_id) as (record, token):
                markers.append(token.decode('utf-8'))
                try:
                    with self._client(token) as client:
                        result = self._execute(client, request, record, progress, markers)
                    status = 'succeeded'
                except CloudflareApiError as exc:
                    error = str(exc)
                    result = {**progress, 'errors': self._redact_errors(exc.errors, markers)}
                finally:
                    del token
        except HTTPException:
            error = 'Cloudflare connector credentials unavailable'
        except (SecretError, TunnelError) as exc:
            error = str(exc)
            result = dict(progress) or None
        except Exception as exc:
            error = 'Cloudflare tunnel execution failed'
            result = {**progress, 'error_type': type(exc).__name__}
        with self.store.lock:
            current = self.get_request(identity)
            current.update(status=status, error=error, result=result, executed_at=now())
            self._put(current)
            self.store.audit(actor['username'], 'cloudflare_tunnel.' + status, identity,
                             {'operation': request['operation']})
            return self.get_request(identity)

    # -- execution ----------------------------------------------------------

    def _client(self, token):
        return httpx.Client(base_url=API_ORIGIN, transport=self.transport,
                            trust_env=False, follow_redirects=False,
                            timeout=httpx.Timeout(20.0, connect=5.0),
                            headers={'Authorization': 'Bearer ' + token.decode('utf-8')})

    def _call(self, client, method, path, *, json_body=None, params=None):
        try:
            response = client.request(method, path, json=json_body, params=params)
        except httpx.HTTPError:
            raise CloudflareApiError('Cloudflare API unreachable') from None
        if len(response.content) > _MAX_RESPONSE:
            raise CloudflareApiError('Cloudflare API response too large')
        try:
            payload = response.json()
        except ValueError:
            raise CloudflareApiError('Cloudflare API response invalid') from None
        if response.status_code >= 400 or not isinstance(payload, dict) or payload.get('success') is not True:
            raise CloudflareApiError('Cloudflare API request failed', errors=_bounded_errors(payload))
        return payload

    def _paginate(self, client, path, params):
        items, page, truncated = [], 1, False
        while True:
            payload = self._call(client, 'GET', path,
                                 params={**params, 'page': page, 'per_page': _PER_PAGE})
            result = payload.get('result')
            if not isinstance(result, list):
                raise CloudflareApiError('Cloudflare API response invalid')
            items.extend(result)
            info = payload.get('result_info')
            total_pages = info.get('total_pages') if isinstance(info, dict) else None
            if not isinstance(total_pages, int) or page >= total_pages:
                break
            if page >= _MAX_PAGES:
                truncated = True
                break
            page += 1
        return items, truncated

    def _verify(self, client, connector):
        """Live gate: the stored token must verify as active before any write."""
        payload = self._call(client, 'GET', f'/accounts/{connector["account_id"]}/tokens/verify')
        result = payload.get('result')
        if not isinstance(result, dict) or result.get('status') != 'active':
            raise CloudflareApiError('Cloudflare API token is not active')

    def _record_upstream(self, identity, tunnel_id):
        """Durable trace of a created tunnel even if a later step fails."""
        with self.store.lock:
            current = self.get_request(identity)
            current['upstream_tunnel_id'] = tunnel_id
            self._put(current)

    def _execute(self, client, request, connector, progress, markers):
        self._verify(client, connector)
        account_id = connector['account_id']
        operation = request['operation']
        if operation == 'create':
            return self._execute_create(client, request, connector, progress, markers)
        if operation == 'configure':
            tunnel_id = request['tunnel_id']
            payload = self._call(client, 'PUT',
                                 f'/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations',
                                 json_body={'config': {'ingress': _ingress_config(request['ingress'])}})
            result = payload.get('result')
            version = result.get('version') if isinstance(result, dict) else None
            return {'token_verified': True, 'tunnel_id': tunnel_id,
                    'ingress': request['ingress'],
                    'config_version': version if type(version) is int else None}
        return self._execute_delete(client, request, connector, progress)

    def _execute_create(self, client, request, connector, progress, markers):
        account_id = connector['account_id']
        payload = self._call(client, 'POST', f'/accounts/{account_id}/cfd_tunnel',
                             json_body={'name': request['name'], 'config_src': 'cloudflare'})
        tunnel = payload.get('result')
        if not isinstance(tunnel, dict) or not isinstance(tunnel.get('id'), str) \
                or not re.fullmatch(_UUID[1:-1], tunnel['id']):
            raise CloudflareApiError('Cloudflare API response invalid')
        tunnel_id = tunnel['id']
        progress['tunnel'] = self._sanitize_fields(tunnel, _TUNNEL_FIELDS, markers)
        self._record_upstream(request['id'], tunnel_id)
        # Connector credential: fetch, shape-check, vault-store, private staging.
        payload = self._call(client, 'GET',
                             f'/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token')
        token_value = payload.get('result')
        validated = validate_tunnel_token(token_value, account_id=account_id, tunnel_id=tunnel_id)
        markers.append(validated)
        reference = request.get('tunnel_secret_ref') or default_secret_ref(tunnel_id)
        provider = resolve_provider(self.app, 'secrets', connector['secrets_provider'])
        try:
            with provider.resolve(reference):
                raise TunnelError('Tunnel credential reference already exists in the vault')
        except SecretError:
            pass  # absent: the reference is free
        wrapped = SecretValue(validated.encode('utf-8'))
        try:
            store_tunnel_token(provider, reference, wrapped,
                               account_id=account_id, tunnel_id=tunnel_id)
        finally:
            wrapped.close()
        progress['tunnel_secret_ref'] = reference
        progress['credential_stored'] = True
        staging = self._stage_token(tunnel_id, validated)
        progress['staging_file'] = str(staging)
        del validated
        # Ingress configuration, then the optional proxied DNS route.
        self._call(client, 'PUT', f'/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations',
                   json_body={'config': {'ingress': _ingress_config(request['ingress'])}})
        progress['ingress'] = request['ingress']
        route = None
        if request.get('route'):
            body = {'type': 'CNAME', 'name': request['route']['name'],
                    'content': f'{tunnel_id}.cfargotunnel.com', 'proxied': True, 'ttl': 1}
            payload = self._call(client, 'POST', f'/zones/{request["route"]["zone_id"]}/dns_records',
                                 json_body=body)
            route = {'record': self._sanitize_fields(payload.get('result'), _RECORD_FIELDS, markers)}
        return {'token_verified': True, 'tunnel': progress['tunnel'],
                'ingress': request['ingress'], 'route': route,
                'tunnel_secret_ref': reference, 'credential_stored': True,
                'staging_file': str(staging),
                'cloudflared': cloudflared_bundle(reference, str(staging))}

    def _execute_delete(self, client, request, connector, progress):
        account_id = connector['account_id']
        tunnel_id = request['tunnel_id']
        route_deleted = False
        if request.get('route'):
            self._call(client, 'DELETE',
                       f'/zones/{request["route"]["zone_id"]}/dns_records/{request["route"]["record_id"]}')
            route_deleted = True
        self._call(client, 'DELETE', f'/accounts/{account_id}/cfd_tunnel/{tunnel_id}')
        staging_removed = self._wipe_staging(tunnel_id)
        reference = request.get('tunnel_secret_ref') or default_secret_ref(tunnel_id)
        vault_deleted = False
        try:
            provider = resolve_provider(self.app, 'secrets', connector['secrets_provider'])
            deleter = getattr(provider, 'delete', None)
            if callable(deleter):
                deleter(reference)
                vault_deleted = True
        except Exception:
            vault_deleted = False
        return {'token_verified': True, 'tunnel_id': tunnel_id,
                'route_deleted': route_deleted, 'staging_removed': staging_removed,
                'vault_credential_deleted': vault_deleted,
                'tunnel_secret_ref': reference}

    # -- private staging handoff ---------------------------------------------

    def _staging_path(self, tunnel_id):
        return Path(self.app.state.state_dir) / 'cloudflare-tunnels' / tunnel_id / 'token'

    def _stage_token(self, tunnel_id, token):
        """0600 handoff file for the guest install; vault stays the source of
        truth. The value never enters a response, audit row or log."""
        path = self._staging_path(tunnel_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(path.parent.parent, 0o700)
            os.chmod(path.parent, 0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(token)
        except Exception:
            raise TunnelError('Tunnel credential staging unavailable') from None
        return path

    def _wipe_staging(self, tunnel_id):
        path = self._staging_path(tunnel_id)
        try:
            if path.is_file():
                size = path.stat().st_size
                with open(path, 'r+b') as stream:
                    stream.write(b'\x00' * size)
                    stream.flush()
                    os.fsync(stream.fileno())
                path.unlink()
                return True
        except Exception:
            return False
        return False

    # -- reads --------------------------------------------------------------

    def _connector_for_read(self, connector_id):
        connector = self.connectors.get(connector_id)
        if not connector['enabled']:
            raise HTTPException(409, 'Cloudflare connector is disabled')
        if not connector.get('account_id'):
            raise HTTPException(409, 'Cloudflare tunnel operations require a connector account_id')
        return connector

    def tunnels(self, connector_id, actor):
        self._connector_for_read(connector_id)
        with self.connectors.access(connector_id) as (connector, token):
            try:
                with self._client(token) as client:
                    items, truncated = self._paginate(
                        client, f'/accounts/{connector["account_id"]}/cfd_tunnel',
                        {'is_deleted': 'false'})
                clean = [self._sanitize_fields(item, _TUNNEL_FIELDS, [token.decode('utf-8')])
                         for item in items]
            except CloudflareApiError as exc:
                raise HTTPException(502, {'message': str(exc),
                                          'errors': self._redact_errors(exc.errors, [token.decode('utf-8')])}) from None
            finally:
                del token
        self.store.audit(actor['username'], 'cloudflare_tunnel.tunnels_listed', connector_id,
                         {'count': len(clean)})
        return {'items': clean, 'truncated': truncated}

    def tunnel(self, connector_id, tunnel_id, actor):
        if not isinstance(tunnel_id, str) or not re.fullmatch(_UUID[1:-1], tunnel_id):
            raise HTTPException(422, 'Invalid tunnel identifier')
        self._connector_for_read(connector_id)
        with self.connectors.access(connector_id) as (connector, token):
            markers = [token.decode('utf-8')]
            try:
                with self._client(token) as client:
                    base = f'/accounts/{connector["account_id"]}/cfd_tunnel/{tunnel_id}'
                    payload = self._call(client, 'GET', base)
                    tunnel = self._sanitize_fields(payload.get('result'), _TUNNEL_FIELDS, markers)
                    config = self._call(client, 'GET', base + '/configurations')
                    ingress = self._sanitize_ingress(config.get('result'), markers)
            except CloudflareApiError as exc:
                raise HTTPException(502, {'message': str(exc),
                                          'errors': self._redact_errors(exc.errors, markers)}) from None
            finally:
                del token
        self.store.audit(actor['username'], 'cloudflare_tunnel.tunnel_read', connector_id,
                         {'tunnel_id': tunnel_id})
        return {'tunnel': tunnel, 'ingress': ingress}

    # -- evidence sanitization ----------------------------------------------

    @staticmethod
    def _redact(value, markers):
        for marker in markers:
            if marker:
                value = value.replace(marker, '[redacted]')
        return value

    def _redact_errors(self, errors, markers):
        return [{key: self._redact(value, markers) if isinstance(value, str) else value
                 for key, value in entry.items()} for entry in errors]

    def _sanitize_fields(self, item, fields, markers):
        if not isinstance(item, dict):
            return {}
        clean = {}
        for key, limit in fields.items():
            value = item.get(key)
            if isinstance(value, str):
                clean[key] = self._redact(value[:limit], markers)
            elif key in ('ttl', 'proxied') and type(value) in (int, bool):
                clean[key] = value
            elif key == 'deleted_at' and value is None:
                clean[key] = None
        return clean

    def _sanitize_ingress(self, result, markers):
        config = result.get('config') if isinstance(result, dict) else None
        rules = config.get('ingress') if isinstance(config, dict) else None
        if not isinstance(rules, list):
            return []
        clean = []
        for rule in rules[:_MAX_INGRESS]:
            if not isinstance(rule, dict):
                continue
            entry = {'hostname': None, 'service': ''}
            if isinstance(rule.get('hostname'), str):
                entry['hostname'] = self._redact(rule['hostname'][:253], markers)
            if isinstance(rule.get('service'), str):
                entry['service'] = self._redact(rule['service'][:2048], markers)
            clean.append(entry)
        return clean


def install_cloudflare_tunnel_api(app):
    if getattr(app.state, 'cloudflare_tunnel', None) is not None:
        return
    tunnel = app.state.cloudflare_tunnel = CloudflareTunnel(app)
    user, role = app.state.user_dependency, app.state.role_dependency

    @app.get('/api/cloudflare/tunnel/tunnels')
    async def list_tunnels(connector_id: str = Query(pattern=_ID),
                           current=Depends(user)):
        return await run_in_threadpool(tunnel.tunnels, connector_id, current)

    @app.get('/api/cloudflare/tunnel/tunnels/{tunnel_id}')
    async def get_tunnel(tunnel_id: str, connector_id: str = Query(pattern=_ID),
                         current=Depends(user)):
        return await run_in_threadpool(tunnel.tunnel, connector_id, tunnel_id, current)

    @app.post('/api/cloudflare/tunnel/requests', status_code=201)
    async def create_request(request: Request, current=Depends(role('admin', 'operator'))):
        try:
            value = TunnelChangeSpec.model_validate(
                await _request_json(request, 'Invalid Cloudflare tunnel request'))
        except ValidationError:
            raise HTTPException(422, 'Invalid Cloudflare tunnel request') from None
        return await run_in_threadpool(tunnel.create_request, value, current)

    @app.get('/api/cloudflare/tunnel/requests')
    def list_requests(current=Depends(user)):
        return tunnel.list_requests()

    @app.get('/api/cloudflare/tunnel/requests/{identity}')
    def get_request(identity: str, current=Depends(user)):
        return tunnel.get_request(identity)

    @app.post('/api/cloudflare/tunnel/requests/{identity}/approve')
    async def approve_request(identity: str, current=Depends(role('admin'))):
        return await run_in_threadpool(tunnel.approve, identity, current)

    @app.post('/api/cloudflare/tunnel/requests/{identity}/reject')
    def reject_request(identity: str, current=Depends(role('admin'))):
        return tunnel.reject(identity, current)