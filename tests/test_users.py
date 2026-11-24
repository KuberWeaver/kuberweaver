"""User store, password policy, role checks and bootstrap rotation."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from test_core_auth import bootstrap

STRONG = 'tidal harbour lantern 47'
ROTATED = 'harbour lantern tide 91'


@pytest.fixture
def api(tmp_path):
    from backend.app import create_app
    application = create_app(state_dir=tmp_path, testing=True)
    with TestClient(application) as client:
        yield client, application, tmp_path


def login(client, path, username='admin', password=None):
    password = bootstrap(path)[username] if password is None else password
    response = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    return response.json()['csrf_token']


def headers(csrf):
    return {'X-CSRF-Token': csrf}


def legacy_hash(password, salt='ab' * 16):
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000)
    return f'pbkdf2_sha256$600000${salt}${digest.hex()}'


def test_argon2id_hash_is_salted_and_verifiable():
    from backend.passwords import check_password, hash_password, needs_rehash

    encoded = hash_password(STRONG)
    assert encoded.startswith('$argon2id$v=19$m=19456,t=2,p=1$')
    assert check_password(STRONG, encoded)
    assert not check_password(ROTATED, encoded)
    assert hash_password(STRONG) != encoded
    assert not needs_rehash(encoded)
    assert not check_password(STRONG, 'not-a-hash')
    assert not check_password(STRONG, None)


def test_legacy_pbkdf2_account_keeps_working_and_upgrades_on_login(api):
    from backend.passwords import check_password, needs_rehash

    client, app, path = api
    legacy = legacy_hash(STRONG)
    assert check_password(STRONG, legacy) and needs_rehash(legacy)
    app.state.store.execute('UPDATE users SET password_hash=? WHERE username=?', (legacy, 'operator'))
    login(client, path, 'operator', STRONG)
    stored = app.state.store.one('SELECT password_hash FROM users WHERE username=?', ('operator',))
    assert stored['password_hash'].startswith('$argon2id$')


@pytest.mark.parametrize('password,reason', [
    ('short-pass1', 'too_short'),
    ('passwordpassword', 'common'),
    ('freshusername123', 'contains_username'),
    ('aaaaaaaaaaaaaa', 'repetitive'),
    (' ' * 20, 'blank'),
    ('x' * 513, 'too_long'),
])
def test_password_policy_refuses_weak_passwords(password, reason):
    from backend.passwords import PasswordPolicy, WeakPassword

    with pytest.raises(WeakPassword) as refusal:
        PasswordPolicy().validate(password, 'freshuser')
    assert refusal.value.reason == reason


def test_strong_passphrase_without_symbols_is_accepted():
    from backend.passwords import PasswordPolicy

    assert PasswordPolicy().validate('tidal harbour lantern 47', 'admin') == 'tidal harbour lantern 47'


def test_create_user_returns_metadata_never_the_hash(api):
    client, app, path = api
    csrf = login(client, path)
    response = client.post('/api/users', headers=headers(csrf),
                           json={'username': 'Deploy.Bot', 'password': STRONG, 'role': 'operator'})
    assert response.status_code == 201, response.text
    user = response.json()['user']
    assert user['username'] == 'deploy.bot'
    assert user['role'] == 'operator'
    assert set(user) == {'id', 'username', 'role'}
    row = app.state.store.one('SELECT * FROM users WHERE id=?', (user['id'],))
    assert row['password_hash'].startswith('$argon2id$')
    assert STRONG not in row['password_hash']
    assert client.get('/api/users').json()['roles'] == ['admin', 'operator', 'viewer']
    assert user in client.get('/api/users').json()['users']
    audit = app.state.store.one("SELECT * FROM audit WHERE action='user.created'")
    assert audit['resource'] == user['id'] and 'password' not in audit['detail']


def test_duplicate_username_is_refused_case_insensitively(api):
    client, _, path = api
    csrf = login(client, path)
    payload = {'username': 'deploy.bot', 'password': STRONG, 'role': 'operator'}
    assert client.post('/api/users', headers=headers(csrf), json=payload).status_code == 201
    duplicate = client.post('/api/users', headers=headers(csrf), json={**payload, 'username': 'DEPLOY.BOT'})
    assert duplicate.status_code == 409
    assert duplicate.json()['detail'] == 'Username already exists'


@pytest.mark.parametrize('username,role', [('bad name', 'admin'), ('okname', 'superuser'), ('ab', 'viewer')])
def test_invalid_username_or_role_is_refused(api, username, role):
    client, _, path = api
    csrf = login(client, path)
    response = client.post('/api/users', headers=headers(csrf),
                           json={'username': username, 'password': STRONG, 'role': role})
    assert response.status_code == 422
    assert [entry['username'] for entry in client.get('/api/users').json()['users']] == ['admin', 'operator', 'viewer']


def test_weak_password_is_refused_over_http(api):
    client, _, path = api
    csrf = login(client, path)
    response = client.post('/api/users', headers=headers(csrf),
                           json={'username': 'weakling', 'password': 'passwordpassword', 'role': 'viewer'})
    assert response.status_code == 422
    assert response.json()['detail'] == 'Password is too common'


@pytest.mark.parametrize('username', ['operator', 'viewer'])
def test_non_admin_roles_cannot_manage_users(api, username):
    client, _, path = api
    csrf = login(client, path, username)
    assert client.get('/api/users').status_code == 403
    assert client.post('/api/users', headers=headers(csrf),
                       json={'username': 'sneaky', 'password': STRONG, 'role': 'admin'}).status_code == 403


def test_self_rotation_requires_current_password_and_retires_bootstrap_line(api):
    client, app, path = api
    credentials = bootstrap(path)
    csrf = login(client, path)
    account = app.state.store.one('SELECT * FROM users WHERE username=?', ('admin',))
    old_token = client.cookies.get('kw_session')

    wrong = client.post(f"/api/users/{account['id']}/password", headers=headers(csrf),
                        json={'current_password': 'not the password', 'new_password': ROTATED})
    assert wrong.status_code == 403

    rotated = client.post(f"/api/users/{account['id']}/password", headers=headers(csrf),
                          json={'current_password': credentials['admin'], 'new_password': ROTATED})
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()['user']['username'] == 'admin'
    new_csrf = rotated.json()['csrf_token']
    assert new_csrf != csrf
    # Rotation revokes every prior session and the response reissues one.
    assert app.state.sessions.resolve(old_token) is None
    assert client.get('/api/auth/me').json()['user']['username'] == 'admin'

    remaining = dict(line.split(':', 1) for line in (path / 'bootstrap-credentials.txt').read_text().splitlines())
    assert set(remaining) == {'operator', 'viewer'}
    assert credentials['admin'] not in (path / 'bootstrap-credentials.txt').read_text()

    assert client.post('/api/auth/logout', headers=headers(new_csrf)).status_code == 200
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': ROTATED}).status_code == 200


def test_admin_reset_of_another_account_needs_no_current_password(api):
    client, _, path = api
    csrf = login(client, path)
    created = client.post('/api/users', headers=headers(csrf),
                          json={'username': 'marie', 'password': STRONG, 'role': 'operator'}).json()['user']
    reset = client.post(f"/api/users/{created['id']}/password", headers=headers(csrf),
                        json={'new_password': ROTATED})
    assert reset.status_code == 200, reset.text
    assert client.post('/api/auth/logout', headers=headers(csrf)).status_code == 200
    assert client.post('/api/auth/login', json={'username': 'marie', 'password': STRONG}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'marie', 'password': ROTATED}).status_code == 200


def test_non_admin_cannot_reset_another_account(api):
    client, app, path = api
    csrf = login(client, path, 'operator')
    viewer = app.state.store.one('SELECT * FROM users WHERE username=?', ('viewer',))
    response = client.post(f"/api/users/{viewer['id']}/password", headers=headers(csrf),
                           json={'new_password': ROTATED})
    assert response.status_code == 403
    assert response.json()['detail'] == 'Only an administrator can change another account'
    assert app.state.store.one('SELECT password_hash FROM users WHERE id=?', (viewer['id'],))['password_hash'].startswith('$argon2id$')


def test_rotation_refuses_the_current_password(api):
    client, app, path = api
    credentials = bootstrap(path)
    csrf = login(client, path)
    account = app.state.store.one('SELECT * FROM users WHERE username=?', ('admin',))
    response = client.post(f"/api/users/{account['id']}/password", headers=headers(csrf),
                           json={'current_password': credentials['admin'], 'new_password': credentials['admin']})
    assert response.status_code == 422
    assert response.json()['detail'] == 'New password must differ from the current one'


def test_last_admin_cannot_be_demoted(api):
    client, app, path = api
    csrf = login(client, path)
    admin = app.state.store.one('SELECT * FROM users WHERE username=?', ('admin',))
    refused = client.patch(f"/api/users/{admin['id']}", headers=headers(csrf), json={'role': 'viewer'})
    assert refused.status_code == 409
    assert refused.json()['detail'] == 'At least one administrator must remain'
    second = client.post('/api/users', headers=headers(csrf),
                         json={'username': 'second.admin', 'password': STRONG, 'role': 'admin'}).json()['user']
    assert client.post('/api/auth/logout', headers=headers(csrf)).status_code == 200
    csrf = login(client, path, 'second.admin', STRONG)
    assert client.patch(f"/api/users/{admin['id']}", headers=headers(csrf), json={'role': 'viewer'}).status_code == 200
    assert client.patch(f"/api/users/{second['id']}", headers=headers(csrf), json={'role': 'viewer'}).status_code == 409


def test_admin_reset_retires_the_seeded_plaintext(api):
    client, app, path = api
    csrf = login(client, path)
    operator = app.state.store.one('SELECT * FROM users WHERE username=?', ('operator',))
    response = client.post(f"/api/users/{operator['id']}/password", headers=headers(csrf),
                           json={'new_password': ROTATED})
    assert response.status_code == 200, response.text
    remaining = dict(line.split(':', 1) for line in (path / 'bootstrap-credentials.txt').read_text().splitlines())
    assert set(remaining) == {'admin', 'viewer'}


def test_agent_tokens_cannot_manage_users(api):
    client, _, path = api
    csrf = login(client, path)
    token = client.post('/api/agent-tokens', headers=headers(csrf),
                        json={'label': 'automation', 'role': 'admin', 'scopes': ['read'],
                              'expires_in_seconds': 3600}).json()['token']
    client.cookies.clear()
    response = client.get('/api/users', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 403
