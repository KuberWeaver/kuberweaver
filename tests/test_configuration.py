"""Configuration comes from the environment; no developer path ships in the code."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_configuration():
    path = ROOT / 'backend/kuberweaver/configuration.py'
    spec = importlib.util.spec_from_file_location('configuration', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def configuration(monkeypatch):
    for name in ('KUBERWEAVER_STATE_DIR', 'KUBERWEAVER_PLUGINS_DIR',
                 'KUBERWEAVER_FRONTEND_DIR', 'KUBERWEAVER_SSH_CONFIG'):
        monkeypatch.delenv(name, raising=False)
    return load_configuration()


def test_defaults_point_inside_the_checkout(configuration):
    assert configuration.state_dir() == configuration.ROOT / 'data'
    assert configuration.plugins_dir() == configuration.ROOT / 'modules'
    assert configuration.frontend_dir() == configuration.ROOT / 'frontend' / 'dist'
    assert configuration.ssh_config() == Path.home() / '.ssh' / 'config'


def test_no_developer_absolute_path_is_compiled_in():
    source = (ROOT / 'backend/kuberweaver/configuration.py').read_text()
    assert '/home/' not in source
    assert '/Users/' not in source


def test_backend_tree_carries_no_developer_absolute_path():
    offenders = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'backend').rglob('*')
                       if path.is_file() and '__pycache__' not in path.parts
                       and ('/home/' in path.read_text() or '/Users/' in path.read_text()))
    assert offenders == []


def test_explicit_values_win(configuration, monkeypatch, tmp_path):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('KUBERWEAVER_PLUGINS_DIR', str(tmp_path / 'plugins'))
    monkeypatch.setenv('KUBERWEAVER_FRONTEND_DIR', str(tmp_path / 'dist'))
    monkeypatch.setenv('KUBERWEAVER_SSH_CONFIG', str(tmp_path / 'ssh_config'))
    assert configuration.state_dir() == tmp_path / 'state'
    assert configuration.plugins_dir() == tmp_path / 'plugins'
    assert configuration.frontend_dir() == tmp_path / 'dist'
    assert configuration.ssh_config() == tmp_path / 'ssh_config'


@pytest.mark.parametrize('name,getter', [
    ('KUBERWEAVER_STATE_DIR', 'state_dir'),
    ('KUBERWEAVER_PLUGINS_DIR', 'plugins_dir'),
    ('KUBERWEAVER_FRONTEND_DIR', 'frontend_dir'),
    ('KUBERWEAVER_SSH_CONFIG', 'ssh_config'),
])
def test_blank_value_falls_back_to_the_default(configuration, monkeypatch, name, getter):
    expected = getattr(configuration, getter)()
    monkeypatch.setenv(name, '   ')
    assert getattr(configuration, getter)() == expected


@pytest.mark.parametrize('name,getter', [
    ('KUBERWEAVER_STATE_DIR', 'state_dir'),
    ('KUBERWEAVER_PLUGINS_DIR', 'plugins_dir'),
    ('KUBERWEAVER_FRONTEND_DIR', 'frontend_dir'),
    ('KUBERWEAVER_SSH_CONFIG', 'ssh_config'),
])
def test_relative_value_is_refused_with_the_variable_name(configuration, monkeypatch, name, getter):
    monkeypatch.setenv(name, 'relative/path')
    with pytest.raises(configuration.ConfigurationError, match=name):
        getattr(configuration, getter)()


def test_tilde_is_expanded(configuration, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', '~/kuberweaver-state')
    assert configuration.state_dir() == Path.home() / 'kuberweaver-state'


def test_relative_ssh_config_is_refused_by_the_loader(configuration, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_SSH_CONFIG', 'ssh_config')
    with pytest.raises(configuration.ConfigurationError, match='KUBERWEAVER_SSH_CONFIG'):
        configuration.require_ssh_config()


def test_missing_ssh_config_is_refused_with_a_documented_remedy(configuration, monkeypatch, tmp_path):
    monkeypatch.setenv('KUBERWEAVER_SSH_CONFIG', str(tmp_path / 'absent'))
    with pytest.raises(configuration.ConfigurationError) as error:
        configuration.require_ssh_config()
    message = str(error.value)
    assert 'KUBERWEAVER_SSH_CONFIG' in message
    assert str(tmp_path / 'absent') in message
    assert 'absolute path' in message


def test_ssh_config_directory_is_not_a_configuration_file(configuration, tmp_path):
    with pytest.raises(configuration.ConfigurationError, match='KUBERWEAVER_SSH_CONFIG'):
        configuration.require_ssh_config(tmp_path)


def test_existing_ssh_config_is_returned(configuration, tmp_path):
    selected = tmp_path / 'ssh_config'
    selected.write_text('Host *\n  StrictHostKeyChecking yes\n')
    assert configuration.require_ssh_config(selected) == selected


def test_configuration_error_keeps_the_value_error_contract(configuration):
    assert issubclass(configuration.ConfigurationError, ValueError)
