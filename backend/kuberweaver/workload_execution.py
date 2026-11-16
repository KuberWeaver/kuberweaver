"""Workload bricks: apply, verify, snapshot, roll back and remove on a guest.

A brick wraps a pinned upstream Ansible role. This adapter does not carry shell,
does not accept free-form variables and never sees a secret: the caller supplies
the path to a private key file, and every value that reaches the guest comes from
a schema-validated configuration.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
import shlex
import subprocess
from pathlib import Path

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
                 user='ubuntu'):
        self.state_dir = Path(state_dir).absolute()
        self.plugins_dir = Path(plugins_dir or Path(__file__).resolve().parents[2] / 'plugins').absolute()
        self.playbook = playbook
        self.key_path = Path(key_path).absolute() if key_path else None
        self.user = user

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
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=900,
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
