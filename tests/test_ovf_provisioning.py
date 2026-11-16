"""Offline boundaries only; never remote apply."""
import copy
import importlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import hashlib
from contextlib import contextmanager


class Connections:
    def __init__(self):
        self.row = {'id': 'b' * 32, 'endpoint': 'https://192.0.2.50', 'enabled': True,
                    'tls_sha256': 'c' * 64, 'driver': 'esxi'}
        self.raw = b'{"username":"root","password":"fixture-password-only"}'

    def fingerprint(self, identity):
        return module().digest(self.row)

    @contextmanager
    def access(self, identity):
        yield dict(self.row), self.raw

    def driver(self, connection):
        return self

    def discover(self, connection, raw):
        return {'server': {'api_type': 'HostAgent', 'version': '8.0', 'host_id': 'ha-host',
                           'license_edition': 'esx.enterprisePlus.cpuPackage', 'write_api_supported': None},
                'datastores': [{'id': 'datastore-1', 'name': 'datastore1', 'accessible': True,
                                'free_bytes': 100 * 1024**3}],
                'networks': [{'id': 'network-1', 'name': 'VM Network'}],
                'resource_pools': [{'id': 'ha-root-pool', 'name': 'Resources'}],
                'virtual_machines': []}


def executor(tmp_path):
    ex = module().OVFExecutor(tmp_path / 'states', ROOT / 'plugins/vmware-vm')
    ex.connections = Connections()
    from types import SimpleNamespace
    ex.ssh_keys = SimpleNamespace(binding=lambda identity: {
        'id': identity, 'revision': 1, 'fingerprint': 'SHA256:fixture',
        'public_key': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhY test'})
    # Offline injected import boundary is not a waiver for production transport.
    ex._transport_contract = lambda: None
    # The native pin-only namespace is tested separately; SDK ordering uses an injected CLI boundary.
    ex._native_command = lambda folder, args: [str(ex.ovftool), *args]
    images = tmp_path / 'images'
    images.mkdir()
    ova = images / 'ubuntu.ova'
    # Minimal tar/OVF metadata fixture, deliberately not a deployable OS image.
    import tarfile
    import io
    ovf = b'<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1" xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"><DiskSection><Disk ovf:capacity="10737418240" ovf:capacityAllocationUnits="byte"/></DiskSection></Envelope>'
    with tarfile.open(ova, 'w') as archive:
        entry = tarfile.TarInfo('fixture.ovf')
        entry.size = len(ovf)
        archive.addfile(entry, io.BytesIO(ovf))
    metadata = images / 'image.json'
    metadata.write_text(json.dumps({'image_id': 'ubuntu-24.04', 'path': str(ova),
                                    'sha256': hashlib.sha256(ova.read_bytes()).hexdigest()}))
    ex.image_metadata = metadata
    return ex


@pytest.mark.parametrize('foreign_pool', [False, True])
def test_sdk_import_ticket_uuid_and_configuration_order(tmp_path, foreign_pool):
    from types import SimpleNamespace as NS
    from pyVmomi import vim
    ex = executor(tmp_path)
    request = job()
    request['prepared_plan'] = ex.prepare_plan(request, lambda *args: None)
    folder = ex.state_dir / request['id']
    signed = json.loads((folder / 'review.json').read_text())
    ex._execution_job = request['id']
    uuid = '12345678-1234-1234-1234-123456789abc'
    marker = 'kuberweaver-job:' + request['id']
    disk = vim.vm.device.VirtualDisk(key=1000, capacityInKB=3 * 1024**2)
    nic = vim.vm.device.VirtualVmxnet3(key=4000)
    events = []
    vm = NS(name='ubuntu-test', config=NS(uuid=uuid, annotation=marker,
            hardware=NS(device=[disk, nic], numCPU=1, memoryMB=1024)),
            runtime=NS(powerState='poweredOff'))
    delivered = {}
    def reconfigure(spec):
        events.append('reconfigure')
        for entry in (spec.extraConfig or []):
            delivered[entry.key] = entry.value
        vm.config.hardware.numCPU = spec.numCPUs
        vm.config.hardware.memoryMB = spec.memoryMB
        return NS(info=NS(state='success'))
    def poweron():
        events.append('poweron')
        assert vm.config.hardware.numCPU == 2
        return NS(info=NS(state='success'))
    vm.ReconfigVM_Task = reconfigure
    vm.PowerOnVM_Task = poweron
    view = NS(view=[vm], Destroy=lambda: None)
    content = NS(rootFolder=None, sessionManager=NS(AcquireCloneTicket=lambda: 'one-shot-ticket'),
                 viewManager=NS(CreateContainerView=lambda *args: view),
                 searchIndex=NS(FindByUuid=lambda *args: vm))
    ex.connections._connector = lambda **kw: NS(RetrieveContent=lambda: content, _stub=None)
    ex.connections._disconnector = lambda si: None
    original_discover = ex.connections.discover
    def discover(*args):
        value = original_discover(*args)
        value['networks'][0]['name'] = 'VM Network'
        return value
    ex.connections.discover = discover
    def runner(argv, **kwargs):
        if argv[0] == str(ex.ovftool):
            events.append('import')
            assert argv[-1] == 'vi://192.0.2.50/'
            # Under --I:morefArgs ovftool needs typed managed-object references.
            assert '--datastore=vim.Datastore:datastore-1' in argv
            assert '--net:VM Network=vim.Network:network-1' in argv
            assert not any(arg.startswith('--datastore=Datastore:') for arg in argv)
            for key, name in [('HOME', 'ovf-home'), ('TMPDIR', 'ovf-tmp')]:
                directory = Path(kwargs['env'][key])
                assert directory == folder / name
                assert directory.stat().st_mode & 0o777 == 0o700
            (folder / 'ovf-home' / '.vmware').mkdir()
            (folder / 'ovf-tmp' / 'vmware-ubu').mkdir()
            (folder / 'ovf-tmp' / 'vmware-ubu' / 'ovftool.log').write_text('native log')
            assert kwargs['input'] == 'one-shot-ticket\nno\n'
            assert '--I:targetSessionTicket=prompt' in argv
            assert '--targetSSLThumbprint=' + ':'.join(['CC'] * 32) in argv
            assert '--net:VM Network=vim.Network:network-1' in argv
            assert not any('fixture-password-only' in arg or 'one-shot-ticket' in arg for arg in argv)
            assert '--powerOn' not in argv and '--overwrite' not in argv
            assert not any('noSSLVerify' in arg for arg in argv)
        return subprocess.CompletedProcess(argv, 0, '', '')
    ex.runner = runner
    vm.resourcePool = NS(_GetMoId=lambda: 'foreign-pool' if foreign_pool else 'ha-root-pool')
    vm.datastore = [NS(_GetMoId=lambda: 'datastore-1')]
    if foreign_pool:
        with pytest.raises(ValueError, match='placement'):
            ex._import_vm(folder, signed, ex.connections.row, ex.connections.raw)
        assert events == ['import']
        return
    result = ex._import_vm(folder, signed, ex.connections.row, ex.connections.raw)
    assert result['uuid'] == uuid
    assert events == ['import', 'reconfigure', 'poweron']
    # The guest bootstrap must ride on guestinfo, which is the only cloud-init
    # transport available on standalone ESXi.
    assert delivered['guestinfo.metadata.encoding'] == 'base64'
    assert delivered['guestinfo.userdata.encoding'] == 'base64'
    import base64 as b64
    import yaml
    meta = yaml.safe_load(b64.b64decode(delivered['guestinfo.metadata']))
    user_data = b64.b64decode(delivered['guestinfo.userdata']).decode()
    assert meta['local-hostname'] == 'ubuntu-test'
    assert meta['instance-id'] == marker
    assert user_data.startswith('#cloud-config')
    assert 'ssh_pwauth: false' in user_data
    assert 'ssh-ed25519' in user_data
    assert json.loads((folder / 'owned.json').read_text())['uuid'] == uuid


def test_cloud_init_payload_refuses_a_missing_key():
    """A bootstrap without a key would produce an unreachable guest."""
    with pytest.raises(ValueError, match='SSH key is unavailable'):
        module().cloud_init_config('ubuntu-test', '   ', 'instance-1')


def test_import_refuses_a_vanished_inventory_reference(tmp_path):
    """A reference that disappeared between review and apply must be refused
    before any native command crosses."""
    ex = executor(tmp_path)
    request = job()
    request['prepared_plan'] = ex.prepare_plan(request, lambda *args: None)
    folder = ex.state_dir / request['id']
    signed = json.loads((folder / 'review.json').read_text())
    ex._execution_job = request['id']
    original = ex.connections.discover

    def poisoned(*args):
        value = original(*args)
        value['datastores'] = []
        return value

    ex.connections.discover = poisoned
    from types import SimpleNamespace as NS
    content = NS(sessionManager=NS(AcquireCloneTicket=lambda: 'one-shot-ticket'))
    ex.connections._connector = lambda **kw: NS(RetrieveContent=lambda: content, _stub=None)
    ex.connections._disconnector = lambda si: None
    ex.runner = lambda *a, **kw: pytest.fail('No native command may cross')
    with pytest.raises(ValueError, match='reference disappeared'):
        ex._import_vm(folder, signed, ex.connections.row, ex.connections.raw)


def test_native_ovftool_error_is_reported_and_logged_privately(tmp_path):
    """A rejected target must surface the tool's own reason and keep the full
    output in the private job folder."""
    ex = executor(tmp_path)
    request = job()
    request['prepared_plan'] = ex.prepare_plan(request, lambda *args: None)
    folder = ex.state_dir / request['id']
    signed = json.loads((folder / 'review.json').read_text())
    ex._execution_job = request['id']
    from types import SimpleNamespace as NS
    content = NS(sessionManager=NS(AcquireCloneTicket=lambda: 'one-shot-ticket'))
    ex.connections._connector = lambda **kw: NS(RetrieveContent=lambda: content, _stub=None)
    ex.connections._disconnector = lambda si: None
    # Admission gates are covered elsewhere; this test targets the native error path.
    ex._sdk_guard = lambda *a, **k: None
    ex.runner = lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 1, 'Opening VI target\nError: Invalid target datastore specified (Datastore:x)\n', '')
    with pytest.raises(ValueError, match='Invalid target datastore specified'):
        ex._import_vm(folder, signed, ex.connections.row, ex.connections.raw)
    logged = json.loads((folder / 'ovf-output.log').read_text())
    assert logged['returncode'] == 1
    assert 'Invalid target datastore specified' in logged['stdout']
    assert (folder / 'ovf-output.log').stat().st_mode & 0o777 == 0o600


def test_approved_real_tofu_apply_calls_single_injected_import(tmp_path):
    ex = executor(tmp_path)
    request = job()
    request['prepared_plan'] = ex.prepare_plan(request, lambda *args: None)
    request.update(approved_by='operator', approved_at='now',
                   approved_graph_hash=module().digest(request['graph']),
                   approved_execution_provider={'name': 'esxi-ovf'},
                   approved_prepared_plan=module().digest(request['prepared_plan']))
    calls = []
    uuid = '12345678-1234-1234-1234-123456789abc'
    expected = {'uuid': uuid, 'name': 'ubuntu-test', 'cpu': 2, 'memory_mb': 2048, 'power_state': 'poweredOn'}
    def imported(*args):
        calls.append(True)
        return expected
    ex._import_vm = imported
    ex.connections.read_resource = lambda *args: dict(expected)
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'succeeded'
    assert calls == [True]
    assert ex.execute(request, lambda *args: None)['requires_manual_review'] is True
    assert calls == [True]


def test_real_builtin_saved_plan_pins_and_no_remote_mutation(tmp_path):
    ex = executor(tmp_path)
    request = job()
    prepared = ex.prepare_plan(request, lambda *args: None)
    assert prepared['actions'] == {'terraform_data.vm': ['create']}
    assert prepared['summary']['image_id'] == 'ubuntu-24.04'
    assert prepared['summary']['guest_user'] == 'ubuntu'
    request['prepared_plan'] = prepared
    ex.validate_plan(request)
    folder = ex.state_dir / request['id']
    assert not (folder / 'attempt.json').exists()
    assert b'fixture-password-only' not in (folder / 'create.plan').read_bytes()
    ex.connections.row['tls_sha256'] = 'd' * 64
    with pytest.raises(ValueError):
        ex.validate_plan(request)

ROOT = Path(__file__).resolve().parents[1]


def module():
    return importlib.import_module('kuberweaver.ovf_provisioning')


def job():
    return {'id': 'a' * 32, 'graph': {'environment': 'dev', 'nodes': [
        {'id': 'vm', 'plugin_id': 'vmware-vm', 'config': {
            'connection_id': 'b' * 32, 'vm_name': 'ubuntu-test', 'datastore_id': 'datastore-1',
            'network_ids': ['network-1'], 'resource_pool_id': 'ha-root-pool',
            'ssh_key_id': 'd' * 32}}],
        'edges': [], 'failure_mode': 'none'}}


@pytest.mark.parametrize('change', ['image', 'credential', 'plan'])
def test_drift_refused_before_approved_apply(tmp_path, change):
    ex = executor(tmp_path)
    request = job()
    request['prepared_plan'] = ex.prepare_plan(request, lambda *args: None)
    request.update(approved_by='operator', approved_at='now',
                   approved_graph_hash=module().digest(request['graph']),
                   approved_execution_provider={'name': 'esxi-ovf'},
                   approved_prepared_plan=module().digest(request['prepared_plan']))
    if change == 'image':
        Path(json.loads(ex.image_metadata.read_text())['path']).write_bytes(b'changed')
    elif change == 'credential':
        ex.connections.raw = ex.connections.raw + b' '
    else:
        (ex.state_dir / request['id'] / 'create.plan').write_bytes(b'changed')
    ex.runner = lambda *args, **kw: pytest.fail('No command may cross after drift')
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'failed'
    assert not (ex.state_dir / request['id'] / 'attempt.json').exists()


def test_interruption_is_durable_and_not_replayed(tmp_path):
    ex = executor(tmp_path)
    request = job()
    request['prepared_plan'] = ex.prepare_plan(request, lambda *args: None)
    request.update(approved_by='operator', approved_at='now',
                   approved_graph_hash=module().digest(request['graph']),
                   approved_execution_provider={'name': 'esxi-ovf'},
                   approved_prepared_plan=module().digest(request['prepared_plan']))
    def interrupted(*args, **kw):
        raise KeyboardInterrupt()
    ex.runner = interrupted
    result = ex.execute(request, lambda *args: None)
    assert result['requires_manual_review'] is True
    fresh = module().OVFExecutor(ex.state_dir, ex.template_dir)
    fresh.connections, fresh.image_metadata = ex.connections, ex.image_metadata
    fresh.runner = lambda *args, **kw: pytest.fail('Interrupted apply must not replay')
    assert fresh.execute(request, lambda *args: None)['requires_manual_review'] is True


def test_image_minimum_disk_checked_before_attempt(tmp_path):
    ex = executor(tmp_path)
    request = job()
    # The production schema minimum is 10 GiB; descriptor is larger.
    request['graph']['nodes'][0]['config']['disk_gb'] = 10
    import tarfile
    import io
    image = json.loads(ex.image_metadata.read_text())
    ova = Path(image['path'])
    ovf = b'<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1" xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"><DiskSection><Disk ovf:capacity="21474836480" ovf:capacityAllocationUnits="byte"/></DiskSection></Envelope>'
    with tarfile.open(ova, 'w') as archive:
        entry = tarfile.TarInfo('fixture.ovf')
        entry.size = len(ovf)
        archive.addfile(entry, io.BytesIO(ovf))
    image['sha256'] = hashlib.sha256(ova.read_bytes()).hexdigest()
    ex.image_metadata.write_text(json.dumps(image))
    with pytest.raises(ValueError, match='disk'):
        ex.prepare_plan(request, lambda *args: None)
    assert not (ex.state_dir / request['id']).exists()


def test_transport_contract_fails_closed_when_namespace_unavailable(monkeypatch):
    from kuberweaver import ovf_transport
    def unavailable():
        raise ValueError('No user namespaces')
    monkeypatch.setattr(ovf_transport, 'require_transport', unavailable)
    with pytest.raises(ValueError, match='transport'):
        module().OVFExecutor._transport_contract()


def test_pins_hash_image_once_and_ignore_mutable_native_logs(tmp_path, monkeypatch):
    ex = executor(tmp_path)
    request = job()
    ex.prepare_plan(request, lambda *args: None)
    folder = ex.state_dir / request['id']
    image_path = Path(json.loads(ex.image_metadata.read_text())['path'])
    original = module().sha256
    calls = []
    def counted(path):
        if Path(path) == image_path:
            calls.append(path)
        return original(path)
    monkeypatch.setattr(module(), 'sha256', counted)
    before = ex._pins(folder)
    assert len(calls) == 1
    log = folder / 'ovf-tmp' / 'native.log'
    log.write_text('mutable')
    (folder / 'ovf-trust').mkdir(mode=0o700)
    assert ex._pins(folder) == before
    log.write_text('changed')
    assert ex._pins(folder) == before
    image_path.write_bytes(b'drift')
    with pytest.raises(ValueError, match='checksum'):
        ex._pins(folder)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_native_logs_forbid_links(tmp_path, kind):
    ex = executor(tmp_path)
    request = job()
    ex.prepare_plan(request, lambda *args: None)
    folder = ex.state_dir / request['id']
    source = tmp_path / 'foreign.log'
    source.write_text('foreign')
    target = folder / 'ovf-home' / 'native.log'
    if kind == 'symlink':
        target.symlink_to(source)
    else:
        os.link(source, target)
    with pytest.raises(ValueError, match='[Ss]ymlink|hardlink'):
        ex._artifacts(folder)


@pytest.mark.parametrize('kind', ['file', 'public'])
def test_native_write_roots_must_be_private_directories(tmp_path, kind):
    ex = executor(tmp_path)
    request = job()
    ex.prepare_plan(request, lambda *args: None)
    folder = ex.state_dir / request['id']
    root = folder / 'ovf-home'
    if kind == 'file':
        root.rmdir()
        root.write_text('not a directory')
    else:
        root.chmod(0o755)
    with pytest.raises(ValueError, match='private directory'):
        ex._artifacts(folder)


def test_reference_control_characters_rejected(tmp_path):
    ex = executor(tmp_path)
    request = job()
    request['graph']['nodes'][0]['config']['network_ids'] = ['HaNetwork-VM Network']
    assert ex.review(request)['config']['network_ids'] == ['HaNetwork-VM Network']
    request['graph']['nodes'][0]['config']['network_ids'] = ['HaNetwork-VM Network\n']
    with pytest.raises(ValueError):
        ex.review(request)


def test_closed_ubuntu_blueprint(tmp_path):
    ex = executor(tmp_path)
    reviewed = ex.review(job())
    assert reviewed['config']['cpu'] == 2
    assert reviewed['config']['cpu_sockets'] == 1
    assert reviewed['config']['cores_per_socket'] == 2
    assert reviewed['config']['memory_mb'] == 2048
    assert reviewed['config']['disk_gb'] == 20
    assert reviewed['config']['image_id'] == 'ubuntu-24.04'
    for change in ({'disk_gb': 7}, {'disk_gb': 101}, {'image_id': 'url'}, {'password': 'secret'},
                   {'network_ids': []}, {'ssh_public_key': 'PRIVATE KEY'}, {'cores_per_socket': True},
                   {'cpu': 4}):
        request = copy.deepcopy(job())
        request['graph']['nodes'][0]['config'].update(change)
        with pytest.raises(ValueError):
            ex.review(request)


@pytest.mark.parametrize('sockets,cores,total', [(1, 1, 1), (1, 4, 4), (2, 2, 4), (2, 4, 8)])
def test_socket_and_core_topology_is_resolved_to_total_vcpus(tmp_path, sockets, cores, total):
    ex = executor(tmp_path)
    request = job()
    request['graph']['nodes'][0]['config'].update(cpu_sockets=sockets, cores_per_socket=cores)
    reviewed = ex.review(request)
    assert reviewed['config']['cpu'] == total
    assert reviewed['config']['cpu_sockets'] == sockets
    assert reviewed['config']['cores_per_socket'] == cores
    summary = ex.prepare_plan(request, lambda *args: None)['summary']
    assert (summary['cpu'], summary['cpu_sockets'], summary['cores_per_socket']) == (total, sockets, cores)


@pytest.mark.parametrize('sockets,cores', [(2, 8), (4, 8), (4, 3), (0, 2)])
def test_socket_topology_beyond_host_limits_is_refused(tmp_path, sockets, cores):
    ex = executor(tmp_path)
    request = job()
    request['graph']['nodes'][0]['config'].update(cpu_sockets=sockets, cores_per_socket=cores)
    with pytest.raises(ValueError):
        ex.review(request)


def test_admission_accepts_declared_topology_and_charges_total_vcpus(tmp_path):
    from kuberweaver.catalog import Catalog
    from kuberweaver.policy import Admission
    from kuberweaver.storage import Store

    graph = job()['graph'] | {'name': 'Topology admission', 'strategy': {'canary': 1, 'batch_size': 1}}
    graph['nodes'] = [dict(graph['nodes'][0], config=dict(graph['nodes'][0]['config'],
                                                         cpu_sockets=2, cores_per_socket=4))]
    admission = Admission(Catalog(ROOT / 'plugins'), Store(tmp_path / 'state'))
    result = admission.validate(graph)
    assert result['allowed'], result['violations']
    assert result['cost']['breakdown'][0]['resources']['cpu'] == 8


def test_admission_refuses_a_total_cpu_field_the_schema_does_not_declare(tmp_path):
    from kuberweaver.catalog import Catalog
    from kuberweaver.policy import Admission
    from kuberweaver.storage import Store

    graph = job()['graph'] | {'name': 'Legacy cpu field', 'strategy': {'canary': 1, 'batch_size': 1}}
    graph['nodes'] = [dict(graph['nodes'][0], config=dict(graph['nodes'][0]['config'],
                                                         cpu=2, cpu_sockets=1, cores_per_socket=2))]
    result = Admission(Catalog(ROOT / 'plugins'), Store(tmp_path / 'state')).validate(graph)
    assert not result['allowed']
    assert [v['code'] for v in result['violations']] == ['config_schema']
    # The offending field name must be reported so the form can be corrected.
    assert result['violations'][0]['message'].endswith(': cpu')
