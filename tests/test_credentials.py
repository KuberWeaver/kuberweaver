"""First-startup credentials: a reachable development default, then a visible change.

The administrator must be able to sign in on a fresh install without opening a
secret file, so the seed uses `admin` unless `KUBERWEAVER_ADMIN_PASSWORD` says
otherwise. The server reports while that default is still active, and rotation
goes through the normal policy and removes the plaintext line.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def _policy():
    from kuberweaver import passwords
    return passwords


def _headers(csrf):
    return {'X-CSRF-Token': csrf}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv('KUBERWEAVER_ADMIN_PASSWORD', raising=False)
    from kuberweaver.app import create_app
    application = create_app(state_dir=tmp_path, testing=True)
    with TestClient(application) as client:
        yield client, application, tmp_path


def test_administrator_seed_is_the_development_default(monkeypatch):
    monkeypatch.delenv('KUBERWEAVER_ADMIN_PASSWORD', raising=False)
    assert _policy().seeded_password('admin') == 'admin'


def test_environment_overrides_every_seeded_role(monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_ADMIN_PASSWORD', 'set at deploy time')
    monkeypatch.setenv('KUBERWEAVER_VIEWER_PASSWORD', 'another one')
    passwords = _policy()
    assert passwords.seeded_password('admin') == 'set at deploy time'
    assert passwords.seeded_password('viewer') == 'another one'
    # An unset role keeps its random password; only admin has a fixed default.
    monkeypatch.delenv('KUBERWEAVER_OPERATOR_PASSWORD', raising=False)
    first = passwords.seeded_password('operator')
    second = passwords.seeded_password('operator')
    assert first != second and len(first) >= 24


def test_a_fresh_install_signs_in_with_the_development_default(api):
    client, app, path = api
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'})
    assert response.status_code == 200, response.text
    assert client.get('/api/auth/me').json()['default_admin_password'] is True


def test_default_admin_flag_clears_after_rotation(api):
    client, app, path = api
    csrf = client.post('/api/auth/login',
                       json={'username': 'admin', 'password': 'admin'}).json()['csrf_token']
    account = app.state.store.one('SELECT * FROM users WHERE username=?', ('admin',))

    rotated = client.post(f"/api/users/{account['id']}/password", headers=_headers(csrf),
                          json={'current_password': 'admin',
                                'new_password': 'a long enough passphrase 1'})
    assert rotated.status_code == 200, rotated.text
    assert client.get('/api/auth/me').json()['default_admin_password'] is False


def test_an_operator_chosen_seed_is_not_reported_as_a_default(tmp_path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_ADMIN_PASSWORD', 'chosen by the operator 42')
    from kuberweaver.app import create_app
    with TestClient(create_app(state_dir=tmp_path, testing=True)) as client:
        response = client.post('/api/auth/login',
                               json={'username': 'admin', 'password': 'chosen by the operator 42'})
        assert response.status_code == 200, response.text
        assert client.get('/api/auth/me').json()['default_admin_password'] is False
