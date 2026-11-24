"""Compound local VM contract: typed handoff, strict SSH identity, pinned playbooks.

Every command boundary is simulated locally. No guest, QEMU process, SSH server
or Ansible play is executed by this suite.
"""
import base64
import copy
import hashlib
import json
import struct
from contextlib import contextmanager
from pathlib import Path
import shutil
import sys

import pytest

from backend.local_vm_ansible import (
    HANDOFF_SCHEMA,
    LocalVmAnsibleError,
    LocalVmAnsibleProvider,
    PROVIDER_ID,
    RUNTIME_CONTRACT,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / 'modules' / 'resources' / 'local-vm-ansible'
UUID = '564df7bb-3ddf-7a1e-5a9f-f5fc1cb31d7a'
ANSIBLE = Path(sys.executable).with_name('ansible-playbook')


def _blob(kind='ssh-ed25519', payload=b'host-key-material-32-bytes-long!'):
    name = kind.encode('ascii')
    return base64.b64encode(
        struct.pack('>I', len(name)) + name + struct.pack('>I', len(payload)) + payload).decode()


def _public_key(kind='ssh-ed25519', payload=b'host-key-material-32-bytes-long!'):
    return f'{kind} {_blob(kind, payload)}'


def handoff(**overrides):
    document = {
        'schema_version': 1,
        'contract': RUNTIME_CONTRACT,
        'runtime': {'name': 'local-executor', 'version': '1.0.0'},
        'vm': {'uuid': UUID, 'name': 'lab-web-01', 'power_state': 'running'},
        'guest': {'address': '127.0.0.1', 'ssh_port': 2222, 'user': 'ubuntu',
                  'host_key': {'type': 'ssh-ed25519', 'public_key': _public_key()}},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(document.get(key), dict):
            document[key] = {**document[key], **value}
        else:
            document[key] = value
    return document


class FakeRuntime:
    name = 'local-executor'
    contract = RUNTIME_CONTRACT

    def __init__(self, document=None):
        self.document = document or handoff()
        self.calls = []

    def provision(self, request):
        self.calls.append(copy.deepcopy(request))
        return copy.deepcopy(self.document)


class Material:
    def __init__(self, data=b'-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n'):
        self._data = data

    def bytes(self):
        return self._data


class FakeKeys:
    def __init__(self):
        self.used = []

    def binding(self, identity):
        return {'id': identity, 'fingerprint': 'SHA256:approvedbinding', 'revision': 1}

    @contextmanager
    def private_key(self, identity):
        self.used.append(identity)
        yield Material()


class Completed:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Simulates ssh and ansible; never executes either."""

    def __init__(self, *, ssh=0, apply=0, verify=0, report=None):
        self.calls = []
        self.ssh, self.apply, self.verify = ssh, apply, verify
        self.report = {'guest-reachable': True, 'python-available': True,
                       'baseline-marker': True} if report is None else report

    def __call__(self, argv, **kwargs):
        self.calls.append({'argv': list(argv), 'kwargs': kwargs})
        name = Path(argv[0]).name
        if name == 'ssh':
            return Completed(self.ssh, '', 'Permission denied' if self.ssh else '')
        playbook = Path(argv[3]).name
        if playbook == 'baseline.yml':
            return Completed(self.apply, 'ok=3 changed=1', '')
        variables = json.loads(Path(argv[argv.index('-e') + 1][1:]).read_text())
        if self.report is not None:
            Path(variables['kw_result_path']).write_text(json.dumps({'assertions': self.report}))
        return Completed(self.verify, 'ok=2 changed=0', '')

    def names(self):
        return [Path(call['argv'][0]).name for call in self.calls]


def plugin_copy(tmp_path):
    target = tmp_path / 'plugins' / 'local-vm-ansible'
    shutil.copytree(PLUGIN_DIR, target)
    # Digests are content pins; a private copy only needs the manifest and playbooks.
    return target.parent


def provider(tmp_path, *, runtime=None, runner=None, keys=None, plugins=None, playbook=None):
    instance = LocalVmAnsibleProvider(
        tmp_path / 'state', plugins or PLUGIN_DIR.parent,
        runtime=runtime if runtime is not None else FakeRuntime(),
        playbook=playbook if playbook is not None else ANSIBLE,
        runner=runner or FakeRunner())
    instance.ssh_keys = keys if keys is not None else FakeKeys()
    return instance


def graph(config=None, **overrides):
    document = {
        'name': 'local-lab', 'environment': 'dev', 'failure_mode': 'none',
        'nodes': [{'id': 'guest', 'plugin_id': 'local-vm-ansible',
                   'config': {'vm_name': 'lab-web-01', 'ssh_key_id': 'a' * 32, **(config or {})}}],
        'edges': [], 'strategy': {'canary': 1, 'batch_size': 1},
    }
    document.update(overrides)
    return document


def job(config=None, **overrides):
    document = {'id': 'job-local-1', 'operation': 'execute', 'graph': graph(config)}
    document.update(overrides)
    return document


def prepared(tmp_path, **kwargs):
    instance = provider(tmp_path, **kwargs)
    document = job()
    plan = instance.prepare_plan(document, lambda *a: None)
    document['prepared_plan'] = plan
    document['approved_by'] = 'admin'
    document['approved_at'] = '2026-11-18T00:00:00+00:00'
    document['approved_execution_provider'] = {'name': instance.executor_id}
    document['approved_prepared_plan'] = hashlib.sha256(json.dumps(
        plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return instance, document


# --------------------------------------------------------------------- contract

def test_the_shipped_compound_plugin_satisfies_the_reviewed_contract(tmp_path):
    instance = provider(tmp_path)
    brick = instance.brick()
    assert brick['id'] == 'local-vm-ansible'
    assert brick['executor'] == PROVIDER_ID
    assert brick['execution_provider'] == PROVIDER_ID
    assert brick['admission_profile'] == 'virtual-machine'
    assert brick['implementation']['collections'] == []
    assert brick['implementation']['ansible_core'] == '2.20.11'
    assert brick['_assertions'] == ['guest-reachable', 'python-available', 'baseline-marker']
    assert set(brick['_playbooks']) == {'apply', 'verify'}
    assert brick['_playbooks']['apply'].is_relative_to(PLUGIN_DIR)


def test_a_changed_playbook_is_refused(tmp_path):
    plugins = plugin_copy(tmp_path)
    playbook = plugins / 'local-vm-ansible' / 'iac' / 'baseline.yml'
    playbook.write_text(playbook.read_text() + '\n# unapproved edit\n')
    with pytest.raises(LocalVmAnsibleError, match='playbook changed'):
        provider(tmp_path, plugins=plugins).brick()


def test_a_manifest_cannot_declare_extra_lifecycle_steps(tmp_path):
    plugins = plugin_copy(tmp_path)
    manifest = plugins / 'local-vm-ansible' / 'manifest.yaml'
    body = manifest.read_text().replace('  verify: iac/verify.yml\n', '  verify: iac/verify.yml\n  remove: iac/verify.yml\n')
    manifest.write_text(body)
    with pytest.raises(LocalVmAnsibleError, match='exactly apply and verify'):
        provider(tmp_path, plugins=plugins).brick()


def test_read_is_unavailable_without_a_runtime(tmp_path):
    instance = LocalVmAnsibleProvider(tmp_path / 'state', PLUGIN_DIR.parent, runtime=None,
                                      playbook=ANSIBLE)
    state = instance.read()
    assert state['status'] == 'unavailable'
    assert any('Local runtime' in reason for reason in state['reasons'])
    assert state['supports_rollback'] is False


def test_read_is_configured_with_a_matching_runtime(tmp_path):
    assert provider(tmp_path).read()['status'] == 'configured'


def test_read_is_unavailable_without_ansible(tmp_path):
    instance = provider(tmp_path, playbook=tmp_path / 'missing-ansible-playbook')
    state = instance.read()
    assert state['status'] == 'unavailable'
    assert any('Ansible executable' in reason for reason in state['reasons'])


def test_the_local_runtime_provider_is_accepted_only_with_a_handoff_port(tmp_path):
    class RuntimeProvider:
        executor_id = RUNTIME_CONTRACT
        name = RUNTIME_CONTRACT

        def execute(self, job, emit):
            raise AssertionError('must not run during review')

    instance = provider(tmp_path, runtime=RuntimeProvider())
    state = instance.read()
    assert state['status'] == 'unavailable'
    assert state['reasons'] == ['Local runtime does not expose a typed handoff port']

    setattr(RuntimeProvider, 'provision', lambda self, request: handoff())
    assert provider(tmp_path / 'second', runtime=RuntimeProvider()).read()['status'] == 'configured'


# ---------------------------------------------------------------------- handoff

def test_handoff_rejects_a_remote_address(tmp_path):
    instance = provider(tmp_path)
    document = handoff()
    document['guest']['address'] = '8.8.8.8'
    runtime = FakeRuntime(document)
    instance.runtime = runtime
    with pytest.raises(LocalVmAnsibleError, match='not a local lab address'):
        instance._provision('job-local-1', instance.review(job()))


@pytest.mark.parametrize('address', ['2001:4860:4860::8888', '0.0.0.0', '::', '224.0.0.1'])
def test_handoff_rejects_non_local_or_unusable_addresses(tmp_path, address):
    instance = provider(tmp_path)
    document = handoff()
    document['guest']['address'] = address
    instance.runtime = FakeRuntime(document)
    with pytest.raises(LocalVmAnsibleError, match='not a local lab address'):
        instance._provision('job-local-1', instance.review(job()))


@pytest.mark.parametrize('address', ['10.0.2.15', '192.168.122.40', '127.0.0.1', '::1', 'fd00::5'])
def test_handoff_accepts_private_and_loopback_addresses(tmp_path, address):
    instance = provider(tmp_path)
    document = handoff()
    document['guest']['address'] = address
    instance.runtime = FakeRuntime(document)
    result = instance._provision('job-local-1', instance.review(job()))
    assert result.address == address


def test_handoff_must_declare_the_runtime_contract(tmp_path):
    instance = provider(tmp_path)
    document = handoff(contract='local-runtime-v2')
    instance.runtime = FakeRuntime(document)
    with pytest.raises(LocalVmAnsibleError, match='does not match local-runtime-v1'):
        instance._provision('job-local-1', instance.review(job()))


def test_handoff_is_closed_against_extra_fields(tmp_path):
    instance = provider(tmp_path)
    document = handoff()
    document['guest']['command'] = 'sh -c id'
    instance.runtime = FakeRuntime(document)
    with pytest.raises(LocalVmAnsibleError, match='does not match local-runtime-v1'):
        instance._provision('job-local-1', instance.review(job()))


def test_handoff_host_key_type_must_match_the_blob(tmp_path):
    instance = provider(tmp_path)
    document = handoff()
    document['guest']['host_key'] = {'type': 'ssh-ed25519',
                                     'public_key': _public_key('ecdsa-sha2-nistp256')}
    instance.runtime = FakeRuntime(document)
    with pytest.raises(LocalVmAnsibleError, match='does not match its blob'):
        instance._provision('job-local-1', instance.review(job()))


def test_handoff_fingerprint_is_derived_from_the_key_blob(tmp_path):
    instance = provider(tmp_path)
    document = handoff()
    instance.runtime = FakeRuntime(document)
    result = instance._provision('job-local-1', instance.review(job()))
    expected = 'SHA256:' + base64.b64encode(hashlib.sha256(
        base64.b64decode(_blob())).digest()).decode().rstrip('=')
    assert result.fingerprint == expected
    assert result.known_hosts_line() == f'[127.0.0.1]:2222 ssh-ed25519 {_blob()}\n'


def test_handoff_must_match_the_approved_vm_name(tmp_path):
    instance = provider(tmp_path)
    document = handoff(vm={'name': 'other-guest'})
    instance.runtime = FakeRuntime(document)
    with pytest.raises(LocalVmAnsibleError, match='does not match the approved plan'):
        instance._provision('job-local-1', instance.review(job()))


def test_the_handoff_schema_is_closed_and_bounded():
    assert HANDOFF_SCHEMA['additionalProperties'] is False
    assert HANDOFF_SCHEMA['properties']['guest']['properties']['ssh_port']['maximum'] == 65535
    assert HANDOFF_SCHEMA['properties']['guest']['properties']['user']['const'] == 'ubuntu'


# ----------------------------------------------------------------------- review

def test_review_refuses_two_nodes_or_edges(tmp_path):
    instance = provider(tmp_path)
    two = graph()
    two['nodes'].append({'id': 'second', 'plugin_id': 'local-vm-ansible',
                         'config': {'vm_name': 'lab-web-02', 'ssh_key_id': 'b' * 32}})
    with pytest.raises(LocalVmAnsibleError, match='exactly one node'):
        instance.review(job(graph=two))
    edged = graph()
    edged['edges'] = [{'id': 'dep', 'source': 'guest', 'target': 'guest'}]
    with pytest.raises(LocalVmAnsibleError, match='exactly one node'):
        instance.review(job(graph=edged))


def test_review_refuses_failure_injection_and_other_environments(tmp_path):
    instance = provider(tmp_path)
    with pytest.raises(LocalVmAnsibleError, match='exactly one node'):
        instance.review(job(graph=graph(failure_mode='canary')))
    with pytest.raises(LocalVmAnsibleError, match='dev environment'):
        instance.review(job(graph=graph(environment='production')))


def test_review_refuses_an_unreviewed_plugin(tmp_path):
    instance = provider(tmp_path)
    document = job()
    document['graph']['nodes'][0]['plugin_id'] = 'web-nginx'
    with pytest.raises(LocalVmAnsibleError, match='reviewed local-vm-ansible'):
        instance.review(document)


def test_config_is_closed_and_bounded(tmp_path):
    instance = provider(tmp_path)
    for extra in ({'playbook': '/tmp/evil.yml'}, {'command': 'id'}, {'connection': 'local'}):
        with pytest.raises(LocalVmAnsibleError, match='Invalid compound configuration'):
            instance.review(job(extra))
    for bad in ({'cpu': 0}, {'memory_mb': 128}, {'disk_gb': 1024}, {'vm_name': 'Bad Name'}):
        with pytest.raises(LocalVmAnsibleError, match='Invalid compound configuration'):
            instance.review(job(bad))


def test_review_requires_a_usable_ssh_key_binding(tmp_path):
    instance = provider(tmp_path)
    instance.ssh_keys = None
    with pytest.raises(LocalVmAnsibleError, match='SSH key registry'):
        instance.review(job())


def test_preparation_never_calls_the_runtime_or_a_command(tmp_path):
    runtime, runner = FakeRuntime(), FakeRunner()
    instance = provider(tmp_path, runtime=runtime, runner=runner)
    plan = instance.prepare_plan(job(), lambda *a: None)
    assert runtime.calls == []
    assert runner.calls == []
    assert plan['summary']['runtime'] == RUNTIME_CONTRACT
    assert plan['actions']['check'] == ['ssh-host-identity', 'guest-reachable']


def test_preparation_is_deterministic_and_has_no_private_material(tmp_path):
    first = provider(tmp_path / 'a').prepare_plan(job(), lambda *a: None)
    second = provider(tmp_path / 'b').prepare_plan(job(), lambda *a: None)
    assert first == second
    encoded = json.dumps(first).lower()
    assert 'private key' not in encoded
    assert 'ssh-ed25519 ' not in encoded
    assert '127.0.0.1' not in encoded


def test_prepared_plan_satisfies_the_kernel_public_contract(tmp_path):
    from backend.jobs import safe_prepared_plan

    plan = provider(tmp_path).prepare_plan(job(), lambda *a: None)
    assert safe_prepared_plan(plan) == plan


def test_preparation_cannot_be_replanned(tmp_path):
    instance = provider(tmp_path)
    instance.prepare_plan(job(), lambda *a: None)
    with pytest.raises(LocalVmAnsibleError, match='cannot be resumed or replanned'):
        instance.prepare_plan(job(), lambda *a: None)


def test_approval_check_rejects_a_changed_playbook(tmp_path):
    plugins = plugin_copy(tmp_path)
    instance, document = prepared(tmp_path, plugins=plugins)
    playbook = plugins / 'local-vm-ansible' / 'iac' / 'verify.yml'
    playbook.write_text(playbook.read_text() + '\n# unapproved edit\n')
    result = instance.execute(document, lambda *a: None)
    assert result['status'] == 'failed'
    assert 'playbook changed' in result['error']
    assert instance.runtime is not None
    assert instance.runtime.calls == []


# -------------------------------------------------------------------- execution

def test_execution_runs_provision_then_ssh_then_pinned_playbooks(tmp_path):
    runtime, runner = FakeRuntime(), FakeRunner()
    instance, document = prepared(tmp_path, runtime=runtime, runner=runner)
    result = instance.execute(document, lambda *a: None)
    assert result['status'] == 'succeeded', result
    assert len(runtime.calls) == 1
    assert runtime.calls[0] == {'job_id': 'job-local-1',
                                'vm': {'name': 'lab-web-01', 'cpu': 1,
                                       'memory_mb': 1024, 'disk_gb': 8}}
    assert runner.names() == ['ssh', 'ansible-playbook', 'ansible-playbook']
    assert Path(runner.calls[1]['argv'][3]).name == 'baseline.yml'
    assert Path(runner.calls[2]['argv'][3]).name == 'verify.yml'
    ssh = runner.calls[0]['argv']
    assert ssh[0] in ('/usr/bin/ssh', '/bin/ssh')
    assert 'StrictHostKeyChecking=yes' in ssh
    assert any(arg.startswith('UserKnownHostsFile=') for arg in ssh)
    assert ssh[-2:] == ['ubuntu@127.0.0.1', 'true']
    folder = tmp_path / 'state' / 'local-vm-ansible' / 'job-local-1'
    assert (folder / 'known_hosts').read_text() == f'[127.0.0.1]:2222 ssh-ed25519 {_blob()}\n'
    inventory = json.loads((folder / 'inventory.json').read_text())
    host = inventory['all']['hosts']['target']
    assert host['ansible_host'] == '127.0.0.1'
    assert host['ansible_port'] == 2222
    assert host['ansible_user'] == 'ubuntu'
    assert 'StrictHostKeyChecking=yes' in host['ansible_ssh_common_args']
    assert 'GlobalKnownHostsFile=/dev/null' in host['ansible_ssh_common_args']
    evidence = result['evidence'][0]
    assert evidence['assertions'] == {'guest-reachable': True, 'python-available': True,
                                      'baseline-marker': True}
    assert evidence['changed'] == 1
    assert evidence['handoff_digest']
    assert result['requires_manual_review'] is False
    assert result['can_rollback'] is False


def test_execution_requires_explicit_approval_of_the_exact_plan(tmp_path):
    runtime, runner = FakeRuntime(), FakeRunner()
    instance, document = prepared(tmp_path, runtime=runtime, runner=runner)
    for field, value in (('approved_by', None), ('approved_at', None),
                         ('approved_execution_provider', {'name': 'another-engine'}),
                         ('approved_prepared_plan', '0' * 64)):
        candidate = {**document, field: value}
        result = instance.execute(candidate, lambda *a: None)
        assert result['status'] == 'failed'
        assert 'Explicit approval' in result['error']
    assert runtime.calls == []
    assert runner.calls == []


def test_a_handoff_that_redirects_the_guest_is_refused_before_any_command(tmp_path):
    runtime, runner = FakeRuntime(handoff(guest={'address': '8.8.8.8'})), FakeRunner()
    instance, document = prepared(tmp_path, runtime=runtime, runner=runner)
    result = instance.execute(document, lambda *a: None)
    assert result['status'] == 'failed'
    assert 'not a local lab address' in result['error']
    assert runner.calls == []
    assert result['requires_manual_review'] is True


def test_an_ssh_identity_failure_stops_before_ansible(tmp_path):
    runner = FakeRunner(ssh=255)
    instance, document = prepared(tmp_path, runner=runner)
    result = instance.execute(document, lambda *a: None)
    assert result['status'] == 'failed'
    assert 'SSH host identity' in result['error']
    assert runner.names() == ['ssh']
    assert result['requires_manual_review'] is True


def test_a_failing_apply_playbook_stops_before_verify(tmp_path):
    runner = FakeRunner(apply=2)
    instance, document = prepared(tmp_path, runner=runner)
    result = instance.execute(document, lambda *a: None)
    assert result['status'] == 'failed'
    assert 'apply playbook failed' in result['error']
    assert runner.names() == ['ssh', 'ansible-playbook']
    assert result['requires_manual_review'] is True


def test_a_failed_attempt_cannot_be_replayed_with_a_new_handoff(tmp_path):
    runtime, runner = FakeRuntime(), FakeRunner(ssh=255)
    instance, document = prepared(tmp_path, runtime=runtime, runner=runner)
    assert instance.execute(document, lambda *a: None)['status'] == 'failed'
    runner.ssh = 0
    runtime.document = handoff(guest={'address': '127.0.0.1', 'ssh_port': 2223})
    second = instance.execute(document, lambda *a: None)
    assert second['status'] == 'failed'
    assert 'already executed' in second['error']
    assert len(runtime.calls) == 1
    assert runner.names() == ['ssh']


def test_verify_must_honour_the_declared_assertions(tmp_path):
    runner = FakeRunner(report={'guest-reachable': True, 'python-available': True})
    instance, document = prepared(tmp_path, runner=runner)
    result = instance.execute(document, lambda *a: None)
    assert result['status'] == 'failed'
    assert 'declared assertions' in result['error']


def test_execution_does_not_inherit_ansible_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv('ANSIBLE_SSH_ARGS', '-o StrictHostKeyChecking=no')
    monkeypatch.setenv('ANSIBLE_CONFIG', '/tmp/evil.cfg')
    runner = FakeRunner()
    instance, document = prepared(tmp_path, runner=runner)
    assert instance.execute(document, lambda *a: None)['status'] == 'succeeded'
    for call in runner.calls[1:]:
        env = call['kwargs']['env']
        assert 'ANSIBLE_CONFIG' not in env
        assert env['ANSIBLE_HOST_KEY_CHECKING'] == 'True'
        assert all('-o StrictHostKeyChecking=no' not in value for value in env.values())


def test_the_staged_key_is_removed_after_execution(tmp_path):
    keys = FakeKeys()
    instance, document = prepared(tmp_path, keys=keys)
    assert instance.execute(document, lambda *a: None)['status'] == 'succeeded'
    assert keys.used == ['a' * 32]
    key_folder = tmp_path / 'state' / 'local-vm-ansible' / 'keys'
    assert list(key_folder.iterdir()) == []


def test_a_job_cannot_be_executed_twice(tmp_path):
    instance, document = prepared(tmp_path)
    assert instance.execute(document, lambda *a: None)['status'] == 'succeeded'
    assert instance.execute(document, lambda *a: None)['status'] == 'failed'
    assert instance.runtime is not None
    assert instance.runtime.calls == [instance.runtime.calls[0]]


def test_the_provider_has_no_automatic_rollback(tmp_path):
    instance = provider(tmp_path)
    assert instance.supports_rollback is False
    result = instance.rollback(job(), lambda *a: None)
    assert result['status'] == 'rollback_failed'
    assert result['can_rollback'] is False
    assert result['requires_manual_review'] is True


# ------------------------------------------------------------------- supply chain

def test_the_composition_contains_no_fetch_or_shell():
    source = (ROOT / 'backend' / 'local_vm_ansible.py').read_text()
    assert '"ansible-galaxy"' not in source and "'ansible-galaxy'" not in source
    assert 'shell=True' not in source
    assert 'pip install' not in source
    assert 'http://' not in source and 'https://' not in source


def test_the_example_extension_package_is_valid():
    from backend.extensions import validate_package

    manifest = validate_package(ROOT / 'extensions' / 'local-vm-ansible')
    assert manifest['id'] == 'local-vm-ansible'
    assert 'providers:execution' in manifest['capabilities']


def _loaded_example(tmp_path):
    from backend.extensions import ExtensionRegistry

    package = tmp_path / 'extensions' / 'local-vm-ansible'
    shutil.copytree(ROOT / 'extensions' / 'local-vm-ansible', package)
    registry = ExtensionRegistry()
    reports = registry.load_directory(tmp_path / 'extensions', {'local-vm-ansible'})
    assert reports[0]['status'] == 'loaded', reports
    return registry, package


def test_the_example_extension_loads_and_fails_closed_without_an_adapter(tmp_path):
    registry, _ = _loaded_example(tmp_path)
    provider = registry.get('execution', PROVIDER_ID)
    assert provider.read()['status'] == 'unavailable'
    assert provider.read()['reasons'] == ['Local runtime is not configured']


def test_the_example_extension_wires_a_reviewed_runtime_adapter(tmp_path):
    adapter = '''
class Runtime:
    name = 'local-executor'
    contract = 'local-runtime-v1'

    def provision(self, request):
        raise RuntimeError('fixture only')

def runtime():
    return Runtime()
'''
    registry, package = _loaded_example(tmp_path)
    (package / 'runtime_adapter.py').write_text(adapter)
    registry = type(registry)()
    reports = registry.load_directory(tmp_path / 'extensions', {'local-vm-ansible'})
    assert reports[0]['status'] == 'loaded', reports
    assert registry.get('execution', PROVIDER_ID).read()['status'] == 'configured'
