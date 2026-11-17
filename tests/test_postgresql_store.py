"""PostgreSQL contract tests. Integration requires a private dedicated DSN file."""
import os
from pathlib import Path
import pytest

from kuberweaver.storage import Store, StorageError


@pytest.fixture
def pg_file(tmp_path, monkeypatch):
    import uuid
    import psycopg
    from psycopg.conninfo import make_conninfo
    from psycopg import sql
    from kuberweaver.postgres import read_dsn_file
    source = Path(os.environ.get('KUBERWEAVER_TEST_DATABASE_URL_FILE', Path(__file__).parents[1] / 'data/postgresql/test-url'))
    if not source.exists():
        pytest.skip('Dedicated PostgreSQL test DSN file not configured')
    dsn = read_dsn_file(source)
    schema = 'kw_test_' + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    target = tmp_path / 'pg-url'
    target.write_text(make_conninfo(dsn, options='-csearch_path=' + schema))
    target.chmod(0o600)
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', str(target))
    try:
        yield target
    finally:
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        admin.close()


def test_postgresql_store_durable_contract(tmp_path, pg_file):
    from kuberweaver.agent_auth import AgentTokens
    from kuberweaver.sessions import Sessions
    store = Store(tmp_path / 'state')
    assert store.backend == 'postgresql'
    assert not (store.path / 'platform.sqlite3').exists()
    user = store.one("SELECT * FROM users WHERE role='admin'")
    token, csrf = Sessions(store).create(user)
    assert Sessions(store).resolve(token)['role'] == 'admin'
    assert Sessions(store).resolve(token)['csrf'] == csrf
    AgentTokens(store)
    import hashlib
    import secrets
    machine_token = 'kw_agent_' + secrets.token_urlsafe(32)
    machine_hash = hashlib.sha256(machine_token.encode()).hexdigest()
    store.execute('INSERT INTO agent_tokens VALUES(?,?,?,?,?,?,?,?,?)', ('id', machine_hash, 'label', 'viewer', '["read"]', 1794778991.1234567, 9999999999.987654, None, user['id']))
    assert store.one('SELECT * FROM agent_tokens WHERE id=?', ('id',))['token_hash'] == machine_hash
    principal = AgentTokens(store).authenticate(machine_token)
    assert principal['role'] == 'viewer'
    assert principal['scopes'] == ['read']
    store.execute('UPDATE agent_tokens SET revoked_at=? WHERE id=?', (1794779001.765432, 'id'))
    assert AgentTokens(store).authenticate(machine_token) is None
    store.put('jobs', 'old', {'exact': 'é'})
    store.put('jobs', 'new', {'exact': 'new'})
    store.put('jobs', 'old', {'exact': 'replaced'})
    assert store.list('jobs') == [{'exact': 'replaced'}, {'exact': 'new'}]
    assert set(store.one('SELECT * FROM jobs LIMIT 1')) == {'id', 'body'}
    store.audit('actor', 'test')
    store.conn.close()
    reopened = Store(store.path)
    assert Sessions(reopened).resolve(token)['id'] == user['id']
    assert reopened.get('jobs', 'old') == {'exact': 'replaced'}
    assert reopened.one('SELECT count(*) AS n FROM audit')['n'] == 1
    reopened.conn.close()


def test_postgresql_fresh_transaction_rollback_and_error_redaction(tmp_path, pg_file):
    store = Store(tmp_path / 'state')
    db = store.open_postgres_connection()
    with pytest.raises(StorageError) as error:
        with db:
            db.execute('INSERT INTO settings VALUES(?,?)', ('rollback', 'secret-value'))
            db.execute('INSERT INTO settings VALUES(?,?)', ('rollback', 'secret-value'))
    assert 'secret-value' not in str(error.value)
    assert db.raw.closed
    assert store.one('SELECT * FROM settings WHERE key=?', ('rollback',)) is None
    with store.open_postgres_connection() as db:
        db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('committed', '{}'))
        db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('committed', 'not-used'))
    assert store.get('settings', 'committed') == {}
    db.close()
    store.conn.close()


