"""Nightly read-only comparison of recorded declarations against live resources.

The durable state record says what a stack owns; the hypervisor is the
authority on what actually exists. A drift report is the field-by-field
difference between the two. This module reads both sides and writes a report
only: it never applies, repairs, powers on, stops or deletes anything, and the
single driver call it makes is ``read_resource``.

One report per state record is kept, so a nightly sweep or an administrator's
request refreshes the evidence without growing a history table. Drift is never
remediated: a changed field is a fact for a human, not a reason to touch a host.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import threading
from typing import Callable

from fastapi import Depends, HTTPException

from .environment_state import DESTROY_ACTIVE, StateError
from .storage import now

INTERVAL_VARIABLE = 'KUBERWEAVER_DRIFT_SECONDS'
DEFAULT_INTERVAL = 86400.0
MIN_INTERVAL = 60.0
MAX_INTERVAL = 7 * 86400.0

VM_TYPE = 'esxi_vm'
FIELDS = ('name', 'power_state', 'cpu', 'memory_mb')

IN_SYNC = 'in_sync'
DRIFT = 'drift'
UNVERIFIED = 'unverified'
UNSUPPORTED = 'unsupported'
EMPTY = 'empty'
UNAVAILABLE = 'unavailable'
STATUSES = (IN_SYNC, DRIFT, UNVERIFIED, UNSUPPORTED, EMPTY, UNAVAILABLE)
_RESOURCE_STATUSES = (IN_SYNC, DRIFT, UNVERIFIED, UNSUPPORTED)

_UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
_IDENTITY = re.compile(r'[0-9a-f]{32}\Z')
_CONNECTION = re.compile(r'[0-9a-f]{32}\Z')


class DriftError(RuntimeError):
    """A stored report or its table is unreadable; never a document value."""


@dataclass(frozen=True)
class DriftSchedule:
    interval: float
    error: str | None


def drift_schedule():
    """Read the nightly interval; an unusable value falls back and reports."""
    interval = DEFAULT_INTERVAL
    error = None
    raw = os.environ.get(INTERVAL_VARIABLE)
    if raw not in (None, ''):
        try:
            interval = float(raw)
            if not 0 <= interval <= MAX_INTERVAL or 0 < interval < MIN_INTERVAL:
                raise ValueError
        except ValueError:
            interval = DEFAULT_INTERVAL
            error = (f'{INTERVAL_VARIABLE} must be 0 or a number of seconds '
                     f'between {MIN_INTERVAL:g} and {MAX_INTERVAL:g}')
    return DriftSchedule(interval, error)


def _scalar(value):
    """A JSON scalar for the report; a provider value never enters as an object."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _tofu_resources(document):
    values = document.get('values')
    module = values.get('root_module') if isinstance(values, dict) else None
    rows = module.get('resources') if isinstance(module, dict) else None
    return rows if isinstance(rows, list) else []


def declared_attributes(document, address):
    """The values a recorded document declares for one resource address.

    Adoption writes the VM attributes at the top level. An applied document is
    the engine's own state, where the same attributes sit in the resource's
    ``values`` object. Only fields the document actually declares are returned,
    so an absent attribute reads as undeclared rather than as a null drift.
    """
    if not isinstance(document, dict):
        return {}
    if document.get('source') == 'adopted-vm':
        uuid = document.get('uuid')
        if not isinstance(uuid, str) or address != VM_TYPE + '.' + uuid.lower():
            return {}
        return {field: document[field] for field in FIELDS if field in document}
    for row in _tofu_resources(document):
        if not isinstance(row, dict) or row.get('address') != address:
            continue
        values = row.get('values')
        if not isinstance(values, dict):
            return {}
        return {field: values[field] for field in FIELDS if field in values}
    return {}


def _connection_of(record, document):
    adopted = record.get('adopted')
    if isinstance(adopted, dict) and isinstance(adopted.get('connection_id'), str) \
            and _CONNECTION.match(adopted['connection_id']):
        return adopted['connection_id']
    value = document.get('connection_id') if isinstance(document, dict) else None
    if isinstance(value, str) and _CONNECTION.match(value):
        return value
    return None


def _summary(entries):
    counts = {status: 0 for status in _RESOURCE_STATUSES}
    for entry in entries:
        counts[entry['status']] += 1
    return {'resources': len(entries), **counts}


def _status(entries):
    if not entries:
        return EMPTY
    present = {entry['status'] for entry in entries}
    for status in (DRIFT, UNVERIFIED, UNSUPPORTED):
        if status in present:
            return status
    return IN_SYNC


