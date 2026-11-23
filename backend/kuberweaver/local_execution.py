"""Administrator-pinned local OCI and QEMU execution.

Two reviewed bricks run on the controller itself:

* ``local-oci`` starts a rootless runc container from a private bundle and
  serves one fixed marker over loopback.
* ``local-qemu`` boots a fixed kernel/initramfs under QEMU TCG and proves the
  guest init ran through a serial marker and QMP.

Nothing in a graph can select a binary, image, bundle, kernel, disk, socket or
filesystem path. Every asset is an absolute path pinned by SHA-256 in the
administrator-only ``config/local-runtime.json``. A job may only choose the
bounded fields of the reviewed manifest bricks. Resources are private to one
job, recorded durably, and removed with checked absence; no container or VM is
adopted or reused.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time
try:  # The portal already depends on both; keep a clear failure when absent.
    import jsonschema
    import yaml
except ImportError:  # pragma: no cover - exercised only in a broken install.
    jsonschema = None
    yaml = None

EXECUTOR_ID = 'local-runtime-v1'
CONFIG_FILE = 'local-runtime.json'
ROOT = Path(__file__).resolve().parents[2]

JOB_ID = re.compile(r'[0-9a-f]{32}\Z')
NODE_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
CONTAINER_ID = re.compile(r'kw-[0-9a-f]{12}-[0-9]{1,2}\Z')
PORT_MIN, PORT_MAX = 18090, 18109
MAX_NODES = 8
QEMU_BOOT_MARKER = 'KW_LOCAL_QEMU_READY'

ASSET_FILES = ('runc', 'busybox', 'qemu_system', 'qemu_img', 'cpio', 'kernel', 'bios')
ASSET_TREES = ('qemu_share', 'qemu_lib')
ASSET_ROLES = ASSET_FILES + ASSET_TREES
EXECUTABLE_ASSETS = ('runc', 'qemu_system', 'qemu_img', 'cpio')
BUILD_ASSETS = ('runc', 'busybox', 'qemu_system', 'qemu_img', 'cpio', 'kernel', 'bios',
                'qemu_share', 'qemu_lib')

PLUGINS = {
    'local-oci': {'engine': 'local-oci', 'schema': {'targets', 'port'}},
    'local-qemu': {'engine': 'local-qemu', 'schema': {'cpu', 'memory_mb', 'disk_mb'}},
}

# Fixed workload baselines. They are code, not API input: no graph field can
# replace a command, image or executable. The digest-pinned busybox is the only
# user-space payload in both cases.
WORKLOADS = {
    'local-oci': {
        'kind': 'oci',
        'container_args': ('/bin/busybox', 'httpd', '-f', '-p', None, '-h', '/www'),
        'loopback': True,
    },
    'local-qemu': {
        'kind': 'qemu',
        'init': '#!/bin/busybox sh\necho ' + QEMU_BOOT_MARKER + '\nwhile :; do /bin/busybox sleep 2; done\n',
        'machine': ['-accel', 'tcg', '-m', None, '-smp', None, '-no-user-config', '-nodefaults',
                    '-display', 'none', '-serial', 'file:console.log', '-monitor', 'none',
                    '-qmp', 'unix:qmp.sock,server=on,wait=off', '-nic', 'none'],
        'kernel_append': 'console=ttyS0 quiet rdinit=/init',
    },
}


class LocalExecutor:
    """Provider implementation registered as ``local-runtime-v1``.

    ``state_dir`` is the portal data directory. The administrator writes
    ``<state_dir>/config/local-runtime.json``; the provider owns only
    ``<state_dir>/local-runtime`` for journals and runtime files.
    """

    name = EXECUTOR_ID
    executor_id = EXECUTOR_ID
    supports_rollback = True
    supports_decommission = True

    def __init__(self, state_dir, *, plugins_dir=None, runner=None):
        state_dir = Path(state_dir)
        if not state_dir.is_absolute():
            state_dir = state_dir.absolute()
        if state_dir.is_symlink():
            raise ValueError('Symlinked local runtime state directory is forbidden')
        self.state_dir = state_dir
        self.plugins_dir = Path(plugins_dir or ROOT / 'plugins').absolute()
        # Kept injectable for direct tests; every normal operation uses the
        # fixed argv built in this file, never a graph-supplied command.
        self.runner = runner or subprocess.run

    # ------------------------------------------------------------------ paths

    @property
    def root(self):
        return self.state_dir / 'local-runtime'

    def _folder(self, identity):
        if not isinstance(identity, str) or not JOB_ID.fullmatch(identity):
            raise ValueError('Invalid local runtime job identifier')
        folder = self.root / 'jobs' / identity
        if folder.parent.is_symlink() or folder.is_symlink():
            raise ValueError('Symlinked local runtime job state is forbidden')
        return folder

    @contextmanager
    def _lock(self):
        for path in (self.state_dir, *self.state_dir.parents, self.root.parent):
            if path.is_symlink():
                raise ValueError('Symlinked local runtime state path is forbidden')
        if self.root.is_symlink() or (self.root / 'jobs').is_symlink():
            raise ValueError('Symlinked local runtime state path is forbidden')
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.root.chmod(0o700)
        (self.root / 'jobs').mkdir(mode=0o700, exist_ok=True)
        (self.root / 'jobs').chmod(0o700)
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    # ------------------------------------------------------------- small I/O

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    @staticmethod
    def _digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _sha_file(path):
        digest = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _read_json(cls, path, *, limit=1024 * 1024):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe local runtime JSON path')
        if path.stat().st_size > limit:
            raise ValueError('Oversized local runtime JSON document')
        return json.loads(path.read_text(encoding='utf-8'))

    @classmethod
    def _write_json(cls, path, value):
        path = Path(path)
        if path.is_symlink():
            raise ValueError('Symlinked local runtime JSON path is forbidden')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name('.' + path.name + '.new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink(missing_ok=True)

    # ------------------------------------------------------------- asset pins

    @staticmethod
    def _safe_path(value, *, directory=False):
        if not isinstance(value, str):
            raise ValueError('Asset paths must be strings')
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('Asset paths must be absolute and canonical')
        if path.is_symlink():
            raise ValueError('Symlinked assets are forbidden')
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError('Asset is missing') from None
        if resolved != path:
            raise ValueError('Asset paths must not traverse symlinks')
        if directory and not path.is_dir():
            raise ValueError('Asset tree is not a directory')
        if not directory and not path.is_file():
            raise ValueError('Asset is not a regular file')
        if path.stat().st_mode & 0o022:
            raise ValueError('Assets must not be writable by group or others')
        return path

    @classmethod
    def _tree_digest(cls, root):
        root = cls._safe_path(str(root), directory=True)
        digest = hashlib.sha256()
        for path in sorted(root.rglob('*'), key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if path.is_symlink():
                target = os.readlink(path)
                if os.path.isabs(target):
                    raise ValueError('Absolute symlink in an asset tree')
                try:
                    resolved = (path.parent / target).resolve(strict=True)
                except (OSError, RuntimeError):
                    raise ValueError('Broken symlink in an asset tree') from None
                if not resolved.is_relative_to(root):
                    raise ValueError('Symlink escapes an asset tree')
                if resolved.stat().st_mode & 0o022:
                    raise ValueError('Asset tree contains a writable target')
                digest.update(b'L\0' + relative.encode() + b'\0' + target.encode() + b'\0')
            elif path.is_dir():
                if info.st_mode & 0o022:
                    raise ValueError('Asset tree contains a writable directory')
                digest.update(b'D\0' + relative.encode() + b'\0')
            elif path.is_file():
                if info.st_nlink > 1:
                    raise ValueError('Hard-linked files in an asset tree are forbidden')
                digest.update(b'F\0' + relative.encode() + b'\0'
                              + hashlib.sha256(path.read_bytes()).digest())
            else:
                raise ValueError('Unsupported file type in an asset tree')
        return digest.hexdigest()

    @classmethod
    def build_config(cls, assets):
        """Return the exact JSON object an administrator should persist.

        ``assets`` maps every role in ``ASSET_ROLES`` to an absolute path. The
        caller writes the returned object to ``<state_dir>/config/
        local-runtime.json`` with mode 0600; no path or digest is inferred.
        """
        if not isinstance(assets, dict) or set(assets) != set(BUILD_ASSETS):
            raise ValueError('Asset map must name every local runtime asset exactly once')
        paths, sha256 = {}, {}
        for role in ASSET_FILES:
            path = cls._safe_path(assets[role])
            paths[role] = str(path)
            sha256[role] = cls._sha_file(path)
        for role in ASSET_TREES:
            path = cls._safe_path(assets[role], directory=True)
            paths[role] = str(path)
            sha256[role] = cls._tree_digest(path)
        return {'schema_version': 1, 'assets': paths, 'sha256': sha256}

    def _configuration(self, *, required):
        path = self.state_dir / 'config' / CONFIG_FILE
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('Unsafe local runtime configuration path')
        if not path.exists():
            if required:
                raise ValueError('Local runtime configuration is absent')
            return None
        if not path.is_file():
            raise ValueError('Unsafe local runtime configuration path')
        if path.stat().st_size > 65536:
            raise ValueError('Oversized local runtime configuration')
        if path.stat().st_mode & 0o077:
            raise ValueError('Local runtime configuration must not be readable by other users')
        value = self._read_json(path)
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'assets', 'sha256'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('Invalid local runtime configuration shape')
        if not isinstance(value['assets'], dict) or set(value['assets']) != set(ASSET_ROLES):
            raise ValueError('Local runtime configuration must name every asset')
        if not isinstance(value['sha256'], dict) or set(value['sha256']) != set(ASSET_ROLES):
            raise ValueError('Local runtime configuration must pin every asset')
        assets, digests = {}, {}
        for role in ASSET_ROLES:
            assets[role] = str(self._safe_path(value['assets'][role], directory=role in ASSET_TREES))
            digest = value['sha256'][role]
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('Invalid asset digest')
            digests[role] = digest
        return {'schema_version': 1, 'assets': assets, 'sha256': digests}

    def _verify_configuration(self, config):
        for role in ASSET_FILES:
            path = self._safe_path(config['assets'][role])
            if role in EXECUTABLE_ASSETS and not os.access(path, os.X_OK):
                raise ValueError('Local runtime asset is not executable: ' + role)
            if self._sha_file(path) != config['sha256'][role]:
                raise ValueError('Local runtime asset digest mismatch: ' + role)
        for role in ASSET_TREES:
            path = self._safe_path(config['assets'][role], directory=True)
            if self._tree_digest(path) != config['sha256'][role]:
                raise ValueError('Local runtime asset tree digest mismatch: ' + role)
        return config

    def _pinned_configuration(self):
        config = self._configuration(required=True)
        if config is None:
            raise ValueError('Local runtime configuration is absent')
        self._verify_configuration(config)
        self.runc_path = Path(config['assets']['runc'])
        return config

    @staticmethod
    def _config_hash(config):
        return LocalExecutor._digest({'schema_version': config['schema_version'],
                                      'assets': config['assets'], 'sha256': config['sha256']})

    def binding_fingerprint(self):
        """Bind approvals to the administrator's exact runtime asset pins."""
        return self._config_hash(self._configuration(required=True))

    def ready(self):
        """True when configured and every pinned asset verifies.

        False means no administrator configuration is present. A present but
        unsafe, incomplete or tampered configuration raises ``ValueError`` with
        a role name, never an external exception message.
        """
        try:
            config = self._configuration(required=False)
        except Exception:
            raise ValueError('Local runtime configuration is invalid') from None
        if config is None:
            return False
        self._verify_configuration(config)
        return True

    def read(self):
        """Small registry-facing status; no paths or exception text."""
        try:
            config = self._configuration(required=False)
            if config is None:
                return {'provider': 'local-runtime', 'executor': EXECUTOR_ID,
                        'configured': False, 'status': 'unconfigured', 'read_only': True}
            self._verify_configuration(config)
            return {'provider': 'local-runtime', 'executor': EXECUTOR_ID, 'configured': True,
                    'status': 'configured', 'read_only': True,
                    'bricks': ['local-oci', 'local-qemu'],
                    'limitations': ['rootless OCI and QEMU TCG only',
                                    'loopback OCI endpoint; no external exposure',
                                    'no arbitrary image, binary, path or command',
                                    'QEMU guest is boot evidence, not an Ansible SSH target']}
        except Exception:
            return {'provider': 'local-runtime', 'executor': EXECUTOR_ID,
                    'configured': False, 'status': 'invalid', 'read_only': True}

    # ------------------------------------------------------------- manifests

    def _manifest(self, plugin_id, config=None):
        if jsonschema is None or yaml is None:
            raise RuntimeError('Local runtime manifest dependencies unavailable')
        if plugin_id not in PLUGINS:
            raise ValueError('Unsupported local runtime brick')
        directory = self.plugins_dir / plugin_id
        manifest_path = directory / 'manifest.yaml'
        if directory.is_symlink() or manifest_path.is_symlink():
            raise ValueError('Symlinked local runtime manifest is forbidden')
        if not manifest_path.is_file() or manifest_path.stat().st_size > 65536:
            raise ValueError('Local runtime manifest is missing or oversized')
        if directory.resolve().parent != self.plugins_dir.resolve():
            raise ValueError('Local runtime manifest is outside the plugin directory')
        manifest = yaml.safe_load(manifest_path.read_text(encoding='utf-8'))
        expected = PLUGINS[plugin_id]
        if (not isinstance(manifest, dict) or manifest.get('id') != plugin_id
                or manifest.get('capability') != 'live'
                or manifest.get('engine') != expected['engine']
                or manifest.get('executor') != EXECUTOR_ID
                or manifest.get('execution_provider') != EXECUTOR_ID):
            raise ValueError('Unsupported local runtime manifest protocol')
        schema = manifest.get('schema')
        if not isinstance(schema, dict) or schema.get('type') != 'object' or schema.get('additionalProperties') is not False:
            raise ValueError('Local runtime manifests require a closed object schema')
        if '$ref' in json.dumps(schema) or '$dynamicRef' in json.dumps(schema):
            raise ValueError('Schema references are unsupported')
        jsonschema.Draft202012Validator.check_schema(schema)
        if config is not None:
            try:
                jsonschema.validate(config, schema)
            except jsonschema.ValidationError:
                raise ValueError('Local runtime configuration does not match its brick') from None
        if set(schema.get('properties', {})) != expected['schema']:
            raise ValueError('Local runtime manifest schema changed')
        return manifest

    def _review(self, job):
        if not isinstance(job, dict):
            raise ValueError('Invalid local runtime job')
        identity = job.get('id')
        if not isinstance(identity, str) or not JOB_ID.fullmatch(identity):
            raise ValueError('Invalid local runtime job identifier')
        if job.get('requires_manual_review'):
            raise ValueError('Manual review is required before local runtime execution')
        if not isinstance(job.get('approved_by'), str) or not job['approved_by']:
            raise ValueError('Local runtime execution requires administrator approval')
        if not isinstance(job.get('approved_at'), str) or not job['approved_at']:
            raise ValueError('Local runtime execution requires approval evidence')
        pinned = job.get('approved_execution_provider')
        if not isinstance(pinned, dict) or pinned.get('name') != EXECUTOR_ID:
            raise ValueError('Approved execution provider does not match')
        graph = job.get('graph')
        if not isinstance(graph, dict):
            raise ValueError('Invalid local runtime graph')
        if self._digest(graph) != job.get('approved_graph_hash'):
            raise ValueError('Approved graph changed')
        if graph.get('environment') != 'dev':
            raise ValueError('Local runtime resources are restricted to the dev environment')
        if graph.get('edges'):
            raise ValueError('Local runtime jobs do not support dependency edges')
        if graph.get('failure_mode', 'none') != 'none':
            raise ValueError('Local runtime jobs do not support failure injection')
        strategy = graph.get('strategy') or {}
        if (type(strategy.get('canary', 1)) is not int or strategy.get('canary', 1) != 1
                or type(strategy.get('batch_size', 1)) is not int
                or not 1 <= strategy.get('batch_size', 1) <= MAX_NODES):
            raise ValueError('Unsupported local runtime execution strategy')
        name = graph.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.:-]{0,79}', name):
            raise ValueError('Invalid local runtime graph name')
        nodes = graph.get('nodes')
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
            raise ValueError('Local runtime graphs need 1..%d nodes' % MAX_NODES)
        identifiers, ports, entries = set(), set(), []
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                raise ValueError('Invalid local runtime node')
            node_id = node.get('id')
            if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id) or node_id in identifiers:
                raise ValueError('Invalid or duplicate local runtime node identifier')
            identifiers.add(node_id)
            plugin_id = node.get('plugin_id')
            if plugin_id not in PLUGINS:
                raise ValueError('Unsupported local runtime brick')
            supplied = node.get('config')
            if not isinstance(supplied, dict):
                raise ValueError('Local runtime node configuration must be an object')
            manifest = self._manifest(plugin_id)
            config = {key: value['default'] for key, value in manifest['schema']['properties'].items()
                      if isinstance(value, dict) and 'default' in value}
            config.update(manifest.get('defaults') or {})
            config.update(supplied)
            json.dumps(config, allow_nan=False)
            self._manifest(plugin_id, config)
            if plugin_id == 'local-oci':
                if config.get('targets') != ['app']:
                    raise ValueError('Local OCI is restricted to its local selector')
                port = config.get('port')
                if type(port) is not int or not PORT_MIN <= port <= PORT_MAX or port in ports:
                    raise ValueError('Invalid or duplicate local OCI port')
                ports.add(port)
            else:
                for key, low, high in (('cpu', 1, 2), ('memory_mb', 128, 1024), ('disk_mb', 64, 512)):
                    if type(config.get(key)) is not int or not low <= config[key] <= high:
                        raise ValueError('Invalid local QEMU resource bound')
            entries.append({'index': index, 'node_id': node_id, 'plugin_id': plugin_id,
                            'config': config})
        if any(entry['plugin_id'] == 'local-qemu' for entry in entries) and len(entries) != 1:
            raise ValueError('A local QEMU job contains exactly one virtual machine')
        return identity, graph, entries

    # ---------------------------------------------------------- ownership

    def _ledger(self):
        path = self.root / 'ownership.json'
        if path.is_symlink():
            raise ValueError('Unsafe local runtime ownership ledger')
        if not path.exists():
            return {'schema_version': 1, 'jobs': {}}
        value = self._read_json(path)
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'jobs'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['jobs'], dict)):
            raise ValueError('Invalid local runtime ownership ledger')
        for identity, row in value['jobs'].items():
            if (not isinstance(identity, str) or not JOB_ID.fullmatch(identity)
                    or not isinstance(row, dict) or not isinstance(row.get('claims'), dict)
                    or not isinstance(row.get('status'), str)):
                raise ValueError('Invalid local runtime ownership record')
        return value

    def _write_ledger(self, value):
        self._write_json(self.root / 'ownership.json', value)

    @staticmethod
    def _port_claim(port):
        return 'oci-port:%d' % port

    @staticmethod
    def _check_claim_free(ledger, claim, identity):
        for other, row in ledger['jobs'].items():
            if other != identity and claim in row['claims']:
                raise ValueError('Local runtime resource is owned by another job')

    # ------------------------------------------------------------- proc tools

    @staticmethod
    def _proc_state(pid):
        """Return (state, starttime) for a live/recent PID or None."""
        if type(pid) is not int or pid <= 1:
            return None
        try:
            raw = Path('/proc/%d/stat' % pid).read_text(encoding='utf-8')
            rest = raw.rsplit(')', 1)[1].split()
            return rest[0], int(rest[19])
        except (OSError, IndexError, ValueError):
            return None

    @classmethod
    def _pid_owns(cls, pid, starttime):
        value = cls._proc_state(pid)
        return bool(value and value[1] == starttime and value[0] not in ('Z', 'X'))

    @classmethod
    def _pid_gone(cls, pid, starttime):
        value = cls._proc_state(pid)
        if value is None or value[1] != starttime:
            return True
        return value[0] in ('Z', 'X')

    @staticmethod
    def _reap(pid):
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass

    @staticmethod
    def _run(argv, *, cwd=None, env=None, timeout=30, input_bytes=None, stdout=None):
        return subprocess.run(argv, cwd=cwd, env=env, input=input_bytes,
                              stdout=stdout if stdout is not None else subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)

    @staticmethod
    def _port_closed(port, seconds=0.0):
        deadline = time.monotonic() + seconds
        while True:
            with socket.socket() as probe:
                probe.settimeout(.5)
                closed = probe.connect_ex(('127.0.0.1', port)) != 0
            if closed or time.monotonic() >= deadline:
                return closed
            time.sleep(.1)

    @staticmethod
    def _http_marker(port, expected):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('GET', '/')
            response = connection.getresponse()
            body = response.read(4096).decode('utf-8', 'replace')
            return response.status, body
        finally:
            connection.close()

    @classmethod
    def _qmp(cls, path, commands):
        """Send fixed QMP commands over a short /proc/self/fd alias."""
        path = Path(path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(5)
                sock.connect('/proc/self/fd/%d/%s' % (fd, path.name))
                with sock.makefile('rwb') as stream:
                    greeting = json.loads(stream.readline())
                    if 'QMP' not in greeting:
                        raise RuntimeError('Unexpected QMP greeting')

                    def send(name):
                        stream.write(json.dumps({'execute': name}).encode() + b'\n')
                        stream.flush()
                        while True:
                            line = stream.readline()
                            if not line:
                                return None
                            item = json.loads(line)
                            if 'return' in item:
                                return item['return']
                            if 'error' in item:
                                raise RuntimeError('QMP command failed')

                    send('qmp_capabilities')
                    return [send(name) for name in commands]
        finally:
            os.close(fd)

    # ------------------------------------------------------------------ OCI

    def _runc(self, state_root, *args, timeout=20, capture=True):
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR')
               if key in os.environ}
        env['PATH'] = env.get('PATH', '/usr/bin:/bin')
        if not capture:
            # A detached container inherits runc's standard streams. Capturing
            # them would keep subprocess.run waiting until the container exits.
            return subprocess.run([str(self.runc_path), '--root', str(state_root), *args],
                                  env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=timeout)
        return self._run([str(self.runc_path), '--root', str(state_root), *args],
                         env=env, timeout=timeout)

    def _runc_inventory(self, state_root):
        result = self._runc(state_root, 'list', '--format', 'json')
        if result.returncode:
            raise RuntimeError('Cannot inspect OCI inventory')
        value = json.loads(result.stdout or '[]')
        if value is None:  # runc emits JSON null for an empty inventory.
            value = []
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise RuntimeError('Invalid OCI inventory')
        return value

    def _runc_state(self, state_root, container_id):
        result = self._runc(state_root, 'state', container_id, timeout=10)
        if result.returncode:
            return None
        try:
            value = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError('Invalid OCI state readback') from None
        if not isinstance(value, dict) or value.get('id') != container_id:
            raise RuntimeError('OCI state identity mismatch')
        return value

    def _start_oci(self, folder, entry, journal, config):
        job_id = journal['job_id']
        bundle = folder / ('oci-%d' % entry['index'])
        bundle.mkdir(mode=0o700)
        rootfs = bundle / 'rootfs'
        (rootfs / 'bin').mkdir(parents=True)
        (rootfs / 'www').mkdir()
        busybox = Path(config['assets']['busybox'])
        (rootfs / 'bin/busybox').write_bytes(busybox.read_bytes())
        (rootfs / 'bin/busybox').chmod(0o755)
        if self._sha_file(rootfs / 'bin/busybox') != config['sha256']['busybox']:
            raise RuntimeError('OCI payload copy did not match the pinned asset')
        marker = 'kw-local-oci:%s:%s' % (job_id, entry['node_id'])
        (rootfs / 'www/index.html').write_text(marker + '\n', encoding='utf-8')
        (rootfs / 'www/index.html').chmod(0o644)

        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR')
               if key in os.environ}
        result = self._run([str(self.runc_path), 'spec', '--rootless'], cwd=bundle, env=env, timeout=10)
        if result.returncode:
            raise RuntimeError('Cannot create the rootless OCI specification')
        spec_path = bundle / 'config.json'
        if spec_path.is_symlink() or not spec_path.is_file():
            raise RuntimeError('OCI specification is missing or unsafe')
        specification = self._read_json(spec_path, limit=1024 * 1024)
        linux = specification.get('linux') or {}
        process = specification.get('process') or {}
        namespaces = {row.get('type') for row in linux.get('namespaces', []) if isinstance(row, dict)}
        if (namespaces - {'pid', 'ipc', 'uts', 'mount', 'cgroup', 'user'} or 'network' in namespaces
                or not {'user', 'pid', 'mount'} <= namespaces):
            raise RuntimeError('OCI specification does not match the rootless baseline')
        if linux.get('uidMappings') != [{'containerID': 0, 'hostID': os.getuid(), 'size': 1}]:
            raise RuntimeError('OCI user-namespace mapping changed')
        if linux.get('gidMappings') != [{'containerID': 0, 'hostID': os.getgid(), 'size': 1}]:
            raise RuntimeError('OCI group-namespace mapping changed')
        if (specification.get('root') or {}).get('readonly') is not True:
            raise RuntimeError('OCI root filesystem must be read-only')
        if process.get('noNewPrivileges') is not True:
            raise RuntimeError('OCI no-new-privileges is required')
        if process.get('user') != {'uid': 0, 'gid': 0}:
            raise RuntimeError('OCI process identity changed')
        process.update(terminal=False, cwd='/', env=['PATH=/bin'],
                       args=['/bin/busybox', 'httpd', '-f', '-p', '127.0.0.1:%d' % entry['config']['port'],
                             '-h', '/www'])
        process['capabilities'] = {key: [] for key in ('bounding', 'effective', 'inheritable',
                                                       'permitted', 'ambient')}
        specification['process'] = process
        specification['hostname'] = 'kw-local'
        self._write_json(spec_path, specification)

        state_root = folder / 'runc-state'
        if state_root.is_symlink():
            raise RuntimeError('Unsafe OCI state root')
        state_root.mkdir(mode=0o700, exist_ok=True)
        state_root.chmod(0o700)
        container_id = 'kw-%s-%d' % (job_id[:12], entry['index'])
        if not CONTAINER_ID.fullmatch(container_id):
            raise RuntimeError('Invalid OCI container identity')
        if not self._port_closed(entry['config']['port']):
            raise RuntimeError('Local OCI port is already in use')
        os.close(os.open(bundle / 'config.json', os.O_RDONLY | os.O_NOFOLLOW))
        # Persist ownership before the runtime starts: even an interrupted or
        # ambiguous start can then be found by the checked removal path.
        evidence = {
            'kind': 'oci', 'node_id': entry['node_id'], 'plugin_id': 'local-oci',
            'container_id': container_id, 'bundle': bundle.relative_to(folder).as_posix(),
            'runc_root': state_root.relative_to(folder).as_posix(),
            'port': entry['config']['port'], 'pid': None, 'pid_starttime': None,
            'config_sha256': self._sha_file(spec_path), 'status': 'starting',
            'verified': False, 'started_at': self._now(),
        }
        journal['resources'].append(evidence)
        self._write_json(folder / 'journal.json', journal)
        result = self._runc(state_root, 'run', '--detach', '--bundle', str(bundle),
                            container_id, timeout=20, capture=False)
        if result.returncode:
            raise RuntimeError('Rootless OCI start failed')
        state = self._runc_state(state_root, container_id)
        if not state or state.get('status') != 'running' or type(state.get('pid')) is not int or state['pid'] <= 1:
            raise RuntimeError('OCI container is not running')
        pid = state['pid']
        proc = self._proc_state(pid)
        if proc is None or proc[0] in ('Z', 'X'):
            raise RuntimeError('OCI container process is absent')
        state_rootfs = Path(str(state.get('rootfs', '')))
        if (not state.get('bundle') or Path(state['bundle']).resolve() != bundle.resolve()
                or not state_rootfs.is_absolute() or not state_rootfs.resolve().is_relative_to(bundle.resolve())):
            raise RuntimeError('OCI state does not identify this bundle')
        evidence.update(pid=pid, pid_starttime=proc[1], status='running',
                        marker_sha256=hashlib.sha256((marker + '\n').encode()).hexdigest(),
                        state_sha256=self._digest(state))
        self._write_json(folder / 'journal.json', journal)
        status, body = self._http_marker(entry['config']['port'], marker)
        if status != 200 or body.strip() != marker:
            raise RuntimeError('OCI loopback verification failed')
        evidence.update(verified=True, http_status=status, observed_marker_sha256=hashlib.sha256(body.encode()).hexdigest())
        self._write_json(folder / 'journal.json', journal)
        return evidence

    def _remove_oci(self, folder, evidence):
        state_root = folder / evidence['runc_root']
        container_id = evidence['container_id']
        if not CONTAINER_ID.fullmatch(str(container_id)):
            raise RuntimeError('Invalid recorded OCI identity')
        if state_root.is_symlink():
            raise RuntimeError('Recorded OCI state path is unsafe')
        state_root = state_root.resolve()
        if not state_root.is_relative_to(folder.resolve()):
            raise RuntimeError('Recorded OCI state path is unsafe')
        state = self._runc_state(state_root, container_id)
        if state is not None:
            status = state.get('status')
            pid = state.get('pid')
            if status == 'running':
                if type(pid) is not int:
                    raise RuntimeError('OCI process identity is missing')
                if evidence.get('pid') is not None and pid != evidence['pid']:
                    raise RuntimeError('OCI process ownership changed')
                if evidence.get('pid_starttime') is not None:
                    current = self._proc_state(pid)
                    if current is None or current[1] != evidence['pid_starttime']:
                        raise RuntimeError('OCI process identity changed')
                self._runc(state_root, 'kill', container_id, 'TERM', timeout=10)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    current = self._runc_state(state_root, container_id)
                    if current is None or current.get('status') in ('stopped', 'created'):
                        break
                    time.sleep(.1)
            result = self._runc(state_root, 'delete', '--force', container_id, timeout=10)
            if result.returncode:
                raise RuntimeError('OCI removal failed')
        inventory = self._runc_inventory(state_root)
        if any(row.get('id') == container_id for row in inventory):
            raise RuntimeError('OCI container is still present')
        if self._runc_state(state_root, container_id) is not None:
            raise RuntimeError('OCI state remains after removal')
        if not self._port_closed(evidence['port'], seconds=2):
            raise RuntimeError('OCI loopback port remains open')
        evidence['removed_at'] = self._now()
        evidence['removal'] = {'inventory_absent': True, 'state_absent': True, 'port_closed': True}
        return evidence

    # ----------------------------------------------------------------- QEMU

    def _qemu_env(self, config, work):
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL')
               if key in os.environ}
        env.update(PATH=env.get('PATH', '/usr/bin:/bin'), HOME=str(work), TMPDIR=str(work),
                   LD_LIBRARY_PATH=config['assets']['qemu_lib'],
                   QEMU_MODULE_DIR=str(Path(config['assets']['qemu_lib']) / 'qemu'))
        return env

    def _start_qemu(self, folder, entry, journal, config):
        job_id = journal['job_id']
        work = folder / ('qemu-%d' % entry['index'])
        work.mkdir(mode=0o700)
        ram = work / 'ram'
        (ram / 'bin').mkdir(parents=True)
        busybox = Path(config['assets']['busybox'])
        (ram / 'bin/busybox').write_bytes(busybox.read_bytes())
        (ram / 'bin/busybox').chmod(0o755)
        if self._sha_file(ram / 'bin/busybox') != config['sha256']['busybox']:
            raise RuntimeError('QEMU payload copy did not match the pinned asset')
        init = ram / 'init'
        init.write_text(WORKLOADS['local-qemu']['init'], encoding='utf-8')
        init.chmod(0o755)
        archive = work / 'guest.cpio'
        env = self._qemu_env(config, work)
        with archive.open('wb') as stream:
            result = self._run([config['assets']['cpio'], '-o', '-H', 'newc', '--quiet'],
                               cwd=ram, env=env, timeout=15,
                               input_bytes=b'.\nbin\nbin/busybox\ninit\n', stdout=stream)
        if result.returncode or not archive.is_file() or archive.stat().st_size == 0:
            raise RuntimeError('QEMU initramfs creation failed')
        with archive.open('rb') as stream:
            listing = self._run([config['assets']['cpio'], '-it', '--quiet'], timeout=10,
                                input_bytes=stream.read())
        names = [line.strip().lstrip('./') for line in listing.stdout.decode('utf-8', 'replace').splitlines()]
        if listing.returncode or 'init' not in names or 'bin/busybox' not in names:
            raise RuntimeError('QEMU initramfs content is incomplete')
        disk = work / 'guest.qcow2'
        disk_mb = entry['config']['disk_mb']
        result = self._run([config['assets']['qemu_img'], 'create', '-q', '-f', 'qcow2',
                            str(disk), '%dM' % disk_mb], env=env, timeout=15)
        if result.returncode or not disk.is_file() or disk.is_symlink():
            raise RuntimeError('QEMU disk creation failed')
        info = self._run([config['assets']['qemu_img'], 'info', '--output=json', str(disk)], env=env, timeout=10)
        if info.returncode:
            raise RuntimeError('QEMU disk readback failed')
        document = json.loads(info.stdout)
        if document.get('format') != 'qcow2' or document.get('virtual-size') != disk_mb * 1024 * 1024:
            raise RuntimeError('QEMU disk does not match its baseline')
        argv = [
            config['assets']['qemu_system'],
            '-accel', 'tcg', '-m', str(entry['config']['memory_mb']),
            '-smp', str(entry['config']['cpu']), '-no-user-config', '-nodefaults',
            '-name', 'kw-local-%s-%d' % (job_id[:8], entry['index']),
            '-display', 'none', '-serial', 'file:console.log', '-monitor', 'none',
            '-qmp', 'unix:qmp.sock,server=on,wait=off', '-nic', 'none',
            '-L', config['assets']['qemu_share'], '-bios', config['assets']['bios'],
            '-kernel', config['assets']['kernel'], '-initrd', 'guest.cpio',
            '-append', WORKLOADS['local-qemu']['kernel_append'],
            '-drive', 'file=guest.qcow2,format=qcow2,if=virtio',
        ]
        log_fd = os.open(work / 'qemu.log', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            process = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log_fd, stderr=log_fd, close_fds=True,
                                       start_new_session=True)
        finally:
            os.close(log_fd)
        proc = self._proc_state(process.pid)
        if proc is None or proc[0] in ('Z', 'X'):
            process.terminate()
            raise RuntimeError('QEMU process did not start')
        evidence = {
            'kind': 'qemu', 'node_id': entry['node_id'], 'plugin_id': 'local-qemu',
            'work': work.relative_to(folder).as_posix(), 'pid': process.pid,
            'pid_starttime': proc[1], 'disk': 'guest.qcow2', 'qmp': 'qmp.sock',
            'console': 'console.log', 'argv_sha256': self._digest(argv),
            'disk_mb': disk_mb, 'status': 'starting', 'verified': False,
            'ansible': {'ready': False, 'reason': 'no guest network or SSH baseline'},
            'started_at': self._now(),
        }
        journal['resources'].append(evidence)
        self._write_json(folder / 'journal.json', journal)
        console = work / 'console.log'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                evidence['exit_code'] = process.returncode
                self._write_json(folder / 'journal.json', journal)
                raise RuntimeError('QEMU stopped before the guest boot marker')
            if console.is_file() and console.stat().st_size <= 1024 * 1024:
                if QEMU_BOOT_MARKER in console.read_text(encoding='utf-8', errors='replace'):
                    break
            time.sleep(.15)
        else:
            raise RuntimeError('QEMU did not reach the guest boot marker')
        if process.poll() is not None or not self._pid_owns(process.pid, proc[1]):
            raise RuntimeError('QEMU exited after the boot marker')
        qmp_socket = work / 'qmp.sock'
        if not qmp_socket.is_socket() or qmp_socket.is_symlink():
            raise RuntimeError('QEMU QMP socket is absent')
        status = self._qmp(qmp_socket, ['query-status'])[0]
        if not isinstance(status, dict) or status.get('running') is not True:
            raise RuntimeError('QEMU QMP does not report a running guest')
        evidence.update(status='running', verified=True, qmp_running=True,
                        qmp_status_sha256=self._digest(status),
                        console_sha256=self._sha_file(console),
                        guest_marker=QEMU_BOOT_MARKER)
        self._write_json(folder / 'journal.json', journal)
        return evidence

    def _remove_qemu(self, folder, evidence):
        work = folder / evidence['work']
        if work.is_symlink():
            raise RuntimeError('Recorded QEMU work path is unsafe')
        work = work.resolve()
        if not work.is_relative_to(folder.resolve()):
            raise RuntimeError('Recorded QEMU work path is unsafe')
        pid, starttime = evidence.get('pid'), evidence.get('pid_starttime')
        if type(pid) is not int or type(starttime) is not int:
            raise RuntimeError('Recorded QEMU process evidence is invalid')
        if self._pid_owns(pid, starttime):
            qmp_socket = work / evidence['qmp']
            quit_ok = False
            if qmp_socket.is_socket() and not qmp_socket.is_symlink():
                try:
                    self._qmp(qmp_socket, ['quit'])
                    quit_ok = True
                except Exception:
                    quit_ok = False
            deadline = time.monotonic() + (10 if quit_ok else 0)
            while time.monotonic() < deadline and not self._pid_gone(pid, starttime):
                time.sleep(.1)
            if not self._pid_gone(pid, starttime):
                # The PID and its start time still name our process; pidfd
                # prevents a recycled PID from being signalled.
                for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                    self._signal_owned(pid, starttime, sig)
                    deadline = time.monotonic() + wait
                    while time.monotonic() < deadline and not self._pid_gone(pid, starttime):
                        time.sleep(.1)
                    if self._pid_gone(pid, starttime):
                        break
            if not self._pid_gone(pid, starttime):
                raise RuntimeError('QEMU process did not stop')
        self._reap(pid)
        qmp_socket = work / evidence['qmp']
        if qmp_socket.exists():
            if qmp_socket.is_symlink() or not qmp_socket.is_socket():
                raise RuntimeError('Recorded QEMU socket is unsafe')
            qmp_socket.unlink()
        disk = work / evidence['disk']
        if disk.exists():
            if disk.is_symlink() or not disk.is_file():
                raise RuntimeError('Recorded QEMU disk is unsafe')
            evidence['disk_sha256'] = self._sha_file(disk)
            disk.unlink()
        if disk.exists() or qmp_socket.exists() or not self._pid_gone(pid, starttime):
            raise RuntimeError('QEMU removal could not be verified')
        evidence['removed_at'] = self._now()
        evidence['removal'] = {'process_stopped': True, 'disk_absent': True, 'socket_absent': True}
        return evidence

    @staticmethod
    def _signal_owned(pid, starttime, sig):
        if not LocalExecutor._pid_owns(pid, starttime):
            raise RuntimeError('QEMU process identity changed before signalling')
        if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            fd = os.pidfd_open(pid)
            try:
                signal.pidfd_send_signal(fd, sig)
            finally:
                os.close(fd)
        else:
            os.kill(pid, sig)

    # ------------------------------------------------------------- lifecycle

    def _remove_resources(self, folder, journal, emit):
        removed, failures = 0, []
        for evidence in reversed(journal.get('resources', [])):
            if evidence.get('removed_at'):
                removed += 1
                continue
            try:
                if evidence.get('kind') == 'oci':
                    self._remove_oci(folder, evidence)
                elif evidence.get('kind') == 'qemu':
                    self._remove_qemu(folder, evidence)
                else:
                    raise RuntimeError('Unknown local runtime resource')
                removed += 1
                emit('rollback', 'info', '%s: owned %s removed and absent' %
                     (evidence.get('node_id'), evidence.get('kind')))
            except Exception:
                failures.append(evidence.get('node_id'))
                emit('rollback', 'error', '%s: removal could not be verified' % evidence.get('node_id'))
        return removed, failures

    def execute(self, job, emit):
        emit = emit or (lambda *_: None)
        try:
            identity, graph, entries = self._review(job)
            config = self._pinned_configuration()
            self.runc_path = Path(config['assets']['runc'])
        except Exception:
            emit('execution', 'error', 'Local runtime preflight denied')
            return {'status': 'failed', 'resources': [],
                    'error': 'Local runtime preflight denied; inspect administrator configuration and approval'}

        with self._lock():
            folder = self._folder(identity)
            if folder.exists():
                return {'status': 'failed', 'resources': [],
                        'error': 'Existing local runtime job state requires manual review'}
            ledger = self._ledger()
            if identity in ledger['jobs']:
                return {'status': 'failed', 'resources': [],
                        'error': 'Local runtime job was already recorded; manual review required'}
            claims = {}
            try:
                for entry in entries:
                    if entry['plugin_id'] == 'local-oci':
                        claim = self._port_claim(entry['config']['port'])
                        self._check_claim_free(ledger, claim, identity)
                        claims[claim] = entry['node_id']
            except Exception:
                return {'status': 'failed', 'resources': [],
                        'error': 'Local runtime resource is owned by another job'}
            folder.mkdir(mode=0o700)
            journal = {
                'schema_version': 1, 'job_id': identity, 'graph_hash': self._digest(graph),
                'config_hash': self._config_hash(config), 'status': 'preparing',
                'created_at': self._now(), 'resources': [],
            }
            self._write_json(folder / 'journal.json', journal)
            ledger['jobs'][identity] = {'status': 'preparing', 'claims': dict(claims),
                                        'created_at': self._now(), 'updated_at': self._now()}
            self._write_ledger(ledger)
            try:
                for entry in entries:
                    emit('execution', 'info', '%s: preparing the pinned local workload' % entry['node_id'])
                    if entry['plugin_id'] == 'local-oci':
                        evidence = self._start_oci(folder, entry, journal, config)
                        emit('verify', 'info', 'OCI loopback HTTP 200 with the job marker')
                    else:
                        evidence = self._start_qemu(folder, entry, journal, config)
                        emit('verify', 'info', 'QEMU guest boot marker and QMP running verified')
                    evidence['verified'] = True
                    journal['status'] = 'running'
                    self._write_json(folder / 'journal.json', journal)
                journal['status'] = 'succeeded'
                journal['finished_at'] = self._now()
                ledger['jobs'][identity].update(status='succeeded', updated_at=self._now())
                self._write_json(folder / 'journal.json', journal)
                self._write_ledger(ledger)
            except Exception:
                journal['status'] = 'failed'
                journal['finished_at'] = self._now()
                self._write_json(folder / 'journal.json', journal)
                removed, failures = self._remove_resources(folder, journal, emit)
                journal['removed_resources'] = removed
                if failures:
                    journal['status'] = 'rollback_failed'
                    ledger['jobs'][identity].update(status='rollback_failed', updated_at=self._now())
                    self._write_json(folder / 'journal.json', journal)
                    self._write_ledger(ledger)
                    emit('execution', 'error', 'Local runtime cleanup could not be verified; manual review required')
                    return {'status': 'rollback_failed', 'requires_manual_review': True,
                            'resources': self._summaries(journal),
                            'error': 'Local runtime cleanup could not be verified; inspect private job evidence'}
                ledger['jobs'][identity].update(status='failed', claims={}, updated_at=self._now())
                self._write_json(folder / 'journal.json', journal)
                self._write_ledger(ledger)
                emit('execution', 'error', 'Local runtime execution failed; created resources were removed')
                return {'status': 'failed', 'resources': self._summaries(journal),
                        'error': 'Local runtime execution failed; created resources were removed'}
            return {'status': 'succeeded', 'operation': 'create-owned-local-resources',
                    'resources': self._summaries(journal),
                    'verified_resources': len(journal['resources'])}

    @staticmethod
    def _summaries(journal):
        rows = []
        for evidence in journal.get('resources', []):
            row = {'node_id': evidence.get('node_id'), 'plugin_id': evidence.get('plugin_id'),
                   'kind': evidence.get('kind'), 'verified': evidence.get('verified') is True,
                   'removed': bool(evidence.get('removed_at'))}
            if evidence.get('kind') == 'oci':
                row.update(endpoint='http://127.0.0.1:%d' % evidence['port'],
                           container_id=evidence['container_id'])
            else:
                row.update(state=('removed' if evidence.get('removed_at') else
                                  'running' if evidence.get('verified') else evidence.get('status')),
                           ansible=evidence.get('ansible'))
            rows.append(row)
        return rows

    def _job_identity(self, job):
        if not isinstance(job, dict):
            raise ValueError('Invalid local runtime job')
        identity = job.get('rollback_of', job.get('id'))
        if not isinstance(identity, str) or not JOB_ID.fullmatch(identity):
            raise ValueError('Invalid local runtime job identifier')
        pinned = job.get('approved_execution_provider')
        if not isinstance(pinned, dict) or pinned.get('name') != EXECUTOR_ID:
            raise ValueError('Approved execution provider does not match')
        return identity

    def _recorded(self, identity, config):
        folder = self._folder(identity)
        if not folder.is_dir() or folder.is_symlink():
            raise ValueError('No durable local runtime evidence for this job')
        journal = self._read_json(folder / 'journal.json')
        if (not isinstance(journal, dict) or journal.get('schema_version') != 1
                or journal.get('job_id') != identity or not isinstance(journal.get('resources'), list)):
            raise ValueError('Invalid local runtime journal')
        if journal.get('config_hash') != self._config_hash(config):
            raise ValueError('Administrator asset pins changed since this job ran')
        ledger = self._ledger()
        record = ledger['jobs'].get(identity)
        if not record or not isinstance(record.get('claims'), dict):
            raise ValueError('Local runtime ownership record is missing')
        return folder, journal, ledger, record

    def rollback(self, job, emit):
        emit = emit or (lambda *_: None)
        try:
            identity = self._job_identity(job)
            config = self._pinned_configuration()
            with self._lock():
                folder, journal, ledger, record = self._recorded(identity, config)
                if journal['status'] == 'rolled_back':
                    return {'status': 'rolled_back', 'verified_removed': 0, 'already_absent': True}
                if journal['status'] not in ('succeeded', 'rollback_failed', 'failed'):
                    raise ValueError('Job state does not permit owned-resource removal')
                removed, failures = self._remove_resources(folder, journal, emit)
                journal['removed_resources'] = removed
                if failures:
                    journal['status'] = 'rollback_failed'
                    record.update(status='rollback_failed', updated_at=self._now())
                    self._write_json(folder / 'journal.json', journal)
                    self._write_ledger(ledger)
                    return {'status': 'rollback_failed', 'requires_manual_review': True,
                            'resources': self._summaries(journal),
                            'error': 'Owned local-resource removal was not verified; inspect private evidence'}
                journal['status'] = 'rolled_back'
                journal['rolled_back_at'] = self._now()
                record.update(status='rolled_back', claims={}, updated_at=self._now())
                self._write_json(folder / 'journal.json', journal)
                self._write_ledger(ledger)
                emit('rollback', 'info', 'All owned local runtime resources are absent')
                return {'status': 'rolled_back', 'operation': 'remove-owned-local-resources',
                        'verified_removed': removed, 'resources': self._summaries(journal)}
        except Exception:
            emit('rollback', 'error', 'Owned local-resource removal was not verified')
            return {'status': 'rollback_failed', 'requires_manual_review': True,
                    'error': 'Owned local-resource removal was not verified; inspect private job evidence'}

    def decommission(self, job, emit=None):
        """Retire one succeeded service and delete its private job state.

        The worker accepts only the single verified ``decommissioned`` status;
        any missing evidence or failed absence check raises instead of being
        reported as a successful retirement.
        """
        emit = emit or (lambda *_: None)
        identity = self._job_identity(job)
        config = self._pinned_configuration()
        with self._lock():
            folder = self._folder(identity)
            ledger = self._ledger()
            record = ledger['jobs'].get(identity)
            if not folder.exists():
                if not record or record.get('status') != 'decommissioned' or record.get('claims'):
                    raise RuntimeError('Local runtime job evidence is missing; manual review required')
                return {'status': 'decommissioned', 'verified_removed': 0,
                        'already_absent': True, 'operation': 'retire-owned-local-resources'}
            if folder.is_symlink() or not folder.is_dir():
                raise RuntimeError('Unsafe local runtime job state')
            journal = self._read_json(folder / 'journal.json')
            if (not isinstance(journal, dict) or journal.get('job_id') != identity
                    or journal.get('config_hash') != self._config_hash(config)):
                raise RuntimeError('Local runtime evidence is missing or changed')
            if journal.get('status') not in ('succeeded', 'rolled_back', 'failed'):
                raise RuntimeError('Job state does not permit retirement')
            removed, failures = self._remove_resources(folder, journal, emit)
            if failures:
                raise RuntimeError('Owned local-resource removal was not verified')
            for path in folder.rglob('*'):
                if path.is_symlink():
                    raise RuntimeError('Symlink in local runtime job state')
            journal['status'] = 'decommissioned'
            journal['decommissioned_at'] = self._now()
            self._write_json(folder / 'journal.json', journal)
            shutil.rmtree(folder)
            if folder.exists():
                raise RuntimeError('Local runtime job state remains after retirement')
            if record:
                record.update(status='decommissioned', claims={}, updated_at=self._now())
            else:
                ledger['jobs'][identity] = {'status': 'decommissioned', 'claims': {},
                                            'created_at': self._now(), 'updated_at': self._now()}
            self._write_ledger(ledger)
            emit('decommission', 'info', 'Owned local runtime resources and private job state removed')
            return {'status': 'decommissioned', 'operation': 'retire-owned-local-resources',
                    'verified_removed': removed, 'job_state_removed': True}
