"""Offline contract and lifecycle coverage for the Cloudflare DNS brick."""
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from kuberweaver.policy import unsafe_fields
from kuberweaver.secrets import SecretValue
from kuberweaver.workload_execution import WorkloadError, WorkloadExecutor

ROOT = Path(__file__).resolve().parents[1]
BRICK = ROOT / 'plugins/cloudflare-dns'
UUID = '6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b'
TARGET = {'uuid': UUID, 'address': '192.0.2.2'}
CONFIG = {'target_uuid': UUID, 'ssh_key_id': 'b' * 32,
          'api_token_ref': 'cloudflare/dns-example', 'zone_id': 'c' * 32,
          'record_type': 'A', 'record_name': 'demo.example.com',
          'address': '192.0.2.42', 'ttl': 300}


def test_brick_has_closed_vault_referenced_contract(tmp_path):
    executor = WorkloadExecutor(tmp_path, ROOT / 'plugins')
    brick = executor.brick('cloudflare-dns')
    assert brick['capability'] == 'live'
    assert brick['execution_provider'] == 'ansible-workload-v1'
    assert brick['workload_secret_ref'] == 'api_token_ref'
    assert brick['data_class'] == 'config'
    assert set(brick['lifecycle']) == {'snapshot', 'apply', 'verify', 'rollback', 'remove'}
    assert (BRICK / brick['lifecycle']['remove']).is_file()
    assert not list(unsafe_fields(CONFIG))
    assert executor.prepare('cloudflare-dns', CONFIG, TARGET, 'dns-example')['config'] == CONFIG
    role = BRICK / 'roles' / brick['implementation']['role']
    digest = hashlib.sha256()
    for path in sorted(p for p in role.rglob('*') if p.is_file() and '__pycache__' not in p.parts):
        digest.update(str(path.relative_to(role)).encode())
        digest.update(path.read_bytes())
    assert executor.assert_role_installed(brick)['sha256'] == digest.hexdigest()


@pytest.mark.parametrize('override', [
    {'api_token': 'inline-secret'}, {'api_token_ref': '/etc/shadow'},
    {'api_token_ref': 'cloudflare/../secret'}, {'api_token_ref': 'file/secret\n'},
    {'api_token_ref': 'cloudflare/UPPER'}, {'api_token_ref': 'cloudflare/9'},
    {'zone_id': 'a' * 32 + '\n'}, {'record_name': '*.example.com'},
    {'record_name': 'https://example.com'}, {'record_type': 'MX'}, {'ttl': 4},
    {'api_url': 'https://example.com'},
])
def test_invalid_inputs_fail_schema(tmp_path, override):
    executor = WorkloadExecutor(tmp_path, ROOT / 'plugins')
    with pytest.raises(WorkloadError, match='Invalid brick configuration'):
        executor.prepare('cloudflare-dns', {**CONFIG, **override}, TARGET, 'dns-example')


