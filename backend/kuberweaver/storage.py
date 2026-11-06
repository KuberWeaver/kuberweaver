"""Small durable store; all writers share one re-entrant lock."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000)
    return f'pbkdf2_sha256$600000${salt}${digest.hex()}'


def check_password(password, encoded):
    try:
        _, rounds, salt, digest = encoded.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(digest, actual)
    except (ValueError, TypeError):
        return False


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.chmod(0o700)
        self.lock = threading.RLock()
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
        if not self.conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            credentials = []
            for role in ('admin', 'operator', 'viewer'):
                password = secrets.token_urlsafe(24)
                self.conn.execute('INSERT INTO users VALUES (?,?,?,?)', (secrets.token_hex(12), role, role, hash_password(password)))
                credentials.append(f'{role}:{password}')
            target = self.path / 'bootstrap-credentials.txt'
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as file:
                file.write('\n'.join(credentials) + '\n')
            self.conn.commit()
        self.dummy_hash = hash_password(secrets.token_hex(24))

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
        assert table in {'jobs', 'blueprints', 'settings'}
        column = 'key' if table == 'settings' else 'id'
        self.execute(f'INSERT OR REPLACE INTO {table}({column},body) VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def get(self, table, key):
        assert table in {'jobs', 'blueprints', 'settings'}
        column = 'key' if table == 'settings' else 'id'
        row = self.one(f'SELECT body FROM {table} WHERE {column}=?', (key,))
        return json.loads(row['body']) if row else None

    def list(self, table):
        assert table in {'jobs', 'blueprints', 'settings'}
        return [json.loads(row['body']) for row in self.all(f'SELECT body FROM {table} ORDER BY rowid DESC')]

    def create_session(self, user):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user['id'], csrf, time.time() + 8 * 3600))
        return token, csrf

    def session(self, token):
        if not token:
            return None
        return self.one('SELECT users.id,username,role,csrf FROM sessions JOIN users ON user_id=users.id WHERE token_hash=? AND expires>?',
                        (hashlib.sha256(token.encode()).hexdigest(), time.time()))