class DriftService:
    """Produce and read drift reports; nothing here writes to a host."""

    def __init__(self, environments, inspector=None, clock=now, *,
                 interval=DEFAULT_INTERVAL, schedule_error=None):
        self.environments = environments
        self.inspector = inspector
        self.clock = clock
        self.interval = interval
        self.schedule_error = schedule_error
        self.store = environments.store
        self.store.execute('CREATE TABLE IF NOT EXISTS environment_state_drift('
                           'id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self._running = threading.Lock()

    @staticmethod
    def _identity(resource):
        address = resource.get('address')
        if not isinstance(address, str) or not address.startswith(VM_TYPE + '.'):
            return None
        value = address[len(VM_TYPE) + 1:]
        return value if _UUID.match(value) else None

    def _compare(self, resource, document, connection_id):
        address = resource.get('address')
        entry = {'address': address, 'type': resource.get('type'),
                 'mode': resource.get('mode'), 'status': UNSUPPORTED,
                 'reason': 'the resource type has no read-only reader',
                 'checked': [], 'fields': []}
        if resource.get('mode') == 'data':
            return {**entry, 'reason': 'data sources are not managed resources'}
        if resource.get('type') != VM_TYPE:
            return entry
        uuid = self._identity(resource)
        if uuid is None:
            return {**entry, 'status': UNVERIFIED,
                    'reason': 'the resource address is not a virtual machine UUID'}
        if not callable(self.inspector):
            return {**entry, 'status': UNVERIFIED, 'reason': 'no live reader is wired'}
        if connection_id is None:
            return {**entry, 'status': UNVERIFIED,
                    'reason': 'no connection is recorded for this stack'}
        try:
            row = self.inspector(connection_id, uuid)
        except (Exception, SystemExit):
            return {**entry, 'status': UNVERIFIED, 'reason': 'the resource could not be read'}
        if not isinstance(row, dict):
            return {**entry, 'status': UNVERIFIED, 'reason': 'the resource could not be read'}
        declared = declared_attributes(document, address)
        checked, fields = ['uuid'], []
        live_uuid = row.get('uuid')
        if not isinstance(live_uuid, str) or live_uuid.lower() != uuid:
            fields.append({'field': 'uuid', 'declared': uuid, 'live': _scalar(live_uuid)})
        for field in FIELDS:
            if field not in declared:
                continue
            checked.append(field)
            live = _scalar(row.get(field))
            if _scalar(declared[field]) != live:
                fields.append({'field': field, 'declared': _scalar(declared[field]),
                               'live': live})
        return {**entry, 'status': DRIFT if fields else IN_SYNC, 'reason': None,
                'checked': checked, 'fields': fields}

    def _check(self, view, stamp):
        base = {'id': view['id'], 'environment': view['environment'],
                'stack': view['stack'], 'generation': view['generation'],
                'source': view.get('source'), 'checked_at': stamp}
        resources = view.get('resources') or []
        if not resources:
            return {**base, 'status': EMPTY, 'reason': None,
                    'summary': _summary([]), 'resources': []}
        if view.get('lease'):
            # A half-applied stack has no stable declaration to compare with.
            return {**base, 'status': UNVERIFIED, 'reason': 'an apply holds the state lease',
                    'summary': _summary([]), 'resources': []}
        destroy = view.get('destroy') or {}
        if destroy.get('status') in DESTROY_ACTIVE:
            return {**base, 'status': UNVERIFIED, 'reason': 'a destruction is in progress',
                    'summary': _summary([]), 'resources': []}
        try:
            document = self.environments.document(view['environment'], view['stack'])
        except StateError:
            return {**base, 'status': UNAVAILABLE,
                    'reason': 'the recorded document is unavailable',
                    'summary': _summary([]), 'resources': []}
        connection_id = _connection_of(view, document)
        entries = [self._compare(resource, document, connection_id)
                   for resource in resources if isinstance(resource, dict)]
        return {**base, 'status': _status(entries), 'reason': None,
                'summary': _summary(entries), 'resources': entries}

    def _store(self, report):
        body = json.dumps(report, sort_keys=True)
        self.store.execute('INSERT OR REPLACE INTO environment_state_drift(id,body) VALUES(?,?)',
                           (report['id'], body))
        row = self.store.one('SELECT body FROM environment_state_drift WHERE id=?',
                             (report['id'],))
        if row is None or row['body'] != body:
            raise DriftError('Drift report write could not be confirmed')

    def run(self, actor=None):
        """Compare every state record once, store the reports, change nothing else."""
        if not self._running.acquire(blocking=False):
            raise HTTPException(409, 'A drift report is already being produced')
        try:
            stamp = self.clock()
            try:
                views = self.environments.list()
            except StateError:
                raise HTTPException(503, 'Environment state is unavailable') from None
            reports = [self._check(view, stamp) for view in views]
            for report in reports:
                self._store(report)
            counts = {status: 0 for status in STATUSES}
            for report in reports:
                counts[report['status']] += 1
            summary = {'total': len(reports), **counts}
            if actor is not None:
                self.store.audit(actor, 'environment_state.drift_checked', '',
                                 {'checked_at': stamp, **summary})
            return {'checked_at': stamp, 'summary': summary, 'reports': reports}
        finally:
            self._running.release()

    def _reports(self):
        reports = []
        for row in self.store.all('SELECT body FROM environment_state_drift'):
            try:
                report = json.loads(row['body'])
            except (TypeError, ValueError):
                raise DriftError('Drift report is unreadable') from None
            if (not isinstance(report, dict) or not isinstance(report.get('id'), str)
                    or report.get('status') not in STATUSES
                    or not isinstance(report.get('resources'), list)):
                raise DriftError('Drift report is unreadable')
            reports.append(report)
        return sorted(reports, key=lambda item: (item.get('environment') or '',
                                                 item.get('stack') or ''))

    def collection(self):
        reports = self._reports()
        counts = {status: 0 for status in STATUSES}
        for report in reports:
            counts[report['status']] += 1
        return {'reports': reports,
                'summary': {'total': len(reports), **counts},
                'schedule': {'interval_seconds': self.interval,
                             'error': self.schedule_error}}

    def present(self, identity):
        if not isinstance(identity, str) or not _IDENTITY.match(identity):
            raise HTTPException(404, 'Unknown environment state')
        self.environments.present(identity)
        for report in self._reports():
            if report['id'] == identity:
                return report
        raise HTTPException(404, 'No drift report has been produced for this state')


class DriftLoop:
    """One sweep per interval; a failed sweep never stops the reporting loop."""

    def __init__(self, sweep: Callable[[], object], *, interval, initial_delay=None):
        self.sweep = sweep
        self.interval = float(interval)
        self.initial_delay = (min(5.0, self.interval) if initial_delay is None
                              else float(initial_delay))
        self._stop = threading.Event()
        self.thread = None

    def start(self):
        if self.interval <= 0:
            return
        if self.thread is not None and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name='kuberweaver-drift-reports',
                                       daemon=True)
        self.thread.start()

    def _run(self):
        if self._stop.wait(self.initial_delay):
            return
        while True:
            try:
                self.sweep()
            except Exception:
                # One unreadable stack must not end the nightly report.
                pass
            if self._stop.wait(self.interval):
                return

    def close(self):
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)


