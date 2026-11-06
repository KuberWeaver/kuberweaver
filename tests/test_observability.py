"""Read-only operational evidence over a real application and SQLite store."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from test_core_graph import api, graph


def test_snapshot_counts_durable_approval_reservation_as_known_state(api):
    from kuberweaver.observability import snapshot
    client, app = api
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    job.update(status='approving', requires_manual_review=True)
    app.state.store.put('jobs', job['id'], job)
    evidence = snapshot(app)
    assert evidence['jobs']['by_status']['approving'] == 1
    assert evidence['jobs']['invalid_records'] == 0
    assert client.get('/api/overview').json()['counts']['approving'] == 1


def test_snapshot_reports_actual_persisted_jobs_without_payloads(api):
    from kuberweaver.observability import snapshot
    client, app = api
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    before = app.state.store.conn.total_changes
    result = snapshot(app)
    assert result['schema_version'] == 1
    assert result['jobs']['total'] == 1
    assert result['jobs']['by_status']['pending_approval'] == 1
    assert result['jobs']['manual_review'] == 0
    assert result['worker']['alive'] is True
    assert result['worker']['queue_depth'] == 0
    assert result['storage']['status'] == 'ok'
    assert app.state.store.conn.total_changes == before
    encoded = json.dumps(result)
    for forbidden in (job['id'], job['name'], 'graph', 'created_by', 'password', 'csrf'):
        assert forbidden not in encoded
    # Reopening persisted state is not replaced by process-local counters.
    assert app.state.store.conn.execute('SELECT count(*) FROM jobs').fetchone()[0] == result['jobs']['total']


def test_snapshot_contains_malformed_persisted_states_without_echoing_them(api):
    from kuberweaver.observability import snapshot
    _, app = api
    store = app.state.store
    for identity, body in [('bad-json', 'not-json-secret'), ('list', '[]'),
                           ('missing', '{}'), ('status', '{"status": "raw-secret"}'),
                           ('unhashable', '{"status": []}')]:
        store.execute('INSERT INTO jobs VALUES (?,?)', (identity, body))
    store.put('jobs', 'valid', {'status': 'failed', 'requires_manual_review': True,
                               'error': 'never-echo-this-secret'})
    before = store.conn.total_changes
    result = snapshot(app)
    assert result['jobs']['total'] == 6
    assert result['jobs']['invalid_records'] == 5
    assert result['jobs']['by_status']['unknown'] == 5
    assert result['jobs']['by_status']['failed'] == 1
    assert result['jobs']['manual_review'] == 1
    assert result['status'] == 'degraded'
    assert store.conn.total_changes == before
    for marker in ('not-json-secret', 'raw-secret', 'never-echo-this-secret'):
        assert marker not in json.dumps(result)


def test_capabilities_are_configuration_not_live_probes_or_secret_reads(api, monkeypatch):
    from kuberweaver.observability import snapshot
    _, app = api
    def forbidden(*args, **kwargs):
        raise AssertionError('Health must not call provider consumers')
    for kind, name, method in [('inventory', 'gns3', 'read'), ('diagnostics', 'rules', 'read'),
                               ('secrets', 'restricted-file', 'resolve')]:
        monkeypatch.setattr(app.state.registry.get(kind, name), method, forbidden)
    result = snapshot(app)
    inventory = result['capabilities']['inventory']
    assert inventory['provider'] == 'gns3'
    assert inventory['selected'] is True
    assert inventory['available'] is True
    assert inventory['status'] == 'configured'
    assert inventory['probe']['status'] == 'never_observed'
    assert inventory['probe']['last_success_at'] is None
    app.state.provider_bindings['inventory'] = 'missing-provider'
    assert snapshot(app)['capabilities']['inventory']['available'] is False
    app.state.provider_config_error = 'secret-in-invalid-configuration'
    result = snapshot(app)
    assert result['status'] == 'degraded'
    assert result['provider_configuration'] == 'invalid'
    assert 'secret-in-invalid' not in json.dumps(result)


@pytest.mark.parametrize('age,error,expected', [(10, False, 'succeeded'), (600, False, 'stale'),
                                               (10, True, 'failed'), (None, None, 'never_observed')])
def test_probe_evidence_is_cached_and_distinguishes_stale_success(api, age, error, expected):
    from kuberweaver.observability import snapshot
    _, app = api
    provider = app.state.registry.get('inventory', 'gns3')
    provider.last_success = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat() if age is not None else None
    provider.last_error = error
    probe = snapshot(app)['capabilities']['inventory']['probe']
    assert probe['status'] == expected
    assert probe['last_success_at'] == provider.last_success
    assert probe['stale_after_seconds'] == 300
    if age is not None:
        assert age <= probe['age_seconds'] < age + 10
    provider.last_success = 'malformed-secret'
    assert snapshot(app)['capabilities']['inventory']['probe']['last_success_at'] is None
    assert 'malformed-secret' not in json.dumps(snapshot(app))


def test_duration_evidence_uses_only_valid_terminal_timestamps(api):
    from kuberweaver.observability import snapshot
    _, app = api
    base = {'status': 'succeeded', 'created_at': '2026-01-01T00:00:00+00:00',
            'finished_at': '2026-01-01T00:02:00+00:00'}
    app.state.store.put('jobs', 'valid-duration', base)
    for identity, changes in [('backwards', {'finished_at': '2025-01-01T00:00:00Z'}),
                               ('missing', {'finished_at': None}),
                               ('naive', {'finished_at': '2026-01-01T00:02:00'}),
                               ('not-terminal', {'status': 'running'})]:
        app.state.store.put('jobs', identity, {**base, **changes})
    evidence = snapshot(app)['jobs']['submission_to_finish_seconds']
    assert evidence == {'observations': 1, 'sum': 120.0, 'max': 120.0, 'invalid_terminal_timestamps': 3}
    from kuberweaver.observability import metrics_text
    metrics = metrics_text(app)
    assert 'kuberweaver_job_submission_to_finish_seconds{statistic="sum"} 120.0' in metrics
    assert 'kuberweaver_job_submission_to_finish_seconds{statistic="max"} 120.0' in metrics
    assert 'kuberweaver_job_submission_to_finish_observations 1' in metrics
    assert 'kuberweaver_job_submission_to_finish_seconds_sum' not in metrics


def test_unreadable_storage_and_stopped_worker_never_report_healthy(api):
    from kuberweaver.observability import snapshot
    _, app = api
    app.state.worker.close()
    result = snapshot(app)
    assert result['worker']['alive'] is False
    assert result['status'] == 'degraded'
    app.state.store.conn.close()
    result = snapshot(app)
    assert result['status'] == 'degraded'
    assert result['storage']['status'] == 'unavailable'
    assert result['jobs'] is None
    assert result['audit_events'] is None


def test_metrics_export_aggregate_health_without_inventing_probe_success(api):
    from kuberweaver.observability import metrics_text
    client, app = api
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    text = metrics_text(app)
    assert 'kuberweaver_jobs{status="pending_approval"} 1' in text
    assert 'kuberweaver_worker_alive 1' in text
    assert 'kuberweaver_storage_readable 1' in text
    assert 'kuberweaver_provider_probe_observed{kind="inventory"} 0' in text
    assert 'kuberweaver_provider_probe_success{kind="inventory"}' not in text
    assert 'kuberweaver_job_submission_to_finish_seconds{statistic="max"}' not in text
    assert job['id'] not in text and job['name'] not in text
    provider = app.state.registry.get('inventory', 'gns3')
    provider.last_success = datetime.now(timezone.utc).isoformat()
    provider.last_error = False
    assert 'kuberweaver_provider_probe_success{kind="inventory"} 1' in metrics_text(app)
    provider.last_success = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    assert 'kuberweaver_provider_probe_success{kind="inventory"} 0' in metrics_text(app)
    app.state.store.conn.close()
    text = metrics_text(app)
    assert 'kuberweaver_storage_readable 0' in text
    assert 'kuberweaver_jobs{' not in text

