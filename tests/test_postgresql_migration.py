"""Conservative offline migration tests; PostgreSQL tests use unique schemas."""
import importlib.util
from pathlib import Path
import sqlite3
import pytest

from test_postgresql_store import pg_file
from kuberweaver.storage import Store, StorageError
from kuberweaver.postgres import Connection, read_dsn_file

SPEC = importlib.util.spec_from_file_location('migrate_postgresql', Path(__file__).parents[1] / 'scripts/migrate_postgresql.py')


def migration():
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def test_unknown_source_schema_refused(tmp_path):
    db = sqlite3.connect(tmp_path / 'platform.sqlite3')
    db.execute('CREATE TABLE surprise(secret TEXT)')
    db.commit()
    db.close()
    with pytest.raises(Exception, match='schema'):
        migration().inspect_source(tmp_path)


def test_active_source_refused(tmp_path):
    import fcntl
    with (tmp_path / '.server.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Exception, match='active'):
            with migration().source_lock(tmp_path):
                pass


def sqlite_source(tmp_path, monkeypatch):
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL_FILE', raising=False)
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    root = tmp_path / 'source'
    store = Store(root)
    store.execute('CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    from kuberweaver.agent_auth import AgentTokens
    AgentTokens(store)
    user = store.one("SELECT * FROM users WHERE role='admin'")
    token, csrf = store.create_session(user)
    store.execute('INSERT INTO agent_tokens VALUES(?,?,?,?,?,?,?,?,?)', ('token-id', 'opaque-hash', 'label', 'viewer', '[ "read" ]', 1794778991.1234567, 1794779999.9876542, 1794779000.7654321, user['id']))
    for identity, body in [('z', ' { "é":1, "x" : [ ] }\n'), ('a', '{"second": 2}')]:
        store.execute('INSERT INTO jobs(id,body) VALUES(?,?)', (identity, body))
    store.execute('DELETE FROM jobs WHERE id=?', ('z',))
    store.execute('INSERT INTO jobs(id,body) VALUES(?,?)', ('z', ' { "é":1, "x" : [ ] }\n'))
    store.audit('fixture', 'preserve')
    store.execute('UPDATE audit SET id=42')
    store.conn.close()
    outbox = root / 'notifications'
    outbox.mkdir()
    db = sqlite3.connect(outbox / 'deliveries.sqlite3')
    db.execute('CREATE TABLE deliveries(id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL, error TEXT, updated TEXT NOT NULL)')
    db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)', ('outbox', ' { "exact": "é" } ', 'uncertain', 2, 'unknown', 'timestamp'))
    db.commit()
    db.close()
    return root, user, token, csrf


def test_real_migration_preserves_bytes_order_sequences_and_auth(tmp_path, monkeypatch, pg_file):
    root, user, token, csrf = sqlite_source(tmp_path, monkeypatch)
    script = migration()
    before = script.inspect_source(root)
    report = script.migrate(root, pg_file)
    assert report['verified'] is True
    assert script.inspect_source(root) == before
    connection = Connection(read_dsn_file(pg_file))
    assert connection.execute('SELECT body FROM jobs ORDER BY rowid DESC').fetchall() == [{'body': row[2]} for row in reversed(before['jobs'])]
    assert connection.execute('SELECT * FROM notification_deliveries').fetchone()['payload'] == ' { "exact": "é" } '
    connection.execute('INSERT INTO audit(time,actor,action) VALUES(?,?,?)', ('now', 'fixture', 'next'))
    assert connection.execute('SELECT max(id) AS id FROM audit').fetchone()['id'] == 43
    connection.close()
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', str(pg_file))
    reopened = Store(tmp_path / 'reopened')
    assert reopened.session(token) == {'id': user['id'], 'username': 'admin', 'role': 'admin', 'csrf': csrf}
    assert reopened.one('SELECT * FROM users WHERE id=?', (user['id'],))['password_hash'] == user['password_hash']
    assert reopened.one('SELECT count(*) AS n FROM users')['n'] == 3
    reopened.conn.close()


def test_cli_migrates_only_fixture_source(tmp_path, monkeypatch, pg_file):
    import json
    import subprocess
    import sys
    root, *_ = sqlite_source(tmp_path, monkeypatch)
    result = subprocess.run([sys.executable, str(SPEC.origin), '--source-state-dir', str(root), '--target-dsn-file', str(pg_file)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['verified']
    assert report['tables']['audit']['count'] == 1
    assert report['tables']['notification_deliveries']['count'] == 1
    assert read_dsn_file(pg_file) not in result.stdout + result.stderr


def test_deleted_audit_high_water_mark_is_preserved(tmp_path, monkeypatch, pg_file):
    root, *_ = sqlite_source(tmp_path, monkeypatch)
    with sqlite3.connect(root / 'platform.sqlite3') as db:
        db.execute("INSERT INTO audit(id,action) VALUES(100,'deleted')")
        db.execute('DELETE FROM audit WHERE id=100')
    report = migration().migrate(root, pg_file)
    target = Connection(read_dsn_file(pg_file))
    try:
        target.execute("INSERT INTO audit(action) VALUES('next')")
        assert target.execute('SELECT max(id) AS id FROM audit').fetchone() == {'id': 101}
        assert report['sequences']['audit'] == {'source_sequence': 100, 'maximum': 42, 'next_value': 101}
    finally:
        target.close()


def test_migration_refuses_active_postgresql_controller(tmp_path, monkeypatch, pg_file):
    root, *_ = sqlite_source(tmp_path, monkeypatch)
    from kuberweaver.postgres import acquire_controller_lock
    active = Connection(read_dsn_file(pg_file))
    acquire_controller_lock(active)
    with pytest.raises(StorageError, match='active'):
        migration().migrate(root, pg_file)
    active.close()
    assert migration().migrate(root, pg_file)['verified']


def test_nonempty_target_refused_without_changes(tmp_path, monkeypatch, pg_file):
    root, *_ = sqlite_source(tmp_path, monkeypatch)
    db = Connection(read_dsn_file(pg_file))
    db.execute('CREATE TABLE foreign_marker (id TEXT)')
    with pytest.raises(StorageError, match='empty'):
        migration().migrate(root, pg_file)
    assert db.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall() == [{'tablename': 'foreign_marker'}]
    db.close()


def test_migration_verification_failure_rolls_back_all_ddl(tmp_path, monkeypatch, pg_file):
    root, *_ = sqlite_source(tmp_path, monkeypatch)
    # A SQLite BLOB in a dynamically typed TEXT column cannot be copied as TEXT
    # without changing bytes. The conservative path must reject the import.
    db = sqlite3.connect(root / 'platform.sqlite3')
    db.execute('UPDATE jobs SET body=? WHERE id=?', (b'bad\x00bytes', 'z'))
    db.commit()
    db.close()
    with pytest.raises(StorageError):
        migration().migrate(root, pg_file)
    target = Connection(read_dsn_file(pg_file))
    assert target.execute('SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema()').fetchall() == []
    target.close()


def test_failure_after_sequence_restart_rolls_back_sequences_and_rows(tmp_path, monkeypatch, pg_file):
    root, *_ = sqlite_source(tmp_path, monkeypatch)
    with sqlite3.connect(root / 'platform.sqlite3') as db:
        db.execute("INSERT INTO audit(id,action) VALUES(100,'deleted')")
        db.execute('DELETE FROM audit WHERE id=100')
    original = Connection.execute
    restarted = []
    def fail_after_restart(self, sql, args=()):
        result = original(self, sql, args)
        if sql.startswith('ALTER SEQUENCE audit_'):
            restarted.append(sql)
            assert self.raw.execute('SELECT last_value,is_called FROM audit_id_seq').fetchone() == {'last_value': 101, 'is_called': False}
            raise StorageError('Injected failure after sequence restart')
        return result
    monkeypatch.setattr(Connection, 'execute', fail_after_restart)
    with pytest.raises(StorageError, match='Injected'):
        migration().migrate(root, pg_file)
    assert restarted
    target = Connection(read_dsn_file(pg_file))
    try:
        assert target.execute('SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema()').fetchall() == []
    finally:
        target.close()
    monkeypatch.setattr(Connection, 'execute', original)
    assert migration().migrate(root, pg_file)['sequences']['audit']['next_value'] == 101
