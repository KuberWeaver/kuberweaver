"""Cloudflare DNS records: list/create/update/delete through vault-scoped tokens,
behind the same durable approval/audit model as deployments.

A change is a durable request pinned to the connector fingerprint and the payload
digest; it never touches Cloudflare before approval. Approval re-verifies the
pins, resolves the token from the vault at execution time, verifies the token
against the live API, then executes exactly the approved payload. Reads are
authenticated and audited but need no approval, like every other platform read.

The API origin is the fixed constant from the secrets contract. Credential
material never enters a request record, audit detail, log or response; upstream
evidence is allowlisted, bounded and redacted before it is persisted.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
from contextlib import contextmanager
from typing import Literal

import httpx
from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from .cloudflare import API_ORIGIN, SCOPE_PROFILES, _request_json
from .jobs import digest
from .storage import now

_ID = r'^[0-9a-f]{32}$'
RECORD_TYPES = frozenset({
    'A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA', 'PTR', 'HTTPS', 'SVCB',
    'TLSA', 'SSHFP', 'URI', 'LOC', 'NAPTR', 'CERT', 'DNSKEY', 'DS', 'SMIMEA',
})
_LABEL = r'(?:[A-Za-z0-9_]|[A-Za-z0-9_][A-Za-z0-9_-]{0,61}[A-Za-z0-9_])'
_NAME = re.compile(r'(?:@|\*|' + _LABEL + r')(?:\.(?:\*|' + _LABEL + r'))*\.?')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')

# Bounded upstream evidence: allowlisted fields only, never raw responses.
_RECORD_FIELDS = {'id': 64, 'zone_id': 64, 'zone_name': 253, 'type': 16, 'name': 253,
                  'content': 2048, 'created_on': 64, 'modified_on': 64,
                  'ttl': 0, 'proxied': 0, 'priority': 0}
_ZONE_FIELDS = {'id': 64, 'name': 253, 'status': 32}
_MAX_RESPONSE = 1024 * 1024
_MAX_ERRORS = 10
_MAX_ERROR_MESSAGE = 200
_PER_PAGE = 100
_MAX_PAGES = 50
_PAYLOAD_KEYS = ('connector_id', 'zone_id', 'operation', 'record_id', 'record')


class CloudflareApiError(Exception):
    """Sanitized upstream failure: the message is generic, details ride in
    ``errors`` and are redacted before persistence. str/repr never carry them."""

    def __init__(self, message, *, errors=None):
        super().__init__(message)
        self.errors = errors or []


def _check_type(value):
    if value is not None and value not in RECORD_TYPES:
        raise ValueError('Unsupported DNS record type')
    return value


def _check_name(value):
    if value is not None and not _NAME.fullmatch(value):
        raise ValueError('Invalid DNS name')
    return value


def _check_content(value):
    if value is not None and _CONTROL.search(value):
        raise ValueError('Invalid DNS record content')
    return value


def _check_ttl(value):
    if value is not None and value != 1 and value < 60:
        raise ValueError('TTL must be 1 (auto) or between 60 and 86400')
    return value


def _check_type_rules(record_type, proxied, priority):
    if proxied is not None and record_type not in ('A', 'AAAA', 'CNAME'):
        raise ValueError('proxied applies only to A, AAAA and CNAME records')
    if priority is not None and record_type not in ('MX', 'SRV', 'URI'):
        raise ValueError('priority applies only to MX, SRV and URI records')
    if record_type in ('MX', 'SRV') and priority is None:
        raise ValueError('MX and SRV records require a priority')


class DnsRecord(BaseModel):
    """Closed create payload; unknown fields (including secret-shaped ones) fail."""
    model_config = ConfigDict(extra='forbid', strict=True)
    type: str
    name: str = Field(min_length=1, max_length=253)
    content: str = Field(min_length=1, max_length=2048)
    ttl: int = Field(default=1, ge=1, le=86400)
    proxied: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=65535)

    _type = field_validator('type')(_check_type)
    _name = field_validator('name')(_check_name)
    _content = field_validator('content')(_check_content)
    _ttl = field_validator('ttl')(_check_ttl)

    @model_validator(mode='after')
    def type_rules(self):
        _check_type_rules(self.type, self.proxied, self.priority)
        return self


class DnsRecordPatch(BaseModel):
    """Closed update payload: a non-empty subset of record fields."""
    model_config = ConfigDict(extra='forbid', strict=True)
    type: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=253)
    content: str | None = Field(default=None, min_length=1, max_length=2048)
    ttl: int | None = Field(default=None, ge=1, le=86400)
    proxied: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=65535)

    _type = field_validator('type')(_check_type)
    _name = field_validator('name')(_check_name)
    _content = field_validator('content')(_check_content)
    _ttl = field_validator('ttl')(_check_ttl)

    @model_validator(mode='after')
    def non_empty(self):
        if not self.model_dump(exclude_unset=True):
            raise ValueError('Record patch is empty')
        if self.type is not None:
            _check_type_rules(self.type, self.proxied, self.priority)
        return self


class DnsChangeSpec(BaseModel):
    """One proposed DNS mutation, normalized per operation before pinning."""
    model_config = ConfigDict(extra='forbid', strict=True)
    connector_id: str = Field(pattern=_ID)
    zone_id: str = Field(pattern=_ID)
    operation: Literal['create', 'update', 'delete']
    record_id: str | None = Field(default=None, pattern=_ID)
    record: dict | None = None

    @model_validator(mode='after')
    def operation_shape(self):
        if self.operation == 'create':
            if self.record is None or self.record_id is not None:
                raise ValueError('create requires a record and no record_id')
            self.record = DnsRecord.model_validate(self.record).model_dump()
        elif self.operation == 'update':
            if self.record is None or self.record_id is None:
                raise ValueError('update requires a record patch and a record_id')
            self.record = DnsRecordPatch.model_validate(self.record).model_dump(exclude_unset=True)
        elif self.record is not None or self.record_id is None:
            raise ValueError('delete requires a record_id and no record')
        return self


def _bounded_errors(payload):
    """Allowlisted, bounded upstream error evidence (never the raw response)."""
    errors = payload.get('errors') if isinstance(payload, dict) else None
    clean = []
    if isinstance(errors, list):
        for item in errors[:_MAX_ERRORS]:
            if not isinstance(item, dict):
                continue
            entry = {}
            if type(item.get('code')) is int:
                entry['code'] = item['code']
            if isinstance(item.get('message'), str):
                entry['message'] = item['message'][:_MAX_ERROR_MESSAGE]
            if entry:
                clean.append(entry)
    return clean


class CloudflareDns:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self.connectors = app.state.cloudflare_connectors
        # Injectable httpx transport: tests bind a mock; production uses the
        # default TLS transport against the fixed origin only.
        self.transport = None
        self._execution_locks = {}
        self.store.execute('CREATE TABLE IF NOT EXISTS cloudflare_dns_requests(id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    # -- durable requests ---------------------------------------------------

    def get_request(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise HTTPException(404, 'Unknown Cloudflare DNS request')
        row = self.store.one('SELECT body FROM cloudflare_dns_requests WHERE id=?', (identity,))
        if row is None:
            raise HTTPException(404, 'Unknown Cloudflare DNS request')
        return json.loads(row['body'])

    def list_requests(self):
        import json
        return [json.loads(row['body']) for row in self.store.all(
            'SELECT body FROM cloudflare_dns_requests ORDER BY rowid DESC')]

    def _put(self, request):
        import json
        self.store.execute('UPDATE cloudflare_dns_requests SET body=? WHERE id=?',
                           (json.dumps(request), request['id']))

    def create_request(self, value, actor):
        connector = self.connectors.get(value.connector_id)
        if not connector['enabled']:
            raise HTTPException(409, 'Cloudflare connector is disabled')
        if ('zone', 'dns', 'edit') not in SCOPE_PROFILES.get(connector['purpose'], ()):
            raise HTTPException(409, 'Cloudflare connector purpose does not allow DNS edits')
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
            'result': None, 'error': None,
        }
        import json
        with self.store.lock:
            self.store.execute('INSERT INTO cloudflare_dns_requests(id,body) VALUES(?,?)',
                               (request['id'], json.dumps(request)))
            self.store.audit(actor['username'], 'cloudflare_dns.requested', request['id'],
                             {'operation': request['operation'], 'zone_id': request['zone_id']})
            return self.get_request(request['id'])

    def reject(self, identity, actor):
        with self.store.lock:
            request = self.get_request(identity)
            if request['status'] != 'pending_approval':
                raise HTTPException(409, 'DNS request is not awaiting approval')
            request['status'] = 'rejected'
            self._put(request)
            self.store.audit(actor['username'], 'cloudflare_dns.rejected', identity)
            return self.get_request(identity)

    def approve(self, identity, actor):
        with self.store.lock:
            request = self.get_request(identity)
            if request['status'] != 'pending_approval':
                raise HTTPException(409, 'DNS request is not awaiting approval')
            connector_id = request['connector_id']
            lock = self._execution_locks.setdefault(connector_id, threading.Lock())
            if not lock.acquire(blocking=False):
                raise HTTPException(409, 'A Cloudflare DNS operation is already running for this connector')
        try:
            return self._approve(identity, actor, connector_id)
        finally:
            lock.release()

    def _approve(self, identity, actor, connector_id):
        import json
        with self.store.lock:
            request = self.get_request(identity)
            if request['status'] != 'pending_approval':
                raise HTTPException(409, 'DNS request is not awaiting approval')
            connector = self.connectors.get(connector_id)
            if not connector['enabled']:
                raise HTTPException(409, 'Cloudflare connector is disabled')
            if self.connectors.fingerprint(connector_id) != request['connector_fingerprint']:
                raise HTTPException(409, 'Cloudflare connector changed; submit a new request')
            if digest({key: request[key] for key in _PAYLOAD_KEYS}) != request['payload_digest']:
                raise HTTPException(409, 'DNS request payload changed; submit a new request')
            # Fail closed before any state change: credentials must resolve now.
            with self.connectors.access(connector_id):
                pass
            request.update(approved_by=actor['username'], approved_at=now(), status='executing')
            self._put(request)
            self.store.audit(actor['username'], 'cloudflare_dns.approved', identity,
                             {'operation': request['operation'], 'zone_id': request['zone_id']})
        # Execution happens outside the store lock; the durable 'executing' row
        # means an interrupted process never silently re-runs the mutation.
        result, status, error = None, 'failed', None
        try:
            with self.connectors.access(connector_id) as (record, token):
                try:
                    with self._client(token) as client:
                        result = self._execute(client, request, record, token)
                    status = 'succeeded'
                except CloudflareApiError as exc:
                    error = str(exc)
                    result = {'errors': self._redact_errors(exc.errors, token)}
                finally:
                    del token
        except HTTPException:
            error = 'Cloudflare connector credentials unavailable'
        except Exception as exc:
            error = 'Cloudflare DNS execution failed'
            result = {'error_type': type(exc).__name__}
        with self.store.lock:
            current = self.get_request(identity)
            current.update(status=status, error=error, result=result, executed_at=now())
            self._put(current)
            self.store.audit(actor['username'], 'cloudflare_dns.' + status, identity,
                             {'operation': request['operation'], 'zone_id': request['zone_id']})
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
        if connector.get('account_id'):
            path = f'/accounts/{connector["account_id"]}/tokens/verify'
        else:
            path = '/user/tokens/verify'
        payload = self._call(client, 'GET', path)
        result = payload.get('result')
        if not isinstance(result, dict) or result.get('status') != 'active':
            raise CloudflareApiError('Cloudflare API token is not active')

    def _execute(self, client, request, connector, token):
        self._verify(client, connector)
        zone_id = request['zone_id']
        operation = request['operation']
        if operation == 'create':
            body = {key: value for key, value in request['record'].items() if value is not None}
            payload = self._call(client, 'POST', f'/zones/{zone_id}/dns_records', json_body=body)
        elif operation == 'update':
            payload = self._call(client, 'PATCH',
                                 f'/zones/{zone_id}/dns_records/{request["record_id"]}',
                                 json_body=request['record'])
        else:
            payload = self._call(client, 'DELETE',
                                 f'/zones/{zone_id}/dns_records/{request["record_id"]}')
        return {'token_verified': True,
                'record': self._sanitize_fields(payload.get('result'), _RECORD_FIELDS, token)}

    # -- reads --------------------------------------------------------------

    def zones(self, connector_id, actor):
        with self.connectors.access(connector_id) as (connector, token):
            try:
                with self._client(token) as client:
                    items, truncated = self._paginate(client, '/zones', {})
                clean = [self._sanitize_fields(item, _ZONE_FIELDS, token) for item in items]
            except CloudflareApiError as exc:
                raise HTTPException(502, {'message': str(exc),
                                          'errors': self._redact_errors(exc.errors, token)}) from None
            finally:
                del token
        self.store.audit(actor['username'], 'cloudflare_dns.zones_listed', connector_id,
                         {'count': len(clean)})
        return {'items': clean, 'truncated': truncated}

    def records(self, connector_id, zone_id, actor, record_type=None, name=None):
        if record_type is not None and record_type not in RECORD_TYPES:
            raise HTTPException(422, 'Unsupported DNS record type')
        params = {key: value for key, value in (('type', record_type), ('name', name))
                  if value is not None}
        with self.connectors.access(connector_id) as (connector, token):
            try:
                with self._client(token) as client:
                    items, truncated = self._paginate(client, f'/zones/{zone_id}/dns_records', params)
                clean = [self._sanitize_fields(item, _RECORD_FIELDS, token) for item in items]
            except CloudflareApiError as exc:
                raise HTTPException(502, {'message': str(exc),
                                          'errors': self._redact_errors(exc.errors, token)}) from None
            finally:
                del token
        self.store.audit(actor['username'], 'cloudflare_dns.records_listed', connector_id,
                         {'zone_id': zone_id, 'count': len(clean)})
        return {'items': clean, 'truncated': truncated}

    # -- evidence sanitization ----------------------------------------------

    @staticmethod
    def _redact(value, token):
        marker = token.decode('utf-8')
        return value.replace(marker, '[redacted]') if marker else value

    def _redact_errors(self, errors, token):
        return [{key: self._redact(value, token) if isinstance(value, str) else value
                 for key, value in entry.items()} for entry in errors]

    def _sanitize_fields(self, item, fields, token):
        if not isinstance(item, dict):
            return {}
        clean = {}
        for key, limit in fields.items():
            value = item.get(key)
            if isinstance(value, str):
                clean[key] = self._redact(value[:limit], token)
            elif key in ('ttl', 'proxied', 'priority') and type(value) in (int, bool):
                clean[key] = value
        return clean


def install_cloudflare_dns_api(app):
    if getattr(app.state, 'cloudflare_dns', None) is not None:
        return
    dns = app.state.cloudflare_dns = CloudflareDns(app)
    user, role = app.state.user_dependency, app.state.role_dependency

    @app.get('/api/cloudflare/dns/zones')
    async def list_zones(connector_id: str = Query(pattern=_ID),
                         current=Depends(user)):
        return await run_in_threadpool(dns.zones, connector_id, current)

    @app.get('/api/cloudflare/dns/records')
    async def list_records(connector_id: str = Query(pattern=_ID),
                           zone_id: str = Query(pattern=_ID),
                           record_type: str | None = Query(default=None, alias='type', max_length=16),
                           name: str | None = Query(default=None, max_length=253),
                           current=Depends(user)):
        return await run_in_threadpool(dns.records, connector_id, zone_id, current, record_type, name)

    @app.post('/api/cloudflare/dns/requests', status_code=201)
    async def create_request(request: Request, current=Depends(role('admin', 'operator'))):
        try:
            value = DnsChangeSpec.model_validate(
                await _request_json(request, 'Invalid Cloudflare DNS request'))
        except ValidationError:
            raise HTTPException(422, 'Invalid Cloudflare DNS request') from None
        return await run_in_threadpool(dns.create_request, value, current)

    @app.get('/api/cloudflare/dns/requests')
    def list_requests(current=Depends(user)):
        return dns.list_requests()

    @app.get('/api/cloudflare/dns/requests/{identity}')
    def get_request(identity: str, current=Depends(user)):
        return dns.get_request(identity)

    @app.post('/api/cloudflare/dns/requests/{identity}/approve')
    async def approve_request(identity: str, current=Depends(role('admin'))):
        return await run_in_threadpool(dns.approve, identity, current)

    @app.post('/api/cloudflare/dns/requests/{identity}/reject')
    def reject_request(identity: str, current=Depends(role('admin'))):
        return dns.reject(identity, current)
