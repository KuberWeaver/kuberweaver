"""Real local Fernet provider tests; all credential values are disposable."""
import importlib
import os
from pathlib import Path

import pytest

from backend.secrets import SecretError, SecretValue


def vault_module():
    try:
        return importlib.import_module('backend.local_vault')
    except ModuleNotFoundError:
        pytest.fail('optional local vault implementation is missing')


ROOT = Path(__file__).resolve().parents[1]


def test_setup_cli_opt_in_extension_and_no_binding_change(tmp_path):
    import json
    import subprocess
    import sys
    script = ROOT / 'scripts/setup_local_vault.py'
    assert script.is_file(), 'local vault setup CLI is missing'
    config = tmp_path / 'config'
    config.mkdir(mode=0o700)
    binding = config / 'providers.json'
    binding.write_bytes(b'{"preserve":"existing binding"}')
    activation = tmp_path / 'extensions.json'
    activation.write_text(json.dumps({'schema_version': 1, 'enabled': ['existing-extension']}))
    activation.chmod(0o600)
    before = activation.read_bytes()
    command = [sys.executable, str(script), '--data-dir', str(tmp_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert activation.read_bytes() == before
    assert not (tmp_path / 'extensions').exists()
    result = subprocess.run(command + ['--install'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(activation.read_bytes())['enabled'] == ['existing-extension', 'local-vault']
    assert binding.read_bytes() == b'{"preserve":"existing binding"}'
    repeated = subprocess.run(command + ['--install'], capture_output=True, text=True)
    assert repeated.returncode == 0
    assert json.loads(repeated.stdout)['extension_activation_changed'] is False
    from backend.extensions import ExtensionRegistry
    registry = ExtensionRegistry()
    reports = registry.load_directory(tmp_path / 'extensions', {'local-vault'})
    assert reports[0]['status'] == 'loaded'
    provider = registry.get('secrets', 'local-vault')
    assert provider.describe()['can_store'] is True
    provider.store('example', SecretValue(os.urandom(32)))
    assert 'key' not in result.stdout and 'secret_value' not in result.stdout


def test_extension_missing_storage_is_unavailable_without_initializing(tmp_path):
    import shutil
    from backend.extensions import ExtensionRegistry
    source = ROOT / 'extensions/local-vault'
    assert source.is_dir(), 'opt-in local vault extension is missing'
    shutil.copytree(source, tmp_path / 'extensions/local-vault')
    registry = ExtensionRegistry()
    assert registry.load_directory(tmp_path / 'extensions')[0]['status'] == 'disabled'
    assert registry.load_directory(tmp_path / 'extensions', {'local-vault'})[0]['status'] == 'loaded'
    assert registry.get('secrets', 'local-vault') is None
    assert not (tmp_path / 'plugin-data/local-vault/key').exists()


def test_readonly_metadata_and_no_implicit_creation(tmp_path):
    module = vault_module()
    directory = tmp_path / 'absent'
    provider = module.LocalVaultProvider(directory)
    assert provider.describe() == {
        'provider': 'local-vault', 'can_store': False, 'capabilities': ['read'],
        'namespaced_references': True, 'persistent_secret_cache': False,
        'encryption': 'fernet',
    }
    with pytest.raises(SecretError):
        with provider.resolve('example'):
            pass
    with pytest.raises(SecretError):
        provider.store('example', SecretValue(b'disposable'))
    assert not directory.exists()
    for invalid in ('true', 1, None):
        with pytest.raises(SecretError):
            module.LocalVaultProvider(directory, allow_write=invalid)


@pytest.mark.parametrize('attack', ['substitution', 'tamper', 'wrong-key', 'missing-key',
    'key-mode', 'directory-mode', 'cipher-mode', 'cipher-link', 'key-link', 'ancestor-link',
    'empty', 'oversize', 'bad-name'])
def test_fail_closed_preserves_existing_data(tmp_path, attack):
    from cryptography.fernet import Fernet
    import hashlib
    module = vault_module()
    directory = tmp_path / 'vault'
    module.initialize(directory)
    provider = module.LocalVaultProvider(directory, allow_write=True)
    provider.store('first', SecretValue(b'disposable-first'))
    provider.store('second', SecretValue(b'disposable-second'))
    first = directory / (hashlib.sha256(b'first').hexdigest() + '.fernet')
    second = directory / (hashlib.sha256(b'second').hexdigest() + '.fernet')
    if attack == 'substitution': first.write_bytes(second.read_bytes())
    if attack == 'tamper': first.write_bytes(b'corrupted')
    if attack == 'wrong-key': (directory / 'key').write_bytes(Fernet.generate_key())
    if attack == 'missing-key': (directory / 'key').unlink()
    if attack == 'key-mode': (directory / 'key').chmod(0o644)
    if attack == 'directory-mode': directory.chmod(0o755)
    if attack == 'cipher-mode': first.chmod(0o644)
    if attack in ('cipher-link', 'key-link'):
        target = first if attack == 'cipher-link' else directory / 'key'
        original = tmp_path / 'original'
        target.rename(original)
        target.symlink_to(original)
    if attack == 'ancestor-link':
        link = tmp_path / 'linked'
        link.symlink_to(tmp_path, target_is_directory=True)
        provider = module.LocalVaultProvider(link / 'vault')
    if attack in ('empty', 'oversize', 'bad-name'):
        before = first.read_bytes()
        name = '../escape' if attack == 'bad-name' else 'first'
        raw = b'' if attack == 'empty' else b'x' * (128 * 1024 + 1)
        with pytest.raises(SecretError): provider.store(name, SecretValue(raw))
        assert first.read_bytes() == before
    else:
        with pytest.raises(SecretError):
            with provider.resolve('first'): pass
    if attack == 'missing-key':
        with pytest.raises(SecretError): module.initialize(directory)
        assert not (directory / 'key').exists()


def test_authenticated_human_ingestion_and_agent_denial_with_real_extension(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from fastapi.testclient import TestClient
    from test_connections import make_app, login, spec, issue_agent
    from backend.runtime import rebuild_registry
    app = make_app(tmp_path, monkeypatch)
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/setup_local_vault.py'),
        '--data-dir', str(app.state.state_dir), '--install'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
    rebuild_registry(app)
    password = os.urandom(32).hex()
    credentials = {'username': 'disposable-user', 'password': password}
    with TestClient(app) as human:
        login(human, app)
        created = human.post('/api/connections', json=spec(secrets_provider='local-vault'))
        assert created.status_code == 201
        path = '/api/connections/' + created.json()['id']
        response = human.put(path + '/credentials', json=credentials)
        assert response.status_code == 200, response.text
        assert password not in response.text
        with app.state.connections.access(created.json()['id']) as (_, raw):
            assert json.loads(raw) == credentials
        directory = app.state.state_dir / 'plugin-data/local-vault'
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        token = issue_agent(human, 'admin', ['configure', 'read:infrastructure'])
        from contextlib import closing
        with closing(TestClient(app)) as agent:
            agent.headers['Authorization'] = 'Bearer ' + token
            assert agent.put(path + '/credentials', json={**credentials, 'password': 'denied'}).status_code == 403
        assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
        saved = json.dumps(app.state.store.all('SELECT * FROM connections')
                           + app.state.store.all('SELECT * FROM audit'))
        assert password not in saved


def test_atomic_temporary_collision_does_not_delete_foreign_file(tmp_path, monkeypatch):
    module = vault_module()
    directory = tmp_path / 'vault'
    module.initialize(directory)
    collision = directory / '.pending-collision'
    collision.write_bytes(b'foreign-marker')
    collision.chmod(0o600)
    monkeypatch.setattr(module.secrets, 'token_hex', lambda size: 'collision')
    with pytest.raises(SecretError):
        module.LocalVaultProvider(directory, allow_write=True).store('example', SecretValue(b'disposable'))
    assert collision.read_bytes() == b'foreign-marker'


def test_descriptor_pins_parent_swapped_during_write(tmp_path, monkeypatch):
    module = vault_module()
    directory = tmp_path / 'vault'
    module.initialize(directory)
    foreign = tmp_path / 'foreign'
    foreign.mkdir(mode=0o700)
    atomic = module._atomic
    swapped = False

    def swap(fd, name, raw):
        nonlocal swapped
        if not swapped:
            swapped = True
            directory.rename(tmp_path / 'pinned')
            directory.symlink_to(foreign, target_is_directory=True)
        return atomic(fd, name, raw)

    monkeypatch.setattr(module, '_atomic', swap)
    module.LocalVaultProvider(directory, allow_write=True).store('example', SecretValue(b'disposable'))
    assert list(foreign.iterdir()) == []
    with pytest.raises(SecretError):
        with module.LocalVaultProvider(directory).resolve('example'): pass
    with module.LocalVaultProvider(tmp_path / 'pinned').resolve('example') as value:
        assert value.bytes() == b'disposable'


def test_reads_do_not_change_storage_and_maximum_binary_value_roundtrips(tmp_path):
    module = vault_module()
    directory = tmp_path / 'vault'
    module.initialize(directory)
    provider = module.LocalVaultProvider(directory, allow_write=True)
    provider.store('a' * 128, SecretValue(b'\x00' * (128 * 1024)))
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    with provider.resolve('a' * 128) as value:
        assert value.bytes() == b'\x00' * (128 * 1024)
    provider.describe()
    module.initialize(directory)  # idempotent, no key regeneration
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}


def test_interprocess_writes_are_serialized(tmp_path):
    import subprocess
    import sys
    module = vault_module()
    directory = tmp_path / 'vault'
    module.initialize(directory)
    code = '''import os,sys
from backend.local_vault import LocalVaultProvider
from backend.secrets import SecretValue
p = LocalVaultProvider(sys.argv[1], allow_write=True)
for i in range(12):
    p.store('shared', SecretValue(bytes([int(sys.argv[2])]) * 4096))
'''
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    processes = [subprocess.Popen([sys.executable, '-c', code, str(directory), str(index)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) for index in range(1, 5)]
    for process in processes:
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0, err.decode()
        assert out == b''
    with module.LocalVaultProvider(directory).resolve('shared') as value:
        assert value.bytes() in [bytes([index]) * 4096 for index in range(1, 5)]
    assert not any(p.name.startswith('.pending') for p in directory.iterdir())


def test_failed_publication_confirmation_rolls_back(tmp_path, monkeypatch):
    module = vault_module()
    directory = tmp_path / 'vault'
    module.initialize(directory)
    provider = module.LocalVaultProvider(directory, allow_write=True)
    provider.store('example', SecretValue(b'previous-disposable'))
    atomic = module._atomic
    once = True

    def interrupted(fd, name, raw):
        nonlocal once
        atomic(fd, name, raw)
        if once:
            once = False
            raise OSError('injected fsync confirmation failure')

    monkeypatch.setattr(module, '_atomic', interrupted)
    with pytest.raises(SecretError):
        provider.store('example', SecretValue(b'next-disposable'))
    with provider.resolve('example') as value:
        assert value.bytes() == b'previous-disposable'


@pytest.fixture
def group_writable_layout(tmp_path, monkeypatch):
    """Real IO under a virtual root: pytest's private ancestors must not mask 0775."""
    module = vault_module()
    project = tmp_path / 'project'
    project.mkdir()
    project.chmod(0o775)
    (project / 'data').mkdir(mode=0o700)
    (project / 'data/plugin-data').mkdir(mode=0o700)
    original_open = os.open

    def rooted_open(path, flags, *args, **kwargs):
        if path == '/' and 'dir_fd' not in kwargs:
            return original_open(tmp_path, flags, *args, **kwargs)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, 'open', rooted_open)
    return module, project, Path('/project/data/plugin-data/local-vault')


def test_group_writable_ancestor_before_private_barrier_roundtrips(group_writable_layout):
    module, project, directory = group_writable_layout
    module.initialize(directory)
    module.LocalVaultProvider(directory, allow_write=True).store('example', SecretValue(b'disposable'))
    with module.LocalVaultProvider(directory).resolve('example') as value:
        assert value.bytes() == b'disposable'
    assert project.stat().st_mode & 0o777 == 0o775
    assert (project / 'data/plugin-data/local-vault').stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('attack', ['world-writable', 'final-mode', 'ancestor-link',
                                   'foreign-owner', 'key-hardlink'])
def test_group_writable_layout_security_boundaries(group_writable_layout, monkeypatch, attack):
    module, project, directory = group_writable_layout
    module.initialize(directory)
    provider = module.LocalVaultProvider(directory, allow_write=True)
    provider.store('example', SecretValue(b'disposable'))
    vault = project / 'data/plugin-data/local-vault'
    if attack == 'world-writable':
        project.chmod(0o777)
    elif attack == 'final-mode':
        vault.chmod(0o755)
    elif attack == 'ancestor-link':
        project.rename(project.with_name('original'))
        project.symlink_to(project.with_name('original'), target_is_directory=True)
    elif attack == 'foreign-owner':
        original_fstat = os.fstat
        inode = project.stat().st_ino

        def foreign_fstat(fd):
            info = original_fstat(fd)
            if info.st_ino == inode:
                fields = list(info)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return info

        monkeypatch.setattr(module.os, 'fstat', foreign_fstat)
    else:
        os.link(vault / 'key', project / 'linked-key')
    with pytest.raises(SecretError):
        with provider.resolve('example'):
            pass


def test_group_writable_ancestor_exchange_during_traversal_is_pinned(group_writable_layout, monkeypatch):
    module, project, directory = group_writable_layout
    module.initialize(directory)
    foreign = project.with_name('foreign')
    foreign.mkdir(mode=0o700)
    original_open = os.open
    swapped = False

    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == 'data' and not swapped:
            swapped = True
            project.rename(project.with_name('pinned'))
            project.symlink_to(foreign, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, 'open', swap)
    module.LocalVaultProvider(directory, allow_write=True).store('example', SecretValue(b'disposable'))
    assert list(foreign.iterdir()) == []
    with pytest.raises(SecretError):
        with module.LocalVaultProvider(directory).resolve('example'):
            pass
    with module.LocalVaultProvider('/pinned/data/plugin-data/local-vault').resolve('example') as value:
        assert value.bytes() == b'disposable'


def test_initialize_roundtrip_restart_and_lifetime(tmp_path):
    module = vault_module()
    directory = tmp_path / 'vault'
    module.initialize(directory)
    provider = module.LocalVaultProvider(directory, allow_write=True)
    raw = os.urandom(48)
    with_value = SecretValue(raw)
    try:
        provider.store('connections/example', with_value)
    finally:
        with_value.close()
    with module.LocalVaultProvider(directory).resolve('connections/example') as value:
        assert value.bytes() == raw
    with pytest.raises(SecretError):
        value.bytes()
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir())
    assert all(raw not in p.read_bytes() for p in directory.iterdir())
