"""Saved-plan safety tests. Process/TLS/SDK boundaries are fake, never ESXi."""
import copy
import importlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
from contextlib import contextmanager
from typing import Any

import pytest
from test_core_graph import api


def module():
    return importlib.import_module('kuberweaver.provisioning')


def job():
    return {'id': 'a' * 32, 'graph': {'name': 'VM lab', 'environment': 'dev', 'nodes': [
        {'id': 'vm1', 'plugin_id': 'vsphere-vm', 'config': {
            'connection_id': 'b' * 32, 'vm_name': 'blank-vm', 'datastore_id': 'datastore-1',
            'network_ids': ['network-1'], 'resource_pool_id': 'resgroup-1',
            'cpu': 1, 'memory_mb': 128, 'disk_gb': 1, 'guest_id': 'otherLinux64Guest'}}],
        'edges': [], 'failure_mode': 'none'}}


def test_review_accepts_only_one_closed_independent_dev_vm(tmp_path):
    ex = module().SavedPlanExecutor(tmp_path, module().ROOT / 'modules/resources/vsphere-vm')
    request = job()
    reviewed = ex.review(request)
    assert reviewed['name'] == 'kw-aaaaaaaaaaaa-blank-vm'
    assert reviewed['config'] == request['graph']['nodes'][0]['config']
    invalid = [({'targets': ['app']}, None), ({'cpu': True}, None),
               ({'cpu': 9}, None), ({'memory_mb': 127}, None), ({'disk_gb': 101}, None),
               ({'connection_id': 'not-a-reference'}, None), ({'network_ids': []}, None),
               ({'network_ids': ['network-1', 'network-1']}, None), ({'vm_name': '../vm'}, None),
               ({'guest_id': 'customGuest'}, None), ({}, {'environment': 'prod'}),
               ({}, {'edges': [{'source': 'a', 'target': 'b'}]}),
               ({}, {'failure_mode': 'canary'})]
    for config, graph in invalid:
        candidate = copy.deepcopy(request)
        candidate['graph']['nodes'][0]['config'].update(config)
        candidate['graph'].update(graph or {})
        with pytest.raises(ValueError):
            ex.review(candidate)
    assert ex.requires_saved_plan is True
    assert ex.supports_rollback is False
    assert ex.can_rollback(request) is False
    assert ex.rollback(request, lambda *args: None)['status'] == 'rollback_failed'


class ConnectionsBoundary:
    """No sockets: application connection service / SDK explicit fake boundary."""
    def __init__(self):
        self.connection = {'id': 'b' * 32, 'driver': 'esxi', 'endpoint': 'https://192.0.2.50',
                           'enabled': True, 'revision': 1, 'tls_sha256': 'c' * 64}
        self.credentials = json.dumps({'username': 'fixture-admin', 'password': 'fixture-secret-ONLY'}).encode()
        self.inventory = {'server': {'name': 'ESXi', 'version': '8.0', 'api_type': 'HostAgent',
                                     'host_id': 'ha-host', 'write_api_supported': True},
                          'datastores': [{'id': 'datastore-1', 'name': 'disk', 'accessible': True,
                                         'capacity_bytes': 200 * 1024**3, 'free_bytes': 100 * 1024**3}],
                          'networks': [{'id': 'network-1', 'name': 'VM Network'}],
                          'resource_pools': [{'id': 'resgroup-1', 'name': 'Resources'}],
                          'virtual_machines': []}
        self.discoveries = 0

    def get(self, identity):
        assert identity == self.connection['id']
        return copy.deepcopy(self.connection)

    def fingerprint(self, identity):
        return module().digest(self.get(identity))

    @contextmanager
    def access(self, identity):
        yield self.get(identity), self.credentials

    def driver(self, connection):
        return self

    def discover(self, connection, credentials):
        assert credentials == self.credentials
        self.discoveries += 1
        return copy.deepcopy(self.inventory)


def test_pki_failure_is_explicit_before_tofu_or_sdk(tmp_path, monkeypatch):
    m = module()
    ex = m.SavedPlanExecutor(tmp_path / 'state', m.ROOT / 'modules/resources/vsphere-vm',
                             runner=lambda *a, **kw: pytest.fail('No command on PKI failure'))
    ex.connections = ConnectionsBoundary()
    def certificate_failure(*args, **kwargs):
        raise ssl.SSLCertVerificationError('fixture-secret-ONLY hostname mismatch')
    monkeypatch.setattr(socket, 'create_connection', certificate_failure)
    with pytest.raises(ValueError, match='PKI.*hostname') as error:
        ex.prepare_plan(job(), lambda *args: None)
    assert 'fixture-secret' not in str(error.value)
    assert ex.connections.discoveries == 0
    assert not (ex.state_dir / job()['id']).exists()