def test_postgresql_rejects_incompatible_schema_before_bootstrap(tmp_path, pg_file):
    from kuberweaver.postgres import Connection, read_dsn_file
    db = Connection(read_dsn_file(pg_file))
    db.execute('CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, user_id TEXT, csrf TEXT, expires REAL)')
    # execute handles only known CREATE IF NOT EXISTS DDL; this deliberately
    # creates an old/foreign float32 schema that Store must never accept.
    with pytest.raises(StorageError, match='schema'):
        Store(tmp_path / 'state')
    assert db.execute("SELECT 1 FROM pg_tables WHERE schemaname=current_schema() AND tablename='users'").fetchone() is None
    db.close()


def test_schema_without_unique_usernames_is_refused(tmp_path, pg_file):
    from kuberweaver.postgres import Connection, read_dsn_file
    db = Connection(read_dsn_file(pg_file))
    db.execute('CREATE TABLE users(id TEXT PRIMARY KEY, username TEXT, role TEXT, password_hash TEXT)')
    with pytest.raises(StorageError, match='schema'):
        Store(tmp_path / 'state')
    db.close()


def test_epoch_timestamp_precision_and_cursor(tmp_path, pg_file):
    store = Store(tmp_path / 'state')
    epoch = 1794778991.1234567
    store.execute('INSERT INTO sessions VALUES(?,?,?,?)', ('precise', 'uid', 'csrf', epoch))
    assert store.one('SELECT expires FROM sessions WHERE token_hash=?', ('precise',))['expires'] == epoch
    with store.open_postgres_connection() as db:
        with db.cursor() as cursor:
            assert cursor.execute("SELECT '?' AS literal, ? AS value", (epoch,)).fetchone() == {'literal': '?', 'value': epoch}
    store.conn.close()


def test_exclusive_controller_lock(tmp_path, pg_file):
    first = Store(tmp_path / 'one', exclusive=True)
    with pytest.raises(StorageError, match='active'):
        Store(tmp_path / 'two', exclusive=True)
    first.conn.close()
    second = Store(tmp_path / 'two', exclusive=True)
    second.conn.close()


def test_test_role_is_not_administrative(pg_file):
    from kuberweaver.postgres import Connection, read_dsn_file
    db = Connection(read_dsn_file(pg_file))
    assert db.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone() == {'rolsuper': False, 'rolcreatedb': False, 'rolcreaterole': False}
    db.close()


def test_exclusive_locks_are_schema_scoped(tmp_path, monkeypatch, pg_file):
    import uuid
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from kuberweaver.postgres import Connection, read_dsn_file
    first = Store(tmp_path / 'one', exclusive=True)
    db = Connection(read_dsn_file(pg_file))
    schema = 'kw_test_' + uuid.uuid4().hex
    db.raw.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    target = tmp_path / 'second-url'
    target.write_text(make_conninfo(read_dsn_file(pg_file), options='-csearch_path=' + schema))
    target.chmod(0o600)
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', str(target))
    second = None
    try:
        second = Store(tmp_path / 'two', exclusive=True)
        assert second.one('SELECT current_schema() AS schema')['schema'] == schema
    finally:
        if second is not None:
            second.conn.close()
        first.conn.close()
        db.raw.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        db.close()


def test_configured_postgresql_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL', 'postgresql://invalid:secret@127.0.0.1:1/nope?connect_timeout=1')
    with pytest.raises(Exception) as error:
        Store(tmp_path)
    assert 'secret' not in str(error.value)
    assert not (tmp_path / 'platform.sqlite3').exists()


def test_empty_explicit_database_file_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', '')
    with pytest.raises(StorageError, match='configuration'):
        Store(tmp_path)
    assert not (tmp_path / 'platform.sqlite3').exists()


def test_sql_translation_preserves_literals():
    from kuberweaver.postgres import translate_sql
    assert translate_sql("SELECT '?' AS q, ? AS value -- ?\n", (1,)) == "SELECT '?' AS q, %s AS value -- ?\n"
    sql = translate_sql('INSERT OR REPLACE INTO jobs(id,body) VALUES(?,?)', ('id', '{}'))
    assert 'ON CONFLICT (id) DO UPDATE' in sql
    assert '_kw_rowid' in sql
    assert translate_sql('insert into users values(?,?,?,?)').startswith('insert into users (id,username,role,password_hash) VALUES')


def test_private_config_file_required(tmp_path, monkeypatch):
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    file = tmp_path / 'database-url'
    file.write_text('postgresql://secret@localhost/nope')
    file.chmod(0o644)
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', str(file))
    with pytest.raises(Exception, match='private'):
        Store(tmp_path / 'data')


