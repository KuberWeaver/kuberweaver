"""Small durable store; all writers share one re-entrant lock."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from datetime import datetime, timezone

from .passwords import check_password, hash_password, seeded_password
from .postgres import Connection, StorageError, acquire_controller_lock, configured_dsn, initialize


def now():
    return datetime.now(timezone.utc).isoformat()


# The closed set of row collections this store exposes. Anything else is an
# unsupported statement, never an implicit table.
TABLES = frozenset({'jobs', 'blueprints', 'settings', 'studio_artifacts', 'studio_offerings'})

class Store:
    def __init__(self, path: Path, *, exclusive=False):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.chmod(0o700)
        self.lock = threading.RLock()
        self._dsn = configured_dsn(self.path)
        self.backend = 'postgresql' if self._dsn is not None else 'sqlite'
        if self.backend == 'postgresql':
            self.conn = Connection(self._dsn)
            try:
                if exclusive:
                    acquire_controller_lock(self.conn)
                initialize(self.conn)
            except BaseException:
                self.conn.close()
                raise
        else:
            self.conn = sqlite3.connect(self.path / 'platform.sqlite3', check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE, role TEXT, password_hash TEXT);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT, csrf TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT,actor TEXT,action TEXT,resource TEXT,detail TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS blueprints(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, body TEXT NOT NULL);
            ''')
            (self.path / 'platform.sqlite3').chmod(0o600)
        try:
            self._bootstrap()
            self.dummy_hash = hash_password(secrets.token_hex(24))
        except BaseException:
            self.conn.close()
            raise

    def _bootstrap(self):
        target = self.path / 'bootstrap-credentials.txt'
        created = None
        committing = False
        try:
            if self.backend == 'postgresql':
                self.conn.execute('BEGIN')
                # Serialize even non-exclusive Store callers during first seed.
                self.conn.execute('LOCK TABLE users IN EXCLUSIVE MODE')
            if not self.conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                credentials = []
                for role in ('admin', 'operator', 'viewer'):
                    password = seeded_password(role)
                    self.conn.execute('INSERT INTO users VALUES (?,?,?,?)', (secrets.token_hex(12), role, role, hash_password(password)))
                    credentials.append(f'{role}:{password}')
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                info = os.fstat(fd)
                created = (info.st_dev, info.st_ino)
                with os.fdopen(fd, 'w') as file:
                    file.write('\n'.join(credentials) + '\n')
                    file.flush()
                    os.fsync(file.fileno())
                directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            committing = True
            self.conn.commit()
        except BaseException:
            try:
                self.conn.rollback()
            except BaseException:
                # Lost acknowledgement: preserve recovery credentials.
                raise StorageError('Bootstrap outcome uncertain; preserve credentials and inspect database') from None
            if created is not None and not committing:
                info = target.lstat()
                if (info.st_dev, info.st_ino) == created:
                    target.unlink()
            if committing:
                # COMMIT may have succeeded before its acknowledgement was lost.
                raise StorageError('Bootstrap outcome uncertain; preserve credentials and inspect database') from None
            raise

    def open_postgres_connection(self):
        if self.backend != 'postgresql':
            raise StorageError('PostgreSQL is not configured')
        return Connection(self._dsn, close_on_exit=True)

    def execute(self, sql, args=()):
        with self.lock:
            cursor = self.conn.execute(sql, args)
            self.conn.commit()
            return cursor

    def all(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def audit(self, actor, action, resource='', detail=None):
        self.execute('INSERT INTO audit(time,actor,action,resource,detail) VALUES(?,?,?,?,?)',
                     (now(), actor, action, str(resource), json.dumps(detail or {}, ensure_ascii=False)))

    def put(self, table, key, value):
        assert table in TABLES
        column = 'key' if table == 'settings' else 'id'
        self.execute(f'INSERT OR REPLACE INTO {table}({column},body) VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def get(self, table, key):
        assert table in TABLES
        column = 'key' if table == 'settings' else 'id'
        row = self.one(f'SELECT body FROM {table} WHERE {column}=?', (key,))
        return json.loads(row['body']) if row else None

    def list(self, table):
        assert table in TABLES
        return [json.loads(row['body']) for row in self.all(f'SELECT body FROM {table} ORDER BY rowid DESC')]
