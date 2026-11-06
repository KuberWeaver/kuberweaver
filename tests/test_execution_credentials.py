"""Controlled local providers and real child processes; never contacts a lab host."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from kuberweaver.execution import Executor, SSH_CONFIG
from kuberweaver.secrets import RestrictedFileProvider


def configure(state, value=None):
    path = state / 'config/execution-credentials.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value if value is not None else {
        'enabled': True, 'targets': {'app': {'private_key_ref': 'ssh/app', 'username': 'lab-user'}}}))
    return path


def entry(executor):
    nodes, _, _ = executor._prepare({'id': 'credentials', 'graph': {'nodes': [
        {'id': 'web', 'plugin_id': 'nginx', 'config': {'targets': ['app']}}]}})
    return {**nodes[0], 'target': 'app'}


def controlled_provider(tmp_path):
    key = tmp_path / 'controlled-key'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True,
                   capture_output=True)
    return key, RestrictedFileProvider({'ssh/app': key})


def test_real_child_can_use_temporary_key_only_during_operation(tmp_path):
    state = tmp_path / 'state'
    configure(state)
    source, provider = controlled_provider(tmp_path)
    executor = Executor(state)
    executor.secret_resolver = provider.resolve
    seen = []

    def runner(argv, **kwargs):
        key = Path(kwargs['env']['ANSIBLE_PRIVATE_KEY_FILE'])
        seen.append(key)
        assert key != source
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
        assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
        assert kwargs['env']['ANSIBLE_REMOTE_USER'] == 'lab-user'
        assert kwargs['env']['ANSIBLE_HOST_KEY_CHECKING'] == 'True'
        assert source.read_text() not in json.dumps([argv, kwargs['env']])
        inventory = Path(argv[argv.index('-i') + 1]).read_text()
        assert str(key) not in inventory
        assert str(SSH_CONFIG) in inventory
        script = '''
import json, os, pathlib, subprocess, sys
key = pathlib.Path(os.environ['ANSIBLE_PRIVATE_KEY_FILE'])
subprocess.run(['ssh-keygen', '-y', '-f', str(key)], check=True, stdout=subprocess.DEVNULL)
params = json.loads(pathlib.Path(sys.argv[1]).read_text())
pathlib.Path(params['kw_result_path']).write_text(json.dumps({'healthy': True}))
# Deliberately emulate an unsafe upstream diagnostic: it must never be logged.
print(key.read_text())
'''
        return subprocess.run([sys.executable, '-c', script, argv[-1][1:]], **kwargs)

    executor.runner = runner
    assert executor._operation('credentials', entry(executor), 'verify', lambda *_: None) == {'healthy': True}
    assert seen and all(not path.exists() and not path.parent.exists() for path in seen)
    assert all(source.read_bytes() not in path.read_bytes() for path in state.rglob('*') if path.is_file())


@pytest.mark.parametrize('config', [
    None, [], {}, {'enabled': 'yes', 'targets': {}},
    {'enabled': True, 'targets': {}},
    {'enabled': True, 'targets': {'operations': {'private_key_ref': 'ssh/app'}}},
    {'enabled': True, 'targets': {'app': {}}},
    {'enabled': True, 'targets': {'app': {'private_key_ref': '/etc/key'}}},
    {'enabled': True, 'targets': {'app': {'private_key_ref': 'ssh/app', 'host': 'evil'}}},
    *[{'enabled': True, 'targets': {'app': {'private_key_ref': 'ssh/app', 'username': name}}}
      for name in ('-oProxyCommand=id', 'root;id', 'root\n', '', '../root', 'user@host', None, 2)],
])
def test_invalid_configuration_never_falls_back_or_calls_runner(tmp_path, config):
    state = tmp_path / 'state'
    path = configure(state)
    path.write_text(json.dumps(config))
    executor = Executor(state)
    calls = []
    executor.runner = lambda *args, **kwargs: calls.append(args)
    with pytest.raises(RuntimeError, match='credential'):
        executor._operation('credentials', entry(executor), 'verify', lambda *_: None)
    assert not calls
    assert not list(state.rglob('*.vars.json'))


@pytest.mark.parametrize('mode', ['missing-resolver', 'refused', 'empty'])
def test_missing_configured_reference_never_falls_back(tmp_path, mode):
    from contextlib import contextmanager
    from kuberweaver.secrets import SecretValue
    state = tmp_path / 'state'
    configure(state)
    executor = Executor(state)
    @contextmanager
    def resolve(_):
        if mode == 'refused':
            raise RuntimeError('unsafe upstream error')
        yield SecretValue(b'')
    if mode != 'missing-resolver':
        executor.secret_resolver = resolve
    calls = []
    executor.runner = lambda *args, **kwargs: calls.append(args)
    with pytest.raises(RuntimeError, match='credential') as error:
        executor._operation('credentials', entry(executor), 'verify', lambda *_: None)
    assert not calls


@pytest.mark.parametrize('outcome', ['failure', 'timeout', 'raise', 'result-leak'])
def test_sensitive_failures_are_sanitized_and_always_remove_temporary_key(tmp_path, outcome):
    from types import SimpleNamespace
    state = tmp_path / 'state'
    configure(state)
    source, provider = controlled_provider(tmp_path)
    executor = Executor(state)
    executor.secret_resolver = provider.resolve
    seen = []
    events = []
    def runner(argv, **kwargs):
        key = Path(kwargs['env']['ANSIBLE_PRIVATE_KEY_FILE'])
        seen.append(key)
        raw = key.read_text()
        params = json.loads(Path(argv[-1][1:]).read_text())
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(argv, 300, output=raw, stderr=raw)
        if outcome == 'raise':
            raise RuntimeError(raw)
        result = {'healthy': True}
        if outcome in ('failure', 'result-leak'):
            result = {'error' if outcome == 'failure' else 'payload': raw}
        Path(params['kw_result_path']).write_text(json.dumps(result))
        return SimpleNamespace(returncode=1 if outcome == 'failure' else 0, stdout=raw, stderr=raw)
    executor.runner = runner
    with pytest.raises(RuntimeError) as error:
        executor._operation('credentials', entry(executor), 'verify', lambda *args: events.append(args))
    assert source.read_text() not in str(error.value)
    assert source.read_text() not in json.dumps(events)
    assert all(not path.exists() and not path.parent.exists() for path in seen)
    assert all(source.read_bytes() not in path.read_bytes() for path in state.rglob('*') if path.is_file())
    assert not list(state.rglob('*.vars.json'))
    assert not list(state.rglob('*.result.json'))


def test_constructor_resolver_hook_and_no_username_preserve_ssh_user(tmp_path):
    state = tmp_path / 'state'
    configure(state, {'enabled': True, 'targets': {'app': {'private_key_ref': 'ssh/app'}}})
    _, provider = controlled_provider(tmp_path)
    executor = Executor(state, secret_resolver=provider.resolve)
    def runner(argv, **kwargs):
        from types import SimpleNamespace
        assert 'ANSIBLE_REMOTE_USER' not in kwargs['env']
        params = json.loads(Path(argv[-1][1:]).read_text())
        Path(params['kw_result_path']).write_text('{"healthy": true}')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    executor.runner = runner
    assert executor._operation('credentials', entry(executor), 'verify', lambda *_: None)['healthy']


def test_configured_runner_does_not_inherit_secret_or_ansible_override_environment(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    configure(state)
    _, provider = controlled_provider(tmp_path)
    monkeypatch.setenv('CONTROLLED_PROVIDER_TOKEN', 'not-for-subprocess')
    monkeypatch.setenv('ANSIBLE_LOG_PATH', str(tmp_path / 'unsafe-log'))
    monkeypatch.setenv('ANSIBLE_SSH_ARGS', '-o StrictHostKeyChecking=no')
    monkeypatch.setenv('SSH_AUTH_SOCK', '/untrusted/agent')
    executor = Executor(state, secret_resolver=provider.resolve)
    def runner(argv, **kwargs):
        from types import SimpleNamespace
        env = kwargs['env']
        assert 'CONTROLLED_PROVIDER_TOKEN' not in env
        assert 'ANSIBLE_LOG_PATH' not in env
        assert 'SSH_AUTH_SOCK' not in env
        assert 'StrictHostKeyChecking=yes' in env['ANSIBLE_SSH_ARGS']
        assert 'ControlMaster=no' in env['ANSIBLE_SSH_ARGS']
        assert Path(env['ANSIBLE_CONFIG']).suffix == '.cfg'
        assert Path(env['ANSIBLE_CONFIG']).read_text() == '[defaults]\n'
        probe = subprocess.run([str(Path(sys.executable).parent / 'ansible-config'), 'dump', '--only-changed'],
                               env=env, capture_output=True, text=True)
        assert probe.returncode == 0, 'Ansible must accept the isolated configuration'
        params = json.loads(Path(argv[-1][1:]).read_text())
        Path(params['kw_result_path']).write_text('{"healthy": true}')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    executor.runner = runner
    assert executor._operation('credentials', entry(executor), 'verify', lambda *_: None)['healthy']


def test_selected_key_excludes_configured_and_agent_identities(tmp_path):
    import shlex
    state = tmp_path / 'state'
    configure(state)
    _, provider = controlled_provider(tmp_path)
    executor = Executor(state, secret_resolver=provider.resolve)
    def runner(argv, **kwargs):
        from types import SimpleNamespace
        overrides = json.loads(argv[argv.index('-e') + 1])
        command = ['ssh', '-G', *shlex.split(kwargs['env']['ANSIBLE_SSH_ARGS']),
                   *shlex.split(overrides['ansible_ssh_common_args']),
                   '-i', kwargs['env']['ANSIBLE_PRIVATE_KEY_FILE'], 'infra-app']
        probe = subprocess.run(command, capture_output=True, text=True)
        assert probe.returncode == 0, 'The isolated SSH configuration must be valid'
        options = [line.split(' ', 1) for line in probe.stdout.splitlines()]
        assert [value for name, value in options if name == 'identityfile'] == [kwargs['env']['ANSIBLE_PRIVATE_KEY_FILE']]
        assert ['identityagent', 'none'] in options
        assert ['stricthostkeychecking', 'true'] in options
        assert any(name == 'proxycommand' and 'gns3@192.168.10.55' in value for name, value in options)
        params = json.loads(Path(argv[-1][1:]).read_text())
        Path(params['kw_result_path']).write_text('{"healthy": true}')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    executor.runner = runner
    assert executor._operation('credentials', entry(executor), 'verify', lambda *_: None)['healthy']


def test_reference_fingerprint_pins_mapping_and_provider_without_values(tmp_path):
    from kuberweaver import execution_credentials as credentials
    state = tmp_path / 'state'
    path = configure(state)
    binding = {'name': 'controlled', 'provenance': 'source-version'}
    first = credentials.credential_configuration_fingerprint(state, {'app'}, binding)
    path.write_text(json.dumps(json.loads(path.read_text()), indent=4))
    assert credentials.credential_configuration_fingerprint(state, {'app'}, binding) == first
    assert credentials.credential_configuration_fingerprint(state, {'app'}, {'name': 'other'}) != first
    configure(state, {'enabled': True, 'targets': {'app': {'private_key_ref': 'ssh/other'}}})
    assert credentials.credential_configuration_fingerprint(state, {'app'}, binding) != first


def test_real_provider_resolves_each_lifecycle_operation_and_allows_rotation(tmp_path):
    from test_execution import SimulatedAnsible, graph
    from kuberweaver.execution import TARGETS
    from kuberweaver.execution_credentials import credential_configuration_fingerprint
    state = tmp_path / 'state'
    configure(state, {'enabled': True, 'targets': {
        target: {'private_key_ref': 'ssh/app'} for target in TARGETS}})
    source, provider = controlled_provider(tmp_path)
    fingerprint = credential_configuration_fingerprint(state, TARGETS, {'name': 'controlled'})
    executor = Executor(state, secret_resolver=provider.resolve)
    simulated = SimulatedAnsible()
    transient = []
    def runner(argv, **kwargs):
        key = Path(kwargs['env']['ANSIBLE_PRIVATE_KEY_FILE'])
        transient.append(key)
        assert key.read_bytes() == source.read_bytes()
        return simulated(argv, **kwargs)
    executor.runner = runner
    job = {'id': 'credentials', 'graph': graph()}
    assert executor.execute(job, lambda *_: None)['status'] == 'succeeded'
    replacement_dir = tmp_path / 'replacement'
    replacement_dir.mkdir()
    replacement, _ = controlled_provider(replacement_dir)
    source.write_bytes(replacement.read_bytes())
    assert credential_configuration_fingerprint(state, TARGETS, {'name': 'controlled'}) == fingerprint
    assert executor.rollback(job, lambda *_: None)['status'] == 'rolled_back'
    assert len(transient) == 8
    assert all(not key.exists() and not key.parent.exists() for key in transient)




