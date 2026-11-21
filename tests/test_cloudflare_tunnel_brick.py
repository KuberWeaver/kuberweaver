"""The cloudflare-tunnel workload brick: contract, schema and secret hygiene.

The service accepts a vault reference, not a controller path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kuberweaver.workload_execution import WorkloadError, WorkloadExecutor

ROOT = Path(__file__).parents[1]
PLUGINS = ROOT / 'plugins'
BRICK = PLUGINS / 'cloudflare-tunnel'
UUID = '6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b'
KEY = 'b' * 32
TARGET = {'uuid': UUID, 'address': '192.168.10.63'}
ASSERTIONS = {'service-active', 'binary-present', 'token-staged',
              'unit-present', 'unit-enabled', 'edge-connected'}
BASE = {'target_uuid': UUID, 'ssh_key_id': KEY,
        'tunnel_token_ref': 'cloudflare/tunnel-example'}


@pytest.fixture(scope='module')
def executor(tmp_path_factory):
    return WorkloadExecutor(tmp_path_factory.mktemp('state'), PLUGINS)


@pytest.fixture(scope='module')
def brick(executor):
    return executor.brick('cloudflare-tunnel')


def load_play(name):
    return yaml.safe_load((BRICK / name).read_text())


def test_brick_contract(executor, brick):
    assert brick['id'] == 'cloudflare-tunnel'
    assert brick['kind'] == 'workload'
    assert brick['executor'] == 'ansible-workload-v1'
    assert brick['execution_provider'] == 'ansible-workload-v1'
    assert brick['data_class'] == 'config'
    assert brick['implementation']['role'] == 'cloudflared'
    assert brick['implementation']['version'] == '1.0.0'
    for action in ('snapshot', 'apply', 'verify', 'rollback', 'remove'):
        assert (BRICK / brick['lifecycle'][action]).is_file()
    declared = {a['id'] for a in brick['verify']['assertions']}
    assert declared == ASSERTIONS


def test_role_lock_matches_the_local_role(executor, brick):
    installed = executor.assert_role_installed(brick)
    assert installed['version'] == brick['implementation']['version']
    role_dir = BRICK / 'roles' / brick['implementation']['role']
    assert role_dir.is_dir(), 'the pinned local role must sit beside the brick'
    import hashlib
    checksum = hashlib.sha256()
    for path in sorted(p for p in role_dir.rglob('*') if p.is_file()):
        checksum.update(str(path.relative_to(role_dir)).encode())
        checksum.update(path.read_bytes())
    assert installed['sha256'] == checksum.hexdigest()


def test_schema_is_closed_and_secret_free(brick):
    schema = brick['schema']
    assert schema['type'] == 'object'
    assert schema['additionalProperties'] is False
    assert set(schema['required']) == {'target_uuid', 'ssh_key_id',
                                      'tunnel_token_ref'}
    # No inline secret may ever be accepted in this config.
    for secret_field in ('token', 'tunnel_token', 'api_token', 'password', 'secret'):
        assert secret_field not in schema['properties']


def test_config_passes_admission_unsafe_fields():
    from kuberweaver.policy import unsafe_fields
    assert list(unsafe_fields(BASE)) == []


def test_invalid_configs_are_rejected(executor):
    bad = [
        {**BASE, 'tunnel_token_ref': 'relative/path'},
        {**BASE, 'tunnel_token_ref': '/run/kw/dashboard-password'},
        {**BASE, 'tunnel_token_ref': 'cloudflare/../example'},
        {**BASE, 'tunnel_token_ref': 'cloudflare/tunnel-example\n'},
        {**BASE, 'tunnel_token_file_ref': '/run/kw/tunnel-token'},
        {**BASE, 'token': 'inline-secret'},
        {k: v for k, v in BASE.items() if k != 'ssh_key_id'},
    ]
    for config in bad:
        with pytest.raises(WorkloadError, match='Invalid brick configuration'):
            executor.prepare('cloudflare-tunnel', config, TARGET, 'abcdef0123456789abcdef0123456789')


def test_apply_installs_pinned_cloudflared_and_stages_token_without_leaking():
    play = load_play('apply.yml')[0]
    text = json.dumps(play)
    assert 'ansible.builtin.slurp' not in text
    guard, _, write = play['pre_tasks']
    assert 'kw_runtime_secret is defined' in guard['ansible.builtin.assert']['that']
    assert guard['no_log'] is True
    assert write['no_log'] is True
    assert write['diff'] is False
    assert write['ansible.builtin.copy']['mode'] == '0600'
    # The cloudflared package is pinned by sha256 before dpkg (in the local role).
    role_text = ((BRICK / 'roles/cloudflared/defaults/main.yml').read_text()
                 + (BRICK / 'roles/cloudflared/tasks/main.yml').read_text())
    assert 'cloudflared-linux-amd64.deb' in role_text
    assert 'checksum' in role_text or 'sha256sum -c' in role_text
    # The token lands in a root-only env file, never on the command line.
    assert '/etc/kuberweaver/cloudflared/tunnel.env' in text
