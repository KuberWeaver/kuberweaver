"""Workload bricks: apply, verify, snapshot, roll back and remove on a guest.

A brick wraps a pinned upstream Ansible role. This adapter does not carry shell,
does not accept free-form variables and never sees a secret: the caller supplies
the path to a private key file, and every value that reaches the guest comes from
a schema-validated configuration.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import yaml


class WorkloadError(Exception):
    """A brick contract or execution invariant was violated."""


DIGEST_FIELDS = ('brick', 'brick_version', 'role', 'role_version', 'config', 'target_uuid')


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _changed_count(output) -> int:
    match = re.search(r'changed=(\d+)', output or '')
    return int(match.group(1)) if match else 0


def _private_write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)


class WorkloadExecutor:
    """Executes workload bricks against a resolved guest."""

    executor_id = 'ansible-workload-v1'

    def __init__(self, state_dir, plugins_dir=None, *, playbook=None, key_path=None,
                 user='ubuntu', runner=None):
        self.state_dir = Path(state_dir).absolute()
        self.plugins_dir = Path(plugins_dir or Path(__file__).resolve().parents[2] / 'plugins').absolute()
        self.playbook = playbook
        self.key_path = Path(key_path).absolute() if key_path else None
        self.user = user
        # The portal injects a guarded runner so every command boundary re-checks
        # the durable approval; a standalone driver keeps subprocess.run.
        self.runner = runner or subprocess.run

    # ------------------------------------------------------------------ brick

    def brick(self, brick_id):
        """Load and validate a brick manifest against the contract."""
        directory = self.plugins_dir / brick_id
        manifest_path = directory / 'manifest.yaml'
        if not manifest_path.is_file() or not directory.is_dir():
            raise WorkloadError(f'Unknown brick: {brick_id}')
        if manifest_path.is_symlink():
            raise WorkloadError('Unsafe brick manifest path')
        manifest = yaml.safe_load(manifest_path.read_text())
        if not isinstance(manifest, dict) or manifest.get('id') != brick_id:
            raise WorkloadError('Brick identity mismatch')
        if manifest.get('kind') != 'workload':
            raise WorkloadError(f'Brick {brick_id} is not a workload')
        if manifest.get('executor') != self.executor_id:
            raise WorkloadError(f'Brick {brick_id} declares an unsupported executor')
        implementation = manifest.get('implementation') or {}
        if not implementation.get('role') or not implementation.get('version'):
            raise WorkloadError(f'Brick {brick_id} does not pin its upstream role')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', str(implementation['version'])):
            raise WorkloadError('Brick role version is not a plain version string')
        lifecycle = manifest.get('lifecycle') or {}
        for action in ('snapshot', 'apply', 'verify', 'rollback'):
            play = lifecycle.get(action)
            if not play:
                raise WorkloadError(f'Brick {brick_id} does not declare {action}')
            resolved = (directory / play).resolve()
            if not resolved.is_relative_to(directory) or not resolved.is_file():
                raise WorkloadError(f'Brick {brick_id} {action} playbook escapes its directory')
        assertions = (manifest.get('verify') or {}).get('assertions') or []
        if not assertions or any(not isinstance(a, dict) or not a.get('id') for a in assertions):
            raise WorkloadError(f'Brick {brick_id} declares no verifiable assertion')
        if not manifest.get('data_class'):
            raise WorkloadError(f'Brick {brick_id} does not declare its data class')
        return {**manifest, '_directory': directory}

    def assert_role_installed(self, brick):
        """Refuse to run when the installed role drifts from the pinned version."""
        lock_path = brick['_directory'] / 'roles.lock.json'
        if not lock_path.is_file():
            raise WorkloadError('Upstream role is not installed; run ansible-galaxy first')
        lock = json.loads(lock_path.read_text())
        expected = brick['implementation']
        installed = lock.get(expected['role'])
        if not isinstance(installed, dict) or installed.get('version') != str(expected['version']):
            raise WorkloadError('Installed role does not match the pinned version')
        return installed

    # ------------------------------------------------------------------ plan

    def prepare(self, brick_id, config, target, job_id):
        """Validate a brick configuration against a resolved target."""
        brick = self.brick(brick_id)
        self.assert_role_installed(brick)
        merged = copy.deepcopy(brick.get('defaults') or {})
        merged.update(copy.deepcopy(config or {}))
        json.dumps(merged, allow_nan=False)
        try:
            jsonschema.validate(merged, brick['schema'])
        except jsonschema.ValidationError as exc:
            raise WorkloadError(f'Invalid brick configuration: {exc.message}') from exc
        if not isinstance(target, dict):
            raise WorkloadError('Target must be a resolved guest')
        uuid = target.get('uuid')
        address = target.get('address')
        if not isinstance(uuid, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', uuid):
            raise WorkloadError('Target identity is not a VM UUID')
        try:
            ipaddress.ip_address(str(address))
        except (TypeError, ValueError):
            raise WorkloadError('Target address is not an IP address') from None
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', job_id or ''):
            raise WorkloadError('Invalid job identity')
        plan = {
            'job_id': job_id,
            'brick': brick['id'],
            'brick_version': str(brick['version']),
            'role': brick['implementation']['role'],
            'role_version': str(brick['implementation']['version']),
            'role_license': brick['implementation'].get('license'),
            'data_class': brick['data_class'],
            'config': merged,
            'target_uuid': uuid,
            'address': address,
            'assertions': [a['id'] for a in brick['verify']['assertions']],
        }
        plan['plan_digest'] = _digest({key: plan[key] for key in DIGEST_FIELDS})
        return plan

    # --------------------------------------------------------------- execute

    def _inventory(self, folder, plan):
        path = folder / 'inventory.json'
        _private_write(path, {'all': {'hosts': {'target': {
            'ansible_host': plan['address'],
            'ansible_user': self.user,
            'ansible_connection': 'ssh',
            'ansible_python_interpreter': '/usr/bin/python3',
            'ansible_ssh_private_key_file': str(self.key_path) if self.key_path else None,
            # Long state paths overflow ssh ControlPath, so multiplexing is off.
            'ansible_ssh_common_args': '-o IdentitiesOnly=yes -o BatchMode=yes -o ControlMaster=no '
                                       '-o ControlPath=none -o StrictHostKeyChecking=accept-new '
                                       '-o ConnectTimeout=15',
        }}}})
        return path

    def _run(self, brick, action, folder, plan, extra=None):
        if self.playbook is None or not Path(self.playbook).is_file():
            raise WorkloadError('ansible-playbook is unavailable')
        if self.key_path is None or not self.key_path.is_file():
            raise WorkloadError('A private key file is required')
        playbook = brick['_directory'] / brick['lifecycle'][action]
        result_path = folder / f'{action}.result.json'
        result_path.unlink(missing_ok=True)
        variables = {'kw_config': plan['config'], 'kw_result_path': str(result_path)}
        variables.update(extra or {})
        vars_path = folder / f'{action}.vars.json'
        _private_write(vars_path, variables)
        inventory = self._inventory(folder, plan)
        argv = [str(self.playbook), '-i', str(inventory), str(playbook),
                '-e', '@' + str(vars_path), '--diff']
        (folder / 'home').mkdir(exist_ok=True)
        completed = self.runner(argv, capture_output=True, text=True, timeout=900,
                                cwd=str(folder), env={'PATH': '/usr/bin:/bin:/usr/local/bin',
                                                      'HOME': str(folder / 'home'),
                                                      # Bricks keep their pinned roles beside the
                                                      # playbooks, not in a global path.
                                                      'ANSIBLE_ROLES_PATH': str(brick['_directory'] / 'roles'),
                                                      'ANSIBLE_HOST_KEY_CHECKING': 'False',
                                                      'ANSIBLE_NOCOLOR': '1',
                                                      'LANG': 'C.UTF-8'})
        report = None
        if result_path.is_file():
            try:
                report = json.loads(result_path.read_text())
            except ValueError:
                report = None
        return {'action': action, 'returncode': completed.returncode,
                'report': report,
                'stdout_tail': completed.stdout[-1500:],
                'stderr_tail': completed.stderr[-1500:]}

    def execute(self, brick_id, job_id, config, target, *, emit=None):
        """Apply, verify, and roll back on failure. Returns evidence."""
        emit = emit or (lambda *a: None)
        plan = self.prepare(brick_id, config, target, job_id)
        brick = self.brick(brick_id)
        folder = self.state_dir / 'workloads' / job_id
        if (folder / 'evidence.json').exists():
            raise WorkloadError('Job already executed; use a fresh id')
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        _private_write(folder / 'plan.json', plan)

        snapshot = self._run(brick, 'snapshot', folder, plan)
        if snapshot['returncode'] != 0 or snapshot['report'] is None:
            raise WorkloadError('Snapshot failed; nothing was changed')
        _private_write(folder / 'snapshot.json', snapshot['report'])

        emit('apply', 'info', f"{plan['brick']} on {plan['address']}")
        applied = self._run(brick, 'apply', folder, plan)
        evidence = {'status': 'failed', 'plan_digest': plan['plan_digest'],
                    'brick': plan['brick'], 'brick_version': plan['brick_version'],
                    'role': plan['role'], 'role_version': plan['role_version'],
                    'data_class': plan['data_class'], 'target_uuid': plan['target_uuid'],
                    'address': plan['address'], 'assertions': {},
                    'changed': _changed_count(applied.get('stdout_tail'))}
        if applied['returncode'] != 0:
            rolled = self._run(brick, 'rollback', folder, plan, {'kw_snapshot': snapshot['report']})
            evidence['status'] = 'rollback_failed' if rolled['returncode'] != 0 else 'failed'
            evidence['rollback'] = {'returncode': rolled['returncode'], 'report': rolled['report']}
            # The failing task usually explains itself on stdout, not stderr.
            evidence['apply_stdout'] = applied.get('stdout_tail', '')
            evidence['apply_stderr'] = applied.get('stderr_tail', '')
            _private_write(folder / 'evidence.json', evidence)
            raise WorkloadError('Brick apply failed; ' +
                                ('rollback did not succeed' if rolled['returncode'] else 'rollback completed'))

        verified = self._run(brick, 'verify', folder, plan)
        if verified['returncode'] != 0 or verified['report'] is None:
            rolled = self._run(brick, 'rollback', folder, plan, {'kw_snapshot': snapshot['report']})
            evidence['status'] = 'rollback_failed' if rolled['returncode'] != 0 else 'failed'
            evidence['rollback'] = {'returncode': rolled['returncode'], 'report': rolled['report']}
            evidence['verify_stderr'] = verified.get('stderr_tail', '')
            _private_write(folder / 'evidence.json', evidence)
            raise WorkloadError('Brick verification failed; ' +
                                ('rollback did not succeed' if rolled['returncode'] else 'rollback completed'))

        declared = plan['assertions']
        observed = verified['report'].get('assertions') or {}
        if set(observed) != set(declared) or not all(observed[key] is True for key in declared):
            raise WorkloadError('Verification report does not honour the declared assertions')
        evidence['assertions'] = observed
        evidence['status'] = 'succeeded'
        _private_write(folder / 'evidence.json', evidence)
        return evidence

    def remove(self, brick_id, job_id, config, target, *, emit=None):
        """Remove what the brick created, leaving shared resources in place."""
        emit = emit or (lambda *a: None)
        plan = self.prepare(brick_id, config, target, job_id)
        brick = self.brick(brick_id)
        if not (brick.get('lifecycle') or {}).get('remove'):
            raise WorkloadError('Brick declares no removal playbook')
        folder = self.state_dir / 'workloads' / job_id
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        _private_write(folder / 'plan.json', plan)
        emit('remove', 'info', f"{plan['brick']} on {plan['address']}")
        removed = self._run(brick, 'remove', folder, plan)
        evidence = {'status': 'removed' if removed['returncode'] == 0 else 'failed',
                    'plan_digest': plan['plan_digest'], 'brick': plan['brick'],
                    'target_uuid': plan['target_uuid'], 'address': plan['address'],
                    'report': removed['report']}
        _private_write(folder / 'removal.json', evidence)
        if removed['returncode'] != 0:
            raise WorkloadError('Brick removal failed')
        return evidence

    # ------------------------------------------------------------ role setup

    def lock_role(self, brick_id, galaxy=None):
        """Install the pinned role once and record the resolved version."""
        brick = self.brick(brick_id)
        galaxy = galaxy or 'ansible-galaxy'
        roles_dir = brick['_directory'] / 'roles'
        roles_dir.mkdir(exist_ok=True)
        # ansible-galaxy has no --version flag for roles; the pin travels in a
        # requirements file, which is also what an operator can audit.
        requirements = brick['_directory'] / 'requirements.yml'
        completed = subprocess.run(
            [galaxy, 'role', 'install', '-r', str(requirements), '-p', str(roles_dir)],
            capture_output=True, text=True, timeout=600)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip()[-400:]
            raise WorkloadError('Upstream role installation failed: ' + detail)
        installed = roles_dir / brick['implementation']['role']
        if not installed.is_dir():
            raise WorkloadError('Upstream role is missing after installation')
        checksum = hashlib.sha256()
        for path in sorted(p for p in installed.rglob('*') if p.is_file()):
            checksum.update(str(path.relative_to(installed)).encode())
            checksum.update(path.read_bytes())
        lock_path = brick['_directory'] / 'roles.lock.json'
        _private_write(lock_path, {brick['implementation']['role']: {
            'version': str(brick['implementation']['version']),
            'sha256': checksum.hexdigest(),
            'license': brick['implementation'].get('license'),
        }})
        return json.loads(lock_path.read_text())


def public_plan(signed):
    """The bounded plan an operator reviews; private evidence never leaves the process."""
    reviewed, plan = signed['reviewed'], signed['reviewed']['plan']
    return {
        'plan_digest': _digest(signed),
        'actions': {reviewed['brick']: ['snapshot', 'apply', 'verify']},
        'summary': {'brick': reviewed['brick'], 'brick_version': plan['brick_version'],
                    'role': plan['role'], 'role_version': plan['role_version'],
                    'target_uuid': signed['target']['uuid'], 'address': signed['target']['address'],
                    'data_class': plan['data_class'], 'assertions': len(plan['assertions']),
                    'reversible': True},
        'bindings': {'brick': reviewed['brick'], 'brick_version': plan['brick_version'],
                     'role': plan['role'], 'role_version': plan['role_version'],
                     'target_job': signed['target']['job_id'], 'target_uuid': signed['target']['uuid'],
                     'address': signed['target']['address'], 'ssh_key_id': signed['key']['id'],
                     'ssh_key_fingerprint': signed['key']['fingerprint'],
                     'brick_digest': plan['plan_digest'],
                     'evidence_digest': signed['target']['evidence_digest']},
    }


class WorkloadProvider:
    """Saved-plan execution provider for workload bricks on one recorded guest.

    Preparation resolves the target from the deployment evidence of a succeeded
    provisioning job and validates the brick contract; approval pins that exact
    plan; execution runs snapshot, apply and verify with the brick's assertions.
    """

    executor_id = 'ansible-workload-v1'
    requires_saved_plan = True
    supports_rollback = False
    ssh_keys: Any = None

    def __init__(self, state_dir, plugins_dir=None, *, store=None, inspector=None,
                 playbook=None, user='ubuntu'):
        self.state_dir = Path(state_dir).absolute()
        self.plugins_dir = Path(plugins_dir or Path(__file__).resolve().parents[2] / 'plugins').absolute()
        self.store = store
        self.inspector = inspector
        self.playbook = Path(playbook) if playbook else None
        self.user = user
        # A real callable here lets the kernel wrap every command boundary with
        # its durable approval guard before the brick runs anything.
        self.runner = subprocess.run

    def _playbook(self):
        """Resolve the Ansible executable at use time, so a bad path is one refusal."""
        if self.playbook is None:
            from .configuration import ansible_playbook
            try:
                self.playbook = Path(ansible_playbook())
            except ValueError as error:
                raise WorkloadError('Ansible executable is not configured: ' + str(error)[:200]) from None
        if not self.playbook.is_file():
            raise WorkloadError('Ansible executable is unavailable')
        return self.playbook

    def read(self):
        try:
            present = self._playbook().is_file()
        except WorkloadError:
            present = False
        return {'provider': self.executor_id, 'requires_saved_plan': True,
                'supports_rollback': False, 'binary_present': present,
                'status': 'configured' if present else 'unavailable',
                'limitations': ['one workload brick per job',
                                'no manual rollback; a failed brick rolls back its own snapshot',
                                'the target address is the evidence a succeeded provisioning job recorded']}

    def _executor(self, key_path=None, runner=None):
        return WorkloadExecutor(self.state_dir, self.plugins_dir, playbook=self._playbook(),
                                key_path=key_path, user=self.user, runner=runner or self.runner)

    def _recorded_target(self, uuid, environment):
        """The single guest address a succeeded, approved provisioning job proved."""
        if self.store is None:
            raise WorkloadError('Recorded deployment evidence is unavailable')
        matches = []
        for job in self.store.list('jobs'):
            if (job.get('status') != 'succeeded' or job.get('operation') != 'execute'
                    or job.get('environment') != environment or not job.get('approved_by')):
                continue
            result = job.get('result') or {}
            for row in result.get('evidence') or []:
                if not (isinstance(row, dict) and isinstance(row.get('uuid'), str)
                        and row['uuid'].lower() == uuid):
                    continue
                address = row.get('guest_ip')
                if not isinstance(address, str):
                    continue
                try:
                    ipaddress.ip_address(address)
                except ValueError:
                    continue
                if row.get('power_state') != 'poweredOn' or not isinstance(row.get('name'), str):
                    continue
                matches.append((job, row, address))
        if not matches:
            raise WorkloadError('Target guest has no recorded deployment evidence')
        if len({address for _, _, address in matches}) != 1:
            raise WorkloadError('Recorded deployment evidence for the target is ambiguous')
        job, row, address = sorted(matches, key=lambda item: (
            str(item[0].get('finished_at') or item[0].get('created_at') or ''), item[0]['id']))[-1]
        connection_id = None
        for node in (job.get('graph') or {}).get('nodes') or []:
            candidate = (node.get('config') or {}).get('connection_id')
            if isinstance(candidate, str) and re.fullmatch(r'[0-9a-f]{32}', candidate):
                connection_id = candidate
        return {'uuid': uuid, 'address': address, 'name': row['name'],
                'job_id': job['id'], 'connection_id': connection_id,
                'evidence_digest': _digest({'job_id': job['id'], 'uuid': uuid,
                                            'address': address})}

    def _live(self, target):
        if self.inspector is None:
            return
        if not target.get('connection_id'):
            raise WorkloadError('Recorded target carries no connection identity')
        try:
            row = self.inspector(target['connection_id'], target['uuid'])
        except Exception:
            raise WorkloadError('Target guest could not be re-read; preparation refused') from None
        if (not isinstance(row, dict) or str(row.get('uuid', '')).lower() != target['uuid']
                or row.get('power_state') != 'poweredOn'
                or (target.get('name') and row.get('name') != target['name'])):
            raise WorkloadError('Target guest identity or power state changed')

    def _key(self, identity):
        if self.ssh_keys is None:
            raise WorkloadError('SSH key registry is unavailable')
        try:
            binding = self.ssh_keys.binding(identity)
        except Exception:
            raise WorkloadError('Selected SSH key is unavailable or disabled') from None
        if (not isinstance(binding, dict) or binding.get('id') != identity
                or not binding.get('fingerprint')):
            raise WorkloadError('Selected SSH key binding is inconsistent')
        return binding

    @contextmanager
    def _staged_key(self, identity):
        if self.ssh_keys is None:
            raise WorkloadError('SSH key registry is unavailable')
        folder = self.state_dir / 'workloads' / 'keys'
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / (identity + '-' + secrets.token_hex(8))
        try:
            with self.ssh_keys.private_key(identity) as secret:
                material = secret.bytes()
                if not isinstance(material, bytes) or not material or len(material) > 16384:
                    raise ValueError()
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(material)
                del material
                yield path
        except WorkloadError:
            raise
        except Exception:
            raise WorkloadError('SSH key material is unavailable') from None
        finally:
            path.unlink(missing_ok=True)

    def review(self, job, *, live=False):
        """Validate the brick contract and resolve the target from recorded evidence."""
        identity = job.get('id')
        graph = job.get('graph') or {}
        nodes = graph.get('nodes') or []
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', identity):
            raise WorkloadError('Invalid workload job identifier')
        if len(nodes) != 1 or graph.get('edges') or graph.get('failure_mode', 'none') != 'none':
            raise WorkloadError('A workload job carries exactly one brick and no edges')
        node = nodes[0]
        executor = self._executor()
        brick = executor.brick(node.get('plugin_id'))
        config = {**copy.deepcopy(brick.get('defaults') or {}), **(node.get('config') or {})}
        try:
            json.dumps(config, allow_nan=False)
            jsonschema.validate(config, brick['schema'])
        except (TypeError, ValueError):
            raise WorkloadError('Invalid brick configuration') from None
        except jsonschema.ValidationError as exc:
            raise WorkloadError('Invalid brick configuration: ' + str(exc.message)[:200]) from None
        uuid = str(config.get('target_uuid', '')).lower()
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', uuid):
            raise WorkloadError('Target identity is not a VM UUID')
        target = self._recorded_target(uuid, graph.get('environment'))
        if live:
            self._live(target)
        plan = executor.prepare(brick['id'], config, {'uuid': uuid, 'address': target['address']}, identity)
        return {'job_id': identity, 'environment': graph.get('environment'), 'node_id': node.get('id'),
                'brick': brick['id'], 'config': config, 'plan': plan,
                'key': self._key(config.get('ssh_key_id')), 'target': target}

    def _checked(self, job, *, live=False):
        reviewed = self.review(job, live=live)
        folder = self.state_dir / 'workloads' / job['id']
        path = folder / 'review.json'
        if path.is_symlink() or not path.is_file():
            raise WorkloadError('Saved workload plan is missing')
        try:
            signed = json.loads(path.read_text())
        except ValueError:
            raise WorkloadError('Saved workload plan is unreadable') from None
        if not isinstance(signed, dict) or signed.get('reviewed') != reviewed:
            raise WorkloadError('Workload plan or its inputs changed')
        if job.get('prepared_plan') != public_plan(signed):
            raise WorkloadError('Approved workload plan changed')
        return signed, folder

    def prepare_plan(self, job, emit):
        reviewed = self.review(job, live=True)
        folder = self.state_dir / 'workloads' / job['id']
        if folder.exists() and any(folder.iterdir()):
            raise WorkloadError('Existing workload preparation cannot be resumed or replanned')
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        signed = {'reviewed': reviewed, 'target': reviewed['target'],
                  'key': {'id': reviewed['key']['id'], 'fingerprint': reviewed['key']['fingerprint'],
                          'revision': reviewed['key'].get('revision')}}
        _private_write(folder / 'review.json', signed)
        emit('plan', 'info', 'Workload brick plan prepared; no guest was changed')
        return public_plan(signed)

    def validate_plan(self, job):
        """Local approval check: brick files, recorded evidence and the saved plan."""
        self._checked(job)

    def execute(self, job, emit):
        try:
            signed, _folder = self._checked(job)
            if (not job.get('approved_by') or not job.get('approved_at')
                    or job.get('approved_execution_provider', {}).get('name') != self.executor_id
                    or job.get('approved_prepared_plan') != _digest(job.get('prepared_plan'))):
                raise WorkloadError('Explicit approval of this exact workload plan is required')
            self._live(signed['target'])
            with self._staged_key(signed['key']['id']) as key_path:
                executor = self._executor(key_path=key_path)
                evidence = executor.execute(signed['reviewed']['brick'], job['id'],
                                            signed['reviewed']['config'],
                                            {'uuid': signed['target']['uuid'],
                                             'address': signed['target']['address']},
                                            emit=emit)
        except WorkloadError as error:
            rolled = 'rollback did not succeed' in str(error)
            return {'status': 'rollback_failed' if rolled else 'failed',
                    'error': 'Workload brick refused or failed: ' + str(error)[:300],
                    'evidence': [], 'endpoints': [], 'can_rollback': False,
                    'requires_manual_review': rolled}
        except (Exception, KeyboardInterrupt):
            return {'status': 'failed', 'error': 'Workload execution failed; raw output suppressed',
                    'evidence': [], 'endpoints': [], 'can_rollback': False,
                    'requires_manual_review': True}
        keep = ('status', 'plan_digest', 'brick', 'brick_version', 'role', 'role_version',
                'data_class', 'target_uuid', 'address', 'assertions', 'changed')
        return {'status': evidence['status'] if evidence.get('status') == 'succeeded' else 'failed',
                'evidence': [{key: evidence.get(key) for key in keep if key in evidence}],
                'endpoints': [], 'can_rollback': False, 'requires_manual_review': False}

    def rollback(self, job, emit):
        raise WorkloadError('Workload bricks have no manual rollback; submit a new brick')
