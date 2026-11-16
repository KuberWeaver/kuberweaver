"""Create-only Ubuntu import using terraform_data and native OVF Tool pins.

Not a full CRUD VM provider. Credentials remain in the host process; the
OpenTofu provisioner requests one import through a private Unix socket.
"""
from __future__ import annotations
import base64
import json
import re
import time
import jsonschema
import yaml
from referencing import Registry
from .provisioning import ROOT, SavedPlanExecutor, ProvisioningError, digest, TOFU_SHA256, CLI_CONFIG
from pathlib import Path
import hashlib
import os
import shlex
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def cloud_init_config(name, public_key, instance_id):
    """Guest bootstrap delivered through VMware guestinfo keys.

    Standalone ESXi has no vApp option store, so OVF properties accepted by
    ovftool never reach the guest. cloud-init's VMware datasource reads these
    extraConfig keys through open-vm-tools at first boot, which is the only
    channel that survives without vCenter.
    """
    key = (public_key or '').strip()
    if not key:
        raise ProvisioningError('Selected SSH key is unavailable or disabled')
    metadata = yaml.safe_dump({'instance-id': instance_id, 'local-hostname': name})
    userdata = '#cloud-config\n' + yaml.safe_dump({
        'hostname': name, 'preserve_hostname': False, 'ssh_pwauth': False,
        'package_update': False, 'users': ['default'], 'ssh_authorized_keys': [key]})
    return {'guestinfo.metadata': base64.b64encode(metadata.encode()).decode(),
            'guestinfo.metadata.encoding': 'base64',
            'guestinfo.userdata': base64.b64encode(userdata.encode()).decode(),
            'guestinfo.userdata.encoding': 'base64'}


