"""Reporting views over the PostgreSQL store.

The point of these tests is the contract an external dashboard depends on: the
view names, the columns, and the fact that a failed job never looks deployed.
Costs mirror /api/finops: the latest successful graph per (environment, graph
name), last write wins by store rowid, expanded into per-node declared cost.
Resources mirror the live inventory index: destroyed stacks never appear.
"""
import json
import os
import uuid
from decimal import Decimal
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


def breakdown_item(node_id, monthly, plugin='web-nginx', targets=1, resources=True):
    item = {'node_id': node_id, 'plugin_id': plugin, 'targets': targets, 'monthly': monthly}
    if resources:
        item['resources'] = {'cpu': 1, 'memory_mb': 512, 'disk_gb': 10}
    return item


def job(name, status, environment='dev', nodes=1, created='2026-11-17T10:00:00+00:00', label=None,
        graph_name=None, monthly=12.5, breakdown=None, cost=True):
    """A job envelope shaped exactly like policy.admission writes it."""
    body = {
        'id': name,
        'name': name,
        'status': status,
        'environment': environment,
        'operation': 'apply',
        'created_by': 'admin',
        'created_at': created,
        'finished_at': created,
        'graph': {'name': graph_name or f'{name}-graph',
                  'nodes': [{'id': f'n{index}', 'plugin_id': 'web-nginx', 'label': label or f'{name}-web'}
                            for index in range(nodes)]},
    }
    if cost:
        body['cost'] = {
            'monthly': monthly,
            'currency': 'EUR',
            'breakdown': breakdown if breakdown is not None else [
                breakdown_item(f'n{index}', round(monthly / nodes, 2)) for index in range(nodes)],
        }
    return body


def state(identity, environment, stack, resources, destroyed=None,
          updated='2026-11-17T10:00:00+00:00'):
    return {'id': identity, 'environment': environment, 'stack': stack, 'source': 'apply',
            'updated_at': updated, 'destroyed': destroyed, 'resources': resources}


def test_views_report_what_ran_and_what_deployed(pg_store):
    store = pg_store
    result = reporting.ensure_reporting(store)
    assert result['available'] is True, result
    assert sorted(result['views']) == ['costs', 'dashboards', 'deployments', 'drift', 'jobs', 'resources']

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


def test_jobs_cost_monthly_reads_the_policy_shape(pg_store):
    store = pg_store
    reporting.ensure_reporting(store)
    store.put('jobs', 'priced', job('priced', 'succeeded', monthly=12.5))
    store.put('jobs', 'free', job('free', 'succeeded', cost=False))

    rows = store.all(f'SELECT name, cost_monthly FROM {reporting.SCHEMA}.jobs ORDER BY name')
    assert rows[0]['name'] == 'free'
    assert rows[0]['cost_monthly'] is None
    assert rows[1]['name'] == 'priced'
    assert rows[1]['cost_monthly'] == Decimal('12.5')


def test_costs_keep_the_latest_succeeded_graph_per_environment(pg_store):
    store = pg_store
    reporting.ensure_reporting(store)
    # First successful apply of stack 'web' in dev, one node.
    store.put('jobs', 'g1', job('g1', 'succeeded', graph_name='web', monthly=10.0,
                                created='2026-11-17T09:00:00+00:00',
                                breakdown=[breakdown_item('n0', 10.0)]))
    # A failed re-apply never displaces the succeeded graph.
    store.put('jobs', 'g2', job('g2', 'failed', graph_name='web', monthly=99.0))
    # A newer successful apply wins, with two nodes and a different plugin.
    store.put('jobs', 'g3', job('g3', 'succeeded', graph_name='web', monthly=20.0,
                                created='2026-11-17T11:00:00+00:00',
                                breakdown=[breakdown_item('n0', 12.0),
                                           breakdown_item('n1', 8.0, plugin='db-postgresql')]))
    # Another environment keeps its own latest graph; a breakdown item without
    # resources must still surface, with NULL resource columns.
    store.put('jobs', 'g4', job('g4', 'succeeded', environment='prod', graph_name='web',
                                monthly=5.0, breakdown=[breakdown_item('n0', 5.0, resources=False)]))
    # A succeeded job without any cost contributes no cost rows, not an error.
    store.put('jobs', 'g5', job('g5', 'succeeded', graph_name='bare', cost=False))

    rows = store.all(f'SELECT environment, graph_name, job_id, node_id, plugin_id, targets, '
                     f'monthly, cpu, memory_mb, disk_gb, currency '
                     f'FROM {reporting.SCHEMA}.costs ORDER BY environment, node_id')
    assert [(row['environment'], row['graph_name'], row['job_id'], row['node_id']) for row in rows] == [
        ('dev', 'web', 'g3', 'n0'), ('dev', 'web', 'g3', 'n1'), ('prod', 'web', 'g4', 'n0')]
    assert rows[0]['monthly'] == Decimal('12.0')
    assert rows[0]['plugin_id'] == 'web-nginx'
    assert rows[0]['targets'] == 1
    assert rows[0]['cpu'] == Decimal('1')
    assert rows[0]['memory_mb'] == Decimal('512')
    assert rows[0]['disk_gb'] == Decimal('10')
    assert rows[1]['monthly'] == Decimal('8.0')
    assert rows[1]['plugin_id'] == 'db-postgresql'
    assert rows[2]['monthly'] == Decimal('5.0')
    assert rows[2]['cpu'] is None and rows[2]['memory_mb'] is None and rows[2]['disk_gb'] is None
    assert all(row['currency'] == 'EUR' for row in rows)
    total = store.one(f'SELECT sum(monthly) AS total FROM {reporting.SCHEMA}.costs')
    assert total['total'] == Decimal('25.0')

    # Last write wins by store rowid, not by created_at: re-putting the older
    # job (earlier timestamp) makes it the latest again, exactly like /api/finops.
    store.put('jobs', 'g1', job('g1', 'succeeded', graph_name='web', monthly=30.0,
                                created='2026-11-17T09:00:00+00:00',
                                breakdown=[breakdown_item('n0', 30.0)]))
    rows = store.all(f"SELECT job_id, monthly FROM {reporting.SCHEMA}.costs "
                     f"WHERE environment = 'dev' ORDER BY node_id")
    assert [(row['job_id'], row['monthly']) for row in rows] == [('g1', Decimal('30.0'))]


