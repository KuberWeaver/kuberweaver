"""Opt-in vSphere saved-plan boundary; no app imports or generic Jobs branches.

Trusted local code, not a sandbox. Raw plans/state/output are never API results.
vmware/vsphere 2.18.0 cannot guarantee a stopped create; preparation fails closed.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
from urllib.parse import urlsplit
from typing import Any
import zipfile

import jsonschema
from referencing import Registry
import yaml

ROOT = Path(__file__).resolve().parents[2]
TOFU_SHA256 = '9ce9173bf161f6466ef57dab7edebe8f55089fdf81caa967415323c25d004990'
CLI_CONFIG = b'disable_checkpoint = true\n'
ADDRESS = 'vsphere_virtual_machine.vm'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class ProvisioningError(ValueError):
    """Only fixed, locally authored messages may cross the public boundary."""


class SavedPlanExecutor:
    requires_saved_plan = True
    supports_rollback = False

    def __init__(self, state_dir, template_dir, binary=None, runner=None):
        self.state_dir = Path(state_dir).absolute()
        directory = Path(template_dir).absolute()
        # Installed first-party extensions may still pass their old catalogue path.
        # Translate only those exact paths, never a custom template directory.
        if directory.parent == ROOT / 'plugins' and directory.name in {'vmware-vm', 'vsphere-vm'}:
            directory = ROOT / 'modules' / 'resources' / directory.name
        self.template_dir = directory
        self.binary = Path(binary or ROOT / 'tools/tofu').absolute()
        self.runner = runner or subprocess.run
        self._active_job: tuple[int, str] | None = None  # Current flock-owning execution thread only.
        self.connections: Any = None  # Injected by resolve_provider, not by the SDK.
        self.secret_resolver = None
        # Explicit admin-controlled trust, never paths taken from graph/connection.
        self.ca_bundle = Path('/etc/ssl/certs/ca-certificates.crt')

    @contextmanager
    def _errors(self):
        try:
            yield
        except ProvisioningError:
            raise
        except (Exception, SystemExit):
            raise ProvisioningError('Provisioning input or provider unavailable; raw output suppressed') from None

    @staticmethod
    def _credentials(raw):
        try:
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {'username', 'password'}
                    or any(not isinstance(v, str) or not 1 <= len(v) <= 16384 or '\0' in v for v in value.values())):
                raise ValueError()
            return value
        except Exception:
            raise ProvisioningError('Invalid connection credential document; username and password only') from None

    def _tls(self, connection):
        try:
            endpoint = urlsplit(connection['endpoint'])
            if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username
                    or endpoint.password or endpoint.path or endpoint.query or endpoint.fragment):
                raise ProvisioningError('Invalid origin')
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cafile=str(self.ca_bundle))
            with socket.create_connection((endpoint.hostname, endpoint.port or 443), timeout=10) as raw:
                with context.wrap_socket(raw, server_hostname=endpoint.hostname) as secure:
                    leaf = hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest()
            if connection.get('tls_sha256') and connection['tls_sha256'] != leaf:
                raise ProvisioningError('TLS identity changed')
            return leaf
        except Exception:
            raise ProvisioningError('PKI required: trusted certificate chain and endpoint hostname must validate; thumbprint-only trust is insufficient') from None

    @staticmethod
    def _safe(path):
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ProvisioningError('Symlinked provisioning path is forbidden')
        if path.exists() and (not (path.is_file() or path.is_dir())
                              or (path.is_file() and path.stat().st_nlink != 1)):
            raise ProvisioningError('Unsafe provisioning file type or hardlink')

    def _artifacts(self, folder):
        allowed = {'main.tf', 'terraform.tfvars.json', 'cli.tfrc', '.terraform.lock.hcl',
                   '.terraform', '.terraform.d', 'create.plan', 'review.json', 'attempt.json', 'verified.json',
                   'terraform.tfstate', 'terraform.tfstate.backup', '.terraform.tfstate.lock.info'}
        provider = '.terraform/providers/registry.opentofu.org/vmware/vsphere/2.18.0/linux_amd64'
        binaries = {provider + '/' + name for name in (
            'terraform-provider-vsphere_v2.18.0', 'LICENSE', 'README.md', 'CHANGELOG.md')}
        permitted = binaries | {provider + '.lock'} | {str(p) for p in Path(provider).parents} | {provider}
        for path in (folder, *folder.rglob('*')):
            self._safe(path)
            if path.stat().st_uid != os.getuid():
                raise ProvisioningError('Foreign provisioning artifact owner')
            relative = str(path.relative_to(folder))
            if relative != '.' and (relative.split('/')[0] not in allowed
                    or (relative.startswith('.terraform/') and relative not in permitted)
                    or relative.startswith('.terraform.d/')
                    or (path.is_dir() and relative not in permitted | {'.terraform.d'})):
                raise ProvisioningError('Unexpected OpenTofu artifact')
        if (folder / 'cli.tfrc').read_bytes() != CLI_CONFIG:
            raise ProvisioningError('OpenTofu CLI configuration changed')

    @contextmanager
    def _lock(self):
        self._safe(self.state_dir)
        self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state_dir.chmod(0o700)
        self._safe(self.state_dir / '.lock')
        fd = os.open(self.state_dir / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    @staticmethod
    def _write(path, value):
        data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _pins(self, folder):
        self._artifacts(folder)
        paths = [self.binary, self.ca_bundle, Path(__file__),
                 self.template_dir / 'main.tf', self.template_dir / 'manifest.yaml', self.template_dir / '.terraform.lock.hcl',
                 folder / 'main.tf', folder / 'terraform.tfvars.json', folder / 'cli.tfrc']
        paths += [p for p in [folder / '.terraform.lock.hcl'] if p.exists()]
        paths += sorted(p for p in (folder / '.terraform').rglob('*') if p.is_file())
        for path in paths:
            self._safe(path)
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

    @staticmethod
    def _screen(data, credentials):
        data = data.encode() if isinstance(data, str) else data
        for value in credentials.values():
            variants = [value.encode(), json.dumps(value, ensure_ascii=True)[1:-1].encode(),
                        json.dumps(value, ensure_ascii=False)[1:-1].encode()]
            if any(variant in data for variant in variants):
                raise ProvisioningError('Secret evidence detected; raw artifacts remain private and are not approved')
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 128 * 1024**2:
                    raise ProvisioningError('Saved plan archive exceeds review limit')
                for name in archive.namelist():
                    SavedPlanExecutor._screen(archive.read(name), credentials)

    def _run(self, folder, connection, credentials, *args, credential_digest, accepted=(0,),
             pins=None, plan_sha256=None, binding=None):
        self._artifacts(folder)
        pins = self._pins(folder) if pins is None else pins
        binding = binding or self.connections.fingerprint(connection['id'])
        def guard(after=False):
            with self.connections.access(connection['id']) as (_, current_raw):
                if (self.connections.fingerprint(connection['id']) != binding
                        or hashlib.sha256(current_raw).hexdigest() != credential_digest
                        or self._credentials(current_raw) != credentials):
                    raise ProvisioningError('Connection or credential binding changed before command')
            current = self._pins(folder)
            matches = (all(current.get(k) == v for k, v in pins.items())
                       if after and args[0] == 'init' else current == pins)
            if not matches or current[str(self.binary)] != TOFU_SHA256:
                raise ProvisioningError('Pinned execution input changed or OpenTofu binary is unverified')
            if plan_sha256 and hashlib.sha256((folder / 'create.plan').read_bytes()).hexdigest() != plan_sha256:
                raise ProvisioningError('Saved binary plan changed')
        guard()
        self._stopped_contract()
        self._tls(connection)
        guard()  # TLS can block; recheck immediately before crossing the process boundary.
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(folder), 'TMPDIR': str(folder),
               'TF_IN_AUTOMATION': '1', 'TF_INPUT': '0', 'CHECKPOINT_DISABLE': '1',
               'TF_CLI_CONFIG_FILE': str(folder / 'cli.tfrc'),
               'SSL_CERT_FILE': str(self.ca_bundle), 'SSL_CERT_DIR': str(folder / 'no-extra-ca'),
               'VSPHERE_USER': credentials['username'], 'VSPHERE_PASSWORD': credentials['password']}
        try:
            result = self.runner([str(self.binary), *args], cwd=folder, env=env,
                                 capture_output=True, text=True, timeout=300)
        finally:
            self._artifacts(folder)
            for path in folder.rglob('*'):
                if path.is_file():
                    path.chmod(0o700 if path.stat().st_mode & 0o111 else 0o600)
                    if '.terraform' not in path.relative_to(folder).parts:
                        self._screen(path.read_bytes(), credentials)
                elif path.is_dir():
                    path.chmod(0o700)
            guard(after=True)
        if result.returncode not in accepted:
            raise RuntimeError('OpenTofu command failed; raw provider output suppressed')
        if args[0] == 'show':
            self._screen(result.stdout, credentials)
        return result.stdout

    @staticmethod
    def _actions(document):
        def prior_managed(value):
            if isinstance(value, dict):
                return value.get('mode') == 'managed' or any(prior_managed(v) for v in value.values())
            return isinstance(value, list) and any(prior_managed(v) for v in value)
        error = 'Plan must be create-only: exactly one new vsphere_virtual_machine.vm, no import, replacement, deletion or drift'
        if document.get('resource_drift') or prior_managed(document.get('prior_state')):
            raise ProvisioningError(error)
        managed, seen = [], set()
        for item in document.get('resource_changes', []):
            address, change = item.get('address'), item.get('change', {})
            if address in seen or change.get('importing') or change.get('replace_paths'):
                raise ProvisioningError(error)
            seen.add(address)
            if item.get('mode') == 'data' and change.get('actions') in (['read'], ['no-op']):
                continue
            if (item.get('mode') != 'managed' or address != ADDRESS or change.get('actions') != ['create']
                    or change.get('before') is not None):
                raise ProvisioningError(error)
            managed.append(item)
        if len(managed) != 1:
            raise ProvisioningError(error)
        return {ADDRESS: ['create']}

    def _inventory(self, reviewed, connection, raw):
        try:
            if connection.get('enabled') is not True:
                raise ProvisioningError()
            inventory = self.connections.driver(connection).discover(connection, raw)
            server, config = inventory['server'], reviewed['config']
            if (server.get('api_type') != 'HostAgent' or not str(server.get('version', '')).startswith('8.')
                    or server.get('write_api_supported') is not True
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', server.get('host_id', ''))):
                raise ProvisioningError()
            for group, wanted in [('datastores', [config['datastore_id']]),
                                  ('networks', config['network_ids']), ('resource_pools', [config['resource_pool_id']])]:
                for identity in wanted:
                    found = [item for item in inventory[group] if item.get('id') == identity]
                    if len(found) != 1:
                        raise ProvisioningError()
                    if group == 'datastores' and (found[0].get('accessible') is not True
                            or type(found[0].get('free_bytes')) is not int
                            or found[0]['free_bytes'] < config['disk_gb'] * 1024**3):
                        raise ProvisioningError()
            if any(vm.get('name') == reviewed['name'] for vm in inventory['virtual_machines']):
                raise ProvisioningError()
            return inventory
        except Exception:
            raise ProvisioningError('vSphere prerequisites failed: enabled licensed ESXi 8, exact references, capacity and absent VM required') from None

    def prepare_plan(self, job, emit):
        reviewed = self.review(job)
        with self._errors(), self._lock():
            folder = self.state_dir / reviewed['job_id']
            if folder.exists():
                raise ProvisioningError('Existing job state cannot be resumed or replanned')
            connection_id = reviewed['config']['connection_id']
            binding = self.connections.fingerprint(connection_id)  # Local pin, before vault/TLS I/O.
            origin = {key: value for key, value in self.connections.get(connection_id).items() if key != 'observation'}
            def check_connection(snapshot=None):
                if (self.connections.fingerprint(connection_id) != binding
                        or (snapshot is not None and {key: value for key, value in snapshot.items()
                                                     if key != 'observation'} != origin)):
                    raise ProvisioningError('Connection binding changed during plan preparation')
            check_connection()  # Refuse drift between the local fingerprint and metadata reads.
            with self.connections.access(connection_id) as (connection, raw):
                check_connection(connection)  # access() may have blocked while resolving credentials.
                self._tls(connection)
                check_connection(connection)
                self._stopped_contract()
                credentials = self._credentials(raw)
                credential_digest = hashlib.sha256(raw).hexdigest()
                inventory = self._inventory(reviewed, connection, raw)
                check_connection(connection)
                folder.mkdir(mode=0o700)
                for name in ('main.tf', '.terraform.lock.hcl'):
                    self._write(folder / name, (self.template_dir / name).read_bytes())
                self._write(folder / 'cli.tfrc', CLI_CONFIG)
                config = reviewed['config']
                endpoint = urlsplit(connection['endpoint'])
                variables = {key: config[key] for key in ('cpu', 'memory_mb', 'disk_gb', 'datastore_id', 'network_ids', 'resource_pool_id')}
                variables.update(name=reviewed['name'], server=endpoint.netloc,
                                 host_id=inventory['server']['host_id'])
                self._write(folder / 'terraform.tfvars.json', variables)
                self._run(folder, connection, credentials, 'init', '-backend=false', '-input=false', '-no-color', '-lockfile=readonly',
                          credential_digest=credential_digest, binding=binding)
                pins = self._pins(folder)
                if not json.loads(self._run(folder, connection, credentials, 'validate', '-json',
                                           credential_digest=credential_digest, pins=pins, binding=binding))['valid']:
                    raise ProvisioningError('Reviewed template validation failed')
                self._run(folder, connection, credentials, 'plan', '-input=false', '-no-color',
                          '-detailed-exitcode', '-out=create.plan', credential_digest=credential_digest,
                          accepted=(2,), pins=pins, binding=binding)
                plan_sha256 = hashlib.sha256((folder / 'create.plan').read_bytes()).hexdigest()
                document = json.loads(self._run(folder, connection, credentials, 'show', '-json', 'create.plan',
                                               credential_digest=credential_digest, pins=pins,
                                               plan_sha256=plan_sha256, binding=binding))
                actions = self._actions(document)
                summary = {'name': reviewed['name'], 'cpu': config['cpu'], 'memory_mb': config['memory_mb'],
                           'disk_gb': config['disk_gb'], 'os_installed': False, 'power_state': 'poweredOff'}
                bindings = {'connection_id': config['connection_id'],
                            'connection_fingerprint': binding}
                signed = {'reviewed': reviewed, 'pins': pins,
                          'plan_sha256': plan_sha256,
                          'credential_digest': credential_digest,
                          'actions': actions, 'summary': summary, 'bindings': bindings}
                if (self._pins(folder) != pins
                        or hashlib.sha256((folder / 'create.plan').read_bytes()).hexdigest() != plan_sha256
                        or self.connections.fingerprint(config['connection_id']) != binding):
                    raise ProvisioningError('Saved plan or preparation inputs changed before review publication')
                self._write(folder / 'review.json', signed)
                emit('plan', 'info', 'Private saved VM creation plan prepared; no mutation performed')
                return {key: signed[key] for key in ('actions', 'summary', 'bindings')} | {'plan_digest': digest(signed)}

    @staticmethod
    def _stopped_contract():
        # Version-pinned upstream bare-create calls PowerOn; power_state is computed.
        # No runtime flag may waive this gate. A reviewed scope change is required.
        raise ProvisioningError('vmware/vsphere 2.18.0 cannot create a guaranteed stopped VM; provisioning is blocked')

    def read(self):
        return {'provider': 'vsphere-provisioner', 'capability': 'planned', 'status': 'blocked',
                'binary_present': self.binary.is_file(), 'requires_saved_plan': True,
                'supports_rollback': False, 'limitations': [
                    'vmware/vsphere 2.18.0 always powers on a bare-created VM',
                    'strict endpoint PKI required; SDK thumbprint alone is insufficient',
                    'no installed OS, bootstrap, import, update, delete or rollback',
                    'not independently applied or ESXi-certified']}

    def review(self, job):
        identity = job.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', identity):
            raise ProvisioningError('Invalid provisioning job identifier')
        graph = job.get('graph', {})
        nodes = graph.get('nodes', [])
        # Graph always contains strategy metadata; one VM has no rollout stages.
        if (graph.get('environment') != 'dev' or len(nodes) != 1 or graph.get('edges')
                or graph.get('failure_mode', 'none') != 'none'):
            raise ProvisioningError('Only one independent dev VM create is supported')
        node = nodes[0]
        if node.get('plugin_id') != 'vsphere-vm':
            raise ProvisioningError('Only the reviewed vsphere-vm blueprint is supported')
        manifest = yaml.safe_load((self.template_dir / 'manifest.yaml').read_text())
        if any(ref in json.dumps(manifest['schema']) for ref in ('$ref', '$dynamicRef', '$recursiveRef')):
            raise ProvisioningError('Schema references are unsupported; approval validation must remain local')
        config = {**manifest['defaults'], **node.get('config', {})}
        try:
            json.dumps(config, allow_nan=False)
            jsonschema.validate(config, manifest['schema'], registry=Registry())
        except (ValueError, jsonschema.ValidationError):
            raise ProvisioningError('Invalid closed vSphere VM configuration') from None
        return {'job_id': identity, 'name': 'kw-' + identity[:12] + '-' + config['vm_name'],
                'graph_digest': digest(graph), 'config': config}

    def _checked(self, job, folder, raw=None, *, active=False):
        reviewed = self.review(job)
        if not active and any((folder / name).exists() for name in ('attempt.json', 'terraform.tfstate', 'terraform.tfstate.backup')):
            raise ProvisioningError('Existing state or application attempt requires manual review')
        signed = json.loads((folder / 'review.json').read_text())
        public = {key: signed[key] for key in ('actions', 'summary', 'bindings')} | {'plan_digest': digest(signed)}
        if (job.get('prepared_plan') != public or reviewed != signed['reviewed']
                or signed['pins'] != self._pins(folder)
                or (raw is not None and signed['credential_digest'] != hashlib.sha256(raw).hexdigest())
                or signed['bindings']['connection_fingerprint'] != self.connections.fingerprint(reviewed['config']['connection_id'])
                or signed['plan_sha256'] != hashlib.sha256((folder / 'create.plan').read_bytes()).hexdigest()):
            raise ProvisioningError('Private saved plan or approved inputs changed')
        return signed

    def validate_plan(self, job):
        """Strictly local approval check; never vault, credentials, TLS or SDK I/O."""
        reviewed = self.review(job)
        with self._errors():  # Read-only/reentrant: the command guard may already hold flock.
            self._stopped_contract()
            self._checked(job, self.state_dir / reviewed['job_id'],
                          active=self._active_job == (threading.get_ident(), reviewed['job_id']))

    def _verified(self, folder, reviewed, connection, raw, signed):
        state = json.loads(self._run(folder, connection, self._credentials(raw), 'show', '-json', 'terraform.tfstate',
                                    credential_digest=signed['credential_digest'],
                                    pins=signed['pins'], plan_sha256=signed['plan_sha256'],
                                    binding=signed['bindings']['connection_fingerprint']))
        def resources(module):
            return module.get('resources', []) + [r for child in module.get('child_modules', []) for r in resources(child)]
        managed = [r for r in resources(state['values']['root_module']) if r.get('mode') == 'managed']
        if len(managed) != 1 or managed[0]['address'] != ADDRESS:
            raise ProvisioningError('Unexpected managed state after application')
        values = managed[0]['values']
        uuid = values.get('uuid') or values.get('id')
        if (values.get('uuid') and values.get('id') and values['uuid'] != values['id']
                or not isinstance(uuid, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', uuid)):
            raise ProvisioningError('Missing valid VM UUID in application state')
        inventory = self.connections.driver(connection).discover(connection, raw)
        vms = [vm for vm in inventory['virtual_machines'] if vm.get('uuid') == uuid]
        expected = {'name': reviewed['name'], 'cpu': reviewed['config']['cpu'],
                    'memory_mb': reviewed['config']['memory_mb'], 'power_state': 'poweredOff'}
        if (len(vms) != 1 or any(vms[0].get(k) != v for k, v in expected.items())
                or values.get('name') != expected['name'] or values.get('num_cpus') != expected['cpu']
                or values.get('memory') != expected['memory_mb'] or values.get('power_state') != 'off'):
            raise ProvisioningError('Independent VM UUID/name/resources/power readback mismatch')
        return {'source': 'independent-sdk', 'uuid': uuid, **expected, 'os_installed': False}

    def execute(self, job, emit):
        folder, reviewed, attempted, active = None, {}, False, False
        try:
            reviewed = self.review(job)
            folder = self.state_dir / reviewed['job_id']
            with self._errors(), self._lock(), self.connections.access(reviewed['config']['connection_id']) as (connection, raw):
                self._stopped_contract()
                self._tls(connection)
                signed = self._checked(job, folder, raw)
                if (not job.get('approved_by') or not job.get('approved_at')
                        or job.get('approved_graph_hash') != reviewed['graph_digest']
                        or job.get('approved_execution_provider', {}).get('name') != 'vsphere-provisioner'
                        or job.get('approved_prepared_plan') != digest(job['prepared_plan'])):
                    raise ProvisioningError('Explicit approval of this exact saved plan is required')
                self._inventory(reviewed, connection, raw)
                self._active_job, active = (threading.get_ident(), reviewed['job_id']), True
                self._write(folder / 'attempt.json', {'plan_digest': digest(signed), 'requires_manual_review': True})
                attempted = True
                self._run(folder, connection, self._credentials(raw), 'apply', '-input=false', '-no-color', 'create.plan',
                          credential_digest=signed['credential_digest'],
                          pins=signed['pins'], plan_sha256=signed['plan_sha256'],
                          binding=signed['bindings']['connection_fingerprint'])
                evidence = self._verified(folder, reviewed, connection, raw, signed)
                self._write(folder / 'verified.json', evidence)
                return {'status': 'succeeded', 'evidence': [evidence], 'endpoints': [],
                        'requires_manual_review': False, 'can_rollback': False}
        except (Exception, KeyboardInterrupt):
            uncertain = attempted or bool(folder and any((folder / name).exists() for name in (
                'attempt.json', 'terraform.tfstate', 'terraform.tfstate.backup')))
            return {'status': 'failed', 'error': 'vSphere saved-plan execution refused or failed; raw output suppressed',
                    'requires_manual_review': uncertain, 'can_rollback': False, 'evidence': [], 'endpoints': []}
        finally:
            if active and self._active_job == (threading.get_ident(), reviewed['job_id']):
                self._active_job = None

    def can_rollback(self, job=None):
        return False

    def rollback(self, job, emit):
        return {'status': 'rollback_failed', 'error': 'vSphere create-only: rollback and destroy are unsupported',
                'requires_manual_review': True, 'can_rollback': False}
