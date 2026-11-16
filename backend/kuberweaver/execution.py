"""Generic, manifest-driven Ansible executor for explicitly configured GNS3 targets.

Only reviewed local playbooks run. No graph-provided host, executable, shell, image,
or filesystem path is accepted. The injected ``runner`` follows subprocess.run.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

import jsonschema
import yaml

try:
    from .execution_credentials import operation_credentials
except ImportError:  # Standalone loading used by the executor safety tests.
    from backend.kuberweaver.execution_credentials import operation_credentials

try:
    from .configuration import require_ssh_config, ssh_config
except ImportError:  # Standalone loading used by the executor safety tests.
    from backend.kuberweaver.configuration import require_ssh_config, ssh_config

ROOT = Path(__file__).resolve().parents[2]
SSH_CONFIG = ssh_config()
TARGETS = {'app': {'alias': 'infra-app', 'ip': '192.168.122.40'},
           'branch-client': {'alias': 'infra-branch-client', 'ip': '192.168.122.60'}}
IDENTIFIER = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')


def _identifier(value, kind):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f'Invalid {kind}; use 1..64 lowercase alphanumeric/hyphen characters')
    return value


def _private_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temp = path.with_suffix(path.suffix + '.new')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
    temp.chmod(0o600)
    temp.replace(path)


class Executor:
    def __init__(self, state_dir: Path, plugins_dir: Path | None = None, *, secret_resolver=None):
        self.secret_resolver = secret_resolver
        self.state_dir = Path(state_dir).resolve()
        self.plugins_dir = Path(plugins_dir or ROOT / 'plugins').resolve()
        self.runner = subprocess.run
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)

    @contextmanager
    def _locked(self):
        # Per-user global lock also covers distinct Executor instances/state dirs.
        lock = Path(os.environ.get('TMPDIR', str(self.state_dir))) / f'kuberweaver-execution-{os.getuid()}.lock'
        with open(lock, 'a') as stream:
            os.chmod(lock, 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def _plugin(self, plugin_id):
        _identifier(plugin_id, 'plugin')
        directory = (self.plugins_dir / plugin_id).resolve()
        if directory.parent != self.plugins_dir:
            raise ValueError('Unsafe plugin path')
        path = directory / 'manifest.yaml'
        if not path.is_file():
            raise ValueError(f'Unknown plugin: {plugin_id}')
        if path.is_symlink() or not path.resolve().is_relative_to(directory):
            raise ValueError('Unsafe manifest path')
        manifest = yaml.safe_load(path.read_text())
        if '$ref' in json.dumps(manifest.get('schema', {})) or '$dynamicRef' in json.dumps(manifest.get('schema', {})):
            raise ValueError('Schema references are unsupported; embed the schema')
        if manifest.get('id') != plugin_id:
            raise ValueError('Manifest plugin identity mismatch')
        if manifest.get('capability') != 'live' or manifest.get('engine') != 'ansible':
            raise ValueError(f'Plugin {plugin_id} is planned or has an unsupported engine')
        if manifest.get('executor') == 'ansible-workload-v1':
            raise ValueError('Workload bricks run through the workload runner, not the container executor')
        if manifest.get('executor') != 'ansible-container-v1':
            raise ValueError('Unsupported executor protocol')
        for action in ('snapshot', 'apply', 'verify', 'restore'):
            play = (directory / manifest['iac'][action]).resolve()
            if not play.is_relative_to(directory) or not play.is_file():
                raise ValueError('Playbook must be a file inside its plugin directory')
        return manifest

    def _prepare(self, job):
        _identifier(job.get('id'), 'job id')
        graph = job.get('graph', {})
        nodes = graph.get('nodes', [])
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 20:
            raise ValueError('Graph must have 1..20 nodes')
        prepared, node_ids, claims = [], set(), set()
        graph_name = graph.get('name', 'default')
        environment = graph.get('environment', 'lab')
        for label in (graph_name, environment):
            if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.:-]{0,79}', label):
                raise ValueError('Invalid graph name/environment')
        for node in nodes:
            node_id = _identifier(node.get('id'), 'node id')
            if node_id in node_ids:
                raise ValueError('Duplicate node id')
            node_ids.add(node_id)
            manifest = self._plugin(node.get('plugin_id'))
            config = copy.deepcopy(manifest['defaults'])
            config.update(node.get('config', {}))
            # JSON Schema numeric bounds alone do not reject IEEE NaN.
            json.dumps(config, allow_nan=False)
            try:
                jsonschema.validate(config, manifest['schema'])
            except jsonschema.ValidationError as exc:
                raise ValueError(f'Invalid config for {node_id}: {exc.message}') from exc
            owner = hashlib.sha256(json.dumps([graph_name, environment, node_id]).encode()).hexdigest()[:24]
            for target in config['targets']:
                claim = (target, config['port'])
                if claim in claims:
                    raise ValueError('Two nodes claim the same target/port')
                claims.add(claim)
            prepared.append({'node_id': node_id, 'plugin_id': manifest['id'],
                             'owner': owner, 'config': config, 'manifest': manifest})
        # Kahn topological sort, deterministic in original graph order.
        dependencies = {node: set() for node in node_ids}
        edge_ids = set()
        for edge in graph.get('edges', []):
            edge_id = _identifier(edge.get('id'), 'edge id')
            if edge_id in edge_ids:
                raise ValueError('Duplicate edge id')
            edge_ids.add(edge_id)
            source, target = edge.get('source'), edge.get('target')
            if source not in node_ids or target not in node_ids or source == target:
                raise ValueError('Invalid dependency edge')
            dependencies[target].add(source)
        ordered, done = [], set()
        while len(ordered) < len(prepared):
            ready = [n for n in prepared if n['node_id'] not in done and dependencies[n['node_id']] <= done]
            if not ready:
                raise ValueError('Dependency cycle')
            for node in ready:
                ordered.append(node)
                done.add(node['node_id'])
        strategy = graph.get('strategy', {})
        if strategy.get('canary', 1) != 1 or type(strategy.get('batch_size', 1)) is not int or not 1 <= strategy.get('batch_size', 1) <= 2:
            raise ValueError('Exactly one canary and batch_size 1..2 are supported')
        failure = graph.get('failure_mode', 'none')
        if failure not in ('none', 'canary', 'rolling'):
            raise ValueError('Invalid failure_mode')
        return ordered, failure, strategy.get('batch_size', 1)

    def _ownership(self):
        path = self.state_dir / 'ownership.json'
        return json.loads(path.read_text()) if path.exists() else {}

    @staticmethod
    def _keys(entry):
        return [f"{entry['target']}:node:{entry['node_id']}",
                f"{entry['target']}:port:{entry['config']['port']}"]

    def _operation(self, job_id, entry, action, emit, inject=False):
        folder = self.state_dir / 'jobs' / job_id
        require_ssh_config(SSH_CONFIG)
        token = f"{entry['node_id']}-{entry['target']}-{action}"
        result_path = folder / f'{token}.result.json'
        result_path.unlink(missing_ok=True)
        request = {key: copy.deepcopy(entry[key]) for key in ('node_id', 'target', 'plugin_id', 'owner', 'config')}
        request.update({'action': action, 'job_id': job_id, 'management_ip': TARGETS[entry['target']]['ip'],
                        'service': entry['manifest']['service'], 'inject_failure': inject})
        if action == 'restore':
            request['snapshot'] = json.loads(Path(entry['snapshot_path']).read_text())
        params = {'kw_request': request, 'kw_result_path': str(result_path),
                  'kw_request_json': json.dumps(request), 'kw_token': token + '-' + job_id,
                  'kw_helper_path': str(self.plugins_dir / '_shared' / 'guest.py')}
        params_path = folder / f'{token}.vars.json'
        _private_json(params_path, params)
        inventory = folder / 'inventory.json'
        alias = TARGETS[entry['target']]['alias']
        _private_json(inventory, {'all': {'hosts': {alias: {'ansible_connection': 'ssh', 'ansible_host': alias,
            'ansible_python_interpreter': '/usr/bin/python3',
            'ansible_ssh_common_args': f'-F {SSH_CONFIG} -o BatchMode=yes -o ConnectTimeout=15'}}}})
        executable = Path(sys.executable).parent / 'ansible-playbook'
        playbook = self.plugins_dir / entry['plugin_id'] / entry['manifest']['iac'][action]
        argv = [str(executable), '-i', str(inventory), str(playbook), '--extra-vars', '@' + str(params_path)]
        emit(action, 'info', f"{entry['node_id']} on {entry['target']}: {action}")
        try:
            with operation_credentials(self.state_dir, entry['target'], TARGETS,
                                       self.secret_resolver, result_path=result_path,
                                       ssh_config=SSH_CONFIG) as credential_env:
                operation_argv = list(argv)
                if credential_env:
                    # Extra vars outrank the persisted inventory; only ephemeral
                    # config paths, never key bytes, cross the command boundary.
                    operation_argv[-2:-2] = ['-e', json.dumps({
                        'ansible_ssh_common_args': credential_env['ANSIBLE_SSH_COMMON_ARGS']})]
                # Do not forward provider tokens, agent sockets, logging/callback
                # settings or arbitrary ANSIBLE_* overrides to credential consumers.
                inherited = ({key: value for key, value in os.environ.items()
                              if key in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR', 'VIRTUAL_ENV'}}
                             if credential_env else dict(os.environ))
                proc = self.runner(operation_argv, capture_output=True, text=True, timeout=300,
                                   env={**inherited, 'ANSIBLE_NOCOLOR': '1', 'ANSIBLE_HOST_KEY_CHECKING': 'True',
                                        'ANSIBLE_LOCAL_TEMP': str(folder / 'ansible-tmp'), **credential_env})
                _private_json(folder / 'logs' / f'{token}.json',
                              {'returncode': proc.returncode,
                               'stdout': '[suppressed]' if credential_env else proc.stdout,
                               'stderr': '[suppressed]' if credential_env else proc.stderr})
            result = json.loads(result_path.read_text()) if result_path.exists() else {}
            if result_path.exists():
                result_path.chmod(0o600)
            if proc.returncode or result.get('error'):
                # Never expose raw Ansible output, which can include secrets from guest configuration.
                raise RuntimeError(f"{action} failed on {entry['target']}: {result.get('error', 'Ansible failed; inspect private job logs')}")
            if not result:
                raise RuntimeError('Ansible returned no readback evidence')
            return result
        finally:
            params_path.unlink(missing_ok=True)

    def execute(self, job: dict, emit: Callable[[str, str, str], None]) -> dict:
        with self._locked():
            ordered, failure, batch_size = self._prepare(job)
            job_id = job['id']
            folder = self.state_dir / 'jobs' / job_id
            journal_path = folder / 'journal.json'
            if journal_path.exists():
                raise ValueError('Job id already executed; use a fresh id')
            ownership = self._ownership()
            entries = []
            for node in ordered:
                for target in node['config']['targets']:
                    entry = {**copy.deepcopy(node), 'target': target}
                    entry['prior_claims'] = {key: ownership.get(key) for key in self._keys(entry)}
                    for claim in entry['prior_claims'].values():
                        if claim and claim['owner'] != entry['owner']:
                            raise ValueError('Resource ownership conflict; target/node or target/port is already reserved')
                    entries.append(entry)
            journal = {'job_id': job_id, 'entries': entries, 'modified': [], 'status': 'preparing'}
            evidence, snapshots = [], []
            # Snapshot every target before changing any workload.
            for entry in entries:
                snapshot = self._operation(job_id, entry, 'snapshot', emit)
                path = folder / 'snapshots' / f"{entry['node_id']}-{entry['target']}.json"
                _private_json(path, snapshot)
                entry['snapshot_path'] = str(path)
                snapshots.append({'node_id': entry['node_id'], 'target': entry['target'], 'path': str(path), 'existed': snapshot.get('exists', False)})
            journal['status'] = 'deploying'
            _private_json(journal_path, journal)
            try:
                for node in ordered:
                    node_entries = [e for e in entries if e['node_id'] == node['node_id']]
                    # One real canary host, then rolling batches. Batches are conservative/sequential.
                    groups = [('canary', node_entries[:1])]
                    groups += [('rolling', node_entries[i:i + batch_size]) for i in range(1, len(node_entries), batch_size)]
                    for phase, group in groups:
                        emit(phase, 'info', f"{node['node_id']}: {phase} batch of {len(group)} target(s)")
                        for entry in group:
                            journal['modified'].append(entries.index(entry))  # include partially failed apply
                            for key in self._keys(entry):
                                ownership[key] = {'owner': entry['owner'], 'job_id': job_id}
                            _private_json(self.state_dir / 'ownership.json', ownership)
                            _private_json(journal_path, journal)
                            self._operation(job_id, entry, 'apply', emit)
                        for entry in group:
                            proof = self._operation(job_id, entry, 'verify', emit, inject=failure == phase)
                            evidence.append({'phase': phase, 'node_id': entry['node_id'], 'target': entry['target'], **proof})
                journal['status'] = 'succeeded'
                _private_json(journal_path, journal)
                return {'status': 'succeeded', 'evidence': evidence, 'snapshots': snapshots,
                        'endpoints': self._endpoints(entries)}
            except Exception as exc:
                emit('failure', 'error', str(exc))
                rollback = self._restore(journal, emit)
                return {'status': 'failed' if rollback['status'] == 'rolled_back' else 'rollback_failed',
                        'error': str(exc), 'rollback': rollback, 'evidence': evidence,
                        'snapshots': snapshots, 'endpoints': self._endpoints(entries)}

    @staticmethod
    def _endpoints(entries):
        return [{'node_id': e['node_id'], 'target': e['target'],
                 'url': f"http://{TARGETS[e['target']]['ip']}:{e['config']['port']}",
                 'management_only': True} for e in entries]

    def _restore(self, journal, emit):
        ownership = self._ownership()
        results = journal.setdefault('restoration_evidence', [])
        restored = journal.setdefault('restored', [])
        errors = []
        for index in reversed(journal['modified']):
            if index in restored:
                continue
            entry = journal['entries'][index]
            try:
                result = self._operation(journal['job_id'], entry, 'restore', emit)
                if not result.get('restored'):
                    raise RuntimeError('Restore did not return verified restoration evidence')
                results.append({'node_id': entry['node_id'], 'target': entry['target'], **result})
                restored.append(index)
                for key, old in entry['prior_claims'].items():
                    if old:
                        ownership[key] = old
                    else:
                        ownership.pop(key, None)
            except Exception as exc:
                errors.append({'node_id': entry['node_id'], 'target': entry['target'], 'error': str(exc)})
                emit('restore', 'error', str(exc))
            _private_json(self.state_dir / 'ownership.json', ownership)
            _private_json(self.state_dir / 'jobs' / journal['job_id'] / 'journal.json', journal)
        status = 'rollback_failed' if errors else 'rolled_back'
        journal['status'] = status
        _private_json(self.state_dir / 'jobs' / journal['job_id'] / 'journal.json', journal)
        return {'status': status, 'evidence': results, 'errors': errors}

    def rollback(self, job: dict, emit: Callable[[str, str, str], None]) -> dict:
        with self._locked():
            job_id = _identifier(job.get('rollback_of', job.get('id')), 'job id')
            journal_path = self.state_dir / 'jobs' / job_id / 'journal.json'
            if not journal_path.exists():
                raise ValueError('No persisted snapshots for this job')
            journal = json.loads(journal_path.read_text())
            if journal['status'] not in ('succeeded', 'rollback_failed'):
                raise ValueError('Only a successful job or failed rollback can be rolled back')
            ownership = self._ownership()
            for index in journal['modified']:
                if index in journal.get('restored', []):
                    continue
                for key in self._keys(journal['entries'][index]):
                    claim = ownership.get(key)
                    if claim and claim['job_id'] != job_id:
                        raise ValueError('Stale rollback would overwrite a newer deployment')
            result = self._restore(journal, emit)
            result['snapshots'] = [{'path': e['snapshot_path'], 'target': e['target'], 'node_id': e['node_id']} for e in journal['entries']]
            result['endpoints'] = self._endpoints(journal['entries'])
            return result
