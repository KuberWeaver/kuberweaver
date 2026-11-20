"""The grafana workload brick: contract, schema and secret hygiene.

The brick is data. These tests prove it is a valid workload brick for the
catalogue and the executor, that its configuration schema is bounded, that the
pinned upstream role is really installed and locked, and that the playbooks
keep the generated admin password out of plans, reports and logs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from kuberweaver.catalog import Catalog
from kuberweaver.workload_execution import WorkloadError, WorkloadExecutor

ROOT = Path(__file__).parents[1]
PLUGINS = ROOT / 'plugins'
BRICK = PLUGINS / 'grafana'
UUID = '6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b'
KEY = 'b' * 32
TARGET = {'uuid': UUID, 'address': '192.168.10.63'}
ASSERTIONS = {'service-active', 'port-listening', 'login-page',
              'config-present', 'password-protected'}


@pytest.fixture(scope='module')
def executor(tmp_path_factory):
    return WorkloadExecutor(tmp_path_factory.mktemp('state'), PLUGINS)


@pytest.fixture(scope='module')
def brick(executor):
    return executor.brick('grafana')


def load_play(name):
    return yaml.safe_load((BRICK / name).read_text())


def tasks_of(play):
    """Every task of the first play, including pre_tasks and role vars."""
    return play[0]


def test_brick_contract(executor, brick):
    assert brick['id'] == 'grafana'
    assert brick['kind'] == 'workload'
    assert brick['executor'] == 'ansible-workload-v1'
    assert brick['execution_provider'] == 'ansible-workload-v1'
    assert brick['data_class'] == 'config'
    assert brick['implementation']['role'] == 'grafana.grafana.grafana'
    assert brick['implementation']['version'] == '6.1.0'
    for action in ('snapshot', 'apply', 'verify', 'rollback', 'remove'):
        assert (BRICK / brick['lifecycle'][action]).is_file()
    declared = {a['id'] for a in brick['verify']['assertions']}
    assert declared == ASSERTIONS


def test_role_lock_matches_the_installed_role(executor, brick):
    installed = executor.assert_role_installed(brick)
    assert installed['version'] == brick['implementation']['version']
    role_dir = (BRICK / 'collections/ansible_collections/grafana/grafana'
                / 'roles/grafana')
    assert role_dir.is_dir(), 'pinned collection must be installed beside the brick'
    checksum = hashlib.sha256()
    for path in sorted(p for p in role_dir.rglob('*') if p.is_file()):
        checksum.update(str(path.relative_to(role_dir)).encode())
        checksum.update(path.read_bytes())
    assert installed['sha256'] == checksum.hexdigest()


def test_prepare_defaults_to_port_3000(executor):
    plan = executor.prepare('grafana', {'target_uuid': UUID, 'ssh_key_id': KEY},
                            TARGET, 'grafana-test-job')
    assert plan['config']['http_port'] == 3000
    assert plan['config']['admin_user'] == 'admin'
    assert plan['config']['grafana_version'] == 'latest'
    assert plan['address'] == '192.168.10.63'
    assert set(plan['assertions']) == ASSERTIONS
    assert len(plan['plan_digest']) == 64


def test_schema_is_bounded(executor, brick):
    schema = brick['schema']
    base = {'target_uuid': UUID, 'ssh_key_id': KEY}
    bad_configs = [
        {**base, 'http_port': 80},          # privileged ports need extra caps
        {**base, 'http_port': 70000},
        {**base, 'http_port': '3000'},
        {**base, 'admin_password': 'x'},    # secrets never enter the config
        {**base, 'grafana_version': 'rm -rf'},
        {**base, 'target_uuid': 'not-a-uuid'},
        {**base, 'ssh_key_id': 'short'},
        {**base, 'extra': True},
    ]
    for config in bad_configs:
        merged = {**brick.get('defaults', {}), **config}
        with pytest.raises(Exception):
            import jsonschema
            jsonschema.validate(merged, schema)
    good = {**base, 'http_port': 3001, 'grafana_version': '12.3.0'}
    import jsonschema
    jsonschema.validate({**brick.get('defaults', {}), **good}, schema)


def test_catalog_discovers_and_fingerprints_the_brick():
    catalog = Catalog(PLUGINS)
    items = catalog.discover()
    assert catalog.errors == []
    assert 'grafana' in items
    assert items['grafana']['category'] == 'monitoring'
    digest = catalog.fingerprint('grafana')
    assert len(digest) == 64  # raises on symlinks inside the brick tree


def test_requirements_pin_the_upstream_collection(brick):
    requirements = yaml.safe_load((BRICK / 'requirements.yml').read_text())
    pins = {entry['name']: entry['version'] for entry in requirements['collections']}
    assert pins['grafana.grafana'] == '==' + brick['implementation']['version']
    # every direct dependency of the pinned collection is pinned too
    for name in ('ansible.posix', 'community.general', 'community.grafana'):
        assert pins[name].startswith('==')


def test_apply_generates_the_admin_secret_on_the_guest_only():
    play = tasks_of(load_play('apply.yml'))
    secret_tasks = [t for t in play.get('pre_tasks', [])
                    if 'password' in t['name'].lower()]
    assert secret_tasks, 'apply must manage the admin password file'
    for task in secret_tasks:
        assert task.get('no_log') is True
    roles = play.get('roles', [])
    assert len(roles) == 1
    role_vars = roles[0]['vars']
    ini = role_vars['grafana_ini']
    assert ini['security']['admin_password'] == '{{ kw_grafana_password }}'
    assert ini['server']['http_port'] == '{{ kw_config.http_port }}'
    # nothing secret may travel in the apply report
    report_task = play['tasks'][-1]
    assert 'kw_grafana_password' not in json.dumps(report_task)
    assert report_task['delegate_to'] == 'localhost'


def test_verify_asserts_every_declared_check():
    play = tasks_of(load_play('verify.yml'))
    assert_task = next(t for t in play['tasks']
                       if 'ansible.builtin.assert' in t)
    for assertion in ASSERTIONS:
        assert any(assertion in line
                   for line in assert_task['ansible.builtin.assert']['that'])
    report_task = play['tasks'][-1]
    assert 'kw_grafana_password' not in json.dumps(report_task)


def test_snapshot_and_rollback_keep_the_config_secret_no_log():
    snap = tasks_of(load_play('snapshot.yml'))
    slurps = [t for t in snap['tasks'] if 'ansible.builtin.slurp' in t]
    assert slurps and all(t.get('no_log') is True for t in slurps)
    # the snapshot carries the previous config (which holds the admin
    # password), so persisting it must not echo content into the log
    persist = snap['tasks'][-1]
    assert persist.get('no_log') is True
    rollback = tasks_of(load_play('rollback.yml'))
    restores = [t for t in rollback['tasks']
                if 'kw_snapshot.config_content' in json.dumps(t)]
    assert restores and all(t.get('no_log') is True for t in restores)