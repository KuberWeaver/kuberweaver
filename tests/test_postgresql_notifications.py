"""PostgreSQL notification contracts; real isolated database integration below."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kuberweaver.notifications import NotificationError, WebhookNotifications
from test_notifications_delivery import setup_app, event, receiver


def test_postgresql_unavailable_never_creates_sqlite(tmp_path, receiver):
    app, config = setup_app(tmp_path, receiver)

    def unavailable():
        raise OSError('postgresql://SECRET@127.0.0.1/private')

    app.state.store = SimpleNamespace(backend='postgresql', open_postgres_connection=unavailable)
    with pytest.raises(NotificationError, match='^Notification storage unavailable$') as error:
        WebhookNotifications(app, config)
    assert 'SECRET' not in str(error.value)
    assert not list(app.state.state_dir.rglob('*.sqlite3'))
    assert not receiver.received


@pytest.fixture
def pg_store(tmp_path, monkeypatch):
    psycopg = pytest.importorskip('psycopg')
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg import sql
    from kuberweaver.postgres import read_dsn_file
    from kuberweaver.storage import Store

    source = Path(os.environ.get('KUBERWEAVER_TEST_DATABASE_URL_FILE',
                  Path(__file__).resolve().parents[1] / 'data/postgresql/test-url'))
    if not source.exists():
        pytest.skip('Private PostgreSQL test DSN file not configured')
    dsn = read_dsn_file(source)
    details = conninfo_to_dict(dsn)
    if not str(details.get('dbname', '')).endswith('_test'):
        pytest.fail('Notification integration requires a dedicated _test database')
    schema = 'kw_notify_test_' + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    scoped = make_conninfo(dsn, options=f'-csearch_path={schema}',
                           application_name=schema)
    private = tmp_path / 'test-database-url'
    private.write_text(scoped)
    private.chmod(0o600)
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', str(private))
    store = None
    try:
        store = Store(tmp_path / 'pg-state')
        assert store.backend == 'postgresql'
        yield store, schema
    finally:
        if store is not None:
            store.conn.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_postgresql_uses_real_store_and_closes_operation_connections(tmp_path, receiver, pg_store, monkeypatch):
    store, schema = pg_store
    connections = []
    original = store.open_postgres_connection

    def track_connection():
        connection = original()
        connections.append(connection)
        return connection

    monkeypatch.setattr(store, 'open_postgres_connection', track_connection)
    app, config = setup_app(tmp_path, receiver)
    app.state.store = store
    provider = WebhookNotifications(app, config)
    for _ in range(4):
        provider.send(event())
        assert provider.read()['deliveries'][0]['status'] == 'pending'
    opened = store.one('SELECT count(*) AS count FROM pg_stat_activity WHERE application_name=?',
                       (schema,))['count']
    assert opened == 1, 'Only the Store connection may remain open'
    assert not list(app.state.state_dir.rglob('*.sqlite3'))
    assert not receiver.received


def test_postgresql_delivery_dedup_order_json_and_network_outside_store_lock(tmp_path, receiver, pg_store, monkeypatch):
    store, _ = pg_store
    app, config = setup_app(tmp_path, receiver)
    app.state.store = store
    provider = WebhookNotifications(app, config)
    payload = event(event='before_plan', operation='validate', job_id=None, status='planning')
    first = provider.send(payload)
    second = provider.send(event(operation='rollback', status='rolled_back'))
    assert first['attempts'] == 0
    assert [row['id'] for row in provider.read()['deliveries']] == [first['id'], second['id']]
    assert provider.send(payload) == first
    persisted = store.one('SELECT payload FROM notification_deliveries WHERE id=?', (first['id'],))
    assert json.loads(persisted['payload']) == payload
    assert not receiver.received
    original = WebhookNotifications._post

    @contextmanager
    def checked_post(client, endpoint, body, identity):
        committed = store.one('SELECT status,attempts FROM notification_deliveries WHERE id=?', (identity,))
        assert committed == {'status': 'in_flight', 'attempts': 1}
        acquired = []

        def acquire():
            success = store.lock.acquire(timeout=1)
            acquired.append(success)
            if success:
                store.lock.release()

        thread = threading.Thread(target=acquire)
        thread.start()
        thread.join(timeout=2)
        assert acquired == [True]
        with original(client, endpoint, body, identity) as code:
            yield code

    monkeypatch.setattr(WebhookNotifications, '_post', checked_post)
    rows = provider.retry_pending()['deliveries']
    assert [row['status'] for row in rows] == ['delivered', 'delivered']
    assert receiver.received[0][2] == payload
    assert receiver.received[1][2]['operation'] == 'rollback'
    restarted = WebhookNotifications(app, config)
    assert restarted.send(payload)['id'] == first['id']
    assert restarted.retry_pending()['deliveries'] == []
    assert len(receiver.received) == 2
    assert not list(app.state.state_dir.rglob('*.sqlite3'))


def test_postgresql_pending_attempts_redaction_and_bounded_retry(tmp_path, receiver, pg_store):
    store, _ = pg_store
    app, config = setup_app(tmp_path, receiver)
    app.state.store = store
    provider = WebhookNotifications(app, config)
    queued = provider.send(event())
    receiver.code = 503
    assert provider.retry_pending()['deliveries'][0]['attempts'] == 1
    restarted = WebhookNotifications(app, config)
    assert restarted.send(event())['status'] == 'pending'
    assert restarted.retry_pending()['deliveries'][0]['attempts'] == 2
    assert restarted.retry_pending()['deliveries'][0]['status'] == 'exhausted'
    assert restarted.retry_pending()['deliveries'] == []
    assert len(receiver.received) == 3
    assert {headers['Idempotency-Key'] for _, headers, _ in receiver.received} == {queued['id']}
    evidence = json.dumps(store.all('SELECT * FROM notification_deliveries')) + json.dumps(provider.read())
    for secret in ('TOKEN_SECRET', 'RESPONSE_SECRET', '127.0.0.1', 'webhook/url'):
        assert secret not in evidence


def test_postgresql_crash_commits_inflight_and_never_replays_implicitly(tmp_path, receiver, pg_store, monkeypatch):
    store, _ = pg_store
    app, config = setup_app(tmp_path, receiver)
    app.state.store = store
    provider = WebhookNotifications(app, config)
    queued = provider.send(event())
    original = WebhookNotifications._post

    @contextmanager
    def crash(client, *args):
        with original(client, *args) as code:
            yield code
        raise SystemExit('simulated death')

    with monkeypatch.context() as patcher:
        patcher.setattr(WebhookNotifications, '_post', crash)
        with pytest.raises(SystemExit):
            provider.retry_pending()
    assert store.one('SELECT status,attempts FROM notification_deliveries WHERE id=?', (queued['id'],)) == {
        'status': 'in_flight', 'attempts': 1}
    restarted = WebhookNotifications(app, config)
    assert restarted.read()['deliveries'][0]['status'] == 'uncertain'
    assert restarted.send(event())['status'] == 'uncertain'
    assert restarted.retry_pending()['deliveries'] == []
    assert len(receiver.received) == 1
    assert restarted.retry_pending(include_uncertain=True)['deliveries'][0]['status'] == 'delivered'
    assert len(receiver.received) == 2
    assert receiver.received[0][2] == receiver.received[1][2]


def test_postgresql_failures_rollback_sanitize_and_never_touch_legacy_sqlite(tmp_path, receiver, pg_store):
    store, _ = pg_store
    app, config = setup_app(tmp_path, receiver)
    app.state.store = store
    directory = app.state.state_dir / 'notifications'
    directory.mkdir(parents=True)
    legacy = directory / 'deliveries.sqlite3'
    legacy.write_bytes(b'legacy-source-must-remain-untouched')
    provider = WebhookNotifications(app, config)
    queued = provider.send(event())
    with pytest.raises(NotificationError, match='^Notification storage unavailable$'):
        with provider._connect() as db:
            db.execute("UPDATE notification_deliveries SET status='delivered' WHERE id=?", (queued['id'],))
            db.execute('SELECT missing_SECRET_column FROM notification_deliveries')
    assert provider.read()['deliveries'][0]['status'] == 'pending'
    store.execute('ALTER TABLE notification_deliveries RENAME TO temporary_deliveries')
    try:
        for operation in (provider.read, lambda: provider.send(event()), provider.retry_pending,
                          lambda: WebhookNotifications(app, config)):
            with pytest.raises(NotificationError, match='^Notification storage unavailable$'):
                operation()
    finally:
        store.execute('ALTER TABLE temporary_deliveries RENAME TO notification_deliveries')
    assert legacy.read_bytes() == b'legacy-source-must-remain-untouched'
    assert provider.send(event())['id'] == queued['id']
    assert not receiver.received
