"""Session cookies, expiry, CSRF binding and login throttling."""
import secrets
import time

from fastapi.testclient import TestClient


def bootstrap(path):
    return dict(line.split(':', 1) for line in (path / 'bootstrap-credentials.txt').read_text().splitlines())


def app_for(tmp_path):
    from kuberweaver.app import create_app
    return create_app(state_dir=tmp_path, testing=True)


def login(client, path, *, username='admin', password=None):
    payload = {'username': username, 'password': password or bootstrap(path)[username]}
    return client.post('/api/auth/login', json=payload)


def test_login_cookie_is_http_only_strict_and_scoped(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        response = login(client, tmp_path)
        assert response.status_code == 200
        header = response.headers['set-cookie']
        assert header.startswith('kw_session=')
        assert 'HttpOnly' in header
        assert 'SameSite=strict' in header
        assert 'Path=/' in header
        assert 'Max-Age=28800' in header
        assert 'Secure' not in header
        assert response.json()['expires_in'] == 28800


def test_cookie_is_secure_over_https_and_absent_on_failure(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app, base_url='https://testserver') as client:
        response = login(client, tmp_path)
        assert response.status_code == 200
        assert 'Secure' in response.headers['set-cookie']
        failed = login(client, tmp_path, password='wrong password')
        assert failed.status_code == 401
        assert 'set-cookie' not in failed.headers


def test_idle_timeout_rejects_deletes_and_clears_the_cookie(tmp_path):
    from kuberweaver.sessions import digest
    app = app_for(tmp_path)
    with TestClient(app) as client:
        assert login(client, tmp_path).status_code == 200
        token = client.cookies['kw_session']
        assert client.get('/api/auth/me').status_code == 200
        app.state.store.execute('UPDATE sessions SET expires=? WHERE token_hash=?',
                                (time.time() - 1, digest(token)))
        response = client.get('/api/auth/me')
        assert response.status_code == 401
        assert 'Max-Age=0' in response.headers['set-cookie']
        assert app.state.store.one('SELECT 1 FROM sessions WHERE token_hash=?', (digest(token),)) is None


def test_absolute_deadline_rejects_an_active_token(tmp_path):
    from kuberweaver.sessions import digest
    app = app_for(tmp_path)
    with TestClient(app) as client:
        account = app.state.store.one("SELECT * FROM users WHERE role='admin'")
        # A live idle window does not rescue a token older than eight hours.
        token = f'{int(time.time()) - 9 * 3600}.{secrets.token_urlsafe(32)}'
        app.state.store.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                                (digest(token), account['id'], 'csrf', time.time() + 1800))
        client.cookies.set('kw_session', token)
        assert client.get('/api/auth/me').status_code == 401
        assert app.state.store.one('SELECT 1 FROM sessions WHERE token_hash=?', (digest(token),)) is None


def test_idle_window_slides_on_activity_then_expires(tmp_path):
    from kuberweaver.sessions import Sessions
    from kuberweaver.storage import Store
    store = Store(tmp_path)
    account = store.one("SELECT * FROM users WHERE role='admin'")
    now = [1_000_000.0]
    sessions = Sessions(store, clock=lambda: now[0])
    token, _ = sessions.create(account)
    now[0] += 1700
    assert sessions.resolve(token)['id'] == account['id']
    now[0] += 80
    assert sessions.resolve(token)['id'] == account['id']
    now[0] += 1700
    assert sessions.resolve(token)['id'] == account['id']
    now[0] += 1801
    assert sessions.resolve(token) is None
    store.conn.close()


def test_activity_cannot_extend_the_absolute_deadline(tmp_path):
    from kuberweaver.sessions import Sessions
    from kuberweaver.storage import Store
    store = Store(tmp_path)
    account = store.one("SELECT * FROM users WHERE role='admin'")
    now = [1_000_000.0]
    sessions = Sessions(store, clock=lambda: now[0])
    token, _ = sessions.create(account)
    for _ in range(16):
        now[0] += 1790
        assert sessions.resolve(token) is not None
    now[0] += 160
    assert sessions.resolve(token) is None
    store.conn.close()