def put_state(store, record):
    # environment_state is written by the state module, not Store.put.
    store.execute('INSERT INTO environment_state(id,body) VALUES(?,?)',
                  (record['id'], json.dumps(record)))


def test_resources_report_live_stacks_only(pg_store):
    store = pg_store
    reporting.ensure_reporting(store)
    put_state(store, state(
        'dev-live', 'dev', 'supervision',
        [{'type': 'esxi_vm', 'name': 'supervision-vm', 'mode': 'managed'}]))
    put_state(store, state(
        'dev-gone', 'dev', 'old-stack',
        [{'type': 'esxi_vm', 'name': 'old-vm', 'mode': 'managed'}],
        destroyed={'at': '2026-11-18T10:00:00+00:00', 'job_id': 'destroy-1'}))
    put_state(store, state('dev-empty', 'dev', 'empty', []))
    # A record without the destroyed key at all is live too.
    legacy = state('dev-legacy', 'dev', 'legacy',
                   [{'type': 'esxi_vm', 'name': 'legacy-vm', 'mode': 'managed'}])
    del legacy['destroyed']
    put_state(store, legacy)

    rows = store.all(f'SELECT environment, stack, source, type, name, mode, updated_at '
                     f'FROM {reporting.SCHEMA}.resources ORDER BY stack')
    assert [(row['stack'], row['type'], row['name'], row['mode']) for row in rows] == [
        ('legacy', 'esxi_vm', 'legacy-vm', 'managed'),
        ('supervision', 'esxi_vm', 'supervision-vm', 'managed')]
    assert all(row['environment'] == 'dev' for row in rows)
    assert all(row['source'] == 'apply' for row in rows)
    assert all(row['updated_at'] is not None for row in rows)


def test_reporting_repairs_a_legacy_view_with_the_wrong_column_type(pg_store):
    store = pg_store
    # A previous release published cost_monthly as text and fewer columns;
    # CREATE OR REPLACE cannot change a column type or drop columns, so
    # startup must still converge on the current shape.
    store.execute(f'CREATE SCHEMA IF NOT EXISTS {reporting.SCHEMA}')
    store.execute(f"CREATE VIEW {reporting.SCHEMA}.jobs AS "
                  f"SELECT id, body::jsonb -> 'cost' ->> 'total_monthly' AS cost_monthly FROM jobs")
    result = reporting.ensure_reporting(store)
    assert result['available'] is True, result
    assert 'jobs' in result['views']
    assert not result['failed']
    store.put('jobs', 'priced', job('priced', 'succeeded', monthly=12.5))
    row = store.one(f"SELECT cost_monthly FROM {reporting.SCHEMA}.jobs WHERE id = 'priced'")
    assert row['cost_monthly'] == Decimal('12.5')


def test_absent_postgresql_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(tmp_path))
    store = Store(tmp_path / 'state')
    assert store.backend == 'sqlite'
    assert reporting.ensure_reporting(store) == {
        'available': False, 'reason': 'reporting views require PostgreSQL', 'views': []}
    assert reporting.describe(store)['available'] is False
