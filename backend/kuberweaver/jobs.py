"""Durable approvals, local GitOps artifacts and exactly one queue consumer."""
from __future__ import annotations
import copy
import hashlib
import json
import queue
import secrets
import subprocess
import threading
from pathlib import Path
from fastapi import HTTPException
from .storage import now
from .providers import resolve_provider, execution_binding, execution_credential_binding, invoke_approved_execution
from .gitops import publish as publish_remote

TERMINAL = {'succeeded', 'failed', 'rolled_back', 'rollback_failed', 'rejected'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Jobs:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self.admission = app.state.admission
        self.catalog = app.state.catalog

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

    def present(self, job):
        return {**job, 'can_rollback': self.can_rollback(job)}

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
        return report

    def create(self, graph, actor):
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
            self.store.put('jobs', job['id'], job)
            self.emit(job['id'], 'approval', 'info', 'Awaiting explicit administrator approval')
            self.store.audit(actor['username'], 'job.submitted', job['id'])
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
            job['approved_execution_provider'] = execution_binding(self.app)
            job['approved_credentials'] = execution_credential_binding(self.app)
            job.update(approved_by=actor['username'], approved_at=now(), cost=report['cost'])
            job['approved_graph_hash'] = digest(job['graph'])
            manifests = self.catalog.discover()
            job['approved_manifest_hashes'] = {node['plugin_id']: digest(manifests[node['plugin_id']]) for node in job['graph']['nodes']}
            try:
                job['approved_source_hashes'] = {node['plugin_id']: self.catalog.fingerprint(node['plugin_id']) for node in job['graph']['nodes']}
                job['git_commit'] = self.git_commit(job)
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
                valid = (
                    digest(job['graph']) == job['approved_graph_hash']
                    and digest(self.admission.settings()) == policy_hash
                    and publication_policy_hash() == remote_policy_hash
                    and self.check(job['graph'], job.get('created_by_role', 'operator')) == report
                    and execution_binding(self.app) == job['approved_execution_provider']
                    and execution_credential_binding(self.app) == job['approved_credentials']
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
                if job['status'] in {'queued', 'running', 'rolling_back'}:
                    job.update(status='rollback_failed' if job.get('operation') == 'rollback' else 'failed',
                               error='Interrupted by process restart: manual review required; never automatically reapplied',
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
        rollback = False
        claimed = False
        try:
            with self.store.lock:
                job = self.jobs.get(identity)
                if job['status'] != 'queued':
                    return
                claimed = True
                self.active_identity = identity
                rollback = job.get('operation') == 'rollback'
                if not job.get('approved_by') or not job.get('git_commit'):
                    raise RuntimeError('Approval evidence missing')
                if digest(job['graph']) != job.get('approved_graph_hash'):
                    raise RuntimeError('Approved graph changed; manual review required')
                manifests = self.app.state.catalog.discover()
                if any(plugin_id not in manifests or digest(manifests[plugin_id]) != fingerprint for plugin_id, fingerprint in job.get('approved_manifest_hashes', {}).items()):
                    raise RuntimeError('Plugin manifest changed since approval; manual review required')
                if any(self.app.state.catalog.fingerprint(plugin_id) != fingerprint for plugin_id, fingerprint in job.get('approved_source_hashes', {}).items()):
                    raise RuntimeError('Plugin implementation changed since approval; manual review required')
                if job.get('approved_credentials') != execution_credential_binding(self.app):
                    raise RuntimeError('Approved execution credential mapping changed; manual review required')
                pinned = job.get('approved_execution_provider')
                if not pinned or execution_binding(self.app, pinned['name']) != pinned:
                    raise RuntimeError('Approved execution provider changed or is unavailable')
                if not rollback:
                    self.jobs.check(job['graph'], job.get('created_by_role', 'operator'))
                job['status'] = 'rolling_back' if rollback else 'running'
                self.store.put('jobs', identity, job)
            self.jobs.notify('job_started', {'job_id': identity, 'status': job['status'], 'operation': job.get('operation'), 'environment': job['environment']})
            self.jobs.emit(identity, 'rollback' if rollback else 'execution', 'info', 'Serialized worker started')
            executor = resolve_provider(self.app, 'execution', job['approved_execution_provider']['name'])
            callback = lambda phase, level, message: self.jobs.emit(identity, phase, level, message)
            result = invoke_approved_execution(self.app, executor, 'rollback' if rollback else 'execute', copy.deepcopy(job), callback)
            if not isinstance(result, dict) or result.get('status') not in {'succeeded', 'failed', 'rolled_back', 'rollback_failed'}:
                raise RuntimeError('Execution provider returned an invalid terminal result')
            with self.store.lock:
                job = self.jobs.get(identity)
                job.update(status=result['status'], result=result, finished_at=now(), error=result.get('error'))
                self.store.put('jobs', identity, job)
                self.store.audit('system', 'job.' + result['status'], identity)
            self.jobs.emit(identity, 'complete', 'info' if result['status'] in {'succeeded', 'rolled_back'} else 'error', 'Execution finished: ' + result['status'])
        except Exception as exc:
            # Never persist arbitrary upstream exception text or raw command output.
            with self.store.lock:
                job = self.jobs.get(identity)
                message = 'Execution denied by current admission policy' if isinstance(exc, HTTPException) else 'Execution failed; inspect private provider evidence (' + type(exc).__name__ + ')'
                job.update(status='rollback_failed' if rollback else 'failed', error=message, finished_at=now())
                self.store.put('jobs', identity, job)
                self.store.audit('system', 'job.failed', identity, {'error_type': type(exc).__name__})
            self.jobs.emit(identity, 'complete', 'error', message)
        finally:
            if claimed:
                try:
                    job = self.jobs.get(identity)
                    self.jobs.notify('job_finished', {'job_id': identity, 'status': job['status'], 'operation': job.get('operation'), 'environment': job['environment']})
                finally:
                    with self.store.lock:
                        self.active_identity = None