class OVFExecutor(SavedPlanExecutor):
    guest_wait = 120
    source_dependencies = (Path(__file__).with_name('provisioning.py'), Path(__file__).with_name('ovf_transport.py'),
                           Path(__file__).with_name('ssh_keys.py'))

    @staticmethod
    def _transport_contract():
        from .ovf_transport import require_transport
        try:
            require_transport()
        except Exception:
            raise ProvisioningError('Pin-only OVF transport unavailable; remote import blocked') from None

    def _native_command(self, folder, args):
        from .ovf_transport import pinned_command
        return pinned_command(self.ovftool, folder, args)

    def __init__(self, state_dir, template_dir, binary=None, runner=None):
        super().__init__(state_dir, template_dir, binary, runner)
        self.image_metadata = ROOT / 'data/os-images/ubuntu-24.04/image.json'
        self.ovftool = Path('/usr/bin/ovftool').resolve()
        self.helper = Path(__file__).with_name('ovf_request.py')
        self.ssh_keys = None

    def _image(self):
        self._safe(self.image_metadata)
        image = json.loads(self.image_metadata.read_text())
        path = Path(image['path'])
        self._safe(path)
        if (image.get('image_id') != 'ubuntu-24.04' or not path.is_absolute()
                or not re.fullmatch(r'[0-9a-f]{64}', image.get('sha256', ''))
                or sha256(path) != image['sha256']):
            raise ProvisioningError('Admin-controlled Ubuntu image checksum mismatch')
        import tarfile
        import xml.etree.ElementTree as ET
        with tarfile.open(path) as archive:
            descriptors = [m for m in archive.getmembers() if m.name.endswith('.ovf')]
            if len(descriptors) != 1 or not descriptors[0].isfile() or descriptors[0].size > 2 * 1024**2:
                raise ProvisioningError('Unsupported Ubuntu OVF descriptor')
            descriptor = archive.extractfile(descriptors[0])
            if descriptor is None:
                raise ProvisioningError('Missing Ubuntu OVF descriptor')
            with descriptor:
                document = ET.fromstring(descriptor.read())
        namespace = '{http://schemas.dmtf.org/ovf/envelope/1}'
        disks = list(document.iter(namespace + 'Disk'))
        if len(disks) != 1 or disks[0].get(namespace + 'capacityAllocationUnits') != 'byte':
            raise ProvisioningError('Unsupported Ubuntu OS disk layout')
        raw_capacity = disks[0].get(namespace + 'capacity')
        if raw_capacity is None or not raw_capacity.isdigit():
            raise ProvisioningError('Invalid Ubuntu OS disk capacity')
        capacity = int(raw_capacity)
        if capacity <= 0:
            raise ProvisioningError('Invalid Ubuntu OS disk capacity')
        return {**image, 'minimum_disk_bytes': capacity}

    def _artifacts(self, folder):
        allowed = {'main.tf', 'terraform.tfvars.json', 'cli.tfrc', '.terraform', '.terraform.d',
                   '.terraform.lock.hcl', 'create.plan', 'review.json', 'attempt.json',
                   'verified.json', 'owned.json', 'terraform.tfstate', 'terraform.tfstate.backup',
                   '.terraform.tfstate.lock.info', 'request.sock', 'ovf-home', 'ovf-tmp', 'ovf-trust'}
        self._safe(folder)
        for p in folder.rglob('*'):
            if p.name == 'request.sock' and p.parent == folder and p.is_socket():
                continue
            self._safe(p)
            if p.parent == folder and p.name in {'ovf-home', 'ovf-tmp', 'ovf-trust'}:
                if not p.is_dir() or p.stat().st_mode & 0o777 != 0o700:
                    raise ProvisioningError('OVF native root must be a private directory')
            if p.stat().st_uid != os.getuid() or p.relative_to(folder).parts[0] not in allowed:
                raise ProvisioningError('Unexpected private plan artifact')
        if (folder / 'cli.tfrc').read_bytes() != CLI_CONFIG:
            raise ProvisioningError('CLI configuration changed')

    def _pins(self, folder):
        self._artifacts(folder)
        image = self._image()
        paths = [self.binary, Path(sys.executable).resolve(), Path(__file__), self.helper,
                 *self.source_dependencies, self.image_metadata, Path(image['path']),
                 self.template_dir / 'manifest.yaml', self.template_dir / 'main.tf',
                 folder / 'main.tf', folder / 'terraform.tfvars.json', folder / 'cli.tfrc']
        # The launcher loads sibling libraries and configuration. Pin the complete
        # admin-installed distribution, not merely the shell wrapper.
        paths += sorted(p for p in self.ovftool.parent.rglob('*') if p.is_file())
        paths += sorted(p for p in (folder / '.terraform').rglob('*') if p.is_file())
        result = {}
        for p in paths:
            resolved = p.resolve()
            self._safe(resolved)
            # _image just hashed and validated this OVA; retain its pin without
            # streaming the multi-gigabyte payload a second time in this call.
            result[str(p)] = image['sha256'] if p == Path(image['path']) else sha256(resolved)
        if result[str(self.binary)] != TOFU_SHA256:
            raise ProvisioningError('Unverified OpenTofu executable')
        return result

    def _inventory(self, reviewed, connection, raw):
        if (connection.get('enabled') is not True or connection.get('driver') != 'esxi'
                or not re.fullmatch(r'[0-9a-fA-F]{64}', connection.get('tls_sha256', ''))):
            raise ProvisioningError('Enabled ESXi connection with accepted SHA256 pin required')
        inventory = self.connections.driver(connection).discover(connection, raw)
        server, config = inventory['server'], reviewed['config']
        if (server.get('api_type') != 'HostAgent' or not str(server.get('version', '')).startswith('8.')
                or server.get('write_api_supported') is False
                or not isinstance(server.get('license_edition'), str)
                or not server['license_edition'].strip() or server['license_edition'] == 'esxBasic'):
            raise ProvisioningError('Licensed standalone ESXi 8 required; write permission remains unproven')
        for group, ids in [('datastores', [config['datastore_id']]), ('networks', config['network_ids']),
                           ('resource_pools', [config['resource_pool_id']])]:
            for identity in ids:
                rows = [r for r in inventory[group] if r['id'] == identity]
                if len(rows) != 1:
                    raise ProvisioningError('Selected inventory reference disappeared')
                if group == 'datastores' and (rows[0].get('accessible') is not True
                        or rows[0].get('free_bytes', 0) < config['disk_gb'] * 1024**3):
                    raise ProvisioningError('Selected datastore lacks capacity')
        if any(v['name'] == reviewed['name'] for v in inventory['virtual_machines']):
            raise ProvisioningError('Existing VM name is forbidden')
        return inventory

    def _guard(self, folder, signed, raw=None):
        key_id = signed['reviewed']['config']['ssh_key_id']
        if self._key_binding(key_id) != signed['reviewed']['ssh_key_binding']:
            raise ProvisioningError('Reviewed SSH key binding changed')
        cid = signed['reviewed']['config']['connection_id']
        with self.connections.access(cid) as (_, current):
            if (sha256_bytes(current) != signed['credential_digest']
                    or self.connections.fingerprint(cid) != signed['bindings']['connection_fingerprint']):
                raise ProvisioningError('Connection credential or certificate binding changed')
        if self._pins(folder) != signed['pins']:
            raise ProvisioningError('Reviewed execution source, executable or image changed')
        if signed.get('plan_sha256') and sha256(folder / 'create.plan') != signed['plan_sha256']:
            raise ProvisioningError('Saved plan changed')

    def _command(self, folder, signed, args, accepted=(0,)):
        self._guard(folder, signed)
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(folder), 'TMPDIR': str(folder),
               'TF_CLI_CONFIG_FILE': str(folder / 'cli.tfrc'), 'TF_IN_AUTOMATION': '1',
               'TF_INPUT': '0', 'CHECKPOINT_DISABLE': '1'}
        result = self.runner([str(self.binary), *args], cwd=folder, env=env,
                             capture_output=True, text=True, timeout=600)
        self._guard(folder, signed)
        if result.returncode not in accepted:
            raise ProvisioningError('OpenTofu command failed; raw output suppressed')
        return result.stdout

    def prepare_plan(self, job, emit):
        reviewed = self.review(job)
        with self._errors(), self._lock():
            folder = self.state_dir / reviewed['job_id']
            if folder.exists():
                raise ProvisioningError('Existing job cannot be replanned')
            cid = reviewed['config']['connection_id']
            binding = self.connections.fingerprint(cid)
            with self.connections.access(cid) as (connection, raw):
                self._credentials(raw)
                self._inventory(reviewed, connection, raw)
                image = self._image()
                if reviewed['config']['disk_gb'] * 1024**3 < image['minimum_disk_bytes']:
                    raise ProvisioningError('Requested disk is smaller than official Ubuntu OS disk')
                folder.mkdir(mode=0o700)
                for name in ('ovf-home', 'ovf-tmp'):
                    (folder / name).mkdir(mode=0o700)
                self._write(folder / 'main.tf', (self.template_dir / 'main.tf').read_bytes())
                self._write(folder / 'cli.tfrc', CLI_CONFIG)
                command = shlex.join([str(Path(sys.executable).resolve()), str(self.helper), str(folder / 'request.sock')])
                self._write(folder / 'terraform.tfvars.json', {'request_command': command,
                            'spec': reviewed, 'image_sha256': image['sha256']})
                signed = {'reviewed': reviewed, 'credential_digest': sha256_bytes(raw),
                          'bindings': {'connection_id': cid, 'connection_fingerprint': binding},
                          'pins': self._pins(folder)}
                self._command(folder, signed, ['init', '-backend=false', '-input=false', '-no-color'])
                self._command(folder, signed, ['plan', '-input=false', '-no-color', '-detailed-exitcode', '-out=create.plan'], (2,))
                signed['plan_sha256'] = sha256(folder / 'create.plan')
                document = json.loads(self._command(folder, signed, ['show', '-json', 'create.plan']))
                signed['actions'] = self._actions(document)
                signed['summary'] = {'name': reviewed['name'], 'image_id': 'ubuntu-24.04',
                    'image_sha256': image['sha256'], 'guest_user': 'ubuntu', 'os_payload': 'official-cloud-image',
                    'power_state': 'poweredOn', 'cpu': reviewed['config']['cpu'],
                    'cpu_sockets': reviewed['config']['cpu_sockets'],
                    'cores_per_socket': reviewed['config']['cores_per_socket'],
                    'memory_mb': reviewed['config']['memory_mb'], 'disk_gb': reviewed['config']['disk_gb'],
                    'ssh_key_id': reviewed['config']['ssh_key_id'],
                    'ssh_key_fingerprint': reviewed['ssh_key_binding']['fingerprint'],
                    'limitations': ['create-only composition; not full VM CRUD',
                                    'guest reachability reported by VMware Tools; the injected key governs SSH access']}
                self._guard(folder, signed)
                self._write(folder / 'review.json', signed)
                return {k: signed[k] for k in ('actions', 'summary', 'bindings')} | {'plan_digest': digest(signed)}

    @staticmethod
    def _actions(document):
        changes = document.get('resource_changes', [])
        if (document.get('resource_drift') or document.get('prior_state')
                or len(changes) != 1 or changes[0].get('address') != 'terraform_data.vm'
                or changes[0].get('mode') != 'managed'
                or changes[0].get('change', {}).get('actions') != ['create']
                or changes[0]['change'].get('before') is not None
                or changes[0]['change'].get('importing')):
            raise ProvisioningError('Exactly one new terraform_data VM import required')
        return {'terraform_data.vm': ['create']}

    def validate_plan(self, job):
        with self._errors():
            reviewed = self.review(job)
            self._checked(job, self.state_dir / reviewed['job_id'],
                          active=getattr(self, '_execution_job', None) == reviewed['job_id'])

    def execute(self, job, emit):
        folder, attempted, server, worker = None, False, None, None
        try:
            identity = job.get('id')
            if isinstance(identity, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', identity):
                folder = self.state_dir / identity
            reviewed = self.review(job)
            folder = self.state_dir / reviewed['job_id']
            with self._errors(), self._lock(), self.connections.access(reviewed['config']['connection_id']) as (connection, raw):
                signed = self._checked(job, folder, raw)
                if (not job.get('approved_by') or not job.get('approved_at')
                        or job.get('approved_graph_hash') != reviewed['graph_digest']
                        or job.get('approved_execution_provider', {}).get('name') != 'esxi-ovf'
                        or job.get('approved_prepared_plan') != digest(job['prepared_plan'])):
                    raise ProvisioningError('Exact saved-plan operator approval required')
                self._inventory(reviewed, connection, raw)
                self._transport_contract()
                self._execution_job = reviewed['job_id']
                self._write(folder / 'attempt.json', {'plan_digest': digest(signed), 'requires_manual_review': True})
                attempted = True
                # Linux abstract socket avoids the 108-byte filesystem limit.
                # Peer UID is checked before accepting the secret-free request.
                server = socket.socket(socket.AF_UNIX)
                address = '\0kw-ovf-' + digest(str(folder))
                server.bind(address)
                server.listen(1)
                server.settimeout(15)
                evidence, errors = [], []
                def serve():
                    try:
                        client, _ = server.accept()
                        with client:
                            import struct
                            _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                            if uid != os.getuid():
                                raise ProvisioningError('Foreign local import peer')
                            client.settimeout(10)
                            if client.recv(32) != b'import-v1':
                                raise ProvisioningError('Invalid local import request')
                            evidence.append(self._import_vm(folder, signed, connection, raw))
                            client.sendall(b'ok')
                    except BaseException:
                        errors.append(True)
                worker = threading.Thread(target=serve, daemon=True)
                worker.start()
                self._command(folder, signed, ['apply', '-input=false', '-no-color', 'create.plan'])
                worker.join(timeout=5)
                if worker.is_alive() or errors or len(evidence) != 1:
                    raise ProvisioningError('Import result is uncertain')
                value = evidence[0]
                self._guard(folder, signed)
                actual = self.connections.driver(connection).read_resource(connection, raw, value['uuid'])
                expected = {'name': reviewed['name'], 'cpu': reviewed['config']['cpu'],
                            'memory_mb': reviewed['config']['memory_mb'], 'power_state': 'poweredOn'}
                if any(actual.get(k) != v for k, v in expected.items()) or actual.get('uuid') != value['uuid']:
                    raise ProvisioningError('Independent UUID readback mismatch')
                guest_ip = self._guest_address(connection, raw, value['uuid'])
                verified = {**actual, 'image_id': 'ubuntu-24.04', 'os_payload_imported': True,
                            'guest_ip': guest_ip, 'guest_boot_verified': bool(guest_ip),
                            'source': 'independent-sdk'}
                self._write(folder / 'verified.json', verified)
                return {'status': 'succeeded', 'evidence': [verified], 'endpoints': [],
                        'requires_manual_review': False, 'can_rollback': False}
        except ProvisioningError as error:
            # Curated messages are literal safety-check outcomes and carry no
            # secrets, so the operator gets the real reason instead of a blank.
            uncertain = attempted or bool(folder and (folder / 'attempt.json').exists())
            return {'status': 'failed', 'error': str(error),
                    'requires_manual_review': uncertain, 'can_rollback': False, 'evidence': [], 'endpoints': []}
        except (Exception, KeyboardInterrupt):
            uncertain = attempted or bool(folder and (folder / 'attempt.json').exists())
            return {'status': 'failed', 'error': 'OVF saved-plan execution refused or failed; raw output suppressed',
                    'requires_manual_review': uncertain, 'can_rollback': False, 'evidence': [], 'endpoints': []}
        finally:
            self._execution_job = None
            if server:
                server.close()

    def _sdk_guard(self, folder, signed):
        # Route SDK mutation admission through the same host Jobs command guard.
        if getattr(self, '_execution_job', None) != signed['reviewed']['job_id']:
            raise ProvisioningError('Execution cancelled; manual review required')
        self._guard(folder, signed)
        result = self.runner(['/usr/bin/true'], capture_output=True, timeout=10,
                             env={'PATH': '/usr/bin:/bin'})
        if result.returncode != 0:
            raise ProvisioningError('SDK mutation admission refused')

    def _import_vm(self, folder, signed, connection, raw):
        self._transport_contract()
        import base64
        import time
        from pyVmomi import vim
        reviewed, config = signed['reviewed'], signed['reviewed']['config']
        driver = self.connections.driver(connection)
        credentials = self._credentials(raw)
        self._guard(folder, signed)
        si = driver._connector(connection=connection, **credentials)
        marker = 'kuberweaver-job:' + reviewed['job_id']
        try:
            content = si.RetrieveContent()
            inventory = self._inventory(reviewed, connection, raw)
            before = {v['uuid'] for v in inventory['virtual_machines']}
            # An empty searchTerm addresses the standalone host's default pool.
            # Named pool locators are not supported by this create-only path.
            if config['resource_pool_id'] != 'ha-root-pool':
                raise ProvisioningError('Standalone import requires the default root resource pool')
            payload = cloud_init_config(config['vm_name'], config['ssh_public_key'], marker)
            user_data = payload['guestinfo.userdata']
            ticket = content.sessionManager.AcquireCloneTicket()
            if not isinstance(ticket, str) or not ticket or '\n' in ticket or ticket.lower() in {'yes', 'no'}:
                raise ProvisioningError('Invalid clone session ticket')
            pin = ':'.join(connection['tls_sha256'][i:i+2].upper() for i in range(0, 64, 2))
            target = 'vi://' + urlsplit(connection['endpoint']).netloc + '/'
            # With --I:morefArgs, ovftool only accepts typed managed-object
            # references. Standalone ESXi answers name-based references with
            # "Invalid target datastore specified" and lists the valid forms.
            def resolved(group, identity, kind):
                rows = [row for row in inventory[group] if row['id'] == identity]
                if len(rows) != 1:
                    raise ProvisioningError('Selected inventory reference disappeared')
                return 'vim.' + kind + ':' + identity
            datastore_target = resolved('datastores', config['datastore_id'], 'Datastore')
            network_target = resolved('networks', config['network_ids'][0], 'Network')
            args = [str(self.ovftool), '--acceptAllEulas', '--I:morefArgs',
                '--I:targetSessionTicket=prompt', '--targetSSLThumbprint=' + pin,
                '--name=' + reviewed['name'], '--annotation=' + marker, '--diskMode=thin',
                '--datastore=' + datastore_target,
                '--net:VM Network=' + network_target,
                '--prop:instance-id=' + marker, '--prop:hostname=' + config['vm_name'],
                '--prop:public-keys=' + config['ssh_public_key'],
                '--prop:user-data=' + user_data,
                self._image()['path'], target]
            self._sdk_guard(folder, signed)
            result = self.runner(self._native_command(folder, args[1:]), input=ticket + '\nno\n', capture_output=True, text=True,
                timeout=500, cwd=folder, env={'PATH': '/usr/bin:/bin', 'HOME': str(folder / 'ovf-home'),
                'TMPDIR': str(folder / 'ovf-tmp'), 'LANG': 'C.UTF-8'})
            if result.returncode != 0:
                # Keep the full diagnostics privately so an operator can repair
                # the target instead of guessing, without echoing them to callers.
                try:
                    self._write(folder / 'ovf-output.log', {
                        'returncode': result.returncode,
                        'stdout': (result.stdout or '')[-12000:],
                        'stderr': (result.stderr or '')[-12000:]})
                except (OSError, ValueError):
                    pass
                hint = ''
                for line in (result.stdout or '').splitlines():
                    if line.startswith('Error:'):
                        hint = line.strip()[:300]
                        break
                raise ProvisioningError('OVF import failed: ' + hint if hint
                                        else 'OVF import failed; see the private ovf-output.log')
            self._guard(folder, signed)
            view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
            try:
                matches = [v for v in view.view if v.config.annotation == marker
                           and v.name == reviewed['name'] and v.config.uuid not in before]
                if len(matches) != 1:
                    raise ProvisioningError('Imported VM ownership is ambiguous')
                uuid = matches[0].config.uuid
            finally:
                view.Destroy()
            if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', uuid):
                raise ProvisioningError('Imported UUID missing')
            self._write(folder / 'owned.json', {'uuid': uuid, 'marker': marker})
            def owned():
                vm = content.searchIndex.FindByUuid(None, uuid, True, False)
                if (vm is None or vm.config.uuid != uuid or vm.config.annotation != marker
                        or vm.name != reviewed['name']):
                    raise ProvisioningError('Owned VM UUID binding changed')
                if (vm.resourcePool._GetMoId() != config['resource_pool_id']
                        or {d._GetMoId() for d in vm.datastore} != {config['datastore_id']}):
                    raise ProvisioningError('Imported VM placement does not match selected references')
                return vm
            def wait(task):
                deadline = time.monotonic() + 180
                while task.info.state in {'queued', 'running'} and time.monotonic() < deadline:
                    time.sleep(1)
                if task.info.state != 'success':
                    raise ProvisioningError('VM task failed or timed out')
            vm = owned()
            if vm.runtime.powerState != 'poweredOff':
                raise ProvisioningError('Imported VM unexpectedly running')
            changes = []
            disks = [d for d in vm.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)]
            nics = [d for d in vm.config.hardware.device if isinstance(d, vim.vm.device.VirtualEthernetCard)]
            if len(disks) != 1 or len(nics) != 1:
                raise ProvisioningError('Unsupported cloud image devices')
            if disks[0].capacityInKB > config['disk_gb'] * 1024**2:
                raise ProvisioningError('Requested disk is smaller than imported OS disk')
            disks[0].capacityInKB = config['disk_gb'] * 1024**2
            changes.append(vim.vm.device.VirtualDeviceSpec(operation='edit', device=disks[0]))
            for i, network_id in enumerate(config['network_ids']):
                nic = nics[0] if i == 0 else vim.vm.device.VirtualVmxnet3(key=-100-i)
                nic.backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(
                    network=vim.Network(network_id, si._stub),
                    deviceName=next(n['name'] for n in inventory['networks'] if n['id'] == network_id))
                nic.connectable = vim.vm.device.VirtualDevice.ConnectInfo(startConnected=True, allowGuestControl=True)
                changes.append(vim.vm.device.VirtualDeviceSpec(operation='edit' if i == 0 else 'add', device=nic))
            self._sdk_guard(folder, signed)
            # guestinfo must be present before the first boot, otherwise the
            # image starts with no datasource and never configures itself.
            wait(owned().ReconfigVM_Task(vim.vm.ConfigSpec(numCPUs=config['cpu'],
                                                          numCoresPerSocket=config['cores_per_socket'],
                                                          memoryMB=config['memory_mb'], deviceChange=changes,
                                                          extraConfig=[vim.option.OptionValue(key=k, value=v)
                                                                       for k, v in payload.items()])))
            vm = owned()
            actual_disks = [d for d in vm.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)]
            actual_nics = [d for d in vm.config.hardware.device if isinstance(d, vim.vm.device.VirtualEthernetCard)]
            actual_networks = []
            for device in actual_nics:
                backing = device.backing
                if not isinstance(backing, vim.vm.device.VirtualEthernetCard.NetworkBackingInfo) or backing.network is None:
                    raise ProvisioningError('Unsupported imported network backing')
                actual_networks.append(backing.network._GetMoId())
            if (vm.config.hardware.numCPU != config['cpu'] or vm.config.hardware.memoryMB != config['memory_mb']
                    or getattr(vm.config.hardware, 'numCoresPerSocket', config['cores_per_socket']) != config['cores_per_socket']
                    or len(actual_disks) != 1 or actual_disks[0].capacityInKB != config['disk_gb'] * 1024**2
                    or actual_networks != config['network_ids']):
                raise ProvisioningError('VM resource readback mismatch')
            self._sdk_guard(folder, signed)
            wait(owned().PowerOnVM_Task())
            return {'uuid': uuid}
        finally:
            driver._disconnector(si)

    def _guest_address(self, connection, raw, uuid, timeout=None):
        """Wait for the guest to report an address, which proves cloud-init ran.

        A VM can be powered on and still be unreachable because the image found
        no datasource. Reading the reported address from open-vm-tools is the
        only independent signal that the guest actually configured itself.
        """
        from pyVmomi import vim
        limit = self.guest_wait if timeout is None else timeout
        driver = self.connections.driver(connection)
        try:
            si = driver._connector(connection=connection, **self._credentials(raw))
        except Exception:
            return ''
        try:
            content = si.RetrieveContent()
            view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
            try:
                target = None
                for candidate in view.view:
                    try:
                        if candidate.config.uuid == uuid:
                            target = candidate
                            break
                    except Exception:
                        continue
                if target is None:
                    return ''
                deadline = time.monotonic() + limit
                while time.monotonic() < deadline:
                    address = target.guest.ipAddress
                    if isinstance(address, str) and address:
                        return address
                    time.sleep(3)
                return ''
            finally:
                view.Destroy()
        except Exception:
            return ''
        finally:
            driver._disconnector(si)

    def read(self):
        from .ovf_transport import BWRAP
        ready = self.binary.is_file() and self.ovftool.is_file() and self.image_metadata.is_file() and BWRAP.is_file()
        return {'provider': 'esxi-ovf', 'requires_saved_plan': True, 'supports_rollback': False,
                'status': 'configured' if ready else 'unavailable', 'image_present': self.image_metadata.is_file(),
                'transport_mode': 'sha256-pin-only-private-ca-namespace',
                'limitations': ['create-only; no update/delete/rollback',
                        'guest bootstrap is delivered through VMware guestinfo keys']}

    def _key_binding(self, identity):
        try:
            if self.ssh_keys is None:
                raise ValueError()
            binding = self.ssh_keys.binding(identity)
            if binding.get('id') != identity or not binding.get('public_key') or not binding.get('fingerprint'):
                raise ValueError()
            return binding
        except Exception:
            raise ProvisioningError('Selected SSH key is unavailable or disabled') from None

    def review(self, job):
        identity = job.get('id')
        graph = job.get('graph', {})
        nodes = graph.get('nodes', [])
        if (not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', identity)
                or graph.get('environment') != 'dev' or len(nodes) != 1 or graph.get('edges')
                or graph.get('failure_mode', 'none') != 'none' or nodes[0].get('plugin_id') != 'vmware-vm'):
            raise ProvisioningError('One independent Ubuntu VM is required')
        manifest = yaml.safe_load((self.template_dir / 'manifest.yaml').read_text())
        config = {**manifest['defaults'], **nodes[0].get('config', {})}
        try:
            jsonschema.validate(config, manifest['schema'], registry=Registry())
            if any(type(config[k]) is not int for k in ('cpu_sockets', 'cores_per_socket', 'memory_mb', 'disk_gb')):
                raise ValueError()
            sockets, cores = config['cpu_sockets'], config['cores_per_socket']
            if not 1 <= sockets * cores <= 8:
                raise ValueError()
            config['cpu'] = sockets * cores
            values = [config['datastore_id'], config['resource_pool_id'], *config['network_ids'],
                      config['vm_name'], config['ssh_key_id']]
            if any(any(ord(c) < 32 or ord(c) == 127 for c in value) for value in values):
                raise ValueError()
        except (ValueError, jsonschema.ValidationError):
            raise ProvisioningError('Invalid closed Ubuntu configuration') from None
        binding = self._key_binding(config['ssh_key_id'])
        config['ssh_public_key'] = binding['public_key']
        return {'job_id': identity, 'name': config['vm_name'], 'graph_digest': digest(graph),
                'ssh_key_binding': binding, 'config': config}