@pytest.fixture
def dns(monkeypatch):
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)
    source = BRICK / 'roles/kw_cloudflare_dns/library/kw_cloudflare_dns.py'
    assert source.is_file()
    spec = importlib.util.spec_from_file_location('kw_dns_under_test', source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfflineCloudflare:
    """Request boundary fake: this object does not create network sockets."""

    record_id = 'd' * 32
    token = 'fixture-not-a-real-token'

    def __init__(self):
        self.row = None
        self.calls = []
        self.lose_create_reply = False
        self.lose_delete_reply = False

    def open(self, request, *, timeout):
        url = urlsplit(request.full_url)
        assert (url.scheme, url.netloc) == ('https', 'api.cloudflare.com')
        base = '/client/v4/zones/' + CONFIG['zone_id'] + '/dns_records'
        assert request.get_header('Authorization') == 'Bearer ' + self.token
        method = request.get_method()
        self.calls.append(method)
        if url.path == base and method == 'GET':
            assert parse_qs(url.query) == {'name.exact': [CONFIG['record_name']], 'per_page': ['1']}
            result = [self.row] if self.row else []
        elif url.path == base and method == 'POST':
            assert self.row is None
            result = self.row = {'id': self.record_id, **json.loads(request.data)}
            if self.lose_create_reply:
                raise TimeoutError('untrusted upstream detail')
        else:
            assert url.path == base + '/' + self.record_id
            if method == 'GET' and self.row is None:
                raise HTTPError(request.full_url, 404, '', Message(), io.BytesIO(json.dumps({
                    'success': False, 'errors': [{'code': 81044}],
                }).encode()))
            if method == 'GET':
                result = self.row
            else:
                assert method == 'DELETE'
                self.row = None
                if self.lose_delete_reply:
                    raise TimeoutError('untrusted upstream detail')
                result = {'id': self.record_id}
        return io.BytesIO(json.dumps({'success': True, 'result': result}).encode())


@pytest.fixture
def offline(dns, tmp_path, monkeypatch):
    cloud = OfflineCloudflare()
    monkeypatch.setattr(dns, 'build_opener', lambda *_: cloud)

    def run(action, config=CONFIG, token=cloud.token):
        return dns.run(action, config, tmp_path / f'{action}.result.json', token)

    return cloud, run


@pytest.mark.parametrize('cleanup', ['rollback', 'remove'])
def test_creates_verifies_and_cleans_only_the_receipted_record(offline, tmp_path, cleanup):
    cloud, run = offline
    assert not run('snapshot')
    assert run('apply')
    assert not run('apply')  # retries with a confirmed receipt are read-only
    assert cloud.row['content'] == CONFIG['address']
    assert cloud.row['ttl'] == CONFIG['ttl']
    assert cloud.row['proxied'] is False
    assert not run('verify')
    result = json.loads((tmp_path / 'verify.result.json').read_text())
    assert result['assertions'] == {'record-matches': True}
    assert run(cleanup)
    assert not run(cleanup)
    assert cloud.row is None
    assert cloud.calls == ['GET', 'GET', 'POST', 'GET', 'GET', 'GET', 'DELETE', 'GET']
    for path in tmp_path.iterdir():
        if path.is_file():
            assert path.stat().st_mode & 0o777 == 0o600
            assert cloud.token not in path.read_text()


def test_never_adopts_foreign_record(dns, offline):
    cloud, run = offline
    cloud.row = {'id': cloud.record_id}
    with pytest.raises(dns.BrickError, match='already has a record'):
        run('snapshot')
    for action in ('apply', 'remove', 'rollback'):
        with pytest.raises(dns.BrickError, match='snapshot'):
            run(action)
    assert cloud.calls == ['GET']


def test_refuses_drift_before_delete(dns, offline):
    cloud, run = offline
    run('snapshot')
    run('apply')
    cloud.row['content'] = '192.0.2.10'
    for action in ('verify', 'remove', 'rollback'):
        with pytest.raises(dns.BrickError, match='does not match'):
            run(action)
    assert 'DELETE' not in cloud.calls


def test_lost_create_receipt_blocks_all_later_writes(dns, offline):
    cloud, run = offline
    run('snapshot')
    cloud.lose_create_reply = True
    with pytest.raises(dns.BrickError, match='request failed') as exc:
        run('apply')
    assert 'untrusted upstream' not in str(exc.value)
    for action in ('snapshot', 'apply', 'remove', 'rollback'):
        with pytest.raises(dns.BrickError, match='manual review'):
            run(action)
    assert cloud.calls == ['GET', 'GET', 'POST']
    assert cloud.row is not None


@pytest.mark.parametrize('config,token', [
    (CONFIG, ''), (CONFIG, 'bad\r\ncredential'),
    ({**CONFIG, 'address': 'not-an-ip'}, OfflineCloudflare.token),
    ({**CONFIG, 'record_type': 'AAAA'}, OfflineCloudflare.token),
])
def test_invalid_runtime_input_never_reaches_transport(dns, offline, config, token):
    cloud, run = offline
    with pytest.raises(dns.BrickError):
        run('snapshot', config, token)
    assert cloud.calls == []


def test_lost_delete_receipt_blocks_a_retry(dns, offline):
    cloud, run = offline
    run('snapshot')
    run('apply')
    cloud.lose_delete_reply = True
    with pytest.raises(dns.BrickError, match='request failed'):
        run('remove')
    for action in ('apply', 'remove', 'rollback'):
        with pytest.raises(dns.BrickError, match='manual review'):
            run(action)
    assert cloud.calls.count('DELETE') == 1


def test_real_ansible_passes_secret_locally_without_logging_it(tmp_path):
    playbook = Path(sys.executable).with_name('ansible-playbook')
    if not playbook.is_file():
        pytest.skip('ansible-playbook is not installed beside Python')
    # Replace the module in a disposable copy, not the role/playbook wiring.
    # The stub has no network code and proves receipt of the runtime-only token.
    plugins = tmp_path / 'plugins'
    copied = plugins / BRICK.name
    shutil.copytree(BRICK, copied)
    module = copied / 'roles/kw_cloudflare_dns/library/kw_cloudflare_dns.py'
    module.write_text('''#!/usr/bin/python
import json
from ansible.module_utils.basic import AnsibleModule

m = AnsibleModule(argument_spec={key: {'type': kind} for key, kind in (
    ('action', 'str'), ('api_token', 'str'), ('result_path', 'path'), ('config', 'dict'))})
p = m.params
if p['api_token'] != 'fixture-not-a-real-token' or p['action'] != 'verify':
    m.fail_json(msg='Unexpected invocation')
with open(p['result_path'], 'w') as result:
    json.dump({'assertions': {'record-matches': True}}, result)
m.exit_json(changed=False, confidential=p['api_token'])
''')
    # If a regression tries the guest inventory's SSH transport, fail without opening a socket.
    forbid_ssh = tmp_path / 'forbid-ssh'
    forbid_ssh.write_text('#!/bin/sh\nexit 65\n')
    forbid_ssh.chmod(0o700)

    def invoke(argv, **opts):
        assert OfflineCloudflare.token not in str(argv)
        opts['env']['ANSIBLE_SSH_EXECUTABLE'] = str(forbid_ssh)
        result = subprocess.run(argv, **opts)
        assert OfflineCloudflare.token not in result.stdout + result.stderr
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    key = tmp_path / 'unused-key'
    key.touch(mode=0o600)
    executor = WorkloadExecutor(tmp_path, plugins, playbook=playbook, key_path=key, runner=invoke)
    plan = executor.prepare(BRICK.name, CONFIG, TARGET, 'dns-example')
    secret = SecretValue(OfflineCloudflare.token.encode())
    try:
        executor.runtime_secret = secret
        result = executor._run(executor.brick(BRICK.name), 'verify', tmp_path / 'workload', plan)
    finally:
        secret.close()
    assert result['report']['assertions'] == {'record-matches': True}
    assert OfflineCloudflare.token not in (tmp_path / 'workload/verify.vars.json').read_text()
