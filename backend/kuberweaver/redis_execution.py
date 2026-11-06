"""Reviewed Redis protocol, sharing only the executor's orchestration and journal.

The optional trusted redis-runtime package registers this class. The default
Ansible provider deliberately rejects redis-container-v1; no HTTP emulation.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import sys
from pathlib import Path

import jsonschema
import yaml

from .execution import Executor, SSH_CONFIG, TARGETS, _identifier, _private_json
from .execution_credentials import operation_credentials


class RedisExecutor(Executor):
    # Imported executable helper source, in addition to the class MRO pinned by
    # the host. Guest/playbook assets are independently pinned by the catalogue.
    source_dependencies = (Path(inspect.getfile(inspect.unwrap(operation_credentials))).resolve(),)

    def __init__(self, state_dir, plugins_dir=None):
        super().__init__(state_dir, plugins_dir)
        self._loaded_sources = self._sources()

    def _sources(self):
        paths = {Path(inspect.getfile(cls)).resolve()
                 for cls in type(self).__mro__ if cls is not object}
        paths.add(Path(inspect.getfile(inspect.unwrap(operation_credentials))).resolve())
        root = self.plugins_dir / 'redis'
        paths.update(p for p in root.rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'))
        records = []
        for path in sorted(paths):
            if path.is_symlink():
                raise ValueError('Redis source symlinks are forbidden')
            records.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
        return records

    def _assert_sources(self):
        if self._sources() != self._loaded_sources:
            raise ValueError('Redis source changed since runtime registration; reload and approve again')

    def _plugin(self, plugin_id):
        if plugin_id != 'redis':
            raise ValueError('Redis execution accepts only reviewed redis graphs')
        directory = self.plugins_dir / 'redis'
        path = directory / 'manifest.yaml'
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe Redis manifest path')
        manifest = yaml.safe_load(path.read_text())
        if (manifest.get('id') != 'redis' or manifest.get('capability') != 'live'
                or manifest.get('engine') != 'ansible'
                or manifest.get('executor') != 'redis-container-v1'
                or manifest.get('service') != {
                    'image': 'redis:7.4.2-alpine', 'container_port': 6379,
                    'protocol': 'resp2', 'persistence': 'appendonly-always',
                    'snapshot_limit_bytes': 16777216}):
            raise ValueError('Unsupported reviewed Redis service protocol')
        if '$ref' in json.dumps(manifest['schema']) or '$dynamicRef' in json.dumps(manifest['schema']):
            raise ValueError('Redis schema references are unsupported')
        jsonschema.Draft202012Validator.check_schema(manifest['schema'])
        return manifest

    def _prepare(self, job):
        self._assert_sources()
        # Check the complete graph before the first operation, including mixed graphs.
        if any(node.get('plugin_id') != 'redis' for node in job.get('graph', {}).get('nodes', [])):
            raise ValueError('Redis execution accepts only reviewed redis graphs')
        return super()._prepare(job)

    def _operation(self, job_id, entry, action, emit, inject=False):
        self._assert_sources()
        _identifier(job_id, 'job id')
        _identifier(entry['node_id'], 'node id')
        if entry['target'] not in TARGETS or action not in ('snapshot', 'apply', 'verify', 'restore'):
            raise ValueError('Unsupported Redis operation or target')
        manifest = self._plugin(entry['plugin_id'])
        jsonschema.validate(entry['config'], manifest['schema'])
        folder = self.state_dir / 'jobs' / job_id
        token = f"{entry['node_id']}-{entry['target']}-{action}"
        result_path = folder / f'{token}.result.json'
        result_path.unlink(missing_ok=True)
        request = {k: copy.deepcopy(entry[k]) for k in ('node_id', 'target', 'plugin_id', 'owner', 'config')}
        request.update(action=action, job_id=job_id, management_ip=TARGETS[entry['target']]['ip'],
                       service=manifest['service'], inject_failure=inject)
        if action == 'restore':
            snapshot_path = Path(entry['snapshot_path']).resolve()
            if not snapshot_path.is_relative_to(folder.resolve() / 'snapshots'):
                raise ValueError('Unsafe Redis snapshot path')
            request['snapshot'] = json.loads(snapshot_path.read_text())
        helper = self.plugins_dir / 'redis' / 'guest.py'
        playbook = self.plugins_dir / 'redis' / 'iac' / 'lifecycle.yml'
        for path in (helper, playbook):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.plugins_dir / 'redis'):
                raise ValueError('Unsafe Redis lifecycle source')
        params = {'kw_request': request, 'kw_result_path': str(result_path),
                  'kw_request_json': json.dumps(request), 'kw_token': token + '-' + job_id,
                  'kw_helper_path': str(helper)}
        params_path = folder / f'{token}.vars.json'
        _private_json(params_path, params)
        inventory = folder / 'inventory.json'
        alias = TARGETS[entry['target']]['alias']
        _private_json(inventory, {'all': {'hosts': {alias: {'ansible_connection': 'ssh',
            'ansible_host': alias, 'ansible_python_interpreter': '/usr/bin/python3',
            'ansible_ssh_common_args': f'-F {SSH_CONFIG} -o BatchMode=yes -o ConnectTimeout=15'}}}})
        argv = [str(Path(sys.executable).parent / 'ansible-playbook'), '-i', str(inventory),
                str(playbook), '--extra-vars', '@' + str(params_path)]
        emit(action, 'info', f"{entry['node_id']} on {entry['target']}: Redis {action}")
        try:
            with operation_credentials(self.state_dir, entry['target'], TARGETS,
                                       getattr(self, 'secret_resolver', None),
                                       result_path=result_path, ssh_config=SSH_CONFIG) as credentials:
                operation_argv = list(argv)
                if credentials:
                    # Extra vars outrank legacy host variables in inventory.
                    operation_argv[-2:-2] = ['-e', json.dumps({
                        'ansible_ssh_common_args': credentials['ANSIBLE_SSH_COMMON_ARGS']})]
                inherited = ({key: value for key, value in os.environ.items()
                              if key in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR', 'VIRTUAL_ENV'}}
                             if credentials else dict(os.environ))
                # Keep the parent's job command-wrapper intact; do not replace runner.
                self._assert_sources()
                proc = self.runner(operation_argv, capture_output=True, text=True, timeout=300,
                    env={**inherited, 'ANSIBLE_NOCOLOR': '1', 'ANSIBLE_HOST_KEY_CHECKING': 'True',
                         'ANSIBLE_LOCAL_TEMP': str(folder / 'ansible-tmp'), **credentials})
            _private_json(folder / 'logs' / f'{token}.json', {'returncode': proc.returncode})
            result = json.loads(result_path.read_text()) if result_path.exists() else {}
            if result_path.exists():
                result_path.chmod(0o600)
            if proc.returncode or result.get('error'):
                raise RuntimeError(f"Redis {action} failed on {entry['target']}; inspect private result")
            evidence_ok = {
                'snapshot': type(result.get('exists')) is bool,
                'apply': result.get('applied') is True,
                'verify': result.get('healthy') is True and result.get('protocol') == 'resp2'
                          and result.get('ping') == 'PONG' and result.get('set_get') is True,
                'restore': result.get('restored') is True,
            }
            if not evidence_ok[action]:
                raise RuntimeError('Redis operation returned no verified readback evidence')
            return result
        finally:
            params_path.unlink(missing_ok=True)

    def rollback(self, job, emit):
        self._assert_sources()
        if 'graph' in job:
            self._prepare(job)
        job_id = _identifier(job.get('rollback_of', job.get('id')), 'job id')
        journal_path = self.state_dir / 'jobs' / job_id / 'journal.json'
        if journal_path.exists():
            journal = json.loads(journal_path.read_text())
            for entry in journal['entries']:
                manifest = self._plugin(entry['plugin_id'])
                jsonschema.validate(entry['config'], manifest['schema'])
                if entry['target'] not in TARGETS:
                    raise ValueError('Unsupported Redis rollback target')
        return super().rollback(job, emit)

    @staticmethod
    def _endpoints(entries):
        from .execution import TARGETS
        return [{'node_id': e['node_id'], 'target': e['target'],
                 'url': f"redis://{TARGETS[e['target']]['ip']}:{e['config']['port']}",
                 'management_only': True, 'isolated_lab_only': True} for e in entries]
