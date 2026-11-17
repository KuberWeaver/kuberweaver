"""Small PostgreSQL adapter for the store's deliberately limited SQL contract.

SQL is static application code; values are always passed to psycopg separately.
No driver exception (which can contain SQL, values or DSNs) crosses this boundary.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Any


class StorageError(RuntimeError):
    """Public, secret-free durable storage failure."""


COLUMNS = {
    'users': ('id', 'username', 'role', 'password_hash'),
    'sessions': ('token_hash', 'user_id', 'csrf', 'expires'),
    'audit': ('id', 'time', 'actor', 'action', 'resource', 'detail'),
    'jobs': ('id', 'body'), 'blueprints': ('id', 'body'),
    'settings': ('key', 'body'), 'connections': ('id', 'body'), 'ssh_keys': ('id', 'body'),
    'agent_tokens': ('id', 'token_hash', 'label', 'role', 'scopes', 'created_at', 'expires_at', 'revoked_at', 'issued_by'),
    'oidc_identities': ('id', 'issuer', 'subject', 'user_id', 'created_at', 'last_login'),
    'notification_deliveries': ('id', 'payload', 'status', 'attempts', 'error', 'updated'),
    'dashboards': ('id', 'body'),
    'dashboard_health': ('dashboard_id', 'body'),
    'environment_state': ('id', 'body'),
    'environment_state_documents': ('id', 'body'),
    'environment_state_claim': ('id', 'body'),
    'environment_state_adoption': ('uuid', 'body'),
    'environment_state_destroy': ('id', 'body'),
    'environment_state_drift': ('id', 'body'),
    'declaration_run': ('id', 'body'),
}
ORDERED = {'jobs', 'blueprints', 'settings', 'connections', 'notification_deliveries', 'dashboards',
           'environment_state'}
DDL = {
    'ssh_keys': 'id TEXT PRIMARY KEY, body TEXT NOT NULL',
    'users': 'id TEXT PRIMARY KEY, username TEXT UNIQUE, role TEXT, password_hash TEXT',
    'sessions': 'token_hash TEXT PRIMARY KEY, user_id TEXT, csrf TEXT, expires DOUBLE PRECISION',
    'audit': 'id BIGSERIAL PRIMARY KEY, time TEXT, actor TEXT, action TEXT, resource TEXT, detail TEXT',
    'agent_tokens': 'id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL, role TEXT NOT NULL, scopes TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL, revoked_at DOUBLE PRECISION, issued_by TEXT NOT NULL',
    'oidc_identities': 'id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, user_id TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, last_login DOUBLE PRECISION NOT NULL',
    'notification_deliveries': 'id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL, error TEXT, updated TEXT NOT NULL',
    'dashboard_health': 'dashboard_id TEXT PRIMARY KEY, body TEXT NOT NULL',
}
KEY_COLUMNS = {'settings': 'key', 'environment_state_adoption': 'uuid'}
for _table in ('jobs', 'blueprints', 'settings', 'connections', 'dashboards',
               'environment_state', 'environment_state_documents', 'environment_state_claim',
               'environment_state_adoption', 'environment_state_destroy',
               'environment_state_drift', 'declaration_run'):
    DDL[_table] = KEY_COLUMNS.get(_table, 'id') + ' TEXT PRIMARY KEY, body TEXT NOT NULL'
for _table in ORDERED:
    DDL[_table] += ', _kw_rowid BIGSERIAL UNIQUE NOT NULL'


def read_dsn_file(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
                raise StorageError('Database configuration must be a private 0600 file')
            value = source.read(65537).strip()
        if not value or len(value) > 65536:
            raise StorageError('Invalid database configuration')
        return value
    except StorageError:
        raise
    except Exception:
        raise StorageError('Database configuration must be a readable private file') from None


def configured_dsn(state_dir):
    explicit = os.environ.get('KUBERWEAVER_DATABASE_URL_FILE')
    default = Path(state_dir) / 'config' / 'database-url'
    if explicit is not None:
        if not explicit:
            raise StorageError('Invalid database configuration')
        return read_dsn_file(explicit)
    if default.exists() or default.is_symlink():
        return read_dsn_file(default)
    if 'KUBERWEAVER_DATABASE_URL' in os.environ:
        value = os.environ['KUBERWEAVER_DATABASE_URL'].strip()
        if not value:
            raise StorageError('Invalid database configuration')
        return value
    return None


# Preserve quoted strings/identifiers, comments and dollar-quoted literals.
_TOKENS = re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*(?:\n|$)|/\*.*?\*/|\$(?:[A-Za-z_]\w*)?\$.*?\$(?:[A-Za-z_]\w*)?\$)", re.S)


def translate_sql(sql, args=()):
    create = re.fullmatch(r'\s*CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(.*\)\s*;?\s*', sql, re.I | re.S)
    if create and create[1].lower() in DDL:
        name = create[1].lower()
        return f'CREATE TABLE IF NOT EXISTS {name} ({DDL[name]})'
    parts = _TOKENS.split(sql)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r'\browid\b', '_kw_rowid', parts[index], flags=re.I).replace('%', '%%').replace('?', '%s')
    # psycopg also parses percent signs inside literals when parameters are used.
    for index in range(1, len(parts), 2):
        parts[index] = parts[index].replace('%', '%%')
    result = ''.join(parts)
    insert = re.match(r'\s*INSERT\s+(?:OR\s+(REPLACE|IGNORE)\s+)?INTO\s+(\w+)\s*(\([^)]*\))?\s*VALUES', result, re.I)
    if insert:
        mode, table, columns = insert.groups()
        table = table.lower()
        if table not in COLUMNS:
            raise StorageError('Unsupported storage statement')
        if columns is None:
            names = COLUMNS[table]
            prefix = re.sub(r'\s+OR\s+(?:REPLACE|IGNORE)', '', result[insert.start():insert.end()], flags=re.I)
            prefix = re.sub(r'\bVALUES$', '(' + ','.join(names) + ') VALUES', prefix, flags=re.I)
            result = result[:insert.start()] + prefix + result[insert.end():]
        else:
            names = tuple(c.strip() for c in columns[1:-1].split(','))
            result = re.sub(r'\bINSERT\s+OR\s+(?:REPLACE|IGNORE)\s+INTO\b', 'INSERT INTO', result, count=1, flags=re.I)
        if mode:
            result = result.rstrip().rstrip(';')
            if mode.upper() == 'IGNORE':
                result += ' ON CONFLICT DO NOTHING'
            else:
                key = COLUMNS[table][0]
                updates = [f'{name}=EXCLUDED.{name}' for name in names if name != key]
                if table in ORDERED:
                    updates.append('_kw_rowid=EXCLUDED._kw_rowid')
                result += f' ON CONFLICT ({key}) DO UPDATE SET ' + ','.join(updates)
    return result


class Cursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def close(self):
        self._cursor.close()

    def execute(self, sql, args=()):
        try:
            self._cursor.execute(translate_sql(sql, args), args)
            return self
        except Exception:
            raise StorageError('PostgreSQL operation failed') from None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @staticmethod
    def _clean(row):
        return {key: value for key, value in row.items() if key != '_kw_rowid'} if row is not None else None

    def fetchone(self):
        try:
            return self._clean(self._cursor.fetchone())
        except Exception:
            raise StorageError('Database read failed') from None

    def fetchall(self):
        try:
            return [self._clean(row) for row in self._cursor.fetchall()]
        except Exception:
            raise StorageError('Database read failed') from None

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    def __init__(self, dsn, *, close_on_exit=False):
        try:
            import psycopg
            from psycopg.rows import dict_row
            self.raw: Any = psycopg.Connection[dict[str, Any]].connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5)
        except Exception:
            raise StorageError('PostgreSQL connection unavailable') from None
        self._contexts = []
        self._close_on_exit = close_on_exit

    def execute(self, sql, args=()):
        try:
            return Cursor(self.raw.execute(translate_sql(sql, args), args))
        except Exception:
            raise StorageError('PostgreSQL operation failed') from None

    def cursor(self):
        try:
            return Cursor(self.raw.cursor())
        except Exception:
            raise StorageError('PostgreSQL operation failed') from None

    def commit(self):
        try:
            self.raw.commit()
        except Exception:
            raise StorageError('PostgreSQL commit failed') from None

    def rollback(self):
        try:
            self.raw.rollback()
        except Exception:
            raise StorageError('PostgreSQL rollback failed') from None

    def close(self):
        self.raw.close()

    def __enter__(self):
        try:
            context = self.raw.transaction()
            context.__enter__()
            self._contexts.append(context)
            return self
        except Exception:
            if self._close_on_exit:
                self.close()
            raise StorageError('PostgreSQL transaction failed') from None

    def __exit__(self, *exc):
        try:
            return self._contexts.pop().__exit__(*exc)
        except Exception:
            raise StorageError('PostgreSQL transaction failed') from None
        finally:
            if self._close_on_exit and not self._contexts:
                self.close()


def acquire_controller_lock(connection):
    row = connection.execute("SELECT pg_try_advisory_lock(hashtext('kuberweaver.control-plane'),hashtext(current_schema())) AS acquired").fetchone()
    if not row or not row['acquired']:
        raise StorageError('PostgreSQL controller already active or schema unavailable')


def validate_schema(connection):
    """Reject foreign or lossy schemas rather than silently repairing them."""
    tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall()
    for row in tables:
        table = row['tablename']
        if table not in DDL:
            raise StorageError('Unexpected PostgreSQL schema')
        columns = connection.execute('''SELECT column_name,data_type,is_nullable,column_default
            FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=? ORDER BY ordinal_position''', (table,)).fetchall()
        expected = []
        for definition in DDL[table].split(', '):
            name, kind, *rest = definition.split()
            data_type = {'TEXT': 'text', 'BIGSERIAL': 'bigint', 'DOUBLE': 'double precision', 'INTEGER': 'integer'}[kind]
            nullable = 'NO' if ('PRIMARY KEY' in definition or 'NOT NULL' in definition) else 'YES'
            expected.append((name, data_type, nullable))
        if [(row['column_name'], row['data_type'], row['is_nullable']) for row in columns] != expected:
            raise StorageError('Unexpected PostgreSQL schema')
        for column in columns:
            generated = column['column_name'] == '_kw_rowid' or (table == 'audit' and column['column_name'] == 'id')
            if generated and not str(column['column_default']).startswith('nextval('):
                raise StorageError('Unexpected PostgreSQL schema')
        primary = connection.execute('''SELECT a.attname FROM pg_index i
            JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=ANY(i.indkey)
            WHERE n.nspname=current_schema() AND c.relname=? AND i.indisprimary''', (table,)).fetchall()
        if primary != [{'attname': COLUMNS[table][0]}]:
            raise StorageError('Unexpected PostgreSQL schema')
        unique_rows = connection.execute('''SELECT array_agg(a.attname ORDER BY a.attname) AS columns
            FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=ANY(i.indkey)
            WHERE n.nspname=current_schema() AND c.relname=? AND i.indisunique
                AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indisvalid
            GROUP BY i.indexrelid''', (table,)).fetchall()
        expected_unique = {(COLUMNS[table][0],)}
        if table == 'users':
            expected_unique.add(('username',))
        if table == 'agent_tokens':
            expected_unique.add(('token_hash',))
        if table in ORDERED:
            expected_unique.add(('_kw_rowid',))
        if {tuple(row['columns']) for row in unique_rows} != expected_unique:
            raise StorageError('Unexpected PostgreSQL schema')


def initialize(connection):
    with connection:
        validate_schema(connection)
        for table, definition in DDL.items():
            connection.execute(f'CREATE TABLE IF NOT EXISTS {table} ({definition})')
        validate_schema(connection)
