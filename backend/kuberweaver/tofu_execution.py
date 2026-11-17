"""Opt-in OpenTofu boundary. One stopped, blank-disk libvirt VM per job.

This is trusted code, not a sandbox. Graphs never supply paths, hosts or HCL.
Plans must be created before approval and their returned digest explicitly pinned.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from urllib.parse import quote
from pathlib import Path
import re
import subprocess
from xml.etree import ElementTree as ET

try:
    from .configuration import require_ssh_config, ssh_config
except ImportError:  # Standalone loading used by provisioning tests.
    from backend.kuberweaver.configuration import require_ssh_config, ssh_config

ROOT = Path(__file__).resolve().parents[2]
SSH_CONFIG = ssh_config()
ALIAS = 'infra-hypervisor'
PROVIDER_VERSION = '0.9.9'
ADDRESSES = {'libvirt_domain.vm', 'libvirt_volume.disk'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value):
        raise ValueError('Invalid OpenTofu job/node identifier')
    return value


class TofuExecutor:
    def __init__(self, state_dir, *, binary=None, runner=None, template_dir=None):
        self.state_dir = Path(state_dir).absolute()
        self.binary = Path(binary or ROOT / 'tools/tofu').absolute()
        self.runner = runner or subprocess.run
        self.template_dir = Path(template_dir or ROOT / 'plugins/libvirt-vm')

    def read(self):
        """Cheap registry/status metadata; never claims applied capability."""
        return {'provider': 'opentofu', 'capability': 'planned',
                'binary_present': self.binary.is_file(), 'read_only': True,
                'status': 'preflight-required' if self.binary.is_file() else 'unconfigured',
                'limitations': ['one stopped dev VM with a blank disk', 'no OS bootstrap or boot verification',
                                'saved plan approval integration required', 'separately approved owned-resource removal only']}

    def preflight(self, *, remote=False):
        report = {**self.read(), 'mutations_performed': False, 'checks': {}}
        if self.binary.is_file():
            try:
                result = self.runner([str(self.binary), 'version', '-json'], capture_output=True, text=True, timeout=15)
                report['checks']['tofu'] = result.returncode == 0
                if result.returncode == 0:
                    report['tofu_version'] = json.loads(result.stdout).get('terraform_version')
            except Exception:
                report['checks']['tofu'] = False
        else:
            report['checks']['tofu'] = False
        report['checks']['trusted_template'] = (self.template_dir / 'main.tf').is_file()
        report['checks']['ssh_wrapper'] = os.access(self.template_dir / 'ssh-libvirt', os.X_OK)
        if remote:
            for key, command in {
                'libvirt': 'virsh -c qemu:///system version',
                'pool': 'virsh -c qemu:///system pool-info infra-lab',
                'network': 'virsh -c qemu:///system net-info lab-nested',
                'kvm': 'test -r /dev/kvm && test -w /dev/kvm',
            }.items():
                try:
                    self._ssh(command)
                    report['checks'][key] = True
                except Exception:
                    report['checks'][key] = False
        report['status'] = 'preflight-passed-not-applied' if all(report['checks'].values()) else 'blocked'
        return report

    def review(self, job):
        identifier(job.get('id'))
        graph = job.get('graph', {})
        nodes = graph.get('nodes', [])
        if graph.get('environment') != 'dev' or len(nodes) != 1 or graph.get('edges', []) or graph.get('failure_mode', 'none') != 'none':
            raise ValueError('Only one independent dev VM create is supported')
        node = nodes[0]
        identifier(node.get('id'))
        if node.get('plugin_id') != 'libvirt-vm':
            raise ValueError('Only the reviewed libvirt-vm template is supported')
        config = {'cpu': 1, 'memory_mb': 128, 'disk_gb': 1}
        supplied = node.get('config', {})
        if set(supplied) - set(config):
            raise ValueError('Unsupported VM configuration fields')
        config.update(supplied)
        for key, low, high in [('cpu', 1, 2), ('memory_mb', 64, 512), ('disk_gb', 1, 2)]:
            if type(config[key]) is not int or not low <= config[key] <= high:
                raise ValueError('Unsupported VM resource bounds')
        return {'name': 'kw-' + digest([job['id'], graph])[:24], 'config': config,
                'graph_hash': digest(graph), 'job_id': job['id']}

    @contextmanager
    def _lock(self):
        # No paths from graph. Refuse symlink ancestors, including state root.
        for path in [self.state_dir, *self.state_dir.parents]:
            if path.is_symlink():
                raise ValueError('Symlinked state path is forbidden')
        self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state_dir.chmod(0o700)
        fd = os.open(self.state_dir / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def _folder(self, identity):
        folder = self.state_dir / identifier(identity)
        if folder.is_symlink():
            raise ValueError('Symlinked job state is forbidden')
        return folder

    @staticmethod
    def _write(path, value):
        data = json.dumps(value, sort_keys=True, indent=2) if not isinstance(value, str) else value
        fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(data)
        path.chmod(0o600)

    def _artifacts(self, folder):
        allowed = {'main.tf', 'terraform.tfvars.json', '.terraform.lock.hcl', 'cli.tfrc',
                   '.terraform', 'create.plan', 'destroy.plan', 'refresh.plan', 'review.json',
                   'owned.json', 'attempt.json', 'remove-attempt.json', 'removal.json',
                   'terraform.tfstate', 'terraform.tfstate.backup', '.terraform.tfstate.lock.info'}
        for path in folder.iterdir():
            if path.is_symlink() or path.name not in allowed:
                raise ValueError('Unexpected OpenTofu artifact')
            if path.is_dir() and path.name != '.terraform':
                raise ValueError('Unexpected OpenTofu directory')
        if (folder / 'cli.tfrc').read_bytes() != b'disable_checkpoint = true\n':
            raise ValueError('OpenTofu CLI configuration changed')
        data = folder / '.terraform'
        if data.exists():
            for path in data.rglob('*'):
                if path.is_symlink() or (path.relative_to(data).parts[0] != 'providers'):
                    raise ValueError('Unexpected OpenTofu executable configuration')

    def _command(self, folder, *args, accepted=(0,)):
        self._artifacts(folder)
        trust = self._host_trust()
        if hasattr(self, '_expected_trust') and trust != self._expected_trust:
            raise ValueError('SSH host trust changed')
        if (folder / 'review.json').exists():
            approved = json.loads((folder / 'review.json').read_text())
            if approved['pins'] != self._pins(folder):
                raise ValueError('Approved executable/configuration changed')
        # Do not inherit TF_CLI_ARGS, TF_VAR_*, provider overrides or debug logging.
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'SSH_AUTH_SOCK') if k in os.environ}
        env.update(TF_IN_AUTOMATION='1', TF_INPUT='0', TF_CLI_CONFIG_FILE=str(folder / 'cli.tfrc'), CHECKPOINT_DISABLE='1')
        try:
            result = self.runner([str(self.binary), *args], cwd=folder, env=env,
                                 capture_output=True, text=True, timeout=180)
        finally:
            # Directory is private even during provider writes; artifacts become 0600.
            for path in folder.iterdir():
                if path.is_symlink():
                    raise ValueError('Symlinked artifact is forbidden')
                if path.is_file():
                    path.chmod(0o600)
        if result.returncode not in accepted:
            raise RuntimeError('OpenTofu command failed; raw provider output suppressed')
        return result

    def _host_trust(self):
        # ssh -G must itself be non-executing: Include/Match exec cannot be audited here.
        config = require_ssh_config(SSH_CONFIG)
        for line in config.read_text().splitlines():
            key = re.split(r'[\s=]+', line.strip(), maxsplit=1)[0].lower() if line.strip() else ''
            if key in {'include', 'match'}:
                raise ValueError('Dynamic SSH configuration is unsupported')
        result = self.runner(['ssh', '-G', '-F', str(config), '-o', 'BatchMode=yes', ALIAS],
                             capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError('Cannot inspect effective SSH host trust')
        options = dict(line.split(None, 1) for line in result.stdout.splitlines() if line.strip())
        if options.get('stricthostkeychecking') not in {'true', 'yes'}:
            raise ValueError('Strict host key checking is required')
        for key in ('proxycommand', 'proxyjump', 'controlpath', 'knownhostscommand', 'localcommand'):
            if options.get(key, 'none') != 'none':
                raise ValueError('Uninspectable SSH proxy/controlsocket/command trust')
        for key in ('verifyhostkeydns', 'updatehostkeys', 'controlmaster', 'permitlocalcommand'):
            if options.get(key, 'false') not in {'false', 'no'}:
                raise ValueError('Dynamic SSH host trust is unsupported')
        files = {}
        for key in ('userknownhostsfile', 'globalknownhostsfile'):
            for value in options.get(key, '').split():
                if value == 'none':
                    continue
                path = Path(value).expanduser()
                if (not path.is_absolute() or any(c in value for c in '%$*?\\"\'')
                        or any(p.is_symlink() for p in [path, *path.parents])
                        or not path.is_file()):
                    raise ValueError('Uninspectable SSH known_hosts file')
                files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        if not files:
            raise ValueError('Pinned known_hosts is required')
        return {'effective_ssh': digest(options), **files}

    def _ssh(self, command):
        trust = self._host_trust()
        if hasattr(self, '_expected_trust') and trust != self._expected_trust:
            raise ValueError('SSH host trust changed')
        result = self.runner(['ssh', '-F', str(SSH_CONFIG), '-o', 'BatchMode=yes', ALIAS, command],
                             capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Read-only hypervisor inspection failed; raw output suppressed')
        return result.stdout

    def _absent(self, prepared):
        name = prepared['name']
        domains = self._ssh('virsh -c qemu:///system list --all --name').splitlines()
        volumes = self._ssh('virsh -c qemu:///system vol-list infra-lab')
        if name in domains or name + '.qcow2' in volumes:
            raise ValueError('Preexisting resource: refusing adoption or replacement')

    def _pins(self, folder):
        self._artifacts(folder)
        paths = [folder / 'cli.tfrc', folder / 'main.tf', folder / 'terraform.tfvars.json', folder / '.terraform.lock.hcl',
                 self.binary, Path(__file__), self.template_dir / 'main.tf', self.template_dir / 'ssh-libvirt', SSH_CONFIG]
        # The lockfile pins distribution checksums; also detect local plugin edits
        # between init/approval/apply without ever re-running init at apply time.
        providers = folder / '.terraform/providers'
        if providers.exists():
            paths.extend(sorted(p for p in providers.rglob('*') if p.is_file()))
        if any(p.is_symlink() for p in paths):
            raise ValueError('Symlinked pinned source/artifact is forbidden')
        return {**self._host_trust(), **{str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}

    def _plan_actions(self, document, action):
        changes = document.get('resource_changes', [])
        actions = {x['address']: x['change']['actions'] for x in changes}
        if len(changes) != 2 or set(actions) != ADDRESSES or any(v != [action] for v in actions.values()):
            raise ValueError('Plan must contain exactly the two owned resources, with no updates or replacements')
        if document.get('resource_drift'):
            raise ValueError('External resource drift requires manual review')
        return actions

    def prepare_plan(self, job):
        """Read-only remote operation BEFORE approval; never apply here.

        The host must display returned actions and persist plan_digest into the
        approved job's approved_tofu_plan only after explicit human approval.
        """
        prepared = self.review(job)
        with self._lock():
            folder = self._folder(job['id'])
            if folder.exists():
                raise ValueError('Existing job state cannot be replanned or adopted; use a new job')
            self._expected_trust = self._host_trust()
            self._absent(prepared)
            folder.mkdir(mode=0o700)
            self._write(folder / 'main.tf', (self.template_dir / 'main.tf').read_text())
            uri = 'qemu+sshcmd://infra-hypervisor/system?proxy=netcat&command=' + quote(str(self.template_dir / 'ssh-libvirt'), safe='')
            variables = {'name': prepared['name'], 'uri': uri, **prepared['config']}
            self._write(folder / 'terraform.tfvars.json', variables)
            self._write(folder / 'cli.tfrc', 'disable_checkpoint = true\n')
            self._command(folder, 'init', '-input=false', '-no-color')
            self._command(folder, 'validate', '-no-color')
            self._command(folder, 'plan', '-input=false', '-no-color', '-lock=true', '-lock-timeout=5s',
                          '-detailed-exitcode', '-out=create.plan', accepted=(2,))
            document = json.loads(self._command(folder, 'show', '-json', 'create.plan').stdout)
            actions = self._plan_actions(document, 'create')
            record = {**prepared, 'pins': self._pins(folder), 'actions': actions,
                      'binary_plan_sha256': hashlib.sha256((folder / 'create.plan').read_bytes()).hexdigest()}
            record['plan_digest'] = digest(record)
            self._write(folder / 'review.json', record)
            return {k: record[k] for k in ('job_id', 'graph_hash', 'name', 'plan_digest', 'actions')}

    def _approved(self, job, folder):
        prepared = self.review(job)
        record = json.loads((folder / 'review.json').read_text())
        plan_digest = record.pop('plan_digest')
        if digest(record) != plan_digest or job.get('approved_tofu_plan') != plan_digest:
            raise ValueError('Exact saved OpenTofu plan needs explicit approval before apply')
        if not job.get('approved_by') or not job.get('approved_at') or job.get('approved_graph_hash') != prepared['graph_hash']:
            raise ValueError('Reviewed graph approval is missing or changed')
        if job.get('approved_execution_provider', {}).get('name') != 'opentofu':
            raise ValueError('Approved execution provider must be opentofu')
        if any(record.get(k) != prepared[k] for k in prepared) or record['pins'] != self._pins(folder):
            raise ValueError('Approved source/configuration changed')
        if hashlib.sha256((folder / 'create.plan').read_bytes()).hexdigest() != record['binary_plan_sha256']:
            raise ValueError('Approved saved plan changed')
        self._expected_trust = self._host_trust()
        return record

    def _state(self, folder):
        return json.loads(self._command(folder, 'show', '-json').stdout)

    def execute(self, job, emit):
        try:
            with self._lock():
                folder = self._folder(job['id'])
                record = self._approved(job, folder)
                if (folder / 'terraform.tfstate').exists() or (folder / 'attempt.json').exists():
                    raise ValueError('Existing state or previous apply attempt needs manual review; updates unsupported')
                self._absent(record)
                self._write(folder / 'attempt.json', {'job_id': job['id'], 'operation': 'create'})
                # Apply ONLY the exact approved binary plan. No implicit new plan.
                self._command(folder, 'apply', '-input=false', '-no-color', '-lock=true', '-lock-timeout=5s', 'create.plan')
                state = self._state(folder)
                resources = state.get('values', {}).get('root_module', {}).get('resources', [])
                if {r['address'] for r in resources} != ADDRESSES or len(resources) != 2:
                    raise ValueError('Created resource readback does not match ownership scope')
                remote = self._remote(record, state)
                self._write(folder / 'owned.json', {'job_id': job['id'], 'state_hash': digest(state), 'remote': remote})
                emit('opentofu', 'info', 'Owned stopped VM definition and blank disk created; OS boot is not provided')
                return {'status': 'succeeded', 'operation': 'create-stopped-vm', 'name': record['name']}
        except Exception:
            # Never expose provider stdout, stderr, exception text or plan/state.
            return {'status': 'failed', 'requires_manual_review': True,
                    'error': 'OpenTofu create blocked or failed; no automatic destroy; inspect private state'}

    def _remote(self, record, state):
        """Independent readback binds names to provider-owned UUID and volume key."""
        name = record['name']  # Generated, never a graph supplied shell argument.
        resources = {r['address']: r['values'] for r in state.get('values', {}).get('root_module', {}).get('resources', [])}
        if set(resources) != ADDRESSES:
            raise ValueError('Unexpected owned state resources')
        domain = self._ssh(f'virsh -c qemu:///system dumpxml --inactive {name}')
        volume = self._ssh(f'virsh -c qemu:///system vol-dumpxml --pool infra-lab {name}.qcow2')
        dom, vol = ET.fromstring(domain), ET.fromstring(volume)
        dv, vv = resources['libvirt_domain.vm'], resources['libvirt_volume.disk']
        if (dv.get('name') != name or vv.get('name') != name + '.qcow2'
                or dom.findtext('name') != name or vol.findtext('name') != name + '.qcow2'
                or not dv.get('uuid') or dom.findtext('uuid') != dv['uuid']
                or not vv.get('id') or vol.findtext('key') != vv['id']):
            raise ValueError('Independent ownership verification failed')
        # Full inactive definition / volume XML catches edits outside provider schema.
        return {'domain_xml': digest(domain), 'volume_xml': digest(volume)}

    def _owned_unchanged(self, job, folder):
        record = self._approved(job, folder)
        owned = json.loads((folder / 'owned.json').read_text())
        state = self._state(folder)
        if owned.get('job_id') != job['id'] or owned.get('state_hash') != digest(state):
            raise ValueError('State was not created by this exact job or has changed')
        if owned.get('remote') != self._remote(record, state):
            raise ValueError('External domain/volume changes require manual review')
        # Refuse deletion if any OTHER guest now references the owned disk.
        volume = next(r['values']['id'] for r in state['values']['root_module']['resources'] if r['address'] == 'libvirt_volume.disk')
        volume_xml = ET.fromstring(self._ssh(
            f"virsh -c qemu:///system vol-dumpxml --pool infra-lab {record['name']}.qcow2"))
        paths = {os.path.normpath(p) for p in (volume, volume_xml.findtext('target/path'))
                 if p and p.startswith('/')}
        if not paths:
            raise ValueError('Cannot resolve owned volume path')
        active = set(self._ssh('virsh -c qemu:///system list --name').splitlines())
        domains = set(self._ssh('virsh -c qemu:///system list --all --name').splitlines()) | active
        for other in sorted(domains - {'', record['name']}):
            identifier(other)
            # Always inspect persistent XML; active guests additionally need live XML.
            # Missing XML (including unsupported transient definitions) fails closed.
            views = ['--inactive ', ''] if other in active else ['--inactive ']
            for view in views:
                xml = ET.fromstring(self._ssh(f'virsh -c qemu:///system dumpxml {view}{other}'))
                if xml.tag != 'domain':
                    raise ValueError('Invalid foreign domain XML')
                for source in xml.findall('.//disk//source'):
                    attached = any(os.path.normpath(source.get(k, '')) in paths for k in ('file', 'dev', 'name'))
                    attached |= (source.get('pool') == 'infra-lab'
                                 and source.get('volume') == record['name'] + '.qcow2')
                    if attached:
                        raise ValueError('Owned volume is referenced by another guest')
        result = self._command(folder, 'plan', '-refresh-only', '-detailed-exitcode', '-input=false', '-no-color',
                               '-lock=true', '-lock-timeout=5s', '-out=refresh.plan', accepted=(0, 2))
        if result.returncode != 0:
            raise ValueError('External provider drift requires manual review')
        return record

    def prepare_removal(self, job):
        """Produce separate saved delete plan BEFORE administrator remove approval."""
        with self._lock():
            folder = self._folder(job['id'])
            record = self._owned_unchanged(job, folder)
            if (folder / 'remove-attempt.json').exists() or (folder / 'removal.json').exists():
                raise ValueError('Removal already prepared/attempted; do not replace approved artifacts')
            self._command(folder, 'plan', '-destroy', '-input=false', '-no-color', '-lock=true', '-lock-timeout=5s',
                          '-detailed-exitcode', '-out=destroy.plan', accepted=(2,))
            document = json.loads(self._command(folder, 'show', '-json', 'destroy.plan').stdout)
            actions = self._plan_actions(document, 'delete')
            removal = {'job_id': job['id'], 'create_digest': job['approved_tofu_plan'], 'actions': actions,
                       'binary_plan_sha256': hashlib.sha256((folder / 'destroy.plan').read_bytes()).hexdigest(),
                       'ownership_hash': hashlib.sha256((folder / 'owned.json').read_bytes()).hexdigest()}
            removal['plan_digest'] = digest(removal)
            self._write(folder / 'removal.json', removal)
            return {'job_id': job['id'], 'plan_digest': removal['plan_digest'], 'actions': actions,
                    'operation': 'remove-owned-stopped-vm', 'name': record['name']}

    def rollback(self, job, emit):
        """Narrow approved removal, NOT restoration or recovery of failed applies."""
        try:
            with self._lock():
                folder = self._folder(job['id'])
                removal = json.loads((folder / 'removal.json').read_text())
                approved = removal.pop('plan_digest')
                if approved != digest(removal) or approved != job.get('approved_tofu_removal'):
                    raise ValueError('Separate saved removal plan approval required')
                if removal['job_id'] != job['id'] or removal['create_digest'] != job.get('approved_tofu_plan'):
                    raise ValueError('Removal job does not own these resources')
                if (folder / 'remove-attempt.json').exists():
                    raise ValueError('Interrupted/previous removal requires manual review')
                if hashlib.sha256((folder / 'destroy.plan').read_bytes()).hexdigest() != removal['binary_plan_sha256']:
                    raise ValueError('Saved removal plan changed')
                if hashlib.sha256((folder / 'owned.json').read_bytes()).hexdigest() != removal['ownership_hash']:
                    raise ValueError('Ownership evidence changed')
                record = self._owned_unchanged(job, folder)
                self._write(folder / 'remove-attempt.json', {'job_id': job['id'], 'operation': 'remove-owned-stopped-vm'})
                self._command(folder, 'apply', '-input=false', '-no-color', '-lock=true', '-lock-timeout=5s', 'destroy.plan')
                remaining = self._state(folder).get('values', {}).get('root_module', {}).get('resources', [])
                if remaining:
                    raise ValueError('Removal readback still contains resources')
                self._absent(record)
                emit('opentofu', 'info', 'Removed only this job-owned stopped VM and disk; not universal restoration')
                return {'status': 'rolled_back', 'operation': 'remove-owned-stopped-vm'}
        except Exception:
            return {'status': 'rollback_failed', 'requires_manual_review': True,
                    'error': 'Owned-resource removal blocked or failed; no blind destroy or automatic retry'}
