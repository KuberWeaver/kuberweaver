"""The Zabbix 7 preset: four bricks, one guest, dependency-ordered jobs.

The preset is data. These tests prove that every step is a valid workload brick
for the catalogue and the admission policy the portal uses, that the declared
order is a topological order, and that a broken preset is refused before any
job is submitted.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'scripts' / 'validate_preset.py'
PRESET = ROOT / 'blueprints' / 'zabbix-7.yaml'
PLUGINS = ROOT / 'modules'
UUID = '6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b'
KEY = 'b' * 32


def load_tool():
    spec = importlib.util.spec_from_file_location('kuberweaver_validate_preset', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def tool():
    return load_tool()


@pytest.fixture(scope='module')
def preset(tool):
    return tool.load_preset(PRESET)


@pytest.fixture(scope='module')
def plan(tool, preset):
    return tool.build_plan(preset, plugins_dir=PLUGINS, target_uuid=UUID, ssh_key_id=KEY)


def test_plan_orders_the_four_bricks(plan):
    assert plan['order'] == ['database', 'server', 'web', 'agent']
    assert [step['id'] for step in plan['steps']] == plan['order']
    assert plan['stack']['nodes'] == [
        {'id': 'database', 'brick': 'db-postgresql'},
        {'id': 'server', 'brick': 'zabbix-server'},
        {'id': 'web', 'brick': 'zabbix-web'},
        {'id': 'agent', 'brick': 'zabbix-agent'},
    ]
    assert plan['stack']['edges'] == [
        {'id': 'database--server', 'source': 'database', 'target': 'server'},
        {'id': 'server--web', 'source': 'server', 'target': 'web'},
        {'id': 'server--agent', 'source': 'server', 'target': 'agent'},
    ]


def test_every_step_is_one_brick_and_one_guest(plan):
    assert plan['target'] == {'uuid': UUID, 'ssh_key_id': KEY, 'count': 1}
    for step in plan['steps']:
        graph = step['graph']
        assert len(graph['nodes']) == 1
        assert graph['edges'] == []
        assert graph['failure_mode'] == 'none'
        node = graph['nodes'][0]
        assert node['id'] == step['id']
        assert node['plugin_id'] == step['brick']
        assert node['config']['target_uuid'] == UUID
        assert node['config']['ssh_key_id'] == KEY


def test_every_step_passes_the_portal_admission_alone(tool, plan):
    from kuberweaver.catalog import Catalog
    from kuberweaver.policy import Admission
    admission = Admission(Catalog(PLUGINS), tool._DefaultSettings())
    for step in plan['steps']:
        report = admission.validate(step['graph'])
        assert report['allowed'] is True, (step['id'], report['violations'])
        assert report['violations'] == []
        assert report['phases'][0]['nodes'] == [step['id']]


def test_every_brick_is_a_pinned_live_workload(plan):
    from kuberweaver.workload_execution import WorkloadExecutor
    executor = WorkloadExecutor(PLUGINS / 'unused', PLUGINS / 'services')
    for step in plan['steps']:
        brick = executor.brick(step['brick'])
        assert brick['kind'] == 'workload'
        assert brick['capability'] == 'live'
        assert brick['executor'] == executor.executor_id
        assert brick['execution_provider'] == executor.executor_id
        assert brick['data_class'] in {'config', 'state'}
        assert brick['verify']['assertions']
        lock = json.loads((PLUGINS / 'services' / step['brick'] / 'roles.lock.json').read_text())
        entry = lock[brick['implementation']['role']]
        assert entry['version'] == brick['implementation']['version']
        for action in ('snapshot', 'apply', 'verify', 'rollback'):
            assert (PLUGINS / 'services' / step['brick'] / brick['lifecycle'][action]).is_file()


def test_a_bad_uuid_or_key_is_refused(tool, preset):
    with pytest.raises(tool.PresetError, match='target_uuid'):
        tool.build_plan(preset, plugins_dir=PLUGINS, target_uuid='not-a-uuid', ssh_key_id=KEY)
    with pytest.raises(tool.PresetError, match='ssh_key_id'):
        tool.build_plan(preset, plugins_dir=PLUGINS, target_uuid=UUID, ssh_key_id='short')


def test_an_unknown_brick_is_refused(tool, preset):
    broken = copy.deepcopy(preset)
    broken['steps'][0]['brick'] = 'missing-brick'
    with pytest.raises(tool.PresetError, match='Unknown brick'):
        tool.build_plan(broken, plugins_dir=PLUGINS, target_uuid=UUID, ssh_key_id=KEY)


def test_a_dependency_declared_after_its_consumer_is_refused(tool, preset):
    broken = copy.deepcopy(preset)
    broken['steps'] = [broken['steps'][1], broken['steps'][0], broken['steps'][2], broken['steps'][3]]
    with pytest.raises(tool.PresetError, match='not in dependency order'):
        tool.build_plan(broken, plugins_dir=PLUGINS, target_uuid=UUID, ssh_key_id=KEY)


def test_a_cycle_is_refused(tool, preset):
    broken = copy.deepcopy(preset)
    broken['steps'][0]['requires'] = ['agent']
    with pytest.raises(tool.PresetError, match='cycle'):
        tool.build_plan(broken, plugins_dir=PLUGINS, target_uuid=UUID, ssh_key_id=KEY)


def test_an_unknown_dependency_is_refused(tool, preset):
    broken = copy.deepcopy(preset)
    broken['steps'][1]['requires'] = ['missing']
    with pytest.raises(tool.PresetError, match='unknown step'):
        tool.build_plan(broken, plugins_dir=PLUGINS, target_uuid=UUID, ssh_key_id=KEY)


def test_a_step_config_that_violates_its_brick_is_refused(tool, preset):
    broken = copy.deepcopy(preset)
    broken['steps'][2]['config']['listen_port'] = 70000
    with pytest.raises(tool.PresetError, match='violates'):
        tool.build_plan(broken, plugins_dir=PLUGINS, target_uuid=UUID, ssh_key_id=KEY)


def test_a_preset_must_not_carry_the_portal_inputs(tool, tmp_path):
    preset = tool.load_preset(PRESET)
    preset['steps'][0]['config']['target_uuid'] = UUID
    path = tmp_path / 'leaky.yaml'
    path.write_text(yaml.safe_dump(preset, sort_keys=False))
    with pytest.raises(tool.PresetError, match='portal inputs'):
        tool.load_preset(path)


def test_a_missing_role_lock_is_refused(tool, tmp_path):
    source = PLUGINS / 'services' / 'zabbix-agent'
    target = tmp_path / 'plugins' / 'zabbix-agent'
    target.mkdir(parents=True)
    (target / 'manifest.yaml').write_text((source / 'manifest.yaml').read_text())
    for name in ('snapshot.yml', 'apply.yml', 'verify.yml', 'rollback.yml', 'remove.yml'):
        (target / name).write_text((source / name).read_text())
    with pytest.raises(tool.PresetError, match='roles.lock.json'):
        tool._brick_contract(tmp_path / 'plugins', 'zabbix-agent')


def test_the_cli_prints_the_plan_and_refuses_a_broken_preset(tmp_path):
    good = subprocess.run([sys.executable, str(SCRIPT), str(PRESET),
                           '--uuid', UUID, '--ssh-key', KEY],
                          capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    body = json.loads(good.stdout)
    assert body['order'] == ['database', 'server', 'web', 'agent']
    assert len(body['steps']) == 4

    broken = tmp_path / 'broken.yaml'
    broken.write_text(PRESET.read_text().replace('brick: zabbix-agent', 'brick: missing-brick'))
    bad = subprocess.run([sys.executable, str(SCRIPT), str(broken),
                          '--uuid', UUID, '--ssh-key', KEY],
                         capture_output=True, text=True)
    assert bad.returncode == 1
    assert bad.stderr.startswith('error:')
    assert 'missing-brick' in bad.stderr
