"""Durable approvals, local GitOps artifacts and exactly one queue consumer."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import queue
import re
import secrets
import subprocess
import threading
from pathlib import Path
from fastapi import HTTPException
from .storage import now
from .providers import (resolve_provider, execution_binding,
                        invoke_approved_execution, graph_execution_name,
                        check_execution_pins, check_prepared_plan, ExecutionTrustError)
from .gitops import publish as publish_remote

TERMINAL = {'succeeded', 'failed', 'rolled_back', 'rollback_failed', 'rejected',
            'decommissioned', 'decommission_failed'}
# The only status a retirement provider may report as verified removal.
DECOMMISSION_VERIFIED = 'decommissioned'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def safe_prepared_plan(value):
    """Persist only the bounded public plan contract, never provider raw output."""
    if not isinstance(value, dict):
        raise ValueError('Invalid prepared plan')
    clean = {key: value[key] for key in ('plan_digest', 'actions', 'summary', 'bindings')}
    if (not isinstance(clean['plan_digest'], str) or not 1 <= len(clean['plan_digest']) <= 128
            or not isinstance(clean['actions'], (dict, list))
            or not isinstance(clean['summary'], dict) or not isinstance(clean['bindings'], dict)):
        raise ValueError('Invalid prepared plan')
    forbidden = {'password', 'passwd', 'token', 'private_key', 'api_key', 'secret',
                 'raw', 'raw_output', 'stdout', 'stderr', 'planned_values', 'prior_state',
                 'resource_changes', 'configuration', 'variables'}
    def check(item, depth=0):
        if depth > 8:
            raise ValueError('Prepared plan summary too deep')
        if type(item) is dict:
            if len(item) > 256:
                raise ValueError('Prepared plan summary too large')
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 80:
                    raise ValueError('Invalid prepared plan key')
                normalized = key.lower().replace('-', '_')
                if normalized in forbidden or normalized.endswith(('_password', '_passwd', '_token', '_private_key', '_api_key', '_secret')):
                    raise ValueError('Prepared plan contains private provider evidence')
                check(child, depth + 1)
        elif type(item) is list:
            if len(item) > 256:
                raise ValueError('Prepared plan summary too large')
            for child in item:
                check(child, depth + 1)
        elif type(item) is str:
            if (len(item) > 1024 or 'PRIVATE KEY-----' in item
                    or re.search(r'://[^/\s]+@', item)):
                raise ValueError('Prepared plan contains private or oversized evidence')
        elif item is not None and type(item) not in (bool, int, float):
            raise ValueError('Prepared plan must contain JSON values only')
        elif type(item) is float and not math.isfinite(item):
            raise ValueError('Prepared plan must contain finite values')
    check(clean)
    encoded = json.dumps(clean, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode()) > 65536:
        raise ValueError('Prepared plan summary too large')
    return json.loads(encoded)


class Jobs:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self.admission = app.state.admission
        self.catalog = app.state.catalog

    # Attribution a caller may add at creation time. These are observability
    # facts about where a job came from; they can never replace approval,
    # provider pins or any other security-relevant job field.
    ATTRIBUTION_KEYS = ('service_request',)

    def get(self, identity):
        job = self.store.get('jobs', identity)
        if not job:
            raise HTTPException(404, 'Unknown job')
        return job

    def can_rollback(self, job):
        if job.get('requires_manual_review') or not job.get('approved_execution_provider'):
            return False
        try:
            pinned = job['approved_execution_provider']
            if execution_binding(self.app, pinned['name']) != pinned:
                return False
            if getattr(resolve_provider(self.app, 'execution', pinned['name']), 'supports_rollback', True) is False:
                return False
        except (HTTPException, KeyError, TypeError):
            return False
        if job['status'] == 'succeeded':
            return True
        if job['status'] != 'rollback_failed' or (job.get('result') or {}).get('status') != 'rollback_failed':
            return False
        # A retry is not a blind rerun of an arbitrary failed job. Require durable,
        # internally consistent partial-restoration evidence and real snapshots.
        folder = self.store.path / 'jobs' / job['id']
        try:
            path = folder / 'journal.json'
            if path.is_symlink() or folder.resolve() != folder.absolute():
                return False
            journal = json.loads(path.read_text())
            if journal['job_id'] != job['id'] or journal['status'] != 'rollback_failed':
                return False
            entries, modified = journal['entries'], journal['modified']
            restored = journal.get('restored', [])
            if not isinstance(entries, list) or not isinstance(modified, list) or not isinstance(restored, list):
                return False
            if any(type(index) is not int or index < 0 or index >= len(entries) for index in modified + restored):
                return False
            if len(set(modified)) != len(modified) or len(set(restored)) != len(restored) or not set(restored) <= set(modified):
                return False
            outstanding = set(modified) - set(restored)
            if not outstanding:
                return False
            result_paths = {row['path'] for row in job['result'].get('snapshots', [])}
            for index in outstanding:
                snapshot = Path(entries[index]['snapshot_path'])
                if str(snapshot) not in result_paths or snapshot.resolve() != snapshot.absolute() or not snapshot.is_relative_to(folder / 'snapshots'):
                    return False
                if not isinstance(json.loads(snapshot.read_text()), dict):
                    return False
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def decommission_runner(self, job):
        """The pinned provider's explicit ``decommission(job, emit)`` capability.

        Retirement is never substituted with rollback: the execution provider
        selected for the job must itself expose the method, and it is invoked
        through the durable-approval command guard.
        """
        pinned = job.get('approved_execution_provider')
        if not isinstance(pinned, dict) or not isinstance(pinned.get('name'), str):
            return None
        provider = resolve_provider(self.app, 'execution', pinned['name'])
        if not callable(getattr(provider, 'decommission', None)):
            return None
        return lambda current, emit: invoke_approved_execution(
            self.app, provider, 'decommission', current, emit)

    def can_decommission(self, job):
        """A retired service needs a succeeded request and a pinned provider
        that explicitly supports decommission. Never implied by rollback."""
        if not isinstance(job, dict) or not isinstance(job.get('service_request'), dict):
            return False
        if job.get('status') != 'succeeded' or job.get('requires_manual_review'):
            return False
        try:
            pinned = job['approved_execution_provider']
            if not isinstance(pinned, dict) or execution_binding(self.app, pinned['name']) != pinned:
                return False
            return self.decommission_runner(job) is not None
        except (HTTPException, KeyError, TypeError, ValueError, AttributeError, RuntimeError):
            return False

    def present(self, job):
        return {**job, 'can_rollback': self.can_rollback(job),
                'can_decommission': self.can_decommission(job)}

    def emit(self, identity, phase, level, message):
        with self.store.lock:
            job = self.get(identity)
            event = {'id': len(job['events']) + 1, 'time': now(), 'phase': phase,
                     'level': level, 'message': str(message)[:1000]}
            job['events'].append(event)
            self.store.put('jobs', identity, job)

    def notify(self, event, payload):
        # Observers receive summaries only, never graph/config/provider output.
        summary = {key: payload.get(key) for key in ('job_id', 'status', 'operation', 'environment')}
        summary.update(time=now(), event=event)
        registry = getattr(self.app.state, 'registry', None)
        failures = []
        if registry:
            try:
                failures = [report for report in registry.emit(event, summary) if report['status'] == 'error']
            except (Exception, SystemExit) as exc:
                failures = [{'error_type': type(exc).__name__}]
        try:
            resolve_provider(self.app, 'notifications').send(copy.deepcopy(summary))
        except (Exception, SystemExit) as exc:
            failures.append({'error_type': type(exc).__name__})
        if failures:
            self.store.audit('system', 'lifecycle.observer_failed', summary['job_id'],
                             {'event': event, 'error_types': [row['error_type'] for row in failures]})
            if summary['job_id'] and self.store.get('jobs', summary['job_id']):
                self.emit(summary['job_id'], 'observers', 'warning', 'Lifecycle observer or notification unavailable; deployment status unchanged')

    def check(self, graph, actor_role='admin'):
        report = self.admission.validate(graph, actor_role)
        if not report['allowed']:
            raise HTTPException(422, report)
        graph_execution_name(self.app, graph)
        return report

    def pin_execution(self, job, prefix):
        manifests = self.catalog.discover()
        job[prefix + '_execution_provider'] = execution_binding(self.app, graph_execution_name(self.app, job['graph']))
        job[prefix + '_graph_hash'] = digest(job['graph'])
        job[prefix + '_manifest_hashes'] = {node['plugin_id']: digest(manifests[node['plugin_id']]) for node in job['graph']['nodes']}
        job[prefix + '_source_hashes'] = {node['plugin_id']: self.catalog.fingerprint(node['plugin_id']) for node in job['graph']['nodes']}

    def check_offering_pins(self, job, prefix):
        if 'service_request' not in job:
            return
        pins = job['service_request'].get('pins')
        if pins != {key: job.get(f'{prefix}_{key}')
                    for key in ('graph_hash', 'manifest_hashes', 'source_hashes')}:
            raise HTTPException(409, 'Published offering changed; publish a new version')

    def create(self, graph, actor, attribution=None):
        with self.store.lock:
            identity = secrets.token_hex(16)
            self.notify('before_plan', {'job_id': identity, 'status': 'planning', 'environment': graph['environment'], 'operation': 'execute'})
            report = self.check(graph, actor['role'])
            graph = self.catalog.normalize(graph)
            job = {'id': identity, 'name': graph['name'], 'status': 'pending_approval',
                   'created_at': now(), 'created_by': actor['username'], 'created_by_role': actor['role'],
                   'environment': graph['environment'], 'graph': graph, 'cost': report['cost'],
                   'git_commit': None, 'events': [], 'result': None, 'approved_by': None,
                   'error': None, 'requires_manual_review': False, 'operation': 'execute'}
            if attribution:
                if not isinstance(attribution, dict) or set(attribution) - set(self.ATTRIBUTION_KEYS):
                    raise ValueError('Unsupported job attribution')
                # Persist attribution with the row itself: a request can never
                # return a job id whose origin was not durably recorded.
                job.update(copy.deepcopy(attribution))
            # Legacy graphs remain submittable even if their global provider is
            # unavailable; approval still fails closed, as before.
            try:
                provider = resolve_provider(self.app, 'execution', graph_execution_name(self.app, graph))
            except HTTPException:
                provider = None
            if provider is not None and getattr(provider, 'requires_saved_plan', False):
                self.pin_execution(job, 'preparation')
                self.check_offering_pins(job, 'preparation')
                job.update(status='queued', operation='prepare')
            self.store.put('jobs', job['id'], job)
            self.emit(job['id'], 'preparation' if job['operation'] == 'prepare' else 'approval', 'info',
                      'Queued for serialized plan preparation' if job['operation'] == 'prepare' else 'Awaiting explicit administrator approval')
            self.store.audit(actor['username'], 'job.submitted', job['id'])
            if job['operation'] == 'prepare':
                self.app.state.worker.enqueue(job['id'])
            return self.get(job['id'])

    def git_commit(self, job):
        repo = self.store.path / 'gitops'
        repo.mkdir(mode=0o700, exist_ok=True)
        repo.chmod(0o700)
        def git(*args):
            return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', '-c', 'user.name=KuberWeaver', '-c', 'user.email=local@kuberweaver.invalid', '-C', str(repo), *args], capture_output=True, text=True, check=True, timeout=20).stdout.strip()
        if not (repo / '.git').exists():
            git('init', '--quiet')
        folder = repo / 'jobs'
        folder.mkdir(mode=0o700, exist_ok=True)
        filename = folder / (job['id'] + '.json')
        artifact = {'id': job['id'], 'graph': job['graph'], 'cost': job['cost'], 'approved_by': job['approved_by'], 'approved_at': job['approved_at']}
        if 'prepared_plan' in job:
            artifact.update(prepared_plan=job['prepared_plan'], approved_prepared_plan=job['approved_prepared_plan'])
        filename.write_text(json.dumps(artifact, indent=2, sort_keys=True) + '\n')
        filename.chmod(0o600)
        git('add', '--', 'jobs/' + filename.name)
        git('commit', '--quiet', '-m', 'Approve plan ' + job['id'])
        commit = git('rev-parse', 'HEAD')
        # Read the exact committed artifact rather than trusting git's exit status.
        if json.loads(git('show', commit + ':jobs/' + filename.name)) != artifact:
            raise RuntimeError('GitOps artifact readback failed')
        return commit

    def approve(self, identity, actor, *, expected_graph_hash=None):
        def publication_policy_hash():
            try:
                with (self.store.path / 'config' / 'gitops.json').open('rb') as stream:
                    return hashlib.sha256(stream.read(16385)).hexdigest()
            except FileNotFoundError:
                return None
            except OSError:
                return 'unreadable'

        with self.store.lock:
            job = self.get(identity)
            if job['status'] != 'pending_approval' or job.get('requires_manual_review') or (job.get('approval_claim') or {}).get('state') == 'inflight':
                raise HTTPException(409, 'Job is not awaiting approval')
            if expected_graph_hash is not None and digest(job['graph']) != expected_graph_hash:
                raise HTTPException(409, 'Reviewed graph changed')
            report = self.check(job['graph'], job.get('created_by_role', 'operator'))
            policy_hash = digest(self.admission.settings())
            remote_policy_hash = publication_policy_hash()
            provider = resolve_provider(self.app, 'execution', graph_execution_name(self.app, job['graph']))
            prepared = getattr(provider, 'requires_saved_plan', False) or 'preparation_execution_provider' in job
            if prepared:
                try:
                    if job['preparation_execution_provider']['name'] != graph_execution_name(self.app, job['graph']):
                        raise ValueError('Prepared execution selection changed')
                    check_execution_pins(self.app, job, 'preparation')
                    check_prepared_plan(self.app, provider, job)
                except Exception:
                    raise HTTPException(409, 'Prepared plan missing, invalid or changed; submit a new job') from None
                job['approved_prepared_plan'] = digest(job['prepared_plan'])
                # Approval adopts the original plan's pins, never a fresh
                # fingerprint that could bless drift after initial validation.
                for suffix in ('execution_provider', 'graph_hash', 'manifest_hashes', 'source_hashes'):
                    job['approved_' + suffix] = copy.deepcopy(job['preparation_' + suffix])
            else:
                job['approved_execution_provider'] = execution_binding(self.app, graph_execution_name(self.app, job['graph']))
                job['approved_graph_hash'] = digest(job['graph'])
                manifests = self.catalog.discover()
                job['approved_manifest_hashes'] = {node['plugin_id']: digest(manifests[node['plugin_id']]) for node in job['graph']['nodes']}
            job.update(approved_by=actor['username'], approved_at=now(), cost=report['cost'])
            try:
                if not prepared:
                    job['approved_source_hashes'] = {node['plugin_id']: self.catalog.fingerprint(node['plugin_id']) for node in job['graph']['nodes']}
                self.check_offering_pins(job, 'approved')
                job['git_commit'] = self.git_commit(job)
            except HTTPException:
                raise
            except Exception:
                self.store.audit(actor['username'], 'job.gitops_failed', identity)
                raise HTTPException(503, 'Local GitOps commit failed; execution was not queued')
            # Durable, never automatically expired/reaped. A process interruption
            # leaves an ambiguous publication attempt blocked for manual review.
            job.update(status='approving', requires_manual_review=True,
                       approval_claim={'id': secrets.token_hex(16), 'state': 'inflight'})
            self.store.put('jobs', identity, job)
            reserved = digest({k: v for k, v in job.items() if k != 'events'})
        # No shared store lock, including from authenticated callback callers.
        # Unexpected exceptions deliberately preserve the durable inflight claim.
        remote = publish_remote(self.store.path, job['git_commit'])
        with self.store.lock:
            current = self.get(identity)
            unchanged = digest({k: v for k, v in current.items() if k != 'events'}) == reserved
            if not unchanged:
                # Never overwrite a concurrent rejection or changed graph with our
                # stale approval snapshot, even if the exact old commit published.
                current.update(requires_manual_review=True, error='Approval changed during publication; manual review required')
                if current.get('approval_claim') == job['approval_claim']:
                    current['approval_claim']['state'] = 'conflict'
                if remote['status'] != 'disabled':
                    current['remote_gitops'] = remote
                self.store.put('jobs', identity, current)
                raise HTTPException(409, 'Approval changed during publication; execution was not queued')
            job = current
            job.pop('remote_gitops', None)
            if remote['status'] != 'disabled':
                job['remote_gitops'] = remote
            # Persist sanitized publication evidence, but retain the inflight
            # guard until final validation/queueing or a known required failure.
            self.store.put('jobs', identity, job)
            if remote['status'] not in {'disabled', 'verified'}:
                self.store.audit(actor['username'], 'job.remote_gitops_failed', identity, {'status': remote['status']})
                self.emit(identity, 'gitops', 'warning', 'Remote GitOps publication was not verified')
                if remote['required_before_execution']:
                    job = self.get(identity)
                    job.update(status='pending_approval', requires_manual_review=False)
                    job['approval_claim']['state'] = 'completed'
                    self.store.put('jobs', identity, job)
                    raise HTTPException(503, 'Required remote GitOps publication failed; execution was not queued')
                job = self.get(identity)
            try:
                manifests = self.catalog.discover()
                check_prepared_plan(self.app, provider, job, approved=True)
                valid = (
                    digest(job['graph']) == job['approved_graph_hash']
                    and digest(self.admission.settings()) == policy_hash
                    and publication_policy_hash() == remote_policy_hash
                    and self.check(job['graph'], job.get('created_by_role', 'operator')) == report
                    and execution_binding(self.app, graph_execution_name(self.app, job['graph'])) == job['approved_execution_provider']
                    and all(plugin_id in manifests and digest(manifests[plugin_id]) == value
                            for plugin_id, value in job['approved_manifest_hashes'].items())
                    and all(self.catalog.fingerprint(plugin_id) == value
                            for plugin_id, value in job['approved_source_hashes'].items()))
            except Exception:
                valid = False
            if not valid:
                job.update(requires_manual_review=True, error='Approval dependencies changed during publication; manual review required')
                job['approval_claim']['state'] = 'conflict'
                self.store.put('jobs', identity, job)
                raise HTTPException(409, 'Approval dependencies changed; execution was not queued')
            job.update(status='queued', requires_manual_review=False)
            job['approval_claim']['state'] = 'completed'
            self.store.put('jobs', identity, job)
            self.emit(identity, 'approval', 'info', 'Approved and committed locally; queued for serialized execution')
            self.store.audit(actor['username'], 'job.approved', identity, {'git_commit': job['git_commit']})
            self.notify('after_approval', {'job_id': identity, 'status': 'queued', 'operation': 'execute', 'environment': job['environment']})
            self.app.state.worker.enqueue(identity)
            return self.get(identity)

    def reject(self, identity, actor):
        with self.store.lock:
            job = self.get(identity)
            if job['status'] != 'pending_approval':
                raise HTTPException(409, 'Job is not awaiting approval')
            job['status'] = 'rejected'
            self.store.put('jobs', identity, job)
            self.emit(identity, 'approval', 'warning', 'Rejected by administrator')
            self.store.audit(actor['username'], 'job.rejected', identity)
            return self.get(identity)

    def rollback(self, identity, actor):
        with self.store.lock:
            job = self.get(identity)
            if not self.can_rollback(job):
                raise HTTPException(409, 'Rollback requires a successful job or a verified retryable rollback_failed journal; interrupted jobs require manual review')
            pinned = job['approved_execution_provider']
            if execution_binding(self.app, pinned['name']) != pinned:
                raise HTTPException(409, 'Approved execution provider changed; rollback requires manual review')
            job.update(status='queued', operation='rollback', rollback_requested_by=actor['username'])
            self.store.put('jobs', identity, job)
            self.emit(identity, 'rollback', 'info', 'Explicit administrator rollback queued')
            self.store.audit(actor['username'], 'job.rollback_requested', identity)
            self.app.state.worker.enqueue(identity)
            return self.get(identity)

    def decommission(self, identity, actor):
        """Queue explicit administrator retirement of one succeeded service.

        The request is the approval: it performs no provider call, persists
        ``decommission_queued`` and hands the job to the one serialized worker.
        Only a service request whose pinned provider declares decommission is
        eligible; retirement is never rewritten into a rollback.
        """
        if (not isinstance(actor, dict) or not isinstance(actor.get('username'), str)
                or not actor['username'] or actor.get('role') != 'admin'):
            raise HTTPException(403, 'A signed-in administrator is required')
        if actor.get('principal_type') == 'agent':
            raise HTTPException(403, 'A human administrator session is required')
        with self.store.lock:
            job = self.get(identity)
            attribution = job.get('service_request')
            if not isinstance(attribution, dict) or not attribution.get('offering_id'):
                raise HTTPException(404, 'Unknown service request')
            if not self.can_decommission(job):
                raise HTTPException(409, 'Service retirement requires a succeeded service request '
                                         'whose pinned execution provider supports decommission')
            job.update(status='decommission_queued', operation='decommission',
                       decommission_requested_by=actor['username'],
                       decommission_requested_at=now())
            self.store.put('jobs', identity, job)
            self.emit(identity, 'decommission', 'info',
                      'Explicit administrator retirement queued; rollback was not requested')
            self.store.audit(actor['username'], 'job.decommission_requested', identity)
            self.app.state.worker.enqueue(identity)
            return self.get(identity)


class Worker:
    def __init__(self, app):
        self.app = app
        self.jobs = app.state.jobs
        self.store = app.state.store
        self.queue = queue.Queue()
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.pause.set()
        self.thread = None
        self.active_identity = None

    def recover(self):
        with self.store.lock:
            for job in self.store.list('jobs'):
                if job['status'] in {'queued', 'planning', 'running', 'rolling_back',
                                     'decommission_queued', 'decommissioning'}:
                    retiring = job.get('operation') == 'decommission'
                    job.update(status='decommission_failed' if retiring
                                      else 'rollback_failed' if job.get('operation') == 'rollback'
                                      else 'failed',
                               error=('Service retirement was interrupted by process restart: '
                                      'manual review required; no provider was re-invoked'
                                      if retiring else
                                      'Interrupted by process restart: manual review required; never automatically reapplied'),
                               requires_manual_review=True)
                    self.store.put('jobs', job['id'], job)
                    self.jobs.emit(job['id'], 'recovery', 'error', job['error'])
                    self.store.audit('system', 'job.interrupted', job['id'])

    def start(self):
        self.recover()
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, name='kuberweaver-single-worker', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.pause.set()
        self.queue.put(None)
        if self.thread:
            self.thread.join()

    def enqueue(self, identity):
        self.queue.put(identity)

    def run(self):
        while not self.stop.is_set():
            identity = self.queue.get()
            if identity is None:
                return
            self.pause.wait()
            if self.stop.is_set():
                return
            self.process(identity)

    def process(self, identity):
        rollback = preparing = decommission = False
        claimed = False
        completion = None
        try:
            with self.store.lock:
                job = self.jobs.get(identity)
                requested = job.get('operation')
                decommission = requested == 'decommission'
                # Retirement uses its own visible queue state; it still runs on
                # this one serialized consumer, never inline in the request.
                if decommission:
                    if job['status'] != 'decommission_queued':
                        return
                elif job['status'] != 'queued':
                    return
                claimed = True
                self.active_identity = identity
                rollback = requested == 'rollback'
                preparing = requested == 'prepare'
                if job.get('requires_manual_review'):
                    raise RuntimeError('Manual review required')
                if not preparing and not decommission and (not job.get('approved_by') or not job.get('git_commit')):
                    raise RuntimeError('Approval evidence missing')
                prefix = 'preparation' if preparing else 'approved'
                check_execution_pins(self.app, job, prefix)
                executor = resolve_provider(self.app, 'execution', job[prefix + '_execution_provider']['name'])
                runner = None
                if decommission:
                    # Re-check the capability at execution time: a provider that
                    # no longer declares decommission fails closed, it is never
                    # silently downgraded to rollback.
                    runner = self.jobs.decommission_runner(job)
                    if runner is None:
                        raise RuntimeError('Decommission capability unavailable')
                elif not preparing:
                    check_prepared_plan(self.app, executor, job, approved=True)
                if not rollback and not decommission:
                    self.jobs.check(job['graph'], job.get('created_by_role', 'operator'))
                job['status'] = ('planning' if preparing else 'rolling_back' if rollback
                                 else 'decommissioning' if decommission else 'running')
                self.store.put('jobs', identity, job)
            # Notifications retain their existing schema; the operation names the
            # action that actually ran so a retirement never reads as rollback.
            notification_operation = 'execute' if preparing else requested
            self.jobs.notify('job_started', {'job_id': identity, 'status': job['status'], 'operation': notification_operation, 'environment': job['environment']})
            self.jobs.emit(identity, 'preparation' if preparing else 'rollback' if rollback else 'decommission' if decommission else 'execution', 'info', 'Serialized worker started')
            callback = lambda phase, level, message: self.jobs.emit(identity, phase, level, message)
            if decommission:
                if runner is None:  # Re-checked here so a stale item fails closed.
                    raise RuntimeError('Decommission capability unavailable')
                result = runner(copy.deepcopy(job), callback)
            else:
                result = invoke_approved_execution(self.app, executor, 'prepare_plan' if preparing else 'rollback' if rollback else 'execute', copy.deepcopy(job), callback)
            if preparing:
                check_execution_pins(self.app, job, 'preparation')
                job['prepared_plan'] = safe_prepared_plan(result)
                job['prepared_plan_hash'] = digest(job['prepared_plan'])
                check_prepared_plan(self.app, executor, job)
                with self.store.lock:
                    current = self.jobs.get(identity)
                    if (current['status'] != 'planning' or current['graph'] != job['graph']
                            or any(current.get(key) != value for key, value in job.items() if key.startswith('preparation_'))):
                        raise RuntimeError('Preparation dependencies changed')
                    check_execution_pins(self.app, current, 'preparation')
                    self.jobs.check(current['graph'], current.get('created_by_role', 'operator'))
                    current.update(prepared_plan=job['prepared_plan'], prepared_plan_hash=job['prepared_plan_hash'],
                                   status='pending_approval', operation='execute')
                    self.store.put('jobs', identity, current)
                    completion = {'job_id': identity, 'status': current['status'],
                                  'operation': 'execute', 'environment': current['environment']}
                    self.store.audit('system', 'job.prepared', identity)
                self.jobs.emit(identity, 'approval', 'info', 'Saved plan prepared; awaiting explicit administrator approval')
            else:
                if decommission:
                    # Only the single verified terminal status is accepted; a
                    # partial or unproven retirement is a persistent failure.
                    if (not isinstance(result, dict) or result.get('status') != DECOMMISSION_VERIFIED
                            or result.get('requires_manual_review') is True):
                        raise RuntimeError('Decommission provider did not verify retirement')
                elif not isinstance(result, dict) or result.get('status') not in {'succeeded', 'failed', 'rolled_back', 'rollback_failed'}:
                    raise RuntimeError('Execution provider returned an invalid terminal result')
                with self.store.lock:
                    job = self.jobs.get(identity)
                    job.update(status=result['status'], result=result, finished_at=now(), error=result.get('error'))
                    job['requires_manual_review'] = job.get('requires_manual_review', False) or result.get('requires_manual_review') is True
                    self.store.put('jobs', identity, job)
                    completion = {'job_id': identity, 'status': job['status'],
                                  'operation': 'rollback' if rollback else 'decommission' if decommission else 'execute', 'environment': job['environment']}
                    self.store.audit('system', 'job.' + result['status'], identity)
                verified = result['status'] in {'succeeded', 'rolled_back', DECOMMISSION_VERIFIED}
                self.jobs.emit(identity, 'complete', 'info' if verified else 'error',
                               ('Service retirement finished: ' if decommission else 'Execution finished: ') + result['status'])
        except (Exception, SystemExit, KeyboardInterrupt) as exc:
            if completion is not None:
                # Publication is final for this operation. Audit/event failures
                # must not rewrite its completion or a concurrently queued next
                # operation. The finally block still notifies outside the lock.
                return
            # Never persist arbitrary upstream exception text or raw command output.
            with self.store.lock:
                job = self.jobs.get(identity)
                message = ('Service retirement failed; inspect private provider evidence (' + type(exc).__name__ + ')' if decommission else
                           'Plan preparation failed; inspect private provider evidence' if preparing else
                           'Execution denied by current admission policy' if isinstance(exc, HTTPException) else
                           'Execution failed; inspect private provider evidence (' + type(exc).__name__ + ')')
                job.update(status=('decommission_failed' if decommission else
                                   'rollback_failed' if rollback else 'failed'),
                           error=message, finished_at=now())
                if decommission or preparing or isinstance(exc, (ExecutionTrustError, SystemExit, KeyboardInterrupt)):
                    job['requires_manual_review'] = True
                self.store.put('jobs', identity, job)
                completion = {'job_id': identity, 'status': job['status'],
                              'operation': 'rollback' if rollback else 'decommission' if decommission else 'execute', 'environment': job['environment']}
                self.store.audit('system', 'job.failed', identity, {'error_type': type(exc).__name__})
            self.jobs.emit(identity, 'complete', 'error', message)
        finally:
            if claimed:
                try:
                    # Freeze the operation's result under its publication lock;
                    # approval/rollback may already have queued the next one.
                    if completion is not None:
                        self.jobs.notify('job_finished', completion)
                finally:
                    with self.store.lock:
                        self.active_identity = None
