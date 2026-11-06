import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def bootstrap(path):
    return dict(line.split(':', 1) for line in (path / 'bootstrap-credentials.txt').read_text().splitlines())


def test_persistent_auth_csrf_and_security_boundary(tmp_path):
    from fastapi.testclient import TestClient
    from kuberweaver.app import create_app

    app = create_app(state_dir=tmp_path, testing=True)
    with TestClient(app) as client:
        assert client.get('/api/auth/me').status_code == 401
        credentials = bootstrap(tmp_path)
        assert os.stat(tmp_path).st_mode & 0o777 == 0o700
        assert os.stat(tmp_path / 'bootstrap-credentials.txt').st_mode & 0o777 == 0o600
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'incorrect'}).status_code == 401
        response = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        assert response.status_code == 200
        assert response.json()['user']['role'] == 'admin'
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'SameSite=strict' in response.headers['set-cookie']
        csrf = response.json()['csrf_token']
        assert client.get('/api/auth/me').json()['csrf_token'] == csrf
        assert client.post('/api/auth/logout').status_code == 403
        assert client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf, 'Origin': 'https://evil.example'}).status_code == 403
        assert client.get('/api/auth/me', headers={'Host': 'evil.example'}).status_code == 400
        assert client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf}).status_code == 200
        assert client.get('/api/auth/me').status_code == 401
    with TestClient(create_app(state_dir=tmp_path, testing=True)) as client:
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']}).status_code == 200
        db = (tmp_path / 'platform.sqlite3').read_bytes()
        assert credentials['admin'].encode() not in db
