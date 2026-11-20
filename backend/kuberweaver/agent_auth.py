"""Human-issued, hashed, short-lived machine principals; no model or auto-enrolment."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from typing import Literal, get_args

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

Scope = Literal['read', 'read:finops', 'read:jobs', 'read:catalog', 'read:settings',
                'read:infrastructure', 'read:integrations', 'read:diagnostics', 'read:blueprints',
                'read:audit', 'read:overview', 'read:operations',
                'plan', 'submit', 'approve', 'rollback', 'configure', 'diagnose', 'notify', 'extensions', 'chatops']
SCOPES = get_args(Scope)
READ_SCOPES = tuple(scope for scope in SCOPES if scope == 'read' or scope.startswith('read:'))
ROLE_SCOPES = {
    'viewer': (*READ_SCOPES, 'plan'),
    'operator': (*READ_SCOPES, 'plan', 'submit', 'diagnose'),
    'admin': SCOPES,
}
TOKEN_PREFIX = 'kw_agent_'
TOKEN_PATTERN = re.compile(r'kw_agent_[A-Za-z0-9_-]{43}')
# One audit row per machine request is the operational record of what a token
# did. At a steady poll rate those rows can outgrow the decision trail, so the
# per-use rows alone are capped; issue, rotation, revocation and denials are
# never pruned.
USE_AUDIT_RETENTION = 50_000
USE_AUDIT_PRUNE_INTERVAL = 3600


def permits(scopes, required):
    return required is not None and (not required or required in scopes
                                     or (required.startswith('read:') and 'read' in scopes))


class TokenIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9][A-Za-z0-9 ._:@-]*$')
    role: Literal['viewer', 'operator', 'admin']
    scopes: list[Scope] = Field(default_factory=lambda: ['read'], min_length=1, max_length=len(SCOPES))
    expires_in_seconds: int = Field(ge=60, le=2_592_000, strict=True)


class AgentTokens:
    def __init__(self, store, *, clock=time.time, use_audit_retention=USE_AUDIT_RETENTION,
                 use_audit_prune_interval=USE_AUDIT_PRUNE_INTERVAL):
        self.store = store
        self.clock = clock
        self.use_audit_retention = use_audit_retention
        self.use_audit_prune_interval = use_audit_prune_interval
        self._next_prune = 0.0
        # Store opens WAL before restricting the main file on first bootstrap.
        # The private directory protects this startup interval; restrict sidecars
        # before any machine credential is persisted. Recreated WAL inherits 0600.
        for name in ('platform.sqlite3', 'platform.sqlite3-wal', 'platform.sqlite3-shm'):
            path = store.path / name
            if path.exists():
                path.chmod(0o600)
        store.execute('''CREATE TABLE IF NOT EXISTS agent_tokens(
            id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
            role TEXT NOT NULL, scopes TEXT NOT NULL, created_at REAL NOT NULL,
            expires_at REAL NOT NULL, revoked_at REAL, issued_by TEXT NOT NULL)''')

    @staticmethod
    def metadata(row):
        return {key: json.loads(row[key]) if key == 'scopes' else row[key]
                for key in ('id', 'label', 'role', 'scopes', 'expires_at', 'revoked_at')}

    def authenticate(self, token):
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            return None
        row = self.store.one('SELECT * FROM agent_tokens WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?',
                             (hashlib.sha256(token.encode()).hexdigest(), self.clock()))
        if not row:
            return None
        return {'id': 'agent:' + row['id'], 'username': 'agent:' + row['id'],
                'role': row['role'], 'label': row['label'], 'principal_type': 'agent',
                'token_id': row['id'], 'scopes': json.loads(row['scopes'])}

    def record_use(self, principal, method, path, status, scope):
        """Append the per-request audit row, then bound the high-volume tail."""
        self.store.audit(principal['username'], 'agent.request', principal['token_id'],
                         {'method': method, 'path': path, 'status': status, 'scope': scope})
        current = self.clock()
        if current < self._next_prune:
            return
        self._next_prune = current + self.use_audit_prune_interval
        self.store.execute(
            'DELETE FROM audit WHERE action=? AND id NOT IN '
            '(SELECT id FROM audit WHERE action=? ORDER BY id DESC LIMIT ?)',
            ('agent.request', 'agent.request', self.use_audit_retention))


def install_agent_tokens(app):
    tokens = app.state.agent_tokens
    store = app.state.store

    def human_admin(current=Depends(app.state.role_dependency('admin'))):
        if current.get('principal_type') == 'agent':
            raise HTTPException(403, 'Human administrator session required')
        return current

    @app.get('/api/agent-tokens')
    def listing(current=Depends(human_admin)):
        return {'tokens': [tokens.metadata(row) for row in store.all('SELECT * FROM agent_tokens ORDER BY created_at DESC, id')],
                'available_scopes': list(SCOPES), 'role_scopes': ROLE_SCOPES}

    @app.post('/api/agent-tokens', status_code=201)
    def issue(data: TokenIssue, current=Depends(human_admin)):
        if not set(data.scopes).issubset(ROLE_SCOPES[data.role]) or len(set(data.scopes)) != len(data.scopes):
            raise HTTPException(422, 'Scopes are not permitted for this role or are duplicated')
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        identity = secrets.token_hex(16)
        stamp = time.time()
        row = {'id': identity, 'token_hash': hashlib.sha256(token.encode()).hexdigest(),
               'label': data.label, 'role': data.role, 'scopes': json.dumps(data.scopes),
               'created_at': stamp, 'expires_at': stamp + data.expires_in_seconds,
               'revoked_at': None, 'issued_by': current['id']}
        store.execute('INSERT INTO agent_tokens VALUES(?,?,?,?,?,?,?,?,?)', tuple(row.values()))
        store.audit(current['username'], 'agent_token.issued', identity,
                    {'label': data.label, 'role': data.role, 'scopes': data.scopes, 'expires_at': row['expires_at']})
        return {'token': token, 'metadata': tokens.metadata(row)}

    @app.post('/api/agent-tokens/{identity}/rotate')
    def rotate(identity: str, current=Depends(human_admin)):
        # Rotation replaces the secret only. Identity, role, scopes and the
        # original expiry are kept, so a rotation schedule cannot renew a token
        # indefinitely and monitoring keeps one identity per client.
        with store.lock:
            row = store.one('SELECT * FROM agent_tokens WHERE id=?', (identity,))
            if not row:
                raise HTTPException(404, 'Unknown agent token')
            if row['revoked_at'] is not None:
                raise HTTPException(409, 'Revoked tokens cannot be rotated; issue a new token')
            if row['expires_at'] <= tokens.clock():
                raise HTTPException(409, 'Expired tokens cannot be rotated; issue a new token')
            token = TOKEN_PREFIX + secrets.token_urlsafe(32)
            store.execute('UPDATE agent_tokens SET token_hash=? WHERE id=?',
                          (hashlib.sha256(token.encode()).hexdigest(), identity))
            store.audit(current['username'], 'agent_token.rotated', identity,
                        {'label': row['label'], 'role': row['role'], 'scopes': json.loads(row['scopes']),
                         'expires_at': row['expires_at']})
            return {'token': token, 'metadata': tokens.metadata(store.one('SELECT * FROM agent_tokens WHERE id=?', (identity,)))}

    @app.delete('/api/agent-tokens/{identity}')
    def revoke(identity: str, current=Depends(human_admin)):
        with store.lock:
            row = store.one('SELECT * FROM agent_tokens WHERE id=?', (identity,))
            if not row:
                raise HTTPException(404, 'Unknown agent token')
            if row['revoked_at'] is None:
                store.execute('UPDATE agent_tokens SET revoked_at=? WHERE id=?', (time.time(), identity))
                store.audit(current['username'], 'agent_token.revoked', identity)
            return tokens.metadata(store.one('SELECT * FROM agent_tokens WHERE id=?', (identity,)))


def required_scope(method, path):
    if method == 'GET' and path == '/api/auth/me':
        return ''
    if method == 'GET' and (path == '/api/ssh-keys' or re.fullmatch(r'/api/ssh-keys/[0-9a-f]{32}', path)):
        return 'read:infrastructure'
    # SSH key writes and private export deliberately have no agent scope mapping.
    # Connection configuration never grants secret ingestion to machine clients.
    connection = r'/api/connections/[0-9a-f]{32}'
    if method == 'GET' and (path in {'/api/connections', '/api/connection-drivers'}
                            or re.fullmatch(connection, path)):
        return 'read:infrastructure'
    if (method == 'POST' and path == '/api/connections') or (
            method == 'PATCH' and re.fullmatch(connection, path)):
        return 'configure'
    if method == 'POST' and re.fullmatch(connection + r'/(test|discover)', path):
        return 'read:infrastructure'
    # Cloudflare connectors are reference-only records; credential ingestion
    # deliberately has no agent scope mapping (human administrator sessions only).
    cloudflare = r'/api/cloudflare/connectors/[0-9a-f]{32}'
    if method == 'GET' and (path == '/api/cloudflare/connectors'
                            or re.fullmatch(cloudflare, path)):
        return 'read:infrastructure'
    if (method == 'POST' and path == '/api/cloudflare/connectors') or (
            method == 'PATCH' and re.fullmatch(cloudflare, path)):
        return 'configure'
    # Cloudflare DNS reads are infrastructure evidence; mutations follow the
    # deployment governance: submit to propose, approve/reject only with the
    # explicit human-granted approve scope.
    dns_request = r'/api/cloudflare/dns/requests/[0-9a-f]{32}'
    if method == 'GET' and (path in {'/api/cloudflare/dns/zones', '/api/cloudflare/dns/records',
                                     '/api/cloudflare/dns/requests'}
                            or re.fullmatch(dns_request, path)):
        return 'read:infrastructure'
    if method == 'POST' and path == '/api/cloudflare/dns/requests':
        return 'submit'
    if method == 'POST' and re.fullmatch(dns_request + r'/(approve|reject)', path):
        return 'approve'
    # Cloudflare tunnels follow the same governance: reads are infrastructure
    # evidence, submit proposes, approve/reject needs the explicit human grant.
    tunnel_request = r'/api/cloudflare/tunnel/requests/[0-9a-f]{32}'
    tunnel_remote = r'/api/cloudflare/tunnel/tunnels/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
    if method == 'GET' and (path in {'/api/cloudflare/tunnel/tunnels',
                                     '/api/cloudflare/tunnel/requests'}
                            or re.fullmatch(tunnel_remote, path)
                            or re.fullmatch(tunnel_request, path)):
        return 'read:infrastructure'
    if method == 'POST' and path == '/api/cloudflare/tunnel/requests':
        return 'submit'
    if method == 'POST' and re.fullmatch(tunnel_request + r'/(approve|reject)', path):
        return 'approve'
    # A dashboard record is a link and a probe path, never a credential store.
    dashboard = r'/api/dashboards/[0-9a-f]{32}'
    if method == 'GET' and (path == '/api/dashboards'
                            or re.fullmatch(dashboard + r'(?:/embed)?', path)):
        return 'read:integrations'
    if ((method == 'POST' and path == '/api/dashboards')
            or (method in ('PATCH', 'DELETE') and re.fullmatch(dashboard, path))):
        return 'configure'
    if method == 'POST' and re.fullmatch(dashboard + r'/probe', path):
        return 'configure'
    # Stack state is infrastructure evidence; clearing a lease stays an
    # administrator action and is never a machine write path.
    state = r'/api/environment-states/[0-9a-f]{32}'
    if method == 'GET' and (path == '/api/environment-states' or re.fullmatch(state, path)):
        return 'read:infrastructure'
    # Drift is computed evidence about state the caller may already read; the
    # sweep itself writes a report and stays a human administrator action.
    if method == 'GET' and (path == '/api/environment-states/drift'
                            or re.fullmatch(state + r'/drift', path)):
        return 'read:infrastructure'
    if method == 'POST' and re.fullmatch(state + r'/lease/release', path):
        return 'configure'
    if method == 'GET':
        reads = {
            '/api/jobs': 'read:jobs', '/api/plugins': 'read:catalog', '/openapi.json': 'read:catalog',
            '/api/settings': 'read:settings', '/api/infrastructure': 'read:infrastructure',
            '/api/integrations': 'read:integrations', '/api/providers': 'read:integrations',
            '/api/extensions': 'read:integrations', '/api/diagnostics': 'read:diagnostics',
            '/api/blueprints': 'read:blueprints', '/api/audit': 'read:audit', '/api/finops': 'read:finops',
            '/api/overview': 'read:overview', '/api/operations': 'read:operations',
            '/api/operations/notifications': 'read:operations', '/metrics': 'read:operations', '/healthz': '',
            '/readyz': '',
        }
        if path in reads:
            return reads[path]
        if re.fullmatch(r'/api/jobs/[^/]+(?:/events)?', path):
            return 'read:jobs'
        if re.fullmatch(r'/api/blueprints/[^/]+', path):
            return 'read:blueprints'
    exact = {
        ('POST', '/api/plans/validate'): 'plan',
        ('POST', '/api/jobs'): 'submit',
        ('POST', '/api/blueprints'): 'submit',
        ('PATCH', '/api/settings'): 'configure',
        ('PATCH', '/api/providers'): 'configure',
        ('POST', '/api/diagnostics/run'): 'diagnose',
        ('POST', '/api/operations/notifications/retry'): 'notify',
        ('POST', '/api/chatops/actions'): 'chatops',
    }
    if (method, path) in exact:
        return exact[method, path]
    if method == 'POST' and re.fullmatch(r'/api/jobs/[^/]+/(approve|reject)', path):
        return 'approve'
    if method == 'POST' and re.fullmatch(r'/api/jobs/[^/]+/rollback', path):
        return 'rollback'
    if method == 'POST' and re.fullmatch(r'/api/extensions/[^/]+/(enable|disable)', path):
        return 'extensions'
    return None
