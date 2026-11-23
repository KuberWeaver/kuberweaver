"""Opt-in compound provider: local VM runtime, SSH identity, pinned Ansible.

The composition is deliberately narrow. One reviewed plugin declares the
runtime contract (``local-runtime-v1``) and the exact playbooks it will run.
A job supplies only VM shape inputs and an SSH key identity: no executable,
playbook, inventory, host or command is ever taken from job configuration.

Execution order is fixed:

1. the injected local runtime provisions the VM and returns a typed handoff;
2. this adapter validates that handoff, builds a pinned ``known_hosts`` entry
   from the attested guest host key, and proves SSH host identity plus guest
   reachability with a fixed ``ssh ... true`` command;
3. only then does it run the plugin's digest-pinned ``apply`` and ``verify``
   playbooks, with host key checking still strict.

Nothing is fetched: no ``ansible-galaxy``, no downloads, no auto-install.
A missing runtime, missing guest assets, a changed playbook or a handoff that
points away from a local address is a refusal, never a fallback.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import ipaddress
import json
import os
from contextlib import contextmanager
from pathlib import Path
import re
import shlex
import struct
import subprocess
from typing import Any

import jsonschema
import yaml

from .module_paths import LAYOUT_SOURCE, MODULES_DIR, module_directory


PROVIDER_ID = 'local-vm-ansible-v1'
RUNTIME_CONTRACT = 'local-runtime-v1'
PLUGIN_ID = 'local-vm-ansible'
MARKER_PATH = '/var/tmp/kuberweaver-baseline'
IDEMPOTENCY_ROOT = 'local-vm-ansible'
IDENTIFIER = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
KEY_TYPES = frozenset({'ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-rsa'})
BLOB = re.compile(r'^[A-Za-z0-9+/]{32,8192}={0,2}$')
SSH_CANDIDATES = (Path('/usr/bin/ssh'), Path('/bin/ssh'))
STDERR_LIMIT = 400


class LocalVmAnsibleError(ValueError):
    """A fixed, non-sensitive message may cross the public boundary."""


# The handoff is the only value the runtime hands over. It is typed, closed and
# re-validated here even when the runtime already checked it.
HANDOFF_SCHEMA: dict[str, Any] = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['schema_version', 'contract', 'runtime', 'vm', 'guest'],
    'properties': {
        'schema_version': {'type': 'integer', 'const': 1},
        'contract': {'type': 'string', 'const': RUNTIME_CONTRACT},
        'runtime': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['name', 'version'],
            'properties': {
                'name': {'type': 'string', 'pattern': r'^[a-z][a-z0-9_-]{1,63}$'},
                'version': {'type': 'string', 'pattern': r'^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$'},
            },
        },
        'vm': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['uuid', 'name', 'power_state'],
            'properties': {
                'uuid': {'type': 'string', 'pattern': r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$'},
                'name': {'type': 'string', 'pattern': r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'},
                'power_state': {'type': 'string', 'const': 'running'},
            },
        },
        'guest': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['address', 'ssh_port', 'user', 'host_key'],
            'properties': {
                'address': {'type': 'string', 'minLength': 2, 'maxLength': 45},
                'ssh_port': {'type': 'integer', 'minimum': 1, 'maximum': 65535},
                # The reviewed baseline account; never a job-provided value.
                'user': {'type': 'string', 'const': 'ubuntu'},
                'host_key': {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['type', 'public_key'],
                    'properties': {
                        'type': {'type': 'string', 'enum': sorted(KEY_TYPES)},
                        'public_key': {'type': 'string', 'minLength': 40, 'maxLength': 8192},
                    },
                },
            },
        },
    },
}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _sha256_file(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def _private_json(path: Path, value) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
    temporary.chmod(0o600)
    temporary.replace(path)


def _private_text(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
    temporary.chmod(0o600)
    temporary.replace(path)


def _key_fingerprint(blob: str) -> str:
    """OpenSSH SHA256 fingerprint of the attested host key."""
    try:
        raw = base64.b64decode(blob, validate=True)
    except (ValueError, TypeError):
        raise LocalVmAnsibleError('Handoff guest host key is not valid base64') from None
    return 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')


def _key_type(blob: str) -> str:
    try:
        raw = base64.b64decode(blob, validate=True)
        length = struct.unpack('>I', raw[:4])[0]
        if not 0 < length <= 64:
            raise ValueError()
        name = raw[4:4 + length].decode('ascii')
    except Exception:
        raise LocalVmAnsibleError('Handoff guest host key is malformed') from None
    if name not in KEY_TYPES:
        raise LocalVmAnsibleError('Handoff guest host key type is unsupported')
    return name


def _local_address(value: str) -> str:
    """No remote targets: only loopback, private or link-local addresses."""
    try:
        address = ipaddress.ip_address(value)
    except (ValueError, TypeError):
        raise LocalVmAnsibleError('Handoff guest address is not an IP address') from None
    if address.is_unspecified or address.is_multicast or not (
            address.is_loopback or address.is_private or address.is_link_local):
        raise LocalVmAnsibleError('Handoff guest address is not a local lab address')
    return str(address)


class Handoff:
    """Validated local-runtime-v1 handoff for exactly one running guest."""

    __slots__ = ('runtime_name', 'runtime_version', 'uuid', 'name', 'address',
                 'port', 'user', 'key_type', 'public_key', 'fingerprint', 'digest')

    def __init__(self, document: dict):
        if not isinstance(document, dict):
            raise LocalVmAnsibleError('Local runtime did not return a typed handoff')
        try:
            jsonschema.validate(document, HANDOFF_SCHEMA)
        except jsonschema.ValidationError:
            raise LocalVmAnsibleError('Local runtime handoff does not match local-runtime-v1') from None
        key = document['guest']['host_key']
        parts = key['public_key'].split()
        if len(parts) != 2 or not BLOB.fullmatch(parts[1]):
            raise LocalVmAnsibleError('Handoff guest host key is not a single public key')
        if _key_type(parts[1]) != key['type']:
            raise LocalVmAnsibleError('Handoff guest host key type does not match its blob')
        self.runtime_name = document['runtime']['name']
        self.runtime_version = document['runtime']['version']
        self.uuid = document['vm']['uuid']
        self.name = document['vm']['name']
        self.address = _local_address(document['guest']['address'])
        self.port = document['guest']['ssh_port']
        self.user = document['guest']['user']
        self.key_type = key['type']
        self.public_key = parts[1]
        self.fingerprint = _key_fingerprint(self.public_key)
        self.digest = _digest(document)

    def known_hosts_line(self) -> str:
        return f'[{self.address}]:{self.port} {self.key_type} {self.public_key}\n'

    def summary(self) -> dict:
        return {'runtime': self.runtime_name, 'runtime_version': self.runtime_version,
                'vm_uuid': self.uuid, 'vm_name': self.name, 'address': self.address,
                'ssh_port': self.port, 'ssh_user': self.user,
                'host_key_fingerprint': self.fingerprint, 'handoff_digest': self.digest}


class LocalVmAnsibleProvider:
    """Saved-plan execution provider composing one local guest and one brick.

    ``runtime`` is the local provisioner port: an object whose ``contract`` (or
    ``contract_version``) is ``local-runtime-v1`` and which exposes either
    ``provision(request)`` (create/ensure the VM and return the handoff) or
    ``handoff(identity)`` (return the handoff for an already-running VM).
    Both receive no paths and return only the typed document above.
    """

    executor_id = PROVIDER_ID
    requires_saved_plan = True
    supports_rollback = False
    ssh_keys: Any = None  # Injected by resolve_provider; never by job config.

    source_dependencies = (LAYOUT_SOURCE,)

    def __init__(self, state_dir, plugins_dir=None, *, runtime=None, playbook=None,
                 user='ubuntu', runner=None):
        self.state_dir = Path(state_dir).absolute()
        self.plugins_dir = Path(plugins_dir or MODULES_DIR).absolute()
        self.runtime = runtime
        self.playbook = Path(playbook) if playbook else None
        self.user = user
        # The kernel wraps this boundary with the durable approval guard.
        self.runner = runner or subprocess.run

    # --------------------------------------------------------------- reviewed

    def brick(self) -> dict:
        """Load the single reviewed compound plugin and verify its pins."""
        try:
            directory = module_directory(self.plugins_dir, PLUGIN_ID)
        except FileNotFoundError:
            raise LocalVmAnsibleError('Reviewed compound plugin is not installed') from None
        except ValueError as exc:
            raise LocalVmAnsibleError('Unsafe compound plugin path') from exc
        manifest_path = directory / 'manifest.yaml'
        if directory.is_symlink() or manifest_path.is_symlink():
            raise LocalVmAnsibleError('Unsafe compound plugin path')
        if not directory.is_dir() or not manifest_path.is_file():
            raise LocalVmAnsibleError('Reviewed compound plugin is not installed')
        try:
            manifest = yaml.safe_load(manifest_path.read_text())
        except (OSError, yaml.YAMLError):
            raise LocalVmAnsibleError('Reviewed compound plugin manifest is unreadable') from None
        if not isinstance(manifest, dict) or manifest.get('id') != PLUGIN_ID:
            raise LocalVmAnsibleError('Compound plugin identity mismatch')
        if manifest.get('kind') != 'workload' or manifest.get('capability') != 'live':
            raise LocalVmAnsibleError('Compound plugin must be a live workload')
        if manifest.get('executor') != PROVIDER_ID or manifest.get('execution_provider') != PROVIDER_ID:
            raise LocalVmAnsibleError('Compound plugin declares an unsupported executor')
        if manifest.get('requires') != 'compute.vm':
            raise LocalVmAnsibleError('Compound plugin must require compute.vm')
        if manifest.get('admission_profile') != 'virtual-machine':
            raise LocalVmAnsibleError('Compound plugin must use the virtual-machine admission profile')
        if not manifest.get('data_class'):
            raise LocalVmAnsibleError('Compound plugin does not declare its data class')
        implementation = manifest.get('implementation') or {}
        core = implementation.get('ansible_core')
        if not isinstance(core, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', core):
            raise LocalVmAnsibleError('Compound plugin must pin ansible-core')
        if implementation.get('collections') != []:
            raise LocalVmAnsibleError('Compound plugin must not require fetched collections')
        declared = implementation.get('playbooks')
        digests = implementation.get('sha256')
        lifecycle = manifest.get('lifecycle') or {}
        if not isinstance(declared, dict) or not isinstance(digests, dict) or not isinstance(lifecycle, dict):
            raise LocalVmAnsibleError('Compound plugin does not declare pinned playbooks')
        # Apply and verify are the only executable steps the composition allows.
        if set(lifecycle) != {'apply', 'verify'} or set(declared) != {'apply', 'verify'}:
            raise LocalVmAnsibleError('Compound plugin must declare exactly apply and verify')
        resolved = {}
        for action in ('apply', 'verify'):
            if lifecycle[action] != declared[action]:
                raise LocalVmAnsibleError('Compound plugin playbook declaration is inconsistent')
            play = (directory / declared[action]).resolve()
            if play.is_symlink() or not play.is_relative_to(directory) or not play.is_file():
                raise LocalVmAnsibleError('Compound plugin playbook escapes its directory')
            expected = digests.get(declared[action])
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise LocalVmAnsibleError('Compound plugin playbook digest is missing')
            if _sha256_file(play) != expected:
                raise LocalVmAnsibleError('Compound plugin playbook changed; review and re-pin it')
            resolved[action] = play
        assertions = (manifest.get('verify') or {}).get('assertions') or []
        if not assertions or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                                 or not IDENTIFIER.fullmatch(row['id']) for row in assertions):
            raise LocalVmAnsibleError('Compound plugin declares no verifiable assertion')
        if len({row['id'] for row in assertions}) != len(assertions):
            raise LocalVmAnsibleError('Compound plugin assertions are duplicated')
        return {**manifest, '_directory': directory, '_playbooks': resolved,
                '_manifest_sha256': _sha256_file(manifest_path),
                '_assertions': [row['id'] for row in assertions]}

    def _runtime_contract(self) -> str:
        runtime = self.runtime
        if runtime is None:
            raise LocalVmAnsibleError('Local runtime is not configured')
        contract = getattr(runtime, 'contract', None) or getattr(runtime, 'contract_version', None)
        if contract is None and getattr(runtime, 'executor_id', None) == RUNTIME_CONTRACT:
            # The local-runtime-v1 provider declares its contract this way.
            contract = RUNTIME_CONTRACT
        if contract != RUNTIME_CONTRACT:
            raise LocalVmAnsibleError('Local runtime does not declare local-runtime-v1')
        if not (callable(getattr(runtime, 'provision', None)) or callable(getattr(runtime, 'handoff', None))):
            raise LocalVmAnsibleError('Local runtime does not expose a typed handoff port')
        return contract

    def _ansible(self) -> Path:
        if self.playbook is None:
            from .configuration import ansible_playbook
            try:
                self.playbook = Path(ansible_playbook())
            except ValueError as error:
                raise LocalVmAnsibleError('Ansible executable is not configured: ' + str(error)[:200]) from None
        path = self.playbook
        if path.is_symlink() or not path.is_file() or path.name != 'ansible-playbook' or not os.access(path, os.X_OK):
            raise LocalVmAnsibleError('Ansible executable is unavailable')
        return path

    def _ssh(self) -> Path:
        for candidate in SSH_CANDIDATES:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        raise LocalVmAnsibleError('OpenSSH client is unavailable')

    # ----------------------------------------------------------------- review

    def _key(self, identity):
        if self.ssh_keys is None:
            raise LocalVmAnsibleError('SSH key registry is unavailable')
        try:
            binding = self.ssh_keys.binding(identity)
        except Exception:
            raise LocalVmAnsibleError('Selected SSH key is unavailable or disabled') from None
        if (not isinstance(binding, dict) or binding.get('id') != identity
                or not isinstance(binding.get('fingerprint'), str)):
            raise LocalVmAnsibleError('Selected SSH key binding is inconsistent')
        return {'id': identity, 'fingerprint': binding['fingerprint'],
                'revision': binding.get('revision')}

    def review(self, job):
        """Strictly local review: no runtime call, no guest I/O."""
        identity = job.get('id')
        if not isinstance(identity, str) or not IDENTIFIER.fullmatch(identity):
            raise LocalVmAnsibleError('Invalid compound job identifier')
        graph = job.get('graph') or {}
        nodes = graph.get('nodes') or []
        if len(nodes) != 1 or graph.get('edges') or graph.get('failure_mode', 'none') != 'none':
            raise LocalVmAnsibleError('A compound job carries exactly one node and no edges')
        if graph.get('environment') != 'dev':
            raise LocalVmAnsibleError('Local VM composition is limited to the dev environment')
        node = nodes[0]
        brick = self.brick()
        if node.get('plugin_id') != brick['id']:
            raise LocalVmAnsibleError('Only the reviewed local-vm-ansible plugin is supported')
        config = {**copy.deepcopy(brick.get('defaults') or {}), **(node.get('config') or {})}
        try:
            json.dumps(config, allow_nan=False)
            jsonschema.validate(config, brick['schema'])
        except (TypeError, ValueError):
            raise LocalVmAnsibleError('Invalid compound configuration') from None
        except jsonschema.ValidationError as error:
            raise LocalVmAnsibleError('Invalid compound configuration: ' + str(error.message)[:200]) from None
        runtime_contract = self._runtime_contract()
        ansible = self._ansible()
        key = self._key(config.get('ssh_key_id'))
        playbooks = brick['_playbooks']
        reviewed = {
            'job_id': identity,
            'node_id': node.get('id'),
            'graph_digest': _digest(graph),
            'environment': graph.get('environment'),
            'plugin': {'id': brick['id'], 'version': str(brick['version']),
                       'manifest_sha256': brick['_manifest_sha256']},
            'config': config,
            'runtime_contract': runtime_contract,
            'ansible_core': brick['implementation']['ansible_core'],
            'key': key,
            'pins': {
                'manifest': brick['_manifest_sha256'],
                'apply': _sha256_file(playbooks['apply']),
                'verify': _sha256_file(playbooks['verify']),
                'ansible_playbook': _sha256_file(ansible),
                'provider_source': _sha256_file(Path(__file__).resolve()),
            },
            'assertions': list(brick['_assertions']),
        }
        reviewed['plan_digest'] = _digest({key: reviewed[key] for key in (
            'job_id', 'node_id', 'graph_digest', 'environment', 'plugin', 'config',
            'runtime_contract', 'ansible_core', 'key', 'pins', 'assertions')})
        return reviewed

    def _checked(self, job):
        reviewed = self.review(job)
        folder = self.state_dir / IDEMPOTENCY_ROOT / job['id']
        path = folder / 'review.json'
        if path.is_symlink() or not path.is_file():
            raise LocalVmAnsibleError('Saved compound plan is missing')
        try:
            signed = json.loads(path.read_text())
        except (OSError, ValueError):
            raise LocalVmAnsibleError('Saved compound plan is unreadable') from None
        if not isinstance(signed, dict) or signed.get('reviewed') != reviewed:
            raise LocalVmAnsibleError('Compound plan or its inputs changed')
        if job.get('prepared_plan') != public_plan(signed):
            raise LocalVmAnsibleError('Approved compound plan changed')
        return signed, folder

    # ------------------------------------------------------------------- plan

    def read(self):
        reasons = []
        binary_present = False
        try:
            self.brick()
        except LocalVmAnsibleError as error:
            reasons.append(str(error))
        try:
            self._runtime_contract()
        except LocalVmAnsibleError as error:
            reasons.append(str(error))
        try:
            binary_present = self._ansible().is_file()
        except LocalVmAnsibleError as error:
            reasons.append(str(error))
        return {'provider': PROVIDER_ID, 'requires_saved_plan': True, 'supports_rollback': False,
                'binary_present': binary_present,
                'status': 'unavailable' if reasons else 'configured',
                'reasons': reasons,
                'limitations': [
                    'one local guest per job; one reviewed playbook pair',
                    'the runtime must attest the guest SSH host key in the typed handoff',
                    'only loopback, private or link-local guest addresses are accepted',
                    'no ansible-galaxy fetch, no role installation, no downloaded collection',
                    'a failed apply leaves the guest running for manual review; no automatic destroy',
                ]}

    def prepare_plan(self, job, emit):
        reviewed = self.review(job)
        folder = self.state_dir / IDEMPOTENCY_ROOT / job['id']
        if folder.exists() and any(folder.iterdir()):
            raise LocalVmAnsibleError('Existing compound preparation cannot be resumed or replanned')
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        signed = {'reviewed': reviewed}
        _private_json(folder / 'review.json', signed)
        emit('plan', 'info', 'Local VM composition plan prepared; no guest was created or changed')
        return public_plan(signed)

    def validate_plan(self, job):
        """Local approval check: manifest, playbooks, runtime contract, saved plan."""
        self._checked(job)

    # -------------------------------------------------------------- execution

    @contextmanager
    def _staged_key(self, identity):
        if self.ssh_keys is None:
            raise LocalVmAnsibleError('SSH key registry is unavailable')
        folder = self.state_dir / IDEMPOTENCY_ROOT / 'keys'
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / (identity + '-' + os.urandom(8).hex())
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
        except LocalVmAnsibleError:
            raise
        except Exception:
            raise LocalVmAnsibleError('SSH key material is unavailable') from None
        finally:
            path.unlink(missing_ok=True)

    def _provision(self, identity, reviewed):
        runtime = self.runtime
        if runtime is None:
            raise LocalVmAnsibleError('Local runtime is not configured')
        request = {'job_id': identity,
                   'vm': {'name': reviewed['config']['vm_name'], 'cpu': reviewed['config']['cpu'],
                          'memory_mb': reviewed['config']['memory_mb'],
                          'disk_gb': reviewed['config']['disk_gb']}}
        try:
            if callable(getattr(runtime, 'provision', None)):
                document = runtime.provision(copy.deepcopy(request))
            else:
                document = runtime.handoff(identity)
        except LocalVmAnsibleError:
            raise
        except Exception:
            raise LocalVmAnsibleError('Local provisioner failed; raw output suppressed') from None
        handoff = Handoff(document)
        if handoff.name != reviewed['config']['vm_name']:
            raise LocalVmAnsibleError('Provisioned guest name does not match the approved plan')
        if handoff.user != self.user:
            raise LocalVmAnsibleError('Provisioned guest login does not match the reviewed account')
        return handoff

    def _ssh_identity(self, handoff, key_path, known_hosts, folder):
        """Prove the attested host key and a usable login before Ansible runs."""
        ssh = self._ssh()
        args = [
            str(ssh), '-F', '/dev/null', '-p', str(handoff.port),
            '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'PreferredAuthentications=publickey', '-o', 'PasswordAuthentication=no',
            '-o', 'KbdInteractiveAuthentication=no', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(known_hosts), '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'UpdateHostKeys=no', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
            '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none', '-o', 'ForwardAgent=no',
            '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1',
            '-i', str(key_path), f'{handoff.user}@{handoff.address}', 'true',
        ]
        home = folder / 'home'
        home.mkdir(mode=0o700, exist_ok=True)
        completed = self.runner(args, capture_output=True, text=True, timeout=30, cwd=str(folder),
                                env={'PATH': '/usr/bin:/bin', 'HOME': str(home),
                                     'TMPDIR': str(folder), 'LANG': 'C.UTF-8'})
        if completed.returncode != 0:
            raise LocalVmAnsibleError('SSH host identity or guest reachability check failed')

    def _ansible_env(self, folder):
        return {'PATH': '/usr/bin:/bin:/usr/local/bin', 'HOME': str(folder / 'home'),
                'TMPDIR': str(folder), 'LANG': 'C.UTF-8',
                'ANSIBLE_HOST_KEY_CHECKING': 'True', 'ANSIBLE_NOCOLOR': '1',
                'ANSIBLE_RETRY_FILES_ENABLED': 'False', 'ANSIBLE_GATHERING': 'explicit',
                'ANSIBLE_LOCAL_TEMP': str(folder / 'ansible-tmp')}

    def _inventory(self, handoff, key_path, known_hosts, folder):
        path = folder / 'inventory.json'
        _private_json(path, {'all': {'hosts': {'target': {
            'ansible_host': handoff.address,
            'ansible_port': handoff.port,
            'ansible_user': handoff.user,
            'ansible_connection': 'ssh',
            'ansible_python_interpreter': '/usr/bin/python3',
            'ansible_ssh_private_key_file': str(key_path),
            # strict identity again at the Ansible boundary; nothing inherited
            'ansible_ssh_common_args': '-F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes '
                                       '-o PreferredAuthentications=publickey -o PasswordAuthentication=no '
                                       '-o StrictHostKeyChecking=yes '
                                       '-o UserKnownHostsFile=' + shlex.quote(str(known_hosts)) + ' '
                                       '-o GlobalKnownHostsFile=/dev/null -o UpdateHostKeys=no '
                                       '-o ControlMaster=no -o ControlPath=none '
                                       '-o ProxyCommand=none -o ProxyJump=none '
                                       '-o ForwardAgent=no -o ClearAllForwardings=yes '
                                       '-o ConnectTimeout=15 -o ConnectionAttempts=1',
        }}}})
        return path

    def _play(self, action, brick, handoff, key_path, known_hosts, folder, job_id):
        playbook = brick['_playbooks'][action]
        checksum = brick['implementation']['sha256'][brick['implementation']['playbooks'][action]]
        if _sha256_file(playbook) != checksum:
            raise LocalVmAnsibleError('Pinned playbook changed before execution')
        inventory = self._inventory(handoff, key_path, known_hosts, folder)
        result_path = folder / 'verify.result.json'
        if action == 'verify':
            result_path.unlink(missing_ok=True)
        variables = {'kw_marker_path': MARKER_PATH,
                     'kw_marker_content': 'kuberweaver:' + job_id,
                     'kw_result_path': str(result_path)}
        vars_path = folder / f'{action}.vars.json'
        _private_json(vars_path, variables)
        (folder / 'home').mkdir(mode=0o700, exist_ok=True)
        (folder / 'ansible-tmp').mkdir(mode=0o700, exist_ok=True)
        argv = [str(self._ansible()), '-i', str(inventory), str(playbook),
                '-e', '@' + str(vars_path)]
        completed = self.runner(argv, capture_output=True, text=True,
                                timeout=900 if action == 'apply' else 300,
                                cwd=str(folder), env=self._ansible_env(folder))
        report = None
        if action == 'verify' and result_path.is_file():
            try:
                report = json.loads(result_path.read_text())
            except ValueError:
                report = None
        return {'returncode': completed.returncode, 'report': report,
                'changed': _changed_count(completed.stdout),
                'stdout_tail': (completed.stdout or '')[-1200:],
                'stderr_tail': (completed.stderr or '')[-STDERR_LIMIT:]}

    def execute(self, job, emit):
        emit = emit or (lambda *a: None)
        touched, handoff, observed, folder = False, None, {}, None
        try:
            signed, folder = self._checked(job)
            reviewed = signed['reviewed']
            if (not job.get('approved_by') or not job.get('approved_at')
                    or job.get('approved_execution_provider', {}).get('name') != PROVIDER_ID
                    or job.get('approved_prepared_plan') != _digest(job.get('prepared_plan'))):
                raise LocalVmAnsibleError('Explicit approval of this exact compound plan is required')
            if (folder / 'evidence.json').exists():
                raise LocalVmAnsibleError('Job already executed; use a fresh id')
            brick = self.brick()
            if _sha256_file(brick['_playbooks']['apply']) != reviewed['pins']['apply'] \
                    or _sha256_file(brick['_playbooks']['verify']) != reviewed['pins']['verify'] \
                    or _sha256_file(self._ansible()) != reviewed['pins']['ansible_playbook'] \
                    or _sha256_file(brick['_directory'] / 'manifest.yaml') != reviewed['pins']['manifest']:
                raise LocalVmAnsibleError('Approved executable inputs changed before execution')

            with self._staged_key(reviewed['key']['id']) as key_path:
                emit('provision', 'info', f"Provisioning {reviewed['config']['vm_name']} with local-runtime-v1")
                touched = True
                handoff = self._provision(job['id'], reviewed)
                known_hosts = folder / 'known_hosts'
                _private_text(known_hosts, handoff.known_hosts_line())
                emit('check', 'info', f'Verifying SSH host identity {handoff.fingerprint}')
                self._ssh_identity(handoff, key_path, known_hosts, folder)
                emit('apply', 'info', f"{brick['id']} over the verified guest channel")
                applied = self._play('apply', brick, handoff, key_path, known_hosts, folder, job['id'])
                if applied['returncode'] != 0:
                    raise LocalVmAnsibleError('Pinned apply playbook failed on the verified guest')
                verified = self._play('verify', brick, handoff, key_path, known_hosts, folder, job['id'])
                if verified['returncode'] != 0 or verified['report'] is None:
                    raise LocalVmAnsibleError('Pinned verify playbook failed on the verified guest')
                declared = list(reviewed['assertions'])
                observed = (verified['report'] or {}).get('assertions') or {}
                if set(observed) != set(declared) or not all(observed[key] is True for key in declared):
                    raise LocalVmAnsibleError('Verification report does not honour the declared assertions')
        except LocalVmAnsibleError as error:
            message = 'Local VM composition refused or failed: ' + str(error)[:300]
            self._failed_evidence(folder, touched, message)
            return {'status': 'failed', 'error': message,
                    'evidence': [], 'endpoints': [], 'can_rollback': False,
                    'requires_manual_review': touched}
        except (Exception, KeyboardInterrupt):
            self._failed_evidence(folder, touched, 'Local VM composition failed; raw output suppressed')
            return {'status': 'failed', 'error': 'Local VM composition failed; raw output suppressed',
                    'evidence': [], 'endpoints': [], 'can_rollback': False,
                    'requires_manual_review': touched}

        evidence = {**handoff.summary(),
                    'plugin': brick['id'], 'plugin_version': str(brick['version']),
                    'plan_digest': reviewed['plan_digest'],
                    'runtime_contract': reviewed['runtime_contract'],
                    'ansible_core': reviewed['ansible_core'],
                    'assertions': observed, 'changed': applied['changed']}
        _private_json(folder / 'evidence.json', evidence)
        return {'status': 'succeeded', 'evidence': [evidence], 'endpoints': [],
                'can_rollback': False, 'requires_manual_review': False}

    def rollback(self, job, emit):
        return {'status': 'rollback_failed', 'can_rollback': False, 'requires_manual_review': True,
                'error': 'Local VM composition has no automatic rollback; inspect the guest and destroy it explicitly',
                'evidence': [], 'endpoints': []}

    @staticmethod
    def _failed_evidence(folder, touched, message):
        """A guest may exist after provisioning; a retry must not create it again."""
        if not touched or folder is None or not folder.is_dir():
            return
        try:
            _private_json(folder / 'evidence.json',
                          {'status': 'failed', 'error': message[:300],
                           'requires_manual_review': True})
        except OSError:
            pass


def _changed_count(output) -> int:
    match = re.search(r'changed=(\d+)', output or '')
    return int(match.group(1)) if match else 0


def public_plan(signed):
    """The bounded plan an operator approves; never private evidence."""
    reviewed = signed['reviewed']
    config = reviewed['config']
    return {
        'plan_digest': _digest(signed),
        'actions': {'provision': ['local-runtime-v1'],
                    'check': ['ssh-host-identity', 'guest-reachable'],
                    'apply': ['baseline', 'verify']},
        'summary': {'plugin': reviewed['plugin']['id'],
                    'plugin_version': reviewed['plugin']['version'],
                    'vm_name': config['vm_name'], 'cpu': config['cpu'],
                    'memory_mb': config['memory_mb'], 'disk_gb': config['disk_gb'],
                    'runtime': reviewed['runtime_contract'],
                    'ansible_core': reviewed['ansible_core'],
                    'assertions': len(reviewed['assertions']),
                    'reversible': False},
        'bindings': {'plugin': reviewed['plugin']['id'],
                     'plugin_version': reviewed['plugin']['version'],
                     'manifest_sha256': reviewed['pins']['manifest'],
                     'apply_playbook_sha256': reviewed['pins']['apply'],
                     'verify_playbook_sha256': reviewed['pins']['verify'],
                     'ansible_playbook_sha256': reviewed['pins']['ansible_playbook'],
                     'runtime_contract': reviewed['runtime_contract'],
                     'ssh_key_id': reviewed['key']['id'],
                     'ssh_key_fingerprint': reviewed['key']['fingerprint']},
    }
