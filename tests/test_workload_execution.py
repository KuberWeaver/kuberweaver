"""Brick contract tests: no shell, pinned role, declared verification."""
import json
from pathlib import Path

import pytest
import yaml

from kuberweaver.workload_execution import WorkloadError, WorkloadExecutor


MANIFEST = {
    'id': 'web-nginx',
    'name': 'NGINX on a guest',
    'version': '1.0.0',
    'kind': 'workload',
    'capability': 'live',
    'engine': 'ansible',
    'executor': 'ansible-workload-v1',
    'requires': 'compute.vm',
    'data_class': 'config',
    'implementation': {'role': 'geerlingguy.nginx', 'version': '3.3.1', 'license': 'MIT'},
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'required': ['server_name'],
        'properties': {
            'server_name': {'type': 'string', 'pattern': '^[a-z0-9]([a-z0-9.-]{0,251})$'},
            'listen_port': {'type': 'integer', 'minimum': 1, 'maximum': 65535, 'default': 80},
            'document_root': {'type': 'string', 'pattern': '^/[A-Za-z0-9._/-]{1,200}$',
                              'default': '/var/www/kuberweaver'},
        },
    },
    'defaults': {'listen_port': 80, 'document_root': '/var/www/kuberweaver'},
    'lifecycle': {
        'snapshot': 'iac/snapshot.yml',
        'apply': 'iac/apply.yml',
        'verify': 'iac/verify.yml',
        'rollback': 'iac/rollback.yml',
        'remove': 'iac/remove.yml',
    },
    'verify': {'assertions': [{'id': 'service-active'}, {'id': 'http-200'}]},
}

TARGET = {'uuid': '564df7bb-3ddf-7a1e-5a9f-f5fc1cb31d7a', 'address': '192.168.10.62'}


def make_plugins(tmp_path, manifest=None, lock=True):
    plugins = tmp_path / 'plugins'
    brick = plugins / 'web-nginx'
    (brick / 'iac').mkdir(parents=True)
    (brick / 'manifest.yaml').write_text(yaml.safe_dump(manifest or MANIFEST, sort_keys=False))
    for name in ('snapshot', 'apply', 'verify', 'rollback', 'remove'):
        (brick / 'iac' / f'{name}.yml').write_text('---\n- hosts: all\n  tasks: []\n')
    if lock:
        (brick / 'roles.lock.json').write_text(json.dumps(
            {'geerlingguy.nginx': {'version': '3.3.1', 'sha256': 'a' * 64, 'license': 'MIT'}}))
    return plugins


def executor(tmp_path, **kwargs):
    return WorkloadExecutor(tmp_path / 'state', make_plugins(tmp_path),
                            playbook=kwargs.pop('playbook', '/bin/true'),
                            key_path=kwargs.pop('key_path', None), **kwargs)


def test_brick_manifest_must_declare_verification(tmp_path):
    manifest = json.loads(json.dumps(MANIFEST))
    manifest['verify'] = {'assertions': []}
    ex = WorkloadExecutor(tmp_path / 'state', make_plugins(tmp_path, manifest))
    with pytest.raises(WorkloadError, match='no verifiable assertion'):
        ex.brick('web-nginx')


def test_brick_must_pin_its_upstream_role(tmp_path):
    manifest = json.loads(json.dumps(MANIFEST))
    manifest['implementation'] = {'role': 'geerlingguy.nginx'}
    ex = WorkloadExecutor(tmp_path / 'state', make_plugins(tmp_path, manifest))
    with pytest.raises(WorkloadError, match='does not pin'):
        ex.brick('web-nginx')


def test_brick_playbook_cannot_escape_its_directory(tmp_path):
    manifest = json.loads(json.dumps(MANIFEST))
    manifest['lifecycle']['apply'] = '../../../etc/passwd'
    ex = WorkloadExecutor(tmp_path / 'state', make_plugins(tmp_path, manifest))
    with pytest.raises(WorkloadError, match='escapes its directory'):
        ex.brick('web-nginx')


def test_brick_must_declare_its_data_class(tmp_path):
    manifest = json.loads(json.dumps(MANIFEST))
    manifest.pop('data_class')
    ex = WorkloadExecutor(tmp_path / 'state', make_plugins(tmp_path, manifest))
    with pytest.raises(WorkloadError, match='data class'):
        ex.brick('web-nginx')


