#!/usr/bin/env python3
"""Offline, lossless SQLite → empty PostgreSQL schema migration.

No Store is constructed: migration must never bootstrap accounts. Source databases
are opened read-only and backed up into memory while the server lock is held.
No source database or source directory is removed or rewritten.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from kuberweaver.postgres import COLUMNS, DDL, ORDERED, Connection, StorageError, acquire_controller_lock, initialize, read_dsn_file


@contextmanager
def source_lock(state_dir):
    """Hold the same process lock as the server throughout snapshot and import."""
    handles = []
    try:
        # Also recognize older installations using the unprefixed lock name.
        for name in ('.server.lock', 'server.lock'):
            path = Path(state_dir) / name
            if name == 'server.lock' and not path.exists():
                continue
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            handles.append(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StorageError('Source server is active; stop it before migration') from None
        yield
    finally:
        for fd in handles:
            os.close(fd)


def _snapshot(path):
    if not path.is_file() or path.is_symlink():
        raise StorageError('Source database unavailable')
    source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    backup = sqlite3.connect(':memory:')
    try:
        source.backup(backup)
    finally:
        source.close()
    if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        backup.close()
        raise StorageError('Source integrity check failed')
    return backup


class SourceRows(dict):
    """Rows and AUTOINCREMENT history from the same read-only snapshot."""
    audit_sequence = 0


def _read_database(path, allowed, required, rename=None):
    db = _snapshot(path)
    try:
        objects = db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        tables = {name for kind, name in objects if kind == 'table'}
        if not required.issubset(tables) or tables - allowed or any(kind != 'table' for kind, _ in objects):
            raise StorageError('Unexpected source schema')
        result = SourceRows()
        for original in sorted(tables):
            table = (rename or {}).get(original, original)
            info = db.execute(f'PRAGMA table_info({original})').fetchall()
            if tuple(row[1] for row in info) != COLUMNS[table]:
                raise StorageError('Unexpected source schema')
            # Validate affinity, primary key, defaults and nullability against the
            # exact historical schema, not just a matching set of column names.
            expected = sqlite3.connect(':memory:')
            try:
                definition = DDL[table].split(', _kw_rowid')[0].replace('BIGSERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT').replace('DOUBLE PRECISION', 'REAL')
                expected.execute(f'CREATE TABLE {original} ({definition})')
                canonical = expected.execute(f'PRAGMA table_info({original})').fetchall()
                if info != canonical:
                    raise StorageError('Unexpected source schema')
                actual_indexes = db.execute(f'PRAGMA index_list({original})').fetchall()
                expected_indexes = expected.execute(f'PRAGMA index_list({original})').fetchall()
                if [(r[2], r[3], r[4]) for r in actual_indexes] != [(r[2], r[3], r[4]) for r in expected_indexes]:
                    raise StorageError('Unexpected source schema')
                actual_keys = sorted(tuple(r[2] for r in db.execute(f'PRAGMA index_info({index[1]})')) for index in actual_indexes)
                expected_keys = sorted(tuple(r[2] for r in expected.execute(f'PRAGMA index_info({index[1]})')) for index in expected_indexes)
                if actual_keys != expected_keys or db.execute(f'PRAGMA foreign_key_list({original})').fetchall():
                    raise StorageError('Unexpected source schema')
                schema_sql = db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', ('table', original)).fetchone()[0]
                # CHECK, REFERENCES and COLLATE are not represented by table_info.
                import re
                if re.search(r'\b(CHECK|REFERENCES|COLLATE|GENERATED|WITHOUT|STRICT)\b', schema_sql, re.I):
                    raise StorageError('Unexpected source schema')
            finally:
                expected.close()
            rows = db.execute(f'SELECT rowid,* FROM {original} ORDER BY rowid').fetchall()
            result[table] = rows
            if table == 'audit':
                sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (original,)).fetchone()
                result.audit_sequence = sequence[0] if sequence else 0
                if not isinstance(result.audit_sequence, int) or result.audit_sequence < 0:
                    raise StorageError('Invalid source audit sequence')
        return result
    finally:
        db.close()


def inspect_source(state_dir):
    state_dir = Path(state_dir)
    required = {'users', 'sessions', 'audit', 'jobs', 'blueprints', 'settings'}
    result = _read_database(state_dir / 'platform.sqlite3', required | {'agent_tokens', 'connections',
                                                                        'studio_artifacts',
                                                                        'studio_offerings',
                                                                        'environment_state',
                                                                        'environment_state_documents',
                                                                        'environment_state_claim',
                                                                        'environment_state_adoption',
                                                                        'environment_state_destroy',
                                                                        'environment_state_drift',
                                                                        'declaration_run'}, required)
    candidates = [state_dir / 'notifications' / 'deliveries.sqlite3',
                  state_dir / 'data' / 'notifications' / 'deliveries.sqlite3']
    outboxes = [path for path in candidates if path.exists() or path.is_symlink()]
    if len(outboxes) > 1:
        raise StorageError('Ambiguous source notification databases')
    for outbox in outboxes:
        result.update(_read_database(outbox, {'deliveries'}, {'deliveries'}, {'deliveries': 'notification_deliveries'}))
    return result


def row_digest(rows):
    """Typed, length-framed bytes: no JSON decoding, float rounding or text edits."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(b'R')
        for value in row:
            if value is None:
                tag, data = b'n', b''
            elif isinstance(value, str):
                tag, data = b's', value.encode('utf-8')
            elif isinstance(value, bytes):
                tag, data = b'b', value
            elif isinstance(value, int):
                tag, data = b'i', str(value).encode('ascii')
            elif isinstance(value, float):
                tag, data = b'f', struct.pack('!d', value)
            else:
                raise StorageError('Unsupported source value type')
            digest.update(tag + struct.pack('!Q', len(data)) + data)
    return digest.hexdigest()


