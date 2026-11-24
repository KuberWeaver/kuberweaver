import hashlib
import json
import os
import time

import pytest
from fastapi.testclient import TestClient



@pytest.fixture
def platform(tmp_path):
    from backend.app import create_app
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as human:
        credentials = dict(line.split(':', 1) for line in (tmp_path / 'bootstrap-credentials.txt').read_text().splitlines())
        response = human.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        human.headers['x-csrf-token'] = response.json()['csrf_token']
        agent = TestClient(app)
        yield app, human, agent
        agent.close()


def issue(human, **changes):
    payload = {'label': 'automation-1', 'role': 'viewer', 'scopes': ['read'], 'expires_in_seconds': 3600}
    payload.update(changes)
    response = human.post('/api/agent-tokens', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_background_services_partial_startup_cleanup(tmp_path):
    from backend.app import create_app
    app = create_app(tmp_path, testing=True)
    events = []

    class Service:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail
        def start(self):
            events.append(self.name + '.start')
            if self.fail:
                raise RuntimeError('startup failed')
        def close(self):
            events.append(self.name + '.close')

    app.state.worker = Service('worker')
    assert isinstance(app.state.background_services, list)
    app.state.background_services.clear()
    app.state.background_services.extend([Service('first'), Service('second', True), Service('third')])
    with pytest.raises(RuntimeError, match='startup failed'):
        with TestClient(app):
            pass
    assert events == ['worker.start', 'first.start', 'second.start', 'worker.close', 'first.close']


def test_callback_only_exemption_preserves_browser_checks(platform):
    app, human, agent = platform
    from backend.chatops import install_chatops_api
    install_chatops_api(app)
    # No config: adapter itself rejects, rather than browser middleware.
    assert agent.post('/api/chatops/callback', json={}).status_code == 503
    assert agent.post('/api/chatops/actions', json={}).status_code == 401
    assert agent.post('/api/chatops/callback', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert agent.post('/api/chatops/callback', content=b'x' * 1_048_577).status_code == 413


def test_human_issues_hash_only_token_once(platform):
    app, human, agent = platform
    assert human.get('/api/agent-tokens').json()['tokens'] == []
    issued = issue(human)
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    me = agent.get('/api/auth/me')
    assert me.status_code == 200
    assert me.json()['csrf_token'] is None
    assert me.json()['user']['username'] == 'agent:' + issued['metadata']['id']
    assert me.json()['user']['label'] == 'automation-1'
    assert agent.get('/api/jobs').status_code == 200
    listing = human.get('/api/agent-tokens').json()['tokens']
    assert listing[0]['id'] == issued['metadata']['id']
    assert 'token' not in listing[0] and 'token_hash' not in listing[0]
    row = app.state.store.one('SELECT * FROM agent_tokens WHERE id=?', (issued['metadata']['id'],))
    assert row['token_hash'] == hashlib.sha256(issued['token'].encode()).hexdigest()
    assert issued['token'] not in json.dumps(app.state.store.all('SELECT * FROM audit'))
    assert os.stat(app.state.state_dir).st_mode & 0o777 == 0o700
    assert os.stat(app.state.state_dir / 'platform.sqlite3').st_mode & 0o777 == 0o600
    for path in app.state.state_dir.glob('platform.sqlite3-*'):
        assert path.stat().st_mode & 0o777 == 0o600


def test_notification_delivery_read_is_explicit_admin_read_scope(platform):
    app, human, agent = platform
    issued = issue(human, role='admin', scopes=['read'])
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    response = agent.get('/api/operations/notifications')
    assert response.status_code not in (401, 403), response.text
    assert agent.get('/api/operations/unknown').status_code == 403
    app.state.store.execute('UPDATE agent_tokens SET role=? WHERE id=?', ('viewer', issued['metadata']['id']))
    assert agent.get('/api/operations/notifications').status_code == 403


def test_scoped_writes_without_csrf_and_no_role_elevation(platform):
    app, human, agent = platform
    issued = issue(human, role='admin', scopes=['configure'])
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    assert agent.patch('/api/settings', json={}).status_code == 200
    assert agent.get('/api/settings').status_code == 403
    assert agent.post('/api/jobs/nope/approve').status_code == 403
    assert agent.patch('/api/settings', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert agent.patch('/api/settings', json={}, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert agent.get('/api/settings', headers={'Host': 'evil.example'}).status_code == 400
    # Even corrupted/migrated scope records cannot bypass existing role dependencies.
    app.state.store.execute('UPDATE agent_tokens SET role=? WHERE id=?', ('viewer', issued['metadata']['id']))
    assert agent.patch('/api/settings', json={}).status_code == 403
    audit = app.state.store.all('SELECT * FROM audit')
    assert any(row['actor'] == 'agent:' + issued['metadata']['id'] and row['action'] == 'settings.updated' for row in audit)


@pytest.mark.parametrize('method,path,scope,role', [
    ('POST', '/api/plans/validate', 'plan', 'viewer'),
    ('POST', '/api/jobs', 'submit', 'operator'),
    ('POST', '/api/blueprints', 'submit', 'operator'),
    ('POST', '/api/jobs/missing/approve', 'approve', 'admin'),
    ('POST', '/api/jobs/missing/reject', 'approve', 'admin'),
    ('POST', '/api/jobs/missing/rollback', 'rollback', 'admin'),
    ('PATCH', '/api/providers', 'configure', 'admin'),
    ('POST', '/api/extensions/missing/enable', 'extensions', 'admin'),
    ('POST', '/api/extensions/missing/disable', 'extensions', 'admin'),
    ('POST', '/api/chatops/actions', 'chatops', 'admin'),
    ('POST', '/api/diagnostics/run', 'diagnose', 'operator'),
    ('POST', '/api/operations/notifications/retry', 'notify', 'admin'),
])
def test_closed_operation_allowlist(platform, method, path, scope, role):
    app, human, agent = platform
    allowed = issue(human, role=role, scopes=[scope])
    agent.headers['Authorization'] = 'Bearer ' + allowed['token']
    response = agent.request(method, path, json={})
    assert response.status_code not in (401, 403), response.text
    denied = issue(human, role='admin', scopes=['read'])
    agent.headers['Authorization'] = 'Bearer ' + denied['token']
    assert agent.request(method, path, json={}).status_code == 403


def test_machine_management_unknown_routes_and_mixed_credentials_denied(platform):
    app, human, agent = platform
    issued = issue(human, role='admin', scopes=['read', 'approve', 'configure'])
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    for method, path in [('GET', '/api/agent-tokens'), ('POST', '/api/agent-tokens'),
                         ('DELETE', '/api/agent-tokens/' + issued['metadata']['id']),
                         ('POST', '/api/auth/logout'), ('POST', '/api/auth/login'),
                         ('GET', '/api/future'), ('PATCH', '/api/jobs'), ('GET', '/api/jobs/a/approve')]:
        assert agent.request(method, path, json={}).status_code == 403
    assert human.get('/api/auth/me', headers={'Authorization': agent.headers['Authorization']}).status_code == 401
    assert human.get('/api/auth/me', headers={'Authorization': 'Bearer invalid'}).status_code == 401
    agent.headers['Authorization'] = 'Bearer invalid'
    assert agent.patch('/api/settings', json={}).status_code == 401
    agent.headers['Authorization'] = 'Basic nope'
    assert agent.get('/api/auth/me').status_code == 401
    agent.headers.pop('Authorization')
    assert agent.get('/api/auth/me').status_code == 401
    csrf = human.headers.pop('x-csrf-token')
    assert human.patch('/api/settings', json={}).status_code == 403
    assert human.post('/api/agent-tokens', json={}).status_code == 403
    human.headers['x-csrf-token'] = csrf


def test_issuance_requires_human_admin_and_defaults_to_read(platform):
    app, human, agent = platform
    response = human.post('/api/agent-tokens', json={'label': 'default', 'role': 'admin', 'expires_in_seconds': 60})
    assert response.status_code == 201
    assert response.json()['metadata']['scopes'] == ['read']
    credentials = dict(line.split(':', 1) for line in (app.state.state_dir / 'bootstrap-credentials.txt').read_text().splitlines())
    for role in ('viewer', 'operator'):
        login = agent.post('/api/auth/login', json={'username': role, 'password': credentials[role]})
        agent.headers['x-csrf-token'] = login.json()['csrf_token']
        assert agent.get('/api/agent-tokens').status_code == 403
        assert agent.post('/api/agent-tokens', json={}).status_code == 403
        assert agent.delete('/api/agent-tokens/' + response.json()['metadata']['id']).status_code == 403
        agent.cookies.clear()


def test_revocation_expiry_and_persistence(platform):
    app, human, agent = platform
    issued = issue(human)
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    assert agent.get('/api/jobs').status_code == 200
    revoked = human.delete('/api/agent-tokens/' + issued['metadata']['id'])
    assert revoked.status_code == 200 and revoked.json()['revoked_at'] is not None
    assert human.get('/api/agent-tokens').json()['tokens'][0]['revoked_at'] == revoked.json()['revoked_at']
    assert agent.get('/api/jobs').status_code == 401
    assert agent.patch('/api/settings', json={}).status_code == 401
    assert human.get('/api/auth/me', headers={'Authorization': agent.headers['Authorization']}).status_code == 401
    expired = issue(human)
    agent.headers['Authorization'] = 'Bearer ' + expired['token']
    app.state.store.execute('UPDATE agent_tokens SET expires_at=? WHERE id=?', (time.time() - 1, expired['metadata']['id']))
    assert agent.get('/api/jobs').status_code == 401
    assert expired['token'] not in json.dumps(app.state.store.all('SELECT * FROM audit'))
    from backend.agent_auth import AgentTokens
    from backend.storage import Store
    reopened = AgentTokens(Store(app.state.state_dir))
    assert reopened.authenticate(issued['token']) is None
    live = issue(human)
    assert reopened.authenticate(live['token'])['token_id'] == live['metadata']['id']


@pytest.mark.parametrize('changes', [
    {'role': 'root'}, {'role': 'viewer', 'scopes': ['approve']},
    {'role': 'operator', 'scopes': ['configure']}, {'scopes': ['*']},
    {'scopes': []}, {'scopes': ['read', 'read']}, {'expires_in_seconds': 59},
    {'expires_in_seconds': 2592001}, {'expires_in_seconds': True},
    {'label': 'spoof\nadmin'}, {'label': ''},
])
def test_issuance_constraints(platform, changes):
    app, human, agent = platform
    payload = {'label': 'test', 'role': 'viewer', 'scopes': ['read'], 'expires_in_seconds': 3600, **changes}
    assert human.post('/api/agent-tokens', json=payload).status_code == 422
    assert human.get('/api/agent-tokens').json()['tokens'] == []


def test_rotation_replaces_only_the_secret(platform):
    app, human, agent = platform
    issued = issue(human)
    identity = issued['metadata']['id']
    before = app.state.store.one('SELECT * FROM agent_tokens WHERE id=?', (identity,))
    response = human.post(f'/api/agent-tokens/{identity}/rotate')
    assert response.status_code == 200, response.text
    rotated = response.json()
    assert rotated['token'] != issued['token']
    assert rotated['metadata']['id'] == identity
    assert rotated['metadata']['label'] == issued['metadata']['label']
    assert rotated['metadata']['role'] == issued['metadata']['role']
    assert rotated['metadata']['scopes'] == issued['metadata']['scopes']
    assert rotated['metadata']['expires_at'] == before['expires_at']
    assert rotated['metadata']['revoked_at'] is None
    after = app.state.store.one('SELECT * FROM agent_tokens WHERE id=?', (identity,))
    assert after['created_at'] == before['created_at']
    assert after['expires_at'] == before['expires_at']
    assert after['token_hash'] == hashlib.sha256(rotated['token'].encode()).hexdigest()
    assert after['token_hash'] != hashlib.sha256(issued['token'].encode()).hexdigest()
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    assert agent.get('/api/jobs').status_code == 401
    agent.headers['Authorization'] = 'Bearer ' + rotated['token']
    assert agent.get('/api/jobs').status_code == 200
    assert [row['id'] for row in human.get('/api/agent-tokens').json()['tokens']] == [identity]
    rotated_audit = [row for row in app.state.store.all('SELECT * FROM audit') if row['action'] == 'agent_token.rotated']
    assert len(rotated_audit) == 1
    assert rotated_audit[0]['actor'] == 'admin' and rotated_audit[0]['resource'] == identity
    assert issued['token'] not in json.dumps(rotated_audit) and rotated['token'] not in json.dumps(rotated_audit)


def test_rotation_refuses_revoked_expired_and_unknown_tokens(platform):
    app, human, agent = platform
    revoked = issue(human)
    assert human.delete('/api/agent-tokens/' + revoked['metadata']['id']).status_code == 200
    stored = app.state.store.one('SELECT * FROM agent_tokens WHERE id=?', (revoked['metadata']['id'],))
    response = human.post(f"/api/agent-tokens/{revoked['metadata']['id']}/rotate")
    assert response.status_code == 409
    assert app.state.store.one('SELECT * FROM agent_tokens WHERE id=?', (revoked['metadata']['id'],))['token_hash'] == stored['token_hash']
    expired = issue(human)
    app.state.store.execute('UPDATE agent_tokens SET expires_at=? WHERE id=?', (time.time() - 1, expired['metadata']['id']))
    stale = app.state.store.one('SELECT * FROM agent_tokens WHERE id=?', (expired['metadata']['id'],))
    assert human.post(f"/api/agent-tokens/{expired['metadata']['id']}/rotate").status_code == 409
    assert app.state.store.one('SELECT * FROM agent_tokens WHERE id=?', (expired['metadata']['id'],))['token_hash'] == stale['token_hash']
    assert human.post('/api/agent-tokens/unknown/rotate').status_code == 404


def test_rotation_is_human_admin_only_and_needs_csrf(platform):
    app, human, agent = platform
    issued = issue(human)
    identity = issued['metadata']['id']
    credentials = dict(line.split(':', 1) for line in (app.state.state_dir / 'bootstrap-credentials.txt').read_text().splitlines())
    for role in ('viewer', 'operator'):
        login = agent.post('/api/auth/login', json={'username': role, 'password': credentials[role]})
        agent.headers['x-csrf-token'] = login.json()['csrf_token']
        assert agent.post(f'/api/agent-tokens/{identity}/rotate').status_code == 403
        agent.cookies.clear()
        agent.headers.pop('x-csrf-token')
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    assert agent.post(f'/api/agent-tokens/{identity}/rotate').status_code == 403
    agent.headers.pop('Authorization')
    csrf = human.headers.pop('x-csrf-token')
    assert human.post(f'/api/agent-tokens/{identity}/rotate').status_code == 403
    human.headers['x-csrf-token'] = csrf
    row = app.state.store.one('SELECT * FROM agent_tokens WHERE id=?', (identity,))
    assert row['token_hash'] == hashlib.sha256(issued['token'].encode()).hexdigest()


def test_each_authenticated_agent_request_is_audited(platform):
    app, human, agent = platform
    issued = issue(human, role='operator', scopes=['read:jobs'])
    identity = issued['metadata']['id']
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    assert agent.get('/api/jobs').status_code == 200
    uses = app.state.store.all("SELECT * FROM audit WHERE action='agent.request' ORDER BY id DESC")
    assert len(uses) == 1
    assert uses[0]['actor'] == 'agent:' + identity and uses[0]['resource'] == identity
    assert json.loads(uses[0]['detail']) == {'method': 'GET', 'path': '/api/jobs', 'status': 200, 'scope': 'read:jobs'}
    assert issued['token'] not in json.dumps(uses)
    assert agent.get('/api/infrastructure').status_code == 403
    assert len(app.state.store.all("SELECT * FROM audit WHERE action='agent.request'")) == 1
    denials = app.state.store.all("SELECT * FROM audit WHERE action='agent.request_denied' ORDER BY id DESC")
    assert json.loads(denials[0]['detail']) == {'method': 'GET', 'scope': 'read:infrastructure'}
    assert denials[0]['actor'] == 'agent:' + identity


def test_agent_use_audit_is_bounded_and_keeps_decisions(tmp_path):
    from backend.agent_auth import AgentTokens
    from backend.storage import Store
    store = Store(tmp_path)
    tokens = AgentTokens(store, use_audit_retention=2, use_audit_prune_interval=0)
    store.audit('admin', 'agent_token.issued', 'token-1')
    principal = {'username': 'agent:token-1', 'token_id': 'token-1'}
    for _ in range(4):
        tokens.record_use(principal, 'GET', '/api/jobs', 200, 'read:jobs')
    uses = store.all("SELECT * FROM audit WHERE action='agent.request'")
    assert len(uses) == 2
    decisions = json.loads(json.dumps(store.all("SELECT * FROM audit WHERE action='agent_token.issued'")))
    assert [row['resource'] for row in decisions] == ['token-1']


def test_read_only_agent_reads_infrastructure_but_cannot_write(platform):
    app, human, agent = platform
    issued = issue(human, role='operator', scopes=['read:infrastructure'])
    agent.headers['Authorization'] = 'Bearer ' + issued['token']
    assert agent.get('/api/infrastructure').status_code == 200
    assert agent.get('/api/jobs').status_code == 403
    assert agent.get('/api/audit').status_code == 403
    assert agent.post('/api/jobs', json={}).status_code == 403
    assert agent.patch('/api/settings', json={}).status_code == 403
    assert agent.post('/api/plans/validate', json={}).status_code == 403
    assert agent.get('/api/auth/me').json()['user']['scopes'] == ['read:infrastructure']