def test_unknown_brick_is_refused(tmp_path):
    ex = executor(tmp_path)
    with pytest.raises(WorkloadError, match='Unknown brick'):
        ex.brick('nope')


def test_role_version_drift_blocks_execution(tmp_path):
    ex = executor(tmp_path)
    lock = ex.plugins_dir / 'web-nginx' / 'roles.lock.json'
    lock.write_text(json.dumps({'geerlingguy.nginx': {'version': '2.0.0'}}))
    with pytest.raises(WorkloadError, match='does not match the pinned version'):
        ex.prepare('web-nginx', {'server_name': 'app.lab'}, TARGET, 'job-1')


def test_missing_role_installation_is_refused(tmp_path):
    ex = WorkloadExecutor(tmp_path / 'state', make_plugins(tmp_path, lock=False))
    with pytest.raises(WorkloadError, match='not installed'):
        ex.prepare('web-nginx', {'server_name': 'app.lab'}, TARGET, 'job-1')


def test_prepare_merges_defaults_and_is_deterministic(tmp_path):
    ex = executor(tmp_path)
    first = ex.prepare('web-nginx', {'server_name': 'app.lab'}, TARGET, 'job-1')
    second = ex.prepare('web-nginx', {'server_name': 'app.lab'}, TARGET, 'job-1')
    assert first['config'] == {'server_name': 'app.lab', 'listen_port': 80,
                               'document_root': '/var/www/kuberweaver'}
    assert first['plan_digest'] == second['plan_digest']
    assert first['role_version'] == '3.3.1'
    assert first['assertions'] == ['service-active', 'http-200']
    assert 'private' not in json.dumps(first).lower()


def test_config_rejects_undeclared_keys_and_bad_bounds(tmp_path):
    ex = executor(tmp_path)
    with pytest.raises(WorkloadError, match='Invalid brick configuration'):
        ex.prepare('web-nginx', {'server_name': 'app.lab', 'command': 'reboot'}, TARGET, 'job-1')
    with pytest.raises(WorkloadError, match='Invalid brick configuration'):
        ex.prepare('web-nginx', {'server_name': 'app.lab', 'listen_port': 70000}, TARGET, 'job-1')


def test_target_must_be_a_resolved_guest(tmp_path):
    ex = executor(tmp_path)
    with pytest.raises(WorkloadError, match='not a VM UUID'):
        ex.prepare('web-nginx', {'server_name': 'app.lab'}, {'uuid': 'x', 'address': '1.2.3.4'}, 'job-1')
    with pytest.raises(WorkloadError, match='not an IP address'):
        ex.prepare('web-nginx', {'server_name': 'app.lab'},
                   {'uuid': TARGET['uuid'], 'address': 'app.lab'}, 'job-1')


def test_plan_digest_changes_with_the_configuration(tmp_path):
    ex = executor(tmp_path)
    base = ex.prepare('web-nginx', {'server_name': 'app.lab'}, TARGET, 'job-1')
    other = ex.prepare('web-nginx', {'server_name': 'app.lab', 'listen_port': 8080}, TARGET, 'job-1')
    assert base['plan_digest'] != other['plan_digest']


def stub_runs(ex, mapping):
    calls = []

    def run(brick, action, folder, plan, extra=None):
        calls.append(action)
        return mapping[action]

    ex._run = run
    return calls