def migrate(source_state_dir, target_dsn_file):
    """Import atomically, verify every row and sequence, return a secret-free report."""
    connection = None
    try:
        with source_lock(source_state_dir):
            source = inspect_source(source_state_dir)
            connection = Connection(read_dsn_file(target_dsn_file))
            acquire_controller_lock(connection)
            report = {}
            sequences = {}
            with connection:
                # Reject *any* pre-existing relation: even an empty application
                # table could carry foreign constraints, triggers or defaults.
                existing = connection.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() LIMIT 1").fetchone()
                if existing:
                    raise StorageError('Target schema must be empty')
                if not connection.execute('SELECT current_schema() AS name').fetchone()['name']:
                    raise StorageError('Target schema unavailable')
                initialize(connection)
                for table, rows in source.items():
                    columns = COLUMNS[table]
                    inserted = columns + (('_kw_rowid',) if table in ORDERED else ())
                    sql = f"INSERT INTO {table}({','.join(inserted)}) VALUES({','.join('?' for _ in inserted)})"
                    for row in rows:
                        values = tuple(row[1:]) + ((row[0],) if table in ORDERED else ())
                        connection.execute(sql, values)
                    # Compare all original bytes plus ordering, not model JSON.
                    order = '_kw_rowid' if table in ORDERED else ('id' if table == 'audit' else COLUMNS[table][0])
                    readback = connection.raw.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY {order}").fetchall()
                    actual = [tuple(row[column] for column in columns) for row in readback]
                    expected = [tuple(row[1:]) for row in rows]
                    if table not in ORDERED and table != 'audit':
                        sort_key = lambda row: (row[0] is not None, row[0])
                        expected.sort(key=sort_key)
                        actual.sort(key=sort_key)
                    # SQLite permits integers in REAL columns and PostgreSQL
                    # returns floats; canonicalize only these numeric affinities.
                    numeric = {'sessions': (3,), 'agent_tokens': (5, 6, 7)}.get(table, ())
                    expected = [tuple(float(value) if i in numeric and value is not None else value for i, value in enumerate(row)) for row in expected]
                    if len(actual) != len(expected) or row_digest(actual) != row_digest(expected):
                        raise StorageError('Migration row verification failed')
                    if table in ORDERED:
                        ordinals = [row['_kw_rowid'] for row in connection.raw.execute(f'SELECT _kw_rowid FROM {table} ORDER BY _kw_rowid').fetchall()]
                        if ordinals != [row[0] for row in rows]:
                            raise StorageError('Migration order verification failed')
                    report[table] = {'count': len(actual), 'sha256': row_digest(actual)}
                for table in ('audit', *sorted(ORDERED)):
                    column = 'id' if table == 'audit' else '_kw_rowid'
                    # Restart is transactional (unlike setval), so rollback also
                    # restores sequence state. Generated names are trusted DDL.
                    maximum = connection.raw.execute(f'SELECT max({column}) AS maximum FROM {table}').fetchone()['maximum']
                    historical = source.audit_sequence if table == 'audit' else 0
                    next_value = max(0, historical, maximum or 0) + 1
                    connection.execute(f'ALTER SEQUENCE {table}_{column}_seq RESTART WITH {next_value}')
                    sequence = connection.raw.execute(f'SELECT last_value,is_called FROM {table}_{column}_seq').fetchone()
                    if sequence['last_value'] != next_value or sequence['is_called']:
                        raise StorageError('Migration sequence verification failed')
                    sequences[table] = {'source_sequence': historical, 'maximum': maximum, 'next_value': next_value}
            return {'verified': True, 'tables': report, 'sequences': sequences}
    except StorageError:
        raise
    except Exception:
        raise StorageError('Migration failed; target transaction rolled back') from None
    finally:
        if connection is not None:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-state-dir', required=True, type=Path)
    parser.add_argument('--target-dsn-file', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(migrate(args.source_state_dir, args.target_dsn_file), sort_keys=True))
    except StorageError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
