"""Read-only, allowlisted operational evidence; never invokes a provider consumer."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException
from .providers import CONSUMER_METHODS, registered_provider
from .jobs import TERMINAL
from .storage import StorageError

STATUSES = ('pending_approval', 'approving', 'queued', 'running', 'rolling_back',
            'decommission_queued', 'decommissioning', 'succeeded', 'failed', 'rejected',
            'rolled_back', 'rollback_failed', 'decommissioned', 'decommission_failed')


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def _probe(provider):
    # Cached built-in evidence only: no read(), describe(), resolve() or network.
    evidence = vars(provider) if provider is not None and hasattr(provider, '__dict__') else {}
    stamp = _timestamp(evidence.get('last_success'))
    now = datetime.now(timezone.utc)
    age = (now - stamp).total_seconds() if stamp else None
    if age is not None and age < 0:
        stamp = age = None
    error = evidence.get('last_error')
    state = ('failed' if error is True else 'never_observed' if stamp is None
             else 'stale' if age is not None and age > 300
             else 'succeeded' if error is False else 'unknown')
    return {'status': state, 'last_success_at': stamp.isoformat() if stamp else None,
            'age_seconds': age, 'stale_after_seconds': 300}


def snapshot(app):
    """Return aggregate persisted evidence without graph/config/event contents."""
    store = app.state.store
    try:
        with store.lock:
            rows = store.all('SELECT body FROM jobs')
            audit_count = store.one('SELECT count(*) AS n FROM audit')['n']
        storage_ok = True
    except (sqlite3.Error, StorageError):
        rows, audit_count, storage_ok = [], None, False
    with store.lock:
        counts = dict.fromkeys((*STATUSES, 'unknown'), 0)
        manual_review = invalid_records = 0
        durations = {'observations': 0, 'sum': 0.0, 'max': None, 'invalid_terminal_timestamps': 0}
        for row in rows:
            try:
                job = json.loads(row['body'])
            except (ValueError, TypeError):
                job = None
            state = job.get('status') if isinstance(job, dict) else None
            if not isinstance(state, str) or state not in STATUSES:
                state = 'unknown'
                invalid_records += 1
            counts[state] += 1
            manual_review += isinstance(job, dict) and job.get('requires_manual_review') is True
            if isinstance(job, dict) and state in TERMINAL:
                started, finished = _timestamp(job.get('created_at')), _timestamp(job.get('finished_at'))
                if started is None or finished is None or finished < started:
                    durations['invalid_terminal_timestamps'] += 1
                else:
                    duration = (finished - started).total_seconds()
                    durations['observations'] += 1
                    durations['sum'] += duration
                    durations['max'] = max(durations['max'] or 0.0, duration)
    worker = app.state.worker
    worker_alive = bool(worker.thread and worker.thread.is_alive())
    invalid_config = bool(app.state.provider_config_error)
    capabilities = {}
    descriptors = {(row['kind'], row['name']): row for row in app.state.registry.list()}
    for kind in CONSUMER_METHODS:
        name = app.state.provider_bindings.get(kind)
        if not name:
            capabilities[kind] = {'provider': None, 'selected': False, 'available': False,
                                  'status': 'unconfigured', 'probe': None}
            continue
        available = not invalid_config
        provider = None
        try:
            provider = registered_provider(app.state.registry, kind, name)
        except HTTPException:
            available = False
        status = descriptors.get((kind, name), {}).get('status', 'unavailable')
        if status not in {'configured', 'unconfigured', 'degraded', 'disabled', 'unavailable'}:
            status = 'unknown'
        capabilities[kind] = {'provider': name, 'selected': True, 'available': available,
                              'status': status if available else 'unavailable',
                              'probe': _probe(provider)}
    degraded = (not storage_ok or not worker_alive or invalid_records or invalid_config
                or any(row['selected'] and not row['available'] for row in capabilities.values()))
    return {'schema_version': 1, 'status': 'degraded' if degraded else 'ok',
            'provider_configuration': 'invalid' if invalid_config else 'valid',
            'capabilities': capabilities,
            'storage': {'status': 'ok' if storage_ok else 'unavailable'},
            'jobs': {'total': len(rows), 'by_status': counts, 'manual_review': manual_review,
                     'invalid_records': invalid_records, 'submission_to_finish_seconds': durations} if storage_ok else None,
            'audit_events': audit_count,
            'worker': {'alive': worker_alive,
                       'paused': not worker.pause.is_set(),
                       'active': worker.active_identity is not None,
                       'queue_depth': worker.queue.qsize()}}


def metrics_text(app):
    """Prometheus text format; absent evidence is omitted, never defaulted healthy."""
    evidence = snapshot(app)
    lines = []

    def emit(name, value, help_text, metric_type='gauge', labels=None):
        if value is None:
            return
        if not any(line.startswith('# TYPE ' + name + ' ') for line in lines):
            lines.extend([f'# HELP {name} {help_text}', f'# TYPE {name} {metric_type}'])
        suffix = '{' + ','.join(key + '=' + json.dumps(str(val)) for key, val in labels.items()) + '}' if labels else ''
        lines.append(f'{name}{suffix} {int(value) if isinstance(value, bool) else value}')

    emit('kuberweaver_http_requests_total', app.state.request_count, 'HTTP requests received by this process.', 'counter')
    emit('kuberweaver_audit_events_total', evidence['audit_events'], 'Persisted audit records; may reset when state is replaced.', 'counter')
    emit('kuberweaver_storage_readable', evidence['storage']['status'] == 'ok', 'Current persisted-state read succeeded.')
    emit('kuberweaver_operational_healthy', evidence['status'] == 'ok', 'Local storage worker and provider binding checks; not upstream reachability.')
    for key in ('alive', 'paused', 'active'):
        emit('kuberweaver_worker_' + key, evidence['worker'][key], 'Current local worker ' + key + ' state.')
    emit('kuberweaver_queue_depth', evidence['worker']['queue_depth'], 'In-memory queue entries; approximate and may include shutdown sentinel.')
    jobs = evidence['jobs']
    if jobs is not None:
        for status, count in jobs['by_status'].items():
            emit('kuberweaver_jobs', count, 'Persisted jobs by current status.', labels={'status': status})
        emit('kuberweaver_jobs_manual_review', jobs['manual_review'], 'Persisted jobs explicitly requiring manual review.')
        emit('kuberweaver_jobs_invalid_records', jobs['invalid_records'], 'Persisted malformed or unknown-status job records.')
        durations = jobs['submission_to_finish_seconds']
        for key in ('sum', 'max'):
            emit('kuberweaver_job_submission_to_finish_seconds', durations[key],
                 'Gauge over valid persisted terminal jobs including approval waiting; not execution latency.',
                 labels={'statistic': key})
        emit('kuberweaver_job_submission_to_finish_observations', durations['observations'],
             'Current persisted terminal jobs with valid submission and finish timestamps.')
    for kind, capability in evidence['capabilities'].items():
        labels = {'kind': kind}
        configured = capability['available'] and capability['status'] == 'configured'
        emit('kuberweaver_provider_configured', configured, 'Selected provider is compatible and configured; not a live probe.', labels=labels)
        probe = capability.get('probe') or {}
        observed = probe.get('status') in {'succeeded', 'stale', 'failed'}
        emit('kuberweaver_provider_probe_observed', observed, 'Provider cached probe evidence exists.', labels=labels)
        if observed:
            emit('kuberweaver_provider_probe_success', probe.get('status') == 'succeeded', 'Cached probe succeeded and remains fresh within 300 seconds.', labels=labels)
        emit('kuberweaver_provider_last_success_age_seconds', probe.get('age_seconds'), 'Age of cached successful observation even when latest probe failed.', labels=labels)
    return '\n'.join(lines) + '\n'

