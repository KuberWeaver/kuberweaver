"""Built-in providers. Inventory is GET-only; diagnostics are deterministic rules."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import threading
import inspect
import hashlib
import httpx
from fastapi import HTTPException
from .storage import now

# Consumer protocols, not import paths. Unknown kinds cannot become config keys.
CONSUMER_METHODS = {'inventory': ('read',), 'diagnostics': ('read',),
                    'execution': ('execute', 'rollback'), 'notifications': ('send',),
                    'secrets': ('resolve',)}
DEFAULT_BINDINGS = {'inventory': 'gns3', 'diagnostics': 'rules', 'execution': 'default',
                    'notifications': 'local-outbox', 'secrets': 'restricted-file'}


def resolve_provider(app, kind, name=None):
    if getattr(app.state, 'provider_config_error', None):
        raise HTTPException(503, 'Provider binding configuration is invalid')
    name = name if name is not None else app.state.provider_bindings[kind]
    provider = registered_provider(app.state.registry, kind, name)
    if kind == 'execution':
        if hasattr(provider, 'secret_resolver'):
            provider.secret_resolver = lambda reference: resolve_provider(app, 'secrets').resolve(reference)
        if hasattr(provider, 'connections'):
            provider.connections = getattr(app.state, 'connections', None)
        if hasattr(provider, 'ssh_keys'):
            provider.ssh_keys = getattr(app.state, 'ssh_keys', None)
    return provider


def registered_provider(registry, kind, name):
    try:
        provider = registry.get(kind, name)
    except KeyError:
        provider = None
    methods = CONSUMER_METHODS[kind]
    if kind == 'execution' and getattr(provider, 'requires_saved_plan', False):
        methods += ('prepare_plan', 'validate_plan')
    arity = {'read': 0, 'execute': 2, 'rollback': 2, 'send': 1, 'resolve': 1,
             'prepare_plan': 2, 'validate_plan': 1}
    try:
        if provider is None:
            raise TypeError('No implementation')
        for method in methods:
            consumer = getattr(provider, method, None)
            if not callable(consumer) or inspect.iscoroutinefunction(consumer) or inspect.iscoroutinefunction(getattr(consumer, '__call__', None)):
                raise TypeError('Expected synchronous method')
            inspect.signature(consumer).bind(*([None] * arity[method]))
    except (TypeError, ValueError):
        raise HTTPException(503, {'kind': kind, 'name': name, 'error': 'Selected provider unavailable or incompatible'})
    return provider


def provider_provenance(app, kind, name, *, provider=None):
    # Domain consumers may supply an already validated object without adding
    # their provider family to the closed global binding configuration.
    if provider is None:
        provider = registered_provider(app.state.registry, kind, name)
    # Pin the actual class hierarchy as well as its extension package: an
    # entrypoint may merely import a provider implemented elsewhere.
    paths = [Path(inspect.getfile(cls)).resolve()
             for cls in type(provider).__mro__ if cls is not object]
    if isinstance(provider, RuntimeExecutor):
        # The registry object delegates to a separate implementation. Pin that
        # class's source (and inherited implementations), not only this wrapper.
        paths += [Path(inspect.getfile(cls)).resolve()
                  for cls in type(provider.app.state.executor).__mro__ if cls is not object]
        paths = sorted(set(paths))
    # Include the entire trusted package, not only the entrypoint/class definition.
    for report in app.state.extension_reports:
        if report.get('status') == 'loaded' and any(row['kind'] == kind and row['name'] == name for row in report.get('providers', [])):
            paths = sorted(set(paths) | {p for p in Path(report['path']).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
            break
    owners = [type(provider)]
    if isinstance(provider, RuntimeExecutor):
        owners.append(type(provider.app.state.executor))
    # Walk each MRO declaration explicitly: a subclass must not hide a helper
    # required by an inherited implementation, nor may a wrapper hide delegates.
    for owner in owners:
        for cls in owner.__mro__:
            declared = vars(cls).get('source_dependencies', ())
            if not isinstance(declared, (tuple, list)):
                raise ValueError('Execution source dependencies must be explicit source-file paths')
            for dependency in declared:
                dependency = Path(dependency)
                if not dependency.is_absolute() or dependency.suffix != '.py' or dependency.is_symlink() or not dependency.is_file():
                    raise ValueError('Invalid execution source dependency')
                paths.append(dependency.resolve())
    records = [(str(path), hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(set(paths))]
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def execution_credential_binding(app):
    from .execution import TARGETS
    from .execution_credentials import credential_configuration, credential_configuration_fingerprint
    try:
        configuration = credential_configuration(app.state.state_dir, TARGETS)
        selected = None
        if configuration and configuration['enabled']:
            name = app.state.provider_bindings['secrets']
            resolve_provider(app, 'secrets', name)
            selected = {'name': name, 'provenance': provider_provenance(app, 'secrets', name)}
        return credential_configuration_fingerprint(app.state.state_dir, TARGETS, selected)
    except Exception:
        raise HTTPException(503, 'Execution credential configuration or selected source unavailable') from None


def graph_execution_name(app, graph):
    """Only the trusted top-level manifest field selects registry execution."""
    manifests = app.state.catalog.discover()
    names = {manifests[node['plugin_id']].get('execution_provider', app.state.provider_bindings['execution'])
             for node in graph['nodes']}
    if len(names) != 1:
        raise HTTPException(422, 'A job must use one execution provider')
    return names.pop()


def execution_binding(app, name=None):
    name = name if name is not None else app.state.provider_bindings['execution']
    resolve_provider(app, 'execution', name)
    try:
        provenance = provider_provenance(app, 'execution', name)
    except (OSError, TypeError, ValueError):
        raise HTTPException(503, 'Execution provider source provenance unavailable')
    if app.state.provider_loaded_sources.get(('execution', name)) != provenance:
        raise HTTPException(503, 'Execution provider source changed since registration; reload and approve again')
    return {'name': name, 'provenance': provenance}


def provider_listing(app):
    available = []
    for row in app.state.registry.list():
        if row['kind'] not in CONSUMER_METHODS:
            continue
        try:
            resolve_provider(app, row['kind'], row['name'])
            if row['kind'] == 'execution':
                execution_binding(app, row['name'])
            compatible = True
        except HTTPException:
            compatible = False
        available.append({**row, 'available': compatible, 'methods': list(CONSUMER_METHODS[row['kind']])})
    active = {}
    errors = []
    for kind, name in app.state.provider_bindings.items():
        descriptor = next((row for row in available if row['kind'] == kind and row['name'] == name), {})
        usable = bool(descriptor.get('available'))
        active[kind] = {**descriptor, 'kind': kind, 'name': name, 'available': usable,
                        'selected': True, 'status': descriptor.get('status', 'configured') if usable else 'unavailable'}
        if not usable:
            errors.append({'kind': kind, 'name': name, 'error': 'Selected provider unavailable or incompatible'})
    if app.state.provider_config_error:
        errors.append({'section': 'providers', 'error': app.state.provider_config_error})
    return {'schema_version': 1, 'bindings': dict(app.state.provider_bindings),
            'active': active, 'available': available, 'errors': errors}


GNS3_SERVER = 'http://192.168.10.55'
GNS3_PROJECT = '2c796f12-d163-44d9-8ebd-2a739c383b7b'


class GNS3Inventory:
    def __init__(self):
        self.client = httpx.Client(timeout=4, follow_redirects=False, trust_env=False)
        self.last_success = None
        self.last_error = None

    def read(self):
        result = {'project_id': GNS3_PROJECT, 'server_url': GNS3_SERVER, 'nodes': [],
                  'links': [], 'source': 'gns3', 'errors': []}
        for resource in ('nodes', 'links'):
            try:
                response = self.client.get(f'{GNS3_SERVER}/v2/projects/{GNS3_PROJECT}/{resource}')
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError('Unexpected upstream payload')
                records = []
                for item in payload:
                    if not isinstance(item, dict):
                        raise ValueError('Unexpected upstream record')
                    if resource == 'nodes':
                        records.append({'id': item['node_id'], 'name': item.get('name', item['node_id']),
                                        'status': item.get('status', 'unknown'), 'type': item.get('node_type', 'unknown'),
                                        'x': item.get('x', 0), 'y': item.get('y', 0), 'compute_id': item.get('compute_id')})
                    else:
                        records.append({'id': item['link_id'], 'nodes': item.get('nodes', []),
                                        'capturing': item.get('capturing', False)})
                result[resource] = records
            except Exception as exc:
                result['errors'].append({'resource': resource, 'message': 'GNS3 inventory unavailable', 'error_type': type(exc).__name__})
        self.last_error = bool(result['errors'])
        if not result['errors']:
            self.last_success = now()
        return result

    def describe(self):
        return {'id': 'gns3', 'name': 'GNS3 inventory', 'kind': 'inventory',
                'status': 'degraded' if self.last_error else 'configured', 'read_only': True,
                'server_url': GNS3_SERVER, 'project_id': GNS3_PROJECT, 'last_success': self.last_success,
                'description': 'Live read-only inventory; no topology mutation API is exposed.'}


class RuleDiagnostics:
    def __init__(self, app):
        self.app = app

    def read(self):
        findings = []
        for job in self.app.state.store.list('jobs'):
            if job.get('requires_manual_review'):
                findings.append({'id': 'interrupted-' + job['id'], 'severity': 'critical',
                                 'title': 'Manual review required', 'message': 'An interrupted job was not reapplied.',
                                 'resource': job['id'], 'recommendation': 'Inspect target state and snapshots; submit a fresh approved plan only after review.'})
            elif job['status'] in {'failed', 'rollback_failed'}:
                findings.append({'id': 'failed-' + job['id'], 'severity': 'critical' if job['status'] == 'rollback_failed' else 'warning',
                                 'title': 'Execution needs attention', 'message': job.get('error') or job['status'],
                                 'resource': job['id'], 'recommendation': 'Review persisted job events and target readback evidence.'})
        self.app.state.catalog.discover()
        for index, error in enumerate(self.app.state.catalog.errors):
            findings.append({'id': f'catalog-{index}', 'severity': 'warning', 'title': 'Invalid manifest',
                             'message': error['manifest'], 'recommendation': 'Review the local plugin manifest schema.'})
        for report in self.app.state.extension_reports:
            if report['status'] == 'error':
                findings.append({'id': 'extension-' + report.get('id', 'invalid'), 'severity': 'warning',
                                 'title': 'Extension failed closed', 'message': report.get('error', 'Registration failed'),
                                 'recommendation': 'Inspect and validate the trusted extension package.'})
        return {'mode': 'rules', 'read_only': True, 'findings': findings,
                'summary': {'total': len(findings), 'critical': sum(row['severity'] == 'critical' for row in findings),
                            'warning': sum(row['severity'] == 'warning' for row in findings)},
                'explanation': 'Deterministic rules over persisted evidence. No AI inference or automatic remediation.'}


class LocalOutbox:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / 'outbox' / 'notifications.jsonl'
        self.lock = threading.RLock()

    def send(self, payload):
        if payload.get('event') != 'job_finished':
            return  # This builtin delivers terminal summaries, not every observer event.
        # Lifecycle summaries only; never serialize arbitrary plugin output or config.
        record = {'time': now(), 'job_id': payload.get('job_id'), 'status': payload.get('status'),
                  'delivery': 'local_outbox', 'external_delivery': False}
        with self.lock:
            self.path.parent.mkdir(mode=0o700, exist_ok=True)
            self.path.parent.chmod(0o700)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a') as stream:
                stream.write(json.dumps(record) + '\n')
            self.path.chmod(0o600)


OPTIONAL = [
    {'id': 'git-remote', 'name': 'Remote GitOps', 'kind': 'gitops', 'status': 'planned', 'read_only': False, 'description': 'Local approved graph commits work; remote push and CI callbacks are not configured.'},
    {'id': 'oidc', 'name': 'OIDC / LDAP', 'kind': 'identity', 'status': 'planned', 'read_only': True, 'description': 'Local PBKDF2 accounts only; enterprise SSO is not configured.'},
]


class ExecutionTrustError(RuntimeError):
    """A dependency changed; remote state may need manual review."""


def check_execution_pins(app, job, prefix='approved'):
    """Check initial preparation or approved provenance without remote calls."""
    from .jobs import digest
    pinned = job[prefix + '_execution_provider']
    if execution_binding(app, pinned['name']) != pinned:
        raise RuntimeError('Execution source changed')
    if job.get(prefix + '_credentials') != execution_credential_binding(app):
        raise RuntimeError('Execution credential mapping changed')
    if digest(job['graph']) != job.get(prefix + '_graph_hash'):
        raise RuntimeError('Execution graph changed')
    manifests = app.state.catalog.discover()
    identities = {node['plugin_id'] for node in job['graph']['nodes']}
    for suffix, fingerprint in (('_manifest_hashes', lambda identity: digest(manifests[identity])),
                                ('_source_hashes', app.state.catalog.fingerprint)):
        expected = job.get(prefix + suffix, {})
        if set(expected) != identities or any(fingerprint(identity) != value for identity, value in expected.items()):
            raise RuntimeError('Execution plugin dependencies changed')


def check_prepared_plan(app, provider, job, *, approved=False):
    from .jobs import digest, safe_prepared_plan
    if not getattr(provider, 'requires_saved_plan', False) and 'preparation_execution_provider' not in job:
        return
    try:
        clean = safe_prepared_plan(job.get('prepared_plan'))
        expected = digest(clean)
        if clean != job['prepared_plan'] or expected != job.get('prepared_plan_hash'):
            raise RuntimeError('Prepared plan changed')
        if approved and expected != job.get('approved_prepared_plan'):
            raise RuntimeError('Approved prepared plan changed')
        if provider.validate_plan(copy.deepcopy(job)) is not None:
            raise RuntimeError('Prepared plan validation must return None')
    except Exception:
        raise ExecutionTrustError('Prepared plan unavailable or changed; manual review required') from None


def invoke_approved_execution(app, provider, operation, job, emit):
    """Apply the same command-boundary guards to preparation and application."""
    preparing = operation == 'prepare_plan'
    trust_error = None
    def check():
        nonlocal trust_error
        try:
            prefix = 'preparation' if preparing else 'approved'
            # Re-read durable approval evidence too: the provider's isolated
            # snapshot must not hide edits made between two commands.
            with app.state.store.lock:
                current = app.state.store.get('jobs', job['id'])
                keys = {'id', 'status', 'graph', 'operation', 'approved_by', 'git_commit', 'requires_manual_review'}
                keys.update(key for key in job if key.startswith(prefix + '_'))
                if not preparing:
                    keys.update(('prepared_plan', 'prepared_plan_hash', 'approved_prepared_plan'))
                if not current or any(current.get(key) != job.get(key) for key in keys):
                    raise RuntimeError('Durable execution evidence changed')
            check_execution_pins(app, job, prefix)
            if not preparing:
                check_prepared_plan(app, provider, job, approved=True)
        except Exception:
            trust_error = ExecutionTrustError('Execution dependencies changed; manual review required')
            raise trust_error from None
    check()
    runner = getattr(provider, 'runner', None)
    if runner is None:
        return getattr(provider, operation)(job, emit)
    def guarded_runner(*args, **kwargs):
        check()
        return runner(*args, **kwargs)
    provider.runner = guarded_runner
    try:
        result = getattr(provider, operation)(job, emit)
        if trust_error is not None:
            # Executors may catch a denied command while collecting restore
            # evidence. Preserve that failure, but never lose manual review or
            # accept a claimed success after a trust failure.
            if not isinstance(result, dict) or result.get('status') not in {'failed', 'rollback_failed'}:
                raise trust_error
            result = {**result, 'requires_manual_review': True}
        return result
    finally:
        provider.runner = runner


class RuntimeExecutor:
    source_dependencies = (Path(__file__).with_name("execution_credentials.py").resolve(),)
    """Stable registry contract with an injectable execution implementation."""
    def __init__(self, app):
        self.app = app

    def _invoke(self, operation, job, emit):
        implementation = self.app.state.executor
        if hasattr(implementation, 'secret_resolver'):
            implementation.secret_resolver = lambda reference: resolve_provider(self.app, 'secrets').resolve(reference)

        # Guard the delegated command boundary with the same durable approval,
        # graph and dependency checks as directly registered providers, including
        # restore. The registry wrapper itself has no runner to intercept.
        return invoke_approved_execution(self.app, implementation, operation, job, emit)

    def execute(self, job, emit):
        return self._invoke('execute', job, emit)

    def rollback(self, job, emit):
        return self._invoke('rollback', job, emit)


def register_builtins(registry, app):
    from .secrets import RestrictedFileProvider, OpenBaoProvider
    inventory = GNS3Inventory()
    rules = RuleDiagnostics(app)
    outbox = LocalOutbox(app.state.state_dir)
    registry.register('inventory', 'gns3', inventory, {'read_only': True, 'status': 'configured'})
    registry.register('diagnostics', 'rules', rules, {'mode': 'rules', 'read_only': True, 'status': 'configured'})
    registry.register('notifications', 'local-outbox', outbox, {'external_delivery': False, 'status': 'configured'})
    registry.register('execution', 'default', RuntimeExecutor(app), {'status': 'configured', 'description': 'Reviewed manifest-driven executor; approval is enforced by the microkernel.'})
    from .notifications import register_notifications
    register_notifications(registry, app)
    config = app.state.state_dir / 'config' / 'secrets.json'
    try:
        secrets_config = json.loads(config.read_text()) if config.exists() else {}
        if not isinstance(secrets_config, dict) or secrets_config.keys() - {'restricted_files', 'openbao'}:
            raise ValueError('Invalid secret source configuration')
    except Exception:
        for name in ('restricted-file', 'openbao'):
            registry.register('secrets', name, None, {'status': 'unavailable', 'reference_only': True,
                              'error': 'Invalid administrator secret-source configuration'})
        return
    approved_files = secrets_config.get('restricted_files', {})
    try:
        provider = RestrictedFileProvider(approved_files)
        metadata = {'status': 'configured' if approved_files else 'unconfigured', 'reference_only': True}
    except Exception:
        provider = None
        metadata = {'status': 'unavailable', 'reference_only': True, 'error': 'Invalid restricted-file configuration'}
    registry.register('secrets', 'restricted-file', provider, metadata)
    bao = secrets_config.get('openbao')
    if bao:
        try:
            registry.register('secrets', 'openbao', OpenBaoProvider(**bao), {'status': 'configured', 'reference_only': True})
        except Exception:
            registry.register('secrets', 'openbao', None, {'status': 'unavailable', 'reference_only': True, 'error': 'Invalid OpenBao configuration'})
    else:
        registry.register('secrets', 'openbao', None, {'status': 'unconfigured', 'reference_only': True, 'description': 'A scoped OpenBao provider is available; no server/token is configured.'})


def integrations(app):
    listing = provider_listing(app)
    rows = [{'id': item['name'], **item} for item in listing['active'].values()]
    rows.append({'id': 'local-gitops', 'name': 'Local GitOps', 'kind': 'gitops', 'status': 'configured', 'read_only': False})
    return rows + OPTIONAL