def test_execute_applies_then_verifies(tmp_path):
    ex = executor(tmp_path)
    calls = stub_runs(ex, {
        'snapshot': {'returncode': 0, 'report': {'package_installed': False}},
        'apply': {'returncode': 0, 'report': None, 'stdout_tail': 'ok=4 changed=3'},
        'verify': {'returncode': 0, 'report': {'assertions': {'service-active': True, 'http-200': True}}},
    })
    evidence = ex.execute('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)
    assert evidence['status'] == 'succeeded'
    assert evidence['assertions'] == {'service-active': True, 'http-200': True}
    assert evidence['changed'] == 3
    assert calls == ['snapshot', 'apply', 'verify']
    stored = json.loads((tmp_path / 'state' / 'workloads' / 'job-1' / 'evidence.json').read_text())
    assert stored['plan_digest'] == evidence['plan_digest']


def test_execute_rolls_back_when_apply_fails(tmp_path):
    ex = executor(tmp_path)
    calls = stub_runs(ex, {
        'snapshot': {'returncode': 0, 'report': {'package_installed': True}},
        'apply': {'returncode': 2, 'report': None, 'stderr_tail': 'boom'},
        'rollback': {'returncode': 0, 'report': {'restored_site': True}},
    })
    with pytest.raises(WorkloadError, match='apply failed; rollback completed'):
        ex.execute('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)
    assert calls == ['snapshot', 'apply', 'rollback']
    stored = json.loads((tmp_path / 'state' / 'workloads' / 'job-1' / 'evidence.json').read_text())
    assert stored['status'] == 'failed'


def test_execute_reports_a_failed_rollback_as_its_own_state(tmp_path):
    ex = executor(tmp_path)
    stub_runs(ex, {
        'snapshot': {'returncode': 0, 'report': {}},
        'apply': {'returncode': 2, 'report': None, 'stderr_tail': 'boom'},
        'rollback': {'returncode': 3, 'report': None},
    })
    with pytest.raises(WorkloadError, match='rollback did not succeed'):
        ex.execute('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)
    stored = json.loads((tmp_path / 'state' / 'workloads' / 'job-1' / 'evidence.json').read_text())
    assert stored['status'] == 'rollback_failed'


def test_verification_must_honour_declared_assertions(tmp_path):
    ex = executor(tmp_path)
    stub_runs(ex, {
        'snapshot': {'returncode': 0, 'report': {}},
        'apply': {'returncode': 0, 'report': None, 'stdout_tail': ''},
        'verify': {'returncode': 0, 'report': {'assertions': {'service-active': True}}},
        'rollback': {'returncode': 0, 'report': {}},
    })
    with pytest.raises(WorkloadError, match='does not honour the declared assertions'):
        ex.execute('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)


def test_a_failed_verification_triggers_rollback(tmp_path):
    ex = executor(tmp_path)
    calls = stub_runs(ex, {
        'snapshot': {'returncode': 0, 'report': {}},
        'apply': {'returncode': 0, 'report': None, 'stdout_tail': ''},
        'verify': {'returncode': 2, 'report': None, 'stderr_tail': 'probe failed'},
        'rollback': {'returncode': 0, 'report': {}},
    })
    with pytest.raises(WorkloadError, match='verification failed; rollback completed'):
        ex.execute('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)
    assert calls == ['snapshot', 'apply', 'verify', 'rollback']


def test_a_snapshot_failure_changes_nothing(tmp_path):
    ex = executor(tmp_path)
    calls = stub_runs(ex, {'snapshot': {'returncode': 1, 'report': None}})
    with pytest.raises(WorkloadError, match='Snapshot failed'):
        ex.execute('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)
    assert calls == ['snapshot']


def test_remove_reports_what_it_removed(tmp_path):
    ex = executor(tmp_path)
    stub_runs(ex, {'remove': {'returncode': 0, 'report': {'package_kept': True}}})
    evidence = ex.remove('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)
    assert evidence['status'] == 'removed'
    assert evidence['report']['package_kept'] is True


def test_a_job_cannot_be_executed_twice(tmp_path):
    ex = executor(tmp_path)
    stub_runs(ex, {
        'snapshot': {'returncode': 0, 'report': {}},
        'apply': {'returncode': 0, 'report': None, 'stdout_tail': ''},
        'verify': {'returncode': 0, 'report': {'assertions': {'service-active': True, 'http-200': True}}},
    })
    ex.execute('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)
    with pytest.raises(WorkloadError, match='already executed'):
        ex.execute('web-nginx', 'job-1', {'server_name': 'app.lab'}, TARGET)


def test_the_real_reference_brick_satisfies_the_contract():
    """The shipped brick, not a fixture, must honour the same rules."""
    root = Path(__file__).resolve().parents[1]
    ex = WorkloadExecutor(root / 'state-probe', root / 'modules')
    brick = ex.brick('web-nginx')
    assert brick['kind'] == 'workload'
    assert brick['executor'] == 'ansible-workload-v1'
    assert brick['implementation']['version'] == '3.3.1'
    assert brick['implementation']['license'] == 'MIT'
    assert brick['data_class'] == 'config'
    assert brick['lifecycle']['remove'] == 'iac/remove.yml'
    assert [a['id'] for a in brick['verify']['assertions']] == [
        'service-active', 'config-valid', 'http-200', 'brick-header']
