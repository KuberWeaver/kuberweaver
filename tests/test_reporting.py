"""Reporting views over the PostgreSQL store.

The point of these tests is the contract an external dashboard depends on: the
view names, the columns, and the fact that a failed job never looks deployed.
"""
import json
import os
import uuid
from pathlib import Path

import pytest

from kuberweaver import reporting
from kuberweaver.storage import Store


@pytest.fixture
def pg_store(tmp_path, monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from kuberweaver.postgres import read_dsn_file

    source = Path(os.environ.get('KUBERWEAVER_TEST_DATABASE_URL_FILE',
                                 Path(__file__).parents[1] / 'data/postgresql/test-url'))
    if not source.exists():
        pytest.skip('Dedicated PostgreSQL test DSN file not configured')
    dsn = read_dsn_file(source)
    schema = 'kw_view_' + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    target = tmp_path / 'pg-url'
    target.write_text(make_conninfo(dsn, options='-csearch_path=' + schema))
    target.chmod(0o600)
    monkeypatch.delenv('KUBERWEAVER_DATABASE_URL', raising=False)
    monkeypatch.setenv('KUBERWEAVER_DATABASE_URL_FILE', str(target))
    monkeypatch.setattr(reporting, 'SCHEMA', 'reporting_' + uuid.uuid4().hex[:10])
    try:
        yield Store(tmp_path / 'state')
    finally:
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(reporting.SCHEMA)))
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        admin.close()


def job(name, status, environment='dev', nodes=1, created='2026-11-17T10:00:00+00:00', label=None):
    return {
        'id': name,
        'name': name,
        'status': status,
        'environment': environment,
        'operation': 'apply',
        'created_by': 'admin',
        'created_at': created,
        'finished_at': created,
        'graph': {'nodes': [{'id': f'n{index}', 'plugin_id': 'web-nginx', 'label': label or f'{name}-web'}
                            for index in range(nodes)]},
        'cost': {'total_monthly': 12.5},
    }


def test_views_report_what_ran_and_what_deployed(pg_store):
    store = pg_store
    result = reporting.ensure_reporting(store)
    assert result['available'] is True, result
    assert sorted(result['views']) == ['dashboards', 'deployments', 'drift', 'jobs']

    store.put('jobs', 'ok-1', job('ok-1', 'succeeded', nodes=2))
    store.put('jobs', 'bad-1', job('bad-1', 'failed'))
    store.execute('INSERT INTO dashboards(id,body) VALUES(?,?)',
                  ('grafana', json.dumps({'id': 'grafana', 'label': 'Grafana', 'kind': 'grafana',
                                          'url': 'http://grafana.lab', 'enabled': True, 'embed': False})))

    rows = store.all(f'SELECT name, status, nodes, environment FROM {reporting.SCHEMA}.jobs ORDER BY name')
    assert [(row['name'], row['status'], row['nodes']) for row in rows] == [
        ('bad-1', 'failed', 1), ('ok-1', 'succeeded', 2)]

    deployed = store.all(f'SELECT environment, name, plugin, job_id FROM {reporting.SCHEMA}.deployments ORDER BY name')
    assert {row['name'] for row in deployed} == {'ok-1-web'}
    assert all(row['job_id'] == 'ok-1' for row in deployed)
    assert all(row['plugin'] == 'web-nginx' for row in deployed)

    boards = store.all(f'SELECT label, kind, embed FROM {reporting.SCHEMA}.dashboards')
    assert [(row['label'], row['kind'], row['embed']) for row in boards] == [('Grafana', 'grafana', False)]


def test_deployments_keep_one_row_per_resource_at_the_latest_commit(pg_store):
    store = pg_store
    reporting.ensure_reporting(store)
    store.put('jobs', 'first', job('first', 'succeeded', created='2026-11-17T09:00:00+00:00', label='shared-web'))
    store.put('jobs', 'second', job('second', 'succeeded', created='2026-11-17T11:00:00+00:00', label='shared-web'))

    rows = store.all(f'SELECT job_id, deployed_at FROM {reporting.SCHEMA}.deployments')
    assert len(rows) == 1
    assert rows[0]['job_id'] == 'second'


def test_absent_postgresql_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(tmp_path))
    store = Store(tmp_path / 'state')
    assert store.backend == 'sqlite'
    assert reporting.ensure_reporting(store) == {
        'available': False, 'reason': 'reporting views require PostgreSQL', 'views': []}
    assert reporting.describe(store)['available'] is False