def install_drift_api(app, environments):
    """Register the drift routes and the nightly loop on the state kernel."""
    if getattr(app.state, '_drift_api_installed', False):
        return
    from .adoption import connection_inspector
    schedule = drift_schedule()
    service = app.state.drift = DriftService(environments, connection_inspector(app),
                                             interval=schedule.interval,
                                             schedule_error=schedule.error)
    loop = app.state.drift_loop = DriftLoop(service.run, interval=schedule.interval)
    # The default test suite must not start a nightly thread; a test that
    # exercises it names the interval explicitly, as the probe loop does.
    if schedule.interval and (not getattr(app.state, 'testing', False)
                              or os.environ.get(INTERVAL_VARIABLE)):
        app.state.background_services.append(loop)

    @app.get('/api/environment-states/drift')
    def drift_listing(current=Depends(app.state.user_dependency)):
        try:
            return service.collection()
        except DriftError:
            raise HTTPException(503, 'Drift reports are unavailable') from None

    @app.get('/api/environment-states/{identity}/drift')
    def drift_detail(identity: str, current=Depends(app.state.user_dependency)):
        try:
            return service.present(identity)
        except DriftError:
            raise HTTPException(503, 'Drift reports are unavailable') from None

    @app.post('/api/environment-states/drift/run')
    def drift_run(current=Depends(app.state.role_dependency('admin'))):
        try:
            return service.run(actor=current['username'])
        except DriftError:
            raise HTTPException(503, 'Drift reports are unavailable') from None

    app.state._drift_api_installed = True