def test_bootstrap_existing_credentials_rolls_back_and_releases_lease(tmp_path, pg_file, monkeypatch):
    from kuberweaver.postgres import Connection, read_dsn_file
    state = tmp_path / 'state'
    state.mkdir()
    credentials = state / 'bootstrap-credentials.txt'
    credentials.write_bytes(b'keep existing bytes\n')
    connections = []
    original = Connection.__init__
    def track(self, *args, **kwargs):
        original(self, *args, **kwargs)
        connections.append(self)
    monkeypatch.setattr(Connection, '__init__', track)
    with pytest.raises(Exception):
        Store(state, exclusive=True)
    failed = connections[0]
    db = Connection(read_dsn_file(pg_file))
    try:
        assert db.execute('SELECT count(*) AS n FROM users').fetchone() == {'n': 0}
        assert failed.raw.closed
        assert credentials.read_bytes() == b'keep existing bytes\n'
        credentials.unlink()
        retry = Store(state, exclusive=True)
        retry.conn.close()
    finally:
        failed.close()
        db.close()


@pytest.mark.parametrize('committed', [False, True])
def test_bootstrap_commit_uncertainty_preserves_recovery_file(tmp_path, pg_file, monkeypatch, committed):
    from kuberweaver.postgres import Connection, read_dsn_file
    from kuberweaver.storage import check_password
    original = Connection.commit
    def fail(self):
        if committed:
            original(self)
        raise StorageError('PostgreSQL commit failed')
    monkeypatch.setattr(Connection, 'commit', fail)
    state = tmp_path / 'state'
    with pytest.raises(StorageError, match='uncertain'):
        Store(state, exclusive=True)
    credentials = state / 'bootstrap-credentials.txt'
    assert credentials.stat().st_mode & 0o777 == 0o600
    saved = dict(line.split(':', 1) for line in credentials.read_text().splitlines())
    db = Connection(read_dsn_file(pg_file))
    try:
        rows = db.execute('SELECT * FROM users').fetchall()
        assert len(rows) == (3 if committed else 0)
        for row in rows:
            assert row is not None
            assert check_password(saved[row['username']], row['password_hash'])
    finally:
        db.close()
    monkeypatch.setattr(Connection, 'commit', original)
    if committed:
        reopened = Store(state, exclusive=True)
        reopened.conn.close()
    else:
        with pytest.raises(FileExistsError):
            Store(state, exclusive=True)


@pytest.mark.parametrize('rollback_fails', [False, True])
def test_bootstrap_publication_failure_cleanup_requires_confirmed_rollback(tmp_path, pg_file, monkeypatch, rollback_fails):
    from kuberweaver.postgres import Connection, read_dsn_file, acquire_controller_lock
    def fail_sync(fd):
        raise OSError('injected publication failure')
    def fail_rollback(self):
        raise StorageError('PostgreSQL rollback failed')
    monkeypatch.setattr(os, 'fsync', fail_sync)
    if rollback_fails:
        monkeypatch.setattr(Connection, 'rollback', fail_rollback)
    state = tmp_path / 'state'
    with pytest.raises((OSError, StorageError)):
        Store(state, exclusive=True)
    assert (state / 'bootstrap-credentials.txt').exists() is rollback_fails
    db = Connection(read_dsn_file(pg_file))
    try:
        assert db.execute('SELECT count(*) AS n FROM users').fetchone() == {'n': 0}
        acquire_controller_lock(db)
    finally:
        db.close()


def test_existing_users_never_reseed_or_publish_credentials(tmp_path, pg_file):
    first = Store(tmp_path / 'first')
    before = first.all('SELECT * FROM users ORDER BY id')
    first.conn.close()
    second = Store(tmp_path / 'second')
    try:
        assert second.all('SELECT * FROM users ORDER BY id') == before
        assert not (second.path / 'bootstrap-credentials.txt').exists()
    finally:
        second.conn.close()


def test_sqlite_bootstrap_existing_file_rolls_back(tmp_path, monkeypatch):
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL_FILE', raising=False)
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    credentials = tmp_path / 'bootstrap-credentials.txt'
    credentials.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        Store(tmp_path)
    import sqlite3
    with sqlite3.connect(tmp_path / 'platform.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 0
    assert credentials.read_bytes() == b'preserve'