def test_local_template_init_validate_and_real_schema(tmp_path):
    """Real local CLI/provider only; no credentials, plan, apply or ESXi network."""
    if os.environ.get('KW_TOFU_LOCAL_CHECK') != '1':
        pytest.skip('explicit registry download opt-in: KW_TOFU_LOCAL_CHECK=1')
    m = module()
    template = m.ROOT / 'modules/resources/vsphere-vm/main.tf'
    assert template.is_file(), 'Reviewed HCL template is missing'
    (tmp_path / 'main.tf').write_bytes(template.read_bytes())
    (tmp_path / '.terraform.lock.hcl').write_bytes((template.parent / '.terraform.lock.hcl').read_bytes())
    (tmp_path / 'cli.tfrc').write_text('disable_checkpoint = true\n')
    # Provider ValidateProviderConfig requires nonempty env defaults even offline.
    # Deliberately public placeholders, never a live connection or usable secret.
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'TF_INPUT': '0',
           'VSPHERE_USER': 'local-validation-placeholder', 'VSPHERE_PASSWORD': 'not-a-credential',
           'CHECKPOINT_DISABLE': '1', 'TF_CLI_CONFIG_FILE': str(tmp_path / 'cli.tfrc')}
    def run(*args):
        result = subprocess.run([str(m.ROOT / 'tools/tofu'), *args], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr
        return result.stdout
    version = json.loads(run('version', '-json'))
    assert version['terraform_version'] == '1.13.9'
    run('init', '-backend=false', '-input=false', '-no-color', '-lockfile=readonly')
    assert json.loads(run('validate', '-json'))['valid'] is True
    schemas = json.loads(run('providers', 'schema', '-json'))['provider_schemas']
    vm = next(iter(schemas.values()))['resource_schemas']['vsphere_virtual_machine']['block']['attributes']
    assert vm['power_state']['computed'] is True
    assert not vm['power_state'].get('optional', False)
    assert 'power_on' not in vm  # Real blocker: do not invent HCL for a stopped VM.


def test_official_provider_stopped_create_blocks_before_planning(tmp_path, monkeypatch):
    ex = module().SavedPlanExecutor(tmp_path, module().ROOT / 'modules/resources/vsphere-vm',
                                    runner=lambda *a, **kw: pytest.fail('No command expected'))
    ex.connections = ConnectionsBoundary()
    monkeypatch.setattr(ex, '_tls', lambda connection: 'c' * 64)
    with pytest.raises(ValueError, match='2.18.0.*stopped'):
        ex.prepare_plan(job(), lambda *args: None)
    assert ex.connections.discoveries == 0
    assert ex.read()['status'] == 'blocked'
    assert ex.read()['capability'] == 'planned'


class TofuBoundary:
    """Only subprocess is fake; real files/digests/flock. No ESXi semantics proof."""
    def __init__(self, connections):
        self.calls = []
        self.connections = connections
        self.change: Any = None
        self.fail_apply = False
        self.after_apply: Any = None
        self.show_secret = False
        self.extra_artifact = None

    def __call__(self, argv, **kwargs):
        from types import SimpleNamespace
        folder = Path(kwargs['cwd'])
        self.calls.append((list(argv), dict(kwargs['env'])))
        out, code = '', 0
        if 'init' in argv:
            assert '-lockfile=readonly' in argv
            assert (folder / '.terraform.lock.hcl').is_file()
            provider = folder / '.terraform/providers/registry.opentofu.org/vmware/vsphere/2.18.0/linux_amd64'
            provider.mkdir(parents=True)
            (provider / 'terraform-provider-vsphere_v2.18.0').write_bytes(b'FAKE provider, not ESXi evidence')
        elif 'validate' in argv:
            out = '{"valid":true}'
        elif 'plan' in argv:
            (folder / 'create.plan').write_bytes(b'FAKE saved plan exact bytes')
            code = 2
        elif 'show' in argv:
            if argv[-1] == 'create.plan':
                change = {'address': 'vsphere_virtual_machine.vm', 'mode': 'managed',
                          'type': 'vsphere_virtual_machine', 'change': {'actions': ['create'], 'before': None,
                          'after': {'name': 'kw-aaaaaaaaaaaa-blank-vm'}}}
                out = json.dumps(self.change or {'resource_changes': [change]})
                if self.show_secret:
                    out = out[:-1] + ',"leak":"fixture-secret-ONLY"}'
            else:
                out = (folder / 'terraform.tfstate').read_text()
        elif 'apply' in argv:
            config = json.loads((folder / 'terraform.tfvars.json').read_text())
            vm = {'id': 'vm-1', 'uuid': '42222222-2222-2222-2222-222222222222',
                  'name': config['name'], 'cpu': config['cpu'], 'memory_mb': config['memory_mb'],
                  'power_state': 'poweredOff'}
            self.connections.inventory['virtual_machines'] = [vm]
            (folder / 'terraform.tfstate').write_text(json.dumps({'values': {'root_module': {'resources': [
                {'address': 'vsphere_virtual_machine.vm', 'mode': 'managed', 'values': {
                    'uuid': vm['uuid'], 'id': vm['uuid'], 'name': vm['name'], 'num_cpus': vm['cpu'],
                    'memory': vm['memory_mb'], 'power_state': 'off'}}]}}}))
            if self.after_apply:
                self.after_apply(folder)
            if self.fail_apply:
                raise subprocess.TimeoutExpired(argv, 1, output='fixture-secret-ONLY')
        if self.extra_artifact:
            (folder / self.extra_artifact).write_text('unapproved override')
        return SimpleNamespace(returncode=code, stdout=out, stderr='fixture-secret-ONLY MUST NOT ESCAPE')


def executor(tmp_path, monkeypatch):
    m = module()
    binary = tmp_path / 'tofu'
    binary.write_bytes(b'FAKE subprocess binary')
    binary.chmod(0o700)
    monkeypatch.setattr(m, 'TOFU_SHA256', __import__('hashlib').sha256(binary.read_bytes()).hexdigest(), raising=False)
    template = tmp_path / 'template'
    template.mkdir()
    for name in ('manifest.yaml', 'main.tf', '.terraform.lock.hcl'):
        (template / name).write_bytes((m.ROOT / 'modules/resources/vsphere-vm' / name).read_bytes())
    connections = ConnectionsBoundary()
    boundary = TofuBoundary(connections)
    ex = m.SavedPlanExecutor(tmp_path / 'state', template, binary=binary, runner=boundary)
    ex.connections = connections
    ex.ca_bundle = tmp_path / 'ca.pem'
    ex.ca_bundle.write_text('FAKE CA for injected TLS boundary')
    monkeypatch.setattr(ex, '_tls', lambda connection: 'c' * 64)
    # TEST ONLY: exercise inert saved-plan machinery behind the hard production
    # blocker. This does NOT suggest official vSphere can create a stopped VM.
    monkeypatch.setattr(ex, '_stopped_contract', lambda: None)
    return ex, boundary


def test_saved_plan_is_private_and_summary_contains_no_raw_artifacts(tmp_path, monkeypatch):
    ex, boundary = executor(tmp_path, monkeypatch)
    events = []
    prepared = ex.prepare_plan(job(), lambda *args: events.append(args))
    assert set(prepared) == {'plan_digest', 'actions', 'summary', 'bindings'}
    assert len(prepared['plan_digest']) == 64
    assert prepared['actions'] == {'vsphere_virtual_machine.vm': ['create']}
    assert prepared['summary']['name'] == 'kw-aaaaaaaaaaaa-blank-vm'
    assert prepared['summary']['os_installed'] is False
    assert prepared['summary']['power_state'] == 'poweredOff'
    folder = ex.state_dir / job()['id']
    assert (folder / 'create.plan').read_bytes() == b'FAKE saved plan exact bytes'
    assert (folder / 'review.json').is_file()
    for path in [ex.state_dir, folder, *folder.rglob('*')]:
        assert path.stat().st_mode & 0o077 == 0
    public = json.dumps([prepared, events])
    assert 'fixture-secret' not in public and str(ex.state_dir) not in public
    assert 'FAKE saved plan' not in public
    assert not any('apply' in argv for argv, _ in boundary.calls)
    for path in folder.rglob('*'):
        if path.is_file():
            assert b'fixture-secret-ONLY' not in path.read_bytes()
    with pytest.raises(ValueError, match='Existing'):
        ex.prepare_plan(job(), lambda *args: None)


@pytest.mark.parametrize('blocked', ['tls', 'access'])
def test_preparation_refuses_target_drift_during_initial_blocking_io(tmp_path, monkeypatch, blocked):
    """Pause injected TLS/vault boundaries; no actual network or CLI execution."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    ex, boundary = executor(tmp_path, monkeypatch)
    request, events = job(), []
    identity = request['graph']['nodes'][0]['config']['connection_id']
    origin = ex.connections.get(identity)
    raw = ex.connections.credentials
    entered, release = threading.Event(), threading.Event()
    trace = []
    def pause(connection):
        if not entered.is_set():
            trace.append((blocked, copy.deepcopy(connection)))
            entered.set()
            assert release.wait(10)
    if blocked == 'tls':
        def blocked_tls(connection):
            pause(connection)
            return 'c' * 64
        monkeypatch.setattr(ex, '_tls', blocked_tls)
    else:
        access = ex.connections.access
        @contextmanager
        def blocked_access(identity):
            with access(identity) as (connection, credentials):
                pause(connection)  # Snapshot captured before the vault returns.
                yield connection, credentials
        monkeypatch.setattr(ex.connections, 'access', blocked_access)
    def prepare():
        request['prepared_plan'] = ex.prepare_plan(request, lambda *args: events.append(args))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(prepare)
        try:
            assert entered.wait(10)
            ex.connections.connection.update(endpoint='https://192.0.2.51', revision=origin['revision'] + 1)
        finally:
            release.set()
        with pytest.raises(ValueError, match='Connection.*changed'):
            pending.result(timeout=10)
    assert trace == [(blocked, origin)]
    assert ex.connections.credentials == raw  # Identical secrets must not mask a different target.
    assert 'prepared_plan' not in request and events == []
    assert not (ex.state_dir / request['id'] / 'review.json').exists()
    assert ex.connections.discoveries == 0 and boundary.calls == []
    with pytest.raises(ValueError):
        ex.validate_plan(request)
    assert ex.execute(request, lambda *args: None)['status'] == 'failed'
    assert not any('apply' in argv for argv, _ in boundary.calls)


@pytest.mark.parametrize('field,value', [
    ('id', 'd' * 32), ('revision', 2), ('endpoint', 'https://192.0.2.51'),
    ('driver', 'other-esxi'), ('secrets_provider', 'other-secrets'), ('secret_ref', 'esxi/other'),
    ('tls_sha256', 'd' * 64), ('enabled', False), ('label', 'Other lab')])
def test_preparation_rejects_an_access_snapshot_outside_the_local_origin(tmp_path, monkeypatch, field, value):
    ex, boundary = executor(tmp_path, monkeypatch)
    request, events, tls_reads = job(), [], []
    ex.connections.connection.update(label='ESXi lab', secrets_provider='restricted-file', secret_ref='esxi/lab')
    identity = request['graph']['nodes'][0]['config']['connection_id']
    original_binding = ex.connections.fingerprint(identity)
    access = ex.connections.access
    @contextmanager
    def mismatched_access(identity):
        with access(identity) as (connection, raw):
            connection[field] = value  # Current record/fingerprint stay unchanged.
            yield connection, raw
    monkeypatch.setattr(ex.connections, 'access', mismatched_access)
    monkeypatch.setattr(ex, '_tls', lambda connection: tls_reads.append(connection['endpoint']))
    with pytest.raises(ValueError, match='Connection binding changed'):
        request['prepared_plan'] = ex.prepare_plan(request, lambda *args: events.append(args))
    assert ex.connections.fingerprint(identity) == original_binding
    assert 'prepared_plan' not in request and events == []
    assert not (ex.state_dir / request['id'] / 'review.json').exists()
    assert tls_reads == [] and ex.connections.discoveries == 0 and boundary.calls == []


def test_preparation_keeps_the_origin_binding_when_only_cached_observations_change(tmp_path, monkeypatch):
    ex, boundary = executor(tmp_path, monkeypatch)
    request, events, trace = job(), [], []
    identity = request['graph']['nodes'][0]['config']['connection_id']
    ex.connections.connection['observation'] = None
    get = ex.connections.get
    def local_metadata(identity):
        trace.append('metadata')
        return get(identity)
    def local_fingerprint(identity):
        trace.append('fingerprint')
        # Real Connections.fingerprint excludes the observation cache too.
        return module().digest({key: value for key, value in get(identity).items() if key != 'observation'})
    binding = local_fingerprint(identity)
    trace.clear()
    access = ex.connections.access
    @contextmanager
    def observed_access(identity):
        trace.append('access')
        with access(identity) as (connection, raw):
            ex.connections.connection['observation'] = {'status': 'reachable', 'sample': len(trace)}
            yield connection, raw
    def tls(connection):
        trace.append('tls')
        assert connection['endpoint'] == 'https://192.0.2.50'
        return 'c' * 64
    monkeypatch.setattr(ex.connections, 'get', local_metadata)
    monkeypatch.setattr(ex.connections, 'fingerprint', local_fingerprint)
    monkeypatch.setattr(ex.connections, 'access', observed_access)
    monkeypatch.setattr(ex, '_tls', tls)
    request['prepared_plan'] = ex.prepare_plan(request, lambda *args: events.append(args))
    assert trace[:8] == ['fingerprint', 'metadata', 'fingerprint', 'access',
                         'metadata', 'fingerprint', 'tls', 'fingerprint']
    folder = ex.state_dir / request['id']
    signed = json.loads((folder / 'review.json').read_text())
    assert signed['bindings']['connection_fingerprint'] == binding
    assert request['prepared_plan']['bindings'] == signed['bindings']
    assert json.loads((folder / 'terraform.tfvars.json').read_text())['server'] == '192.0.2.50'
    assert ex.validate_plan(request) is None
    assert len(events) == 1 and events[0][0] == 'plan'
    assert [argv[1] for argv, _ in boundary.calls] == ['init', 'validate', 'plan', 'show']


@pytest.mark.parametrize('target', ['plan', 'provider', 'connection'])
def test_preparation_never_publishes_drift_after_last_show_guard(tmp_path, monkeypatch, target):
    ex, boundary = executor(tmp_path, monkeypatch)
    request, events = job(), []
    folder = ex.state_dir / request['id']
    actions = ex._actions
    def drift_after_show(document):
        assert boundary.calls[-1][0][1:] == ['show', '-json', 'create.plan']
        result = actions(document)
        if target == 'connection':
            ex.connections.connection['revision'] += 1
        else:
            path = (folder / 'create.plan' if target == 'plan' else folder /
                    '.terraform/providers/registry.opentofu.org/vmware/vsphere/2.18.0/linux_amd64/terraform-provider-vsphere_v2.18.0')
            path.write_bytes(b'UNREVIEWED replacement after show')
        return result
    monkeypatch.setattr(ex, '_actions', drift_after_show)
    with pytest.raises(ValueError, match='changed'):
        request['prepared_plan'] = ex.prepare_plan(request, lambda *args: events.append(args))
    assert 'prepared_plan' not in request
    assert not (folder / 'review.json').exists()
    assert events == []
    with pytest.raises(ValueError):
        ex.validate_plan(request)
    assert ex.execute(request, lambda *args: None)['status'] == 'failed'
    assert not any('apply' in argv for argv, _ in boundary.calls)


@pytest.mark.parametrize('kind', ['update', 'replace', 'delete', 'noop', 'foreign', 'duplicate', 'import',
                                  'drift', 'adopt', 'prior_state', 'data_mutation'])
def test_rejects_every_plan_except_one_new_managed_vm(tmp_path, monkeypatch, kind):
    ex, boundary = executor(tmp_path, monkeypatch)
    change = {'address': 'vsphere_virtual_machine.vm', 'mode': 'managed', 'type': 'vsphere_virtual_machine',
              'change': {'actions': ['create'], 'before': None, 'after': {}}}
    document: dict[str, Any] = {'resource_changes': [change]}
    if kind in ('update', 'replace', 'delete', 'noop'):
        change['change']['actions'] = {'update': ['update'], 'replace': ['delete', 'create'],
                                       'delete': ['delete'], 'noop': ['no-op']}[kind]
    elif kind == 'foreign':
        change['address'] = 'vsphere_virtual_machine.foreign'
    elif kind == 'duplicate':
        document['resource_changes'].append(copy.deepcopy(change))
    elif kind == 'import':
        change['change']['importing'] = {'id': 'foreign-vm'}
    elif kind == 'drift':
        document['resource_drift'] = [copy.deepcopy(change)]
    elif kind == 'adopt':
        change['change']['before'] = {'id': 'foreign-vm'}
    elif kind == 'prior_state':
        document['prior_state'] = {'values': {'root_module': {'resources': [copy.deepcopy(change)]}}}
    else:
        document['resource_changes'].append({'address': 'data.vsphere_network.net', 'mode': 'data',
                                             'change': {'actions': ['delete']}})
    boundary.change = document
    with pytest.raises(ValueError, match='create-only'):
        ex.prepare_plan(job(), lambda *args: None)
    assert not (ex.state_dir / job()['id'] / 'review.json').exists()


def prepared_job(ex):
    request = job()
    request['prepared_plan'] = ex.prepare_plan(request, lambda *args: None)
    return request


def approve(request):
    request.update(approved_by='admin', approved_at='reviewed',
                   approved_execution_provider={'name': 'vsphere-provisioner'},
                   approved_graph_hash=module().digest(request['graph']),
                   approved_prepared_plan=module().digest(request['prepared_plan']))
    return request


def test_apply_requires_approval_uses_exact_plan_and_independent_uuid_readback(tmp_path, monkeypatch):
    ex, boundary = executor(tmp_path, monkeypatch)
    request = prepared_job(ex)
    before = len(boundary.calls)
    assert ex.validate_plan(request) is None  # Approval-time check accepts a pending job.
    assert len(boundary.calls) == before
    refused = ex.execute(request, lambda *args: None)
    assert refused['status'] == 'failed' and refused['requires_manual_review'] is False
    approve(request)
    def assert_durable_attempt(folder):
        assert (folder / 'attempt.json').is_file()
        assert json.loads((folder / 'attempt.json').read_text())['requires_manual_review'] is True
    boundary.after_apply = assert_durable_attempt
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'succeeded' and result['can_rollback'] is False
    assert result['evidence'][0]['uuid'] == '42222222-2222-2222-2222-222222222222'
    assert result['evidence'][0]['source'] == 'independent-sdk'
    assert result['endpoints'] == []
    apply = [argv for argv, _ in boundary.calls if 'apply' in argv]
    assert len(apply) == 1 and apply[0][-1] == 'create.plan'
    assert sum('plan' in argv for argv, _ in boundary.calls) == 1
    assert ex.connections.discoveries >= 3  # Prepare, create absence check, independent readback.
    retried = ex.execute(request, lambda *args: None)
    assert retried['status'] == 'failed' and retried['requires_manual_review'] is True
    assert len([argv for argv, _ in boundary.calls if 'apply' in argv]) == 1


@pytest.mark.parametrize('artifact', ['override.tf', 'rogue.auto.tfvars.json', '.terraformrc',
                                     '.terraform/modules.json', '.govmomi-debug'])
def test_unexpected_artifact_blocks_the_next_command(tmp_path, monkeypatch, artifact):
    ex, boundary = executor(tmp_path, monkeypatch)
    boundary.extra_artifact = artifact
    with pytest.raises(ValueError, match='artifact'):
        ex.prepare_plan(job(), lambda *args: None)
    assert len(boundary.calls) == 1  # init only; never validate/plan with an override


@pytest.mark.parametrize('kind', ['state-root-link', 'input-link', 'hardlink', 'local-provider', 'tofu-pin'])
def test_unsafe_execution_inputs_are_rejected_without_commands(tmp_path, monkeypatch, kind):
    ex, boundary = executor(tmp_path, monkeypatch)
    if kind == 'state-root-link':
        outside = tmp_path / 'outside'
        outside.mkdir(mode=0o700)
        ex.state_dir.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match='[Ss]ymlink'):
            ex.prepare_plan(job(), lambda *args: None)
        assert not boundary.calls
        return
    request = approve(prepared_job(ex))
    folder = ex.state_dir / request['id']
    before = len(boundary.calls)
    if kind == 'input-link':
        path = folder / 'main.tf'
        outside = tmp_path / 'unchanged.tf'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif kind == 'hardlink':
        os.link(folder / 'main.tf', tmp_path / 'alias.tf')
    elif kind == 'local-provider':
        provider = folder / '.terraform/providers/registry.opentofu.org/vmware/vsphere/2.18.0/linux_amd64/other-executable'
        provider.write_bytes(b'unknown helper')
    else:
        ex.binary.write_bytes(b'UNVERIFIED binary')
    with pytest.raises(ValueError):
        ex.validate_plan(request)
    assert ex.execute(request, lambda *args: None)['status'] == 'failed'
    assert len(boundary.calls) == before


@pytest.mark.parametrize('stage', ['during-validate', 'before-apply', 'after-apply'])
@pytest.mark.parametrize('target', ['vars', 'provider', 'tofu', 'ca', 'template', 'lock'])
def test_every_command_rechecks_pinned_execution_inputs(tmp_path, monkeypatch, stage, target):
    ex, boundary = executor(tmp_path, monkeypatch)
    folder = ex.state_dir / job()['id']
    def mutate():
        paths = {'vars': folder / 'terraform.tfvars.json', 'provider': folder /
                 '.terraform/providers/registry.opentofu.org/vmware/vsphere/2.18.0/linux_amd64/terraform-provider-vsphere_v2.18.0',
                 'tofu': ex.binary, 'ca': ex.ca_bundle, 'template': ex.template_dir / 'main.tf',
                 'lock': folder / '.terraform.lock.hcl'}
        path = paths[target]
        path.write_bytes(path.read_bytes() + b'\nUNAPPROVED CHANGE')
    original = ex.runner
    if stage == 'during-validate':
        def mutate_after_validate(argv, **kwargs):
            result = original(argv, **kwargs)
            if 'validate' in argv:
                mutate()
            return result
        ex.runner = mutate_after_validate
        with pytest.raises(ValueError):
            prepared_job(ex)
        assert not any('plan' in argv for argv, _ in boundary.calls)
        return
    request = approve(prepared_job(ex))
    if stage == 'before-apply':
        discover = ex.connections.discover
        def changed_discover(*args):
            result = discover(*args)
            mutate()
            return result
        ex.connections.discover = changed_discover
    else:
        boundary.after_apply = lambda folder: mutate()
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'failed'
    assert not any('show' in argv and argv[-1] == 'terraform.tfstate' for argv, _ in boundary.calls)
    if stage == 'before-apply':
        assert not any('apply' in argv for argv, _ in boundary.calls)
    else:
        assert result['requires_manual_review'] is True


@pytest.mark.parametrize('stage', ['prepare', 'execute'])
@pytest.mark.parametrize('kind', ['disabled', 'license-unknown', 'license-false', 'unknown-datastore',
                                  'inaccessible-datastore', 'low-space', 'unknown-network', 'unknown-pool',
                                  'preexisting-vm', 'wrong-api', 'wrong-version', 'missing-host'])
def test_discovered_prerequisites_fail_closed(tmp_path, monkeypatch, stage, kind):
    ex, boundary = executor(tmp_path, monkeypatch)
    request = approve(prepared_job(ex)) if stage == 'execute' else job()
    inventory = ex.connections.inventory
    if kind == 'disabled':
        ex.connections.connection['enabled'] = False
    elif kind.startswith('license'):
        inventory['server']['write_api_supported'] = None if kind.endswith('unknown') else False
    elif kind == 'unknown-datastore':
        inventory['datastores'] = []
    elif kind == 'inaccessible-datastore':
        inventory['datastores'][0]['accessible'] = False
    elif kind == 'low-space':
        inventory['datastores'][0]['free_bytes'] = 1
    elif kind == 'unknown-network':
        inventory['networks'] = []
    elif kind == 'unknown-pool':
        inventory['resource_pools'] = []
    elif kind == 'preexisting-vm':
        inventory['virtual_machines'] = [{'name': 'kw-aaaaaaaaaaaa-blank-vm', 'uuid': 'foreign'}]
    elif kind == 'wrong-api':
        inventory['server']['api_type'] = 'VirtualCenter'
    elif kind == 'wrong-version':
        inventory['server']['version'] = '6.5'
    else:
        inventory['server'].pop('host_id')
    before = len(boundary.calls)
    if stage == 'prepare':
        with pytest.raises(ValueError, match='prerequisites'):
            ex.prepare_plan(request, lambda *args: None)
    else:
        assert ex.execute(request, lambda *args: None)['status'] == 'failed'
    assert len(boundary.calls) == before


@pytest.mark.parametrize('where', ['show-json', 'compressed-plan', 'state'])
def test_secret_evidence_never_becomes_a_public_prepared_plan_or_success(tmp_path, monkeypatch, where):
    import io
    import zipfile
    ex, boundary = executor(tmp_path, monkeypatch)
    original = ex.runner
    if where == 'show-json':
        boundary.show_secret = True
    elif where == 'compressed-plan':
        def leaking_plan(argv, **kwargs):
            result = original(argv, **kwargs)
            if 'plan' in argv:
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('tfplan', 'provider configuration fixture-secret-ONLY')
                (Path(kwargs['cwd']) / 'create.plan').write_bytes(stream.getvalue())
            return result
        ex.runner = leaking_plan
    events = []
    if where != 'state':
        with pytest.raises(ValueError, match='[Ss]ecret') as error:
            ex.prepare_plan(job(), lambda *args: events.append(args))
        assert 'fixture-secret' not in str(error.value)
        assert not (ex.state_dir / job()['id'] / 'review.json').exists()
    else:
        request = approve(prepared_job(ex))
        def leaking_state(folder):
            path = folder / 'terraform.tfstate'
            state = json.loads(path.read_text())
            state['leak'] = 'fixture-secret-ONLY'
            path.write_text(json.dumps(state))
        boundary.after_apply = leaking_state
        result = ex.execute(request, lambda *args: events.append(args))
        assert result['status'] == 'failed' and result['requires_manual_review'] is True
        assert 'fixture-secret' not in json.dumps(result)
    assert 'fixture-secret' not in json.dumps(events)


@pytest.mark.parametrize('stage', ['prepare', 'execute'])
@pytest.mark.parametrize('where', ['inventory', 'tls', 'command-return'])
def test_raw_credential_digest_survives_preflight_and_command_boundaries(tmp_path, monkeypatch, stage, where):
    ex, boundary = executor(tmp_path, monkeypatch)
    request = approve(prepared_job(ex)) if stage == 'execute' else job()
    original_raw = ex.connections.credentials
    rewritten = json.dumps(json.loads(original_raw), sort_keys=True, indent=2).encode()
    assert rewritten != original_raw and json.loads(rewritten) == json.loads(original_raw)
    def rewrite_credentials():
        ex.connections.credentials = rewritten
    if where == 'inventory':
        discover = ex.connections.discover
        def rewrite_after_inventory(*args):
            result = discover(*args)
            rewrite_credentials()
            return result
        monkeypatch.setattr(ex.connections, 'discover', rewrite_after_inventory)
    elif where == 'tls':
        tls_calls = 0
        def rewrite_during_command_tls(connection):
            nonlocal tls_calls
            tls_calls += 1
            if tls_calls == 2:  # Initial preflight passed; now at init/apply's process boundary.
                rewrite_credentials()
            return 'c' * 64
        monkeypatch.setattr(ex, '_tls', rewrite_during_command_tls)
    else:
        runner = ex.runner
        def rewrite_after_command(argv, **kwargs):
            result = runner(argv, **kwargs)
            if argv[1] == ('apply' if stage == 'execute' else 'init'):
                rewrite_credentials()
            return result
        ex.runner = rewrite_after_command
    events = []
    folder = ex.state_dir / request['id']
    if stage == 'prepare':
        with pytest.raises(ValueError, match='credential binding changed'):
            request['prepared_plan'] = ex.prepare_plan(request, lambda *args: events.append(args))
        assert 'prepared_plan' not in request
        assert not (folder / 'review.json').exists()
        assert not any('plan' in argv for argv, _ in boundary.calls)
        assert events == []
    else:
        result = ex.execute(request, lambda *args: events.append(args))
        assert sum('apply' in argv for argv, _ in boundary.calls) == (1 if where == 'command-return' else 0)
        assert not any('show' in argv and argv[-1] == 'terraform.tfstate' for argv, _ in boundary.calls)
        assert result['status'] == 'failed' and result['requires_manual_review'] is True
        assert (folder / 'attempt.json').is_file()
        assert not (folder / 'verified.json').exists()
    assert 'fixture-secret' not in json.dumps(events)


def test_stable_noncanonical_credentials_remain_pinned_and_executable(tmp_path, monkeypatch):
    ex, boundary = executor(tmp_path, monkeypatch)
    raw = json.dumps(json.loads(ex.connections.credentials), sort_keys=True, indent=2).encode()
    assert raw != ex.connections.credentials
    ex.connections.credentials = raw
    request = approve(prepared_job(ex))
    signed = json.loads((ex.state_dir / request['id'] / 'review.json').read_text())
    assert signed['credential_digest'] == __import__('hashlib').sha256(raw).hexdigest()
    assert ex.validate_plan(request) is None
    assert ex.execute(request, lambda *args: None)['status'] == 'succeeded'
    assert sum('apply' in argv for argv, _ in boundary.calls) == 1


@pytest.mark.parametrize('kind', ['revision', 'credentials', 'provider-exception', 'malformed-secret', 'extra-secret-field'])
def test_connection_and_secret_changes_cannot_cross_command_boundary(tmp_path, monkeypatch, kind):
    ex, boundary = executor(tmp_path, monkeypatch)
    if kind in ('provider-exception', 'malformed-secret', 'extra-secret-field'):
        if kind == 'provider-exception':
            @contextmanager
            def unavailable(identity):
                raise RuntimeError('fixture-secret-ONLY leaked provider error')
                yield
            ex.connections.access = unavailable
        elif kind == 'malformed-secret':
            ex.connections.credentials = b'fixture-secret-ONLY NOT JSON'
        else:
            ex.connections.credentials = b'{"username":"fixture-admin","password":"fixture-secret-ONLY","exec":"evil"}'
        with pytest.raises(ValueError) as error:
            prepared_job(ex)
        assert 'fixture-secret' not in str(error.value)
        assert not boundary.calls
        return
    request = approve(prepared_job(ex))
    discover = ex.connections.discover
    def rotate_during_read(*args):
        result = discover(*args)
        if kind == 'revision':
            ex.connections.connection['revision'] += 1
        else:
            ex.connections.credentials = b'{"username":"new-user","password":"different-secret"}'
        return result
    ex.connections.discover = rotate_during_read
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'failed'
    assert not any('apply' in argv for argv, _ in boundary.calls)


def test_catalogue_has_only_real_discovery_selectors():
    from kuberweaver.catalog import Catalog
    blueprint = Catalog(module().ROOT / 'modules').discover()['vsphere-vm']
    assert blueprint['execution_provider'] == 'vsphere-provisioner'
    assert blueprint['admission_profile'] == 'virtual-machine'
    assert blueprint['capability'] == 'planned' and blueprint['engine'] == 'opentofu'
    properties = blueprint['schema']['properties']
    for key, source in [('connection_id', 'connections'), ('datastore_id', 'datastores'),
                        ('resource_pool_id', 'resource_pools')]:
        assert properties[key]['x-options-source'] == source
    assert properties['network_ids']['items']['x-options-source'] == 'networks'
    assert 'targets' not in properties and 'port' not in properties


def test_extension_is_disabled_until_explicit_trust_and_uses_scoped_data(tmp_path, monkeypatch):
    import shutil
    from kuberweaver.extensions import ExtensionRegistry
    source = module().ROOT / 'extensions/vsphere-provisioning'
    assert source.is_dir(), 'Opt-in vSphere extension package missing'
    extensions = tmp_path / 'extensions'
    shutil.copytree(source, extensions / 'vsphere-provisioning')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('registration must be inert'))
    registry = ExtensionRegistry()
    disabled = registry.load_directory(extensions, enabled=set())
    assert disabled[0]['status'] == 'disabled' and registry.list() == []
    reports = registry.load_directory(extensions, enabled={'vsphere-provisioning'})
    assert reports[0]['status'] == 'loaded'
    provider = registry.get('execution', 'vsphere-provisioner')
    assert isinstance(provider, module().SavedPlanExecutor)
    assert provider.state_dir == tmp_path / 'plugin-data/vsphere-provisioning/states'
    assert provider.template_dir == module().ROOT / 'plugins/vsphere-vm'
    assert provider.connections is None  # resolve_provider performs the injection.
    assert not provider.supports_rollback
    fresh = ExtensionRegistry()
    fresh.load_directory(extensions, enabled=set())
    assert fresh.list() == []


def test_plan_is_rechecked_after_tls_immediately_before_apply(tmp_path, monkeypatch):
    ex, boundary = executor(tmp_path, monkeypatch)
    request = approve(prepared_job(ex))
    calls = 0
    def changed_during_tls(connection):
        nonlocal calls
        calls += 1
        if calls == 2:
            (ex.state_dir / request['id'] / 'create.plan').write_bytes(b'SUBSTITUTED PLAN')
        return 'c' * 64
    monkeypatch.setattr(ex, '_tls', changed_during_tls)
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'failed'
    assert not any('apply' in argv for argv, _ in boundary.calls)


@pytest.mark.parametrize('kind', ['uuid-disagreement', 'foreign-child-state'])
def test_ambiguous_managed_state_never_counts_as_verified(tmp_path, monkeypatch, kind):
    ex, boundary = executor(tmp_path, monkeypatch)
    request = approve(prepared_job(ex))
    def ambiguous_state(folder):
        path = folder / 'terraform.tfstate'
        state = json.loads(path.read_text())
        root = state['values']['root_module']
        if kind == 'uuid-disagreement':
            root['resources'][0]['values']['id'] = 'foreign-state-uuid'
        else:
            root['child_modules'] = [{'resources': [{'mode': 'managed', 'address': 'module.foreign.vm'}]}]
        path.write_text(json.dumps(state))
    boundary.after_apply = ambiguous_state
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'failed' and result['requires_manual_review'] is True
    assert not (ex.state_dir / request['id'] / 'verified.json').exists()


def test_reviewed_distribution_lock_precedes_init_and_disallows_reselection(tmp_path, monkeypatch):
    m = module()
    lock = m.ROOT / 'modules/resources/vsphere-vm/.terraform.lock.hcl'
    assert lock.is_file(), 'Reviewed official provider distribution lock is missing'
    ex, boundary = executor(tmp_path, monkeypatch)
    prepared_job(ex)
    folder = ex.state_dir / job()['id']
    assert (folder / '.terraform.lock.hcl').read_bytes() == lock.read_bytes()
    init = next(argv for argv, _ in boundary.calls if 'init' in argv)
    assert '-lockfile=readonly' in init


@pytest.mark.parametrize('failure', ['timeout', 'interrupt', 'system-exit'])
def test_ambiguous_attempt_survives_restart_and_never_destroys(tmp_path, monkeypatch, failure):
    ex, boundary = executor(tmp_path, monkeypatch)
    request = approve(prepared_job(ex))
    if failure == 'timeout':
        boundary.fail_apply = True
    else:
        def interrupted(folder):
            raise (KeyboardInterrupt if failure == 'interrupt' else SystemExit)('fixture-secret-ONLY')
        boundary.after_apply = interrupted
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'failed' and result['requires_manual_review'] is True
    assert 'fixture-secret' not in json.dumps(result)
    folder = ex.state_dir / request['id']
    state = (folder / 'terraform.tfstate').read_bytes()
    restarted = module().SavedPlanExecutor(ex.state_dir, ex.template_dir, binary=ex.binary, runner=boundary)
    restarted.connections, restarted.ca_bundle = ex.connections, ex.ca_bundle
    monkeypatch.setattr(restarted, '_tls', lambda connection: 'c' * 64)
    monkeypatch.setattr(restarted, '_stopped_contract', lambda: None)
    before = len(boundary.calls)
    retry = restarted.execute(request, lambda *args: None)
    assert retry['status'] == 'failed' and retry['requires_manual_review'] is True
    assert restarted.rollback(request, lambda *args: None)['status'] == 'rollback_failed'
    assert (folder / 'terraform.tfstate').read_bytes() == state
    assert len(boundary.calls) == before
    assert not any('destroy' in ' '.join(argv) for argv, _ in boundary.calls)


@pytest.mark.parametrize('field,value', [('uuid', 'foreign-uuid'), ('name', 'foreign-name'),
    ('cpu', 7), ('memory_mb', 8192), ('power_state', 'poweredOn')])
def test_independent_sdk_disagreement_requires_manual_review(tmp_path, monkeypatch, field, value):
    ex, boundary = executor(tmp_path, monkeypatch)
    request = approve(prepared_job(ex))
    boundary.after_apply = lambda folder: ex.connections.inventory['virtual_machines'][0].update({field: value})
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'failed' and result['requires_manual_review'] is True
    assert not (ex.state_dir / request['id'] / 'verified.json').exists()


def test_credentials_are_environment_only_without_inherited_overrides(tmp_path, monkeypatch):
    for name in ('TF_VAR_password', 'TF_LOG', 'TF_LOG_PATH', 'TF_CLI_ARGS', 'TF_CLI_CONFIG_FILE',
                 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'SSH_AUTH_SOCK', 'GOVC_INSECURE',
                 'VSPHERE_ALLOW_UNVERIFIED_SSL', 'VSPHERE_CLIENT_DEBUG', 'VSPHERE_PERSIST_SESSION'):
        monkeypatch.setenv(name, 'UNAPPROVED INHERITED OVERRIDE')
    ex, boundary = executor(tmp_path, monkeypatch)
    request = approve(prepared_job(ex))
    assert ex.execute(request, lambda *args: None)['status'] == 'succeeded'
    for argv, env in boundary.calls:
        assert env['VSPHERE_USER'] == 'fixture-admin'
        assert env['VSPHERE_PASSWORD'] == 'fixture-secret-ONLY'
        assert 'UNAPPROVED INHERITED OVERRIDE' not in env.values()
        assert not any(k.startswith('TF_VAR_') or 'PROXY' in k for k in env)
        assert 'fixture-secret' not in ' '.join(argv)
        assert set(env) <= {'PATH', 'HOME', 'TMPDIR', 'TF_IN_AUTOMATION', 'TF_INPUT', 'CHECKPOINT_DISABLE',
                            'TF_CLI_CONFIG_FILE', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'VSPHERE_USER', 'VSPHERE_PASSWORD'}


def test_flock_excludes_concurrent_planning(tmp_path, monkeypatch):
    ex, boundary = executor(tmp_path, monkeypatch)
    with ex._lock():
        with pytest.raises(ValueError):
            prepared_job(ex)
    assert boundary.calls == []


def test_real_jobs_standard_graph_and_approval_validate_are_strictly_local(api, tmp_path, monkeypatch):
    import shutil
    import yaml
    from kuberweaver.providers import provider_provenance
    from test_prepared_jobs import wait_status
    client, app = api
    ex, boundary = executor(tmp_path, monkeypatch)
    blueprint = app.state.plugins_dir / 'vsphere-vm'
    shutil.copytree(ex.template_dir, blueprint)
    path = blueprint / 'manifest.yaml'
    manifest = yaml.safe_load(path.read_text())
    manifest['capability'] = 'live'  # Authorized disposable fixture only; production stays planned.
    path.write_text(yaml.safe_dump(manifest))
    ex.template_dir = blueprint
    app.state.connections = ex.connections
    app.state.registry.register('execution', 'vsphere-provisioner', ex)
    app.state.provider_loaded_sources[('execution', 'vsphere-provisioner')] = provider_provenance(app, 'execution', 'vsphere-provisioner')
    graph = job()['graph']
    graph['nodes'][0].update(label='Blank VM', position={'x': 1.0, 'y': 2.0})
    response = client.post('/api/jobs', json={'graph': graph})
    assert response.status_code == 201, response.text
    current = wait_status(client, response.json()['id'], {'pending_approval', 'failed'})
    assert current['status'] == 'pending_approval', current
    assert current['graph']['strategy'] == {'canary': 1, 'batch_size': 1}
    app.state.worker.pause.clear()  # Do not execute while transport/vault are forbidden.
    def forbidden(*args, **kwargs):
        pytest.fail('validate_plan must not call vault/access/TLS/socket/SDK/CLI or decode credentials')
    with monkeypatch.context() as local_only:
        local_only.setattr(ex.connections, 'access', forbidden)
        local_only.setattr(ex.connections, 'driver', forbidden)
        local_only.setattr(ex, '_tls', forbidden)
        local_only.setattr(ex, '_credentials', forbidden)
        local_only.setattr(socket, 'create_connection', forbidden)
        local_only.setattr(ex, 'runner', forbidden)
        fingerprint = ex.connections.fingerprint
        local_checks = []
        def local_fingerprint(identity):
            assert app.state.store.lock._is_owned()
            local_checks.append(identity)
            return fingerprint(identity)
        local_only.setattr(ex.connections, 'fingerprint', local_fingerprint)
        before = len(boundary.calls)
        response = client.post('/api/jobs/' + current['id'] + '/approve')
        assert response.status_code == 200, response.text
        approved = client.get('/api/jobs/' + current['id']).json()
        assert approved['approved_prepared_plan'] == module().digest(approved['prepared_plan'])
        assert len(boundary.calls) == before and local_checks
    # Restore the explicit fake boundaries and exercise dispatch of the very same
    # approved job through the real worker. No live ESXi capability is asserted.
    from test_core_jobs import wait_job
    app.state.worker.pause.set()
    result = wait_job(client, current['id'])
    assert result['status'] == 'succeeded', result
    assert result['result']['evidence'][0]['source'] == 'independent-sdk'
    assert sum('plan' in argv for argv, _ in boundary.calls) == 1
    assert sum('apply' in argv for argv, _ in boundary.calls) == 1


def test_local_validation_is_reentrant_during_guarded_execution(tmp_path, monkeypatch):
    ex, _ = executor(tmp_path, monkeypatch)
    request = prepared_job(ex)
    with ex._lock():
        assert ex.validate_plan(request) is None


def test_command_guard_can_validate_its_own_durable_attempt_but_not_retry(tmp_path, monkeypatch):
    ex, boundary = executor(tmp_path, monkeypatch)
    request = approve(prepared_job(ex))
    errors = []
    def host_guard(argv, **kwargs):
        try:
            ex.validate_plan(request)
        except ValueError as error:
            errors.append(str(error))
            raise
        return boundary(argv, **kwargs)
    ex.runner = host_guard
    result = ex.execute(request, lambda *args: None)
    assert result['status'] == 'succeeded', errors
    with pytest.raises(ValueError, match='Existing'):
        ex.validate_plan(request)
    assert ex.execute(request, lambda *args: None)['status'] == 'failed'


def test_completed_thread_does_not_clear_the_next_flock_owners_scope(tmp_path, monkeypatch):
    import threading
    ex, boundary = executor(tmp_path, monkeypatch)
    first = approve(prepared_job(ex))
    second = job()
    second['id'] = 'd' * 32
    second['prepared_plan'] = ex.prepare_plan(second, lambda *args: None)
    approve(second)
    unlocked, release_first, applying, release_second = [threading.Event() for _ in range(4)]
    results = {}
    original_lock = ex._lock
    @contextmanager
    def delayed_exit():
        with original_lock():
            yield
        if threading.current_thread().name == 'first-execution':
            unlocked.set()
            assert release_first.wait(10)
    monkeypatch.setattr(ex, '_lock', delayed_exit)
    def pause_second(folder):
        if folder.name == second['id']:
            applying.set()
            assert release_second.wait(10)
    boundary.after_apply = pause_second
    def guarded_runner(argv, **kwargs):
        ex.validate_plan(first if Path(kwargs['cwd']).name == first['id'] else second)
        return boundary(argv, **kwargs)
    ex.runner = guarded_runner
    def run(name, request):
        results[name] = ex.execute(request, lambda *args: None)
    one = threading.Thread(target=run, args=('first', first), name='first-execution')
    two = threading.Thread(target=run, args=('second', second), name='second-execution')
    one.start()
    try:
        assert unlocked.wait(10)
        two.start()
        assert applying.wait(10)
        release_first.set()
        one.join(10)
        assert ex._active_job == (two.ident, second['id'])
    finally:
        release_first.set()
        release_second.set()
        one.join(10)
        if two.ident is not None:
            two.join(10)
    assert results['first']['status'] == results['second']['status'] == 'succeeded'
    assert ex._active_job is None


@pytest.mark.parametrize('keyword', ['$ref', '$dynamicRef'])
def test_local_validation_rejects_schema_references_before_any_retrieval(tmp_path, monkeypatch, keyword):
    import urllib.request
    import yaml
    ex, _ = executor(tmp_path, monkeypatch)
    request = prepared_job(ex)
    path = ex.template_dir / 'manifest.yaml'
    value = yaml.safe_load(path.read_text())
    value['schema'][keyword] = 'https://no-network.invalid/schema'
    path.write_text(yaml.safe_dump(value))
    retrievals = []
    def forbidden(*args, **kwargs):
        retrievals.append(args)
        raise RuntimeError('implicit schema network retrieval is forbidden')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    with pytest.raises(ValueError):
        ex.validate_plan(request)
    assert retrievals == []
