"""Native transport policy, without contacting ESXi."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def transport():
    path = ROOT / 'backend/ovf_transport.py'
    assert path.exists(), 'Pin-only native transport not implemented'
    spec = importlib.util.spec_from_file_location('ovf_transport_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_namespace_tool_refuses_transport(monkeypatch):
    module = transport()
    assert hasattr(module, 'require_transport'), 'Namespace availability gate missing'
    monkeypatch.setattr(module, 'BWRAP', Path('/nonexistent/ovf-bwrap'))
    with pytest.raises(ValueError, match='transport'):
        module.require_transport()


def test_pin_transport_isolates_ca_stores_and_private_profile(tmp_path):
    module = transport()
    module.BWRAP = Path('/usr/bin/true')
    pin = ':'.join(['A1'] * 32)
    args = ['--targetSSLThumbprint=' + pin, '--name=example']
    command = module.pinned_command('/usr/bin/ovftool', tmp_path, args)
    assert '--unshare-user' in command
    assert str(tmp_path / 'ovf-trust') in command
    assert '/etc/ssl/certs' in command
    assert command[-3:] == ['/usr/bin/ovftool', *args]
    assert (tmp_path / 'ovf-home').stat().st_mode & 0o777 == 0o700
    assert (tmp_path / 'ovf-tmp').stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('override', ['--targetPEM=/tmp/cert', '--sourcePEM=/tmp/cert', '--noSSLVerify'])
def test_pin_transport_refuses_native_trust_bypass(tmp_path, override):
    module = transport()
    module.BWRAP = Path('/usr/bin/true')
    with pytest.raises(ValueError, match='override'):
        module.pinned_command('/usr/bin/ovftool', tmp_path, ['--targetSSLThumbprint=' + ':'.join(['AB'] * 32), override])


def test_pin_transport_refuses_populated_trust_directory(tmp_path):
    module = transport()
    module.BWRAP = Path('/usr/bin/true')
    directory = tmp_path / 'ovf-trust'; directory.mkdir(mode=0o700)
    (directory / 'unexpected.pem').write_text('not permitted')
    with pytest.raises(ValueError, match='empty'):
        module.pinned_command('/usr/bin/ovftool', tmp_path, ['--targetSSLThumbprint=' + ':'.join(['AB'] * 32)])