def test_csrf_token_is_bound_to_the_session(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        csrf = login(client, tmp_path).json()['csrf_token']
        assert client.post('/api/auth/logout').status_code == 403
        assert client.post('/api/auth/logout', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
        assert client.request('PATCH', '/api/settings', json={}, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
        assert client.get('/api/auth/me').status_code == 200
        assert client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf}).status_code == 200


def test_csrf_token_from_another_session_is_refused(tmp_path):
    with TestClient(app_for(tmp_path)) as first:
        borrowed = login(first, tmp_path).json()['csrf_token']
        with TestClient(app_for(tmp_path)) as second:
            login(second, tmp_path)
            assert second.post('/api/auth/logout', headers={'X-CSRF-Token': borrowed}).status_code == 403
            assert second.get('/api/auth/me').status_code == 200


def test_logout_revokes_the_row_and_replay_is_refused(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        csrf = login(client, tmp_path).json()['csrf_token']
        token = client.cookies['kw_session']
        response = client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf})
        assert response.status_code == 200
        assert 'Max-Age=0' in response.headers['set-cookie']
        assert app.state.sessions.resolve(token) is None
    replay = TestClient(app)
    replay.cookies.set('kw_session', token)
    assert replay.get('/api/auth/me').status_code == 401


def test_login_replaces_the_presented_session(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        first = login(client, tmp_path)
        old = client.cookies['kw_session']
        second = login(client, tmp_path)
        new = client.cookies['kw_session']
        assert old != new
        assert app.state.sessions.resolve(old) is None
        me = client.get('/api/auth/me').json()
        assert me['csrf_token'] == second.json()['csrf_token']
        assert me['csrf_token'] != first.json()['csrf_token']


def test_login_throttle_blocks_brute_force_with_retry_after(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        for _ in range(10):
            assert login(client, tmp_path, password='wrong password').status_code == 401
        blocked = login(client, tmp_path, password='wrong password')
        assert blocked.status_code == 429
        assert int(blocked.headers['Retry-After']) >= 1
        assert login(client, tmp_path).status_code == 429
        assert login(client, tmp_path, username='operator').status_code == 200


def test_unknown_account_is_throttled_like_a_wrong_password(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        for _ in range(10):
            response = client.post('/api/auth/login', json={'username': 'ghost', 'password': 'x'})
            assert response.status_code == 401
            assert response.json()['detail'] == 'Invalid credentials'
        assert client.post('/api/auth/login', json={'username': 'ghost', 'password': 'x'}).status_code == 429


def test_throttle_window_expires_and_tracking_stays_bounded():
    from kuberweaver.login_throttle import LoginThrottle
    now = [0.0]
    throttle = LoginThrottle(account_limit=2, window=60, max_keys=4, clock=lambda: now[0])
    throttle.failure('client', 'admin')
    throttle.failure('client', 'admin')
    assert throttle.retry_after('client', 'admin') > 0
    throttle.success('client', 'admin')
    assert throttle.retry_after('client', 'admin') == 0
    now[0] += 61
    for index in range(20):
        throttle.failure(f'client-{index}', 'admin')
    assert len(throttle._buckets) <= 4


def test_tokens_are_stored_hashed_and_legacy_tokens_are_refused(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        assert login(client, tmp_path).status_code == 200
        token = client.cookies['kw_session']
        assert token.split('.', 1)[0].isdigit()
        assert token.encode() not in (tmp_path / 'platform.sqlite3').read_bytes()
        assert app.state.sessions.resolve(secrets.token_urlsafe(32)) is None


def test_session_survives_a_restart_within_both_windows(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        assert login(client, tmp_path).status_code == 200
        token = client.cookies['kw_session']
    with TestClient(app_for(tmp_path)) as restarted:
        restarted.cookies.set('kw_session', token)
        assert restarted.get('/api/auth/me').status_code == 200
