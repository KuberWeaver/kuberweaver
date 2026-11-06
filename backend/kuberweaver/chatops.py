"""Optional gateway-signed ChatOps adapter; never an alternative execution path."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time

from fastapi import Depends, HTTPException, Request

from .jobs import digest
from .providers import resolve_provider


def install_chatops_api(app):
    """Install routes; application middleware exempts only the exact callback path."""
    if getattr(app.state, '_chatops_installed_on', None) is app:
        return
    # State may be deliberately shared by isolated adapter integration tests.
    app.state._chatops_installed_on = app
    store = app.state.store

    def parse(raw):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError('duplicate key')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict:
            raise ValueError('object required')
        return value

    def config():
        try:
            with (store.path / 'config' / 'chatops.json').open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            cfg = parse(raw)
            if set(cfg) != {'schema_version', 'enabled', 'secret_ref', 'actors', 'action_ttl_seconds', 'max_age_seconds'}:
                raise ValueError()
            if type(cfg['schema_version']) is not int or cfg['schema_version'] != 1 or cfg['enabled'] is not True:
                raise ValueError()
            if not isinstance(cfg['secret_ref'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)*', cfg['secret_ref']) or len(cfg['secret_ref']) > 128:
                raise ValueError()
            if type(cfg['actors']) is not dict or not cfg['actors'] or len(cfg['actors']) > 1000:
                raise ValueError()
            if any(not isinstance(k, str) or not 1 <= len(k) <= 200 or not isinstance(v, str) or not 1 <= len(v) <= 80 for k, v in cfg['actors'].items()):
                raise ValueError()
            for name, maximum in [('action_ttl_seconds', 3600), ('max_age_seconds', 300)]:
                if type(cfg[name]) is not int or not 1 <= cfg[name] <= maximum:
                    raise ValueError()
            return cfg
        except (OSError, ValueError, TypeError, RecursionError):
            raise HTTPException(503, 'ChatOps unavailable') from None

    async def raw_body(request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                raise HTTPException(413, 'ChatOps body too large')
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise HTTPException(400, 'Invalid ChatOps request')
        return bytes(body)

    def payload(body, fields):
        try:
            data = parse(body)
            if set(data) != fields:
                raise ValueError()
            return data
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(400, 'Invalid ChatOps request') from None

    @app.post('/api/chatops/actions', status_code=201)
    async def issue(request: Request, actor=Depends(app.state.role_dependency('admin'))):
        data = payload(await raw_body(request), {'job_id', 'decision'})
        if (not isinstance(data['job_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', data['job_id']) or
                data['decision'] not in ('approve', 'reject')):
            raise HTTPException(400, 'Invalid ChatOps request')
        cfg = config()
        with store.lock:
            job = app.state.jobs.get(data['job_id'])
            if job['status'] != 'pending_approval' or job.get('requires_manual_review'):
                raise HTTPException(409, 'ChatOps action unavailable')
            identity = secrets.token_hex(32)
            action = {'schema_version': 1, 'action_id': identity, 'job_id': job['id'],
                      'graph_digest': digest(job['graph']), 'decision': data['decision'],
                      'expires_at': int(time.time()) + cfg['action_ttl_seconds'],
                      'callback_path': '/api/chatops/callback'}
            store.put('settings', 'chatops.action.' + identity, {**action, 'state': 'ready'})
            return action

    @app.post('/api/chatops/callback')
    async def callback(request: Request):
        body = await raw_body(request)
        cfg = config()
        timestamp = request.headers.get('x-kw-timestamp', '')
        signature = request.headers.get('x-kw-signature', '')
        if (len(request.headers.getlist('x-kw-timestamp')) != 1 or
                len(request.headers.getlist('x-kw-signature')) != 1 or
                not re.fullmatch(r'[1-9][0-9]{9,11}', timestamp) or
                not re.fullmatch(r'v1=[0-9a-f]{64}', signature) or
                abs(time.time() - int(timestamp)) > cfg['max_age_seconds']):
            raise HTTPException(401, 'ChatOps authentication failed')
        try:
            with resolve_provider(app, 'secrets').resolve(cfg['secret_ref']) as key:
                value = key.bytes()
                if not 32 <= len(value) <= 4096:
                    raise ValueError()
                expected = 'v1=' + hmac.new(value, b'v1.' + timestamp.encode('ascii') + b'.' + body, hashlib.sha256).hexdigest()
        except Exception:
            raise HTTPException(503, 'ChatOps unavailable') from None
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(401, 'ChatOps authentication failed')
        data = payload(body, {'schema_version', 'action_id', 'actor', 'nonce'})
        if (type(data['schema_version']) is not int or data['schema_version'] != 1 or
                not isinstance(data['action_id'], str) or not re.fullmatch(r'[0-9a-f]{64}', data['action_id']) or
                not isinstance(data['actor'], str) or not 1 <= len(data['actor']) <= 200 or
                not isinstance(data['nonce'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', data['nonce'])):
            raise HTTPException(400, 'Invalid ChatOps request')
        with store.lock:
            nonce = 'chatops.nonce.' + digest(data['nonce'])
            if store.get('settings', nonce):
                raise HTTPException(409, 'ChatOps replay rejected')
            action_key = 'chatops.action.' + data['action_id']
            action = store.get('settings', action_key)
            actor = store.one('SELECT id,username,role FROM users WHERE username=?', (cfg['actors'].get(data['actor'], ''),))
            if not actor or actor['role'] != 'admin':
                raise HTTPException(403, 'ChatOps actor denied')
            if (not action or action['state'] != 'ready' or action['expires_at'] <= time.time()):
                raise HTTPException(409, 'ChatOps action unavailable')
            job = app.state.jobs.get(action['job_id'])
            if (job['status'] != 'pending_approval' or job.get('requires_manual_review') or
                    digest(job['graph']) != action['graph_digest']):
                raise HTTPException(409, 'ChatOps action unavailable')
            # Persist before entering Jobs: replay never executes a second attempt,
            # including after crash. A crashed inflight action requires manual review.
            store.put('settings', nonce, {'used': True})
            action['state'] = 'inflight'
            store.put('settings', action_key, action)
        # The nonce/action reservation is durable; publication must not inherit
        # this outer RLock. Jobs claims and rechecks the exact reviewed graph.
        try:
            if action['decision'] == 'approve':
                result = app.state.jobs.approve(action['job_id'], actor, expected_graph_hash=action['graph_digest'])
            else:
                with store.lock:
                    job = app.state.jobs.get(action['job_id'])
                    if digest(job['graph']) != action['graph_digest']:
                        raise HTTPException(409, 'Reviewed graph changed')
                    result = app.state.jobs.reject(action['job_id'], actor)
        except Exception as exc:
            with store.lock:
                current = app.state.jobs.get(action['job_id'])
                action['state'] = ('ready' if current['status'] == 'pending_approval'
                                   and not current.get('requires_manual_review') else 'consumed')
                store.put('settings', action_key, action)
                store.audit(actor['username'], 'chatops.decision_failed', action['job_id'])
            status = exc.status_code if isinstance(exc, HTTPException) else 503
            raise HTTPException(status, 'ChatOps decision failed') from None
        with store.lock:
            action['state'] = 'consumed'
            store.put('settings', action_key, action)
            store.audit(actor['username'], 'chatops.decision', action['job_id'], {'decision': action['decision']})
        return {'job_id': result['id'], 'status': result['status']}
