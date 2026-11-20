"""The grafana workload brick: contract, schema and secret hygiene.

The brick is data. These tests prove it is a valid workload brick for the
catalogue and the executor, that its configuration schema is bounded, that the
pinned upstream role is really installed and locked, and that the playbooks
keep the generated admin password and the read-only reporting password out of
plans, reports and logs. The reporting datasource is provisioned as code and
references its password only through Grafana's $__file macro.
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
              'config-present', 'password-protected',
              'reporting-datasource', 'reporting-rows',
              'reporting-readonly', 'reporting-dashboard'}
# A full reporting block: prepare() merges defaults shallowly, so the brick
# declares no nested defaults and the blueprint must carry the whole object.
REPORTING = {'db_host': '127.0.0.1', 'db_port': 15433, 'db_name': 'kuberweaver',
             'db_user': 'kw_dashboard', 'password_file_ref': '/run/kw/dashboard-password'}
BASE = {'target_uuid': UUID, 'ssh_key_id': KEY, 'reporting': REPORTING}


def test_reporting_config_passes_admission_unsafe_fields():
    # Regression: the reporting password is a host-file REFERENCE, not an inline
    # secret. The non-bypassable admission gate (policy.unsafe_fields) must accept
    # the whole grafana config; a *_ref file reference is exempt by design.
    from kuberweaver.policy import unsafe_fields
    assert list(unsafe_fields(BASE)) == []
REPORTING_PASSWORD_GUEST = '/etc/kuberweaver/grafana/kw-dashboard.password'
DASHBOARD_UID = 'kw-reporting'


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
    plan = executor.prepare('grafana', dict(BASE), TARGET, 'grafana-test-job')
    assert plan['config']['http_port'] == 3000
    assert plan['config']['admin_user'] == 'admin'
    assert plan['config']['grafana_version'] == 'latest'
    assert plan['config']['reporting'] == REPORTING
    assert plan['address'] == '192.168.10.63'
    assert set(plan['assertions']) == ASSERTIONS
    assert len(plan['plan_digest']) == 64


def test_reporting_is_required_and_has_no_nested_defaults(executor, brick):
    # Shallow defaults merge would clobber a nested reporting default, so the
    # brick must require the full block from the blueprint instead.
    assert 'reporting' in brick['schema']['required']
    assert 'reporting' not in (brick.get('defaults') or {})
    with pytest.raises(WorkloadError):
        executor.prepare('grafana', {'target_uuid': UUID, 'ssh_key_id': KEY},
                         TARGET, 'grafana-test-job')


def test_schema_is_bounded(executor, brick):
    schema = brick['schema']
    bad_configs = [
        {**BASE, 'http_port': 80},          # privileged ports need extra caps
        {**BASE, 'http_port': 70000},
        {**BASE, 'http_port': '3000'},
        {**BASE, 'admin_password': 'x'},    # secrets never enter the config
        {**BASE, 'grafana_version': 'rm -rf'},
        {**BASE, 'target_uuid': 'not-a-uuid'},
        {**BASE, 'ssh_key_id': 'short'},
        {**BASE, 'extra': True},
    ]
    for config in bad_configs:
        merged = {**brick.get('defaults', {}), **config}
        with pytest.raises(Exception):
            import jsonschema
            jsonschema.validate(merged, schema)
    good = {**BASE, 'http_port': 3001, 'grafana_version': '12.3.0'}
    import jsonschema
    jsonschema.validate({**brick.get('defaults', {}), **good}, schema)


def test_reporting_schema_is_bounded_and_secret_free(brick):
    import jsonschema
    schema = brick['schema']['properties']['reporting']
    assert schema['additionalProperties'] is False
    assert set(schema['required']) == {'db_host', 'db_port', 'db_name',
                                       'db_user', 'password_file_ref'}
    # No inline secret may ever be accepted in the reporting block.
    for secret_field in ('password', 'db_password', 'admin_password', 'secret'):
        assert secret_field not in schema['properties']
    bad_reportings = [
        {**REPORTING, 'db_port': 0},
        {**REPORTING, 'db_port': 70000},
        {**REPORTING, 'db_port': '15433'},
        {**REPORTING, 'db_host': '127.0.0.1; rm -rf'},
        {**REPORTING, 'db_name': 'kuberweaver; drop'},
        {**REPORTING, 'db_user': 'kw_dashboard$$'},
        {**REPORTING, 'password_file_ref': 'relative/path'},
        {**REPORTING, 'password_file_ref': '/etc/passwd\n'},
        {**REPORTING, 'password': 'inline-secret'},
        {k: v for k, v in REPORTING.items() if k != 'db_user'},
    ]
    for reporting in bad_reportings:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(reporting, schema)
    jsonschema.validate(REPORTING, schema)


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


def test_apply_stages_the_reporting_password_without_leaking_it():
    play = tasks_of(load_play('apply.yml'))
    pre = play.get('pre_tasks', [])
    # The host password file is slurped on the controller, never echoed.
    slurps = [t for t in pre if 'ansible.builtin.slurp' in t
              and 'kw_config.reporting.password_file_ref' in json.dumps(t)]
    assert slurps, 'apply must slurp the host reporting password file'
    for task in slurps:
        assert task.get('no_log') is True
        assert task.get('delegate_to') == 'localhost'
    # It lands on the guest grafana-readable, before the pinned role restarts.
    copies = [t for t in pre if 'ansible.builtin.copy' in t
              and REPORTING_PASSWORD_GUEST in json.dumps(t)]
    assert copies, 'apply must copy the reporting password to the guest'
    for task in copies:
        assert task.get('no_log') is True
        assert task['ansible.builtin.copy']['group'] == 'grafana'
    # A guest-side reachability precheck guards the tunnel before any change.
    assert any('ansible.builtin.wait_for' in t
               and 'kw_config.reporting.db_port' in json.dumps(t) for t in pre)
    # The report carries no reporting secret either.
    report_task = play['tasks'][-1]
    assert 'kw_reporting_password' not in json.dumps(report_task)


def test_apply_provisions_the_reporting_datasource_by_file_reference():
    play = tasks_of(load_play('apply.yml'))
    role_vars = play['roles'][0]['vars']
    assert role_vars['grafana_use_provisioning'] is True
    assert role_vars['grafana_dashboards_dir'] == '{{ playbook_dir }}/files/dashboards'
    datasources = role_vars['grafana_datasources']
    assert len(datasources) == 1
    ds = datasources[0]
    assert ds['uid'] == DASHBOARD_UID
    assert ds['type'] == 'postgres'
    assert ds['readOnly'] is True
    assert ds['user'] == '{{ kw_config.reporting.db_user }}'
    assert ds['url'] == '{{ kw_config.reporting.db_host }}:{{ kw_config.reporting.db_port }}'
    assert ds['jsonData']['database'] == '{{ kw_config.reporting.db_name }}'
    # The password is a file reference, never an inline value: the role's
    # datasources copy task is not no_log, so --diff must not see a secret.
    assert ds['secureJsonData']['password'] == '$__file{' + REPORTING_PASSWORD_GUEST + '}'
    assert 'kw_reporting_password' not in json.dumps(role_vars['grafana_datasources'])


def test_dashboard_json_is_provisioned_as_code():
    path = BRICK / 'files' / 'dashboards' / (DASHBOARD_UID + '.json')
    assert path.is_file(), 'the dashboard must ship as a provisioning file'
    assert not path.is_symlink()
    dashboard = json.loads(path.read_text())
    assert dashboard['uid'] == DASHBOARD_UID
    panels = dashboard['panels']
    assert len(panels) >= 6
    # Every panel binds the read-only reporting datasource by uid.
    for panel in panels:
        assert panel['datasource']['uid'] == DASHBOARD_UID
        for target in panel.get('targets', []):
            assert target['datasource']['uid'] == DASHBOARD_UID
            assert target.get('rawSql'), 'each panel queries the reporting views'
    # An environment template variable sourced from the reporting views.
    variables = dashboard.get('templating', {}).get('list', [])
    env = next(v for v in variables if v['name'] == 'environment')
    assert env['type'] == 'query'
    assert env['datasource']['uid'] == DASHBOARD_UID
    assert 'reporting.deployments' in env['query']


def test_verify_asserts_every_declared_check():
    play = tasks_of(load_play('verify.yml'))
    assert_task = next(t for t in play['tasks']
                       if 'ansible.builtin.assert' in t)
    for assertion in ASSERTIONS:
        assert any(assertion in line
                   for line in assert_task['ansible.builtin.assert']['that'])
    report_task = play['tasks'][-1]
    assert 'kw_grafana_password' not in json.dumps(report_task)
    assert 'kw_reporting_password' not in json.dumps(report_task)


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


def test_snapshot_and_rollback_cover_reporting_provisioning():
    snap = tasks_of(load_play('snapshot.yml'))
    snapshot_blob = json.dumps(snap)
    # presence of the reporting password and the provisioning files is captured
    assert REPORTING_PASSWORD_GUEST in snapshot_blob
    assert '/etc/grafana/provisioning/datasources/ansible.yml' in snapshot_blob
    assert '/etc/grafana/provisioning/dashboards/ansible.yml' in snapshot_blob
    rollback = tasks_of(load_play('rollback.yml'))
    rollback_blob = json.dumps(rollback)
    assert REPORTING_PASSWORD_GUEST in rollback_blob
    assert '/etc/grafana/provisioning/datasources/ansible.yml' in rollback_blob
    assert '/etc/grafana/provisioning/dashboards/ansible.yml' in rollback_blob
    # any task that touches the reporting password stays no_log
    for task in rollback['tasks']:
        if REPORTING_PASSWORD_GUEST in json.dumps(task):
            assert task.get('no_log') is True