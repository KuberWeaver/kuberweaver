#!/usr/bin/env python3
"""Provision the read-only kw_dashboard reporting role. Never print credentials.

Idempotent: reuses data/postgresql/dashboard-password when present, otherwise
generates a URL-safe password (alphabet A-Za-z0-9_-, no '$': Grafana's $__file
macro truncates at '$', grafana#130055). The password is stored in the local
vault as grafana/kw-dashboard and in the 0600 file the grafana brick slurps
host-side. The script proves the read-only boundary live: SELECT on the
reporting views works, SELECT on base tables and CREATE TABLE are denied.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from kuberweaver.local_vault import LocalVaultProvider
from kuberweaver.secrets import SecretError, SecretValue

ROLE = 'kw_dashboard'
VAULT_NAME = 'grafana/kw-dashboard'
PASSWORD_FILE = ROOT / 'data' / 'postgresql' / 'dashboard-password'
VAULT_DIR = ROOT / 'data' / 'plugin-data' / 'local-vault'
URLSAFE = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-')


def load_password() -> tuple[str, bool]:
    """Reuse the private file when valid; otherwise generate a fresh password."""
    if PASSWORD_FILE.is_symlink():
        raise SecretError('refusing symlink dashboard password file')
    if PASSWORD_FILE.exists():
        info = PASSWORD_FILE.stat()
        if info.st_uid != os.getuid() or (info.st_mode & 0o777) != 0o600:
            raise SecretError('dashboard password file must be private 0600')
        value = PASSWORD_FILE.read_text().strip()
        if not value or not set(value) <= URLSAFE:
            raise SecretError('existing dashboard password has an unsupported shape; rotate it manually')
        return value, False
    value = secrets.token_urlsafe(32)
    if not set(value) <= URLSAFE:  # defensive: the $__file macro must see no '$'
        raise SecretError('generated password outside the URL-safe alphabet')
    return value, True


def write_password_file(value: str) -> None:
    """Atomic 0600 write, no trailing newline: Grafana $__file reads raw bytes."""
    PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.dashboard-private-', dir=PASSWORD_FILE.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        if PASSWORD_FILE.is_symlink():
            raise SecretError('refusing symlink dashboard password file')
        os.replace(temporary, PASSWORD_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    bootstrap_path = ROOT / 'data' / 'postgresql' / 'bootstrap.json'
    boot = json.loads(bootstrap_path.read_text())
    app_role = str(boot['application_role'])
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', app_role):
        raise SecretError('unexpected application role name')
    password, generated = load_password()
    admin_dsn = make_conninfo(host='127.0.0.1', port=int(boot['port']),
                              dbname=boot['database'], user='postgres',
                              password=boot['admin_password'], sslmode='disable',
                              connect_timeout=5)
    # ClientCursor: CREATE/ALTER ROLE are utility statements and reject
    # server-side bind parameters; psycopg quotes the password client-side and
    # it never enters any log line this script produces.
    with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row,
                         cursor_factory=psycopg.ClientCursor) as admin:
        if admin.execute('SELECT to_regclass(%s) AS present', ('reporting.jobs',)).fetchone()['present'] is None:
            raise SecretError('reporting schema missing; start the platform once first')
        existed = admin.execute('SELECT 1 AS present FROM pg_roles WHERE rolname = %s',
                                (ROLE,)).fetchone() is not None
        # The password travels as a bound parameter, never formatted into SQL
        # text that this script could log; attributes stay least-privilege.
        action = 'ALTER' if existed else 'CREATE'
        admin.execute(sql.SQL('{} ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                              'NOREPLICATION NOBYPASSRLS PASSWORD %s').format(
                                  sql.SQL(action), sql.Identifier(ROLE)), (password,))
        admin.execute(sql.SQL('GRANT USAGE ON SCHEMA reporting TO {}').format(sql.Identifier(ROLE)))
        admin.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO {}').format(sql.Identifier(ROLE)))
        # FOR ROLE matters: views are (re)created by the application role at
        # startup, so future views must inherit the SELECT grant too.
        admin.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA reporting '
                              'GRANT SELECT ON TABLES TO {}').format(
                                  sql.Identifier(app_role), sql.Identifier(ROLE)))
        default_acl = admin.execute(
            "SELECT defaclrole::regrole::text AS role, defaclnamespace::regnamespace::text AS schema, "
            'defaclacl::text[] AS acl FROM pg_default_acl').fetchall()
        future_grant = any(row['role'] == app_role and row['schema'] == 'reporting'
                           and any(ROLE + '=r/' in entry for entry in row['acl'])
                           for row in default_acl)
        if not future_grant:
            raise SecretError('default privileges for future reporting views not in place')

    write_password_file(password)
    provider = LocalVaultProvider(VAULT_DIR, allow_write=True)
    provider.store(VAULT_NAME, SecretValue(password.encode()))
    with provider.resolve(VAULT_NAME) as value:
        if value.bytes() != password.encode():
            raise SecretError('vault readback differs')

    # Prove the boundary with the actual dashboard login.
    probe_dsn = make_conninfo(host='127.0.0.1', port=int(boot['port']),
                              dbname=boot['database'], user=ROLE,
                              password=password, sslmode='disable', connect_timeout=5)
    counts, denied = {}, {}
    with psycopg.connect(probe_dsn, autocommit=True) as probe:
        for view in ('jobs', 'deployments', 'costs', 'resources'):
            counts[view] = probe.execute(f'SELECT count(*) FROM reporting.{view}').fetchone()[0]
        for label, statement in (
                ('base_table_select', 'SELECT id FROM public.jobs LIMIT 1'),
                ('create_table', 'CREATE TABLE public.kw_dashboard_probe(x int)'),
                ('insert', "INSERT INTO public.jobs(id, body) VALUES ('probe', '{}')")):
            try:
                probe.execute(statement)
            except psycopg.errors.InsufficientPrivilege:
                denied[label] = True
            else:
                raise SecretError('read-only boundary violated: ' + label)
    print(json.dumps({
        'ok': True, 'role': ROLE, 'created': not existed, 'password_generated': generated,
        'password_file': str(PASSWORD_FILE), 'vault_reference': VAULT_NAME,
        'application_role': app_role, 'default_privileges_for_future_views': future_grant,
        'readable_views': counts, 'denied': denied,
    }, indent=2))


if __name__ == '__main__':
    try:
        main()
    except SecretError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Driver errors can carry DSNs or values; never stringify them here.
        print('reporting role provisioning failed; no credentials printed.', file=sys.stderr)
        sys.exit(1)
