"""Safety tests exercise real orchestration with an injected command boundary."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_executor():
    path = ROOT / 'backend/kuberweaver/execution.py'
    assert path.exists(), 'The manifest-driven Executor is not implemented'
    spec = importlib.util.spec_from_file_location('execution', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Executor


def test_unknown_plugin_rejected_before_commands(tmp_path):
    executor = load_executor()(tmp_path)
    with pytest.raises(ValueError, match='plugin'):
        executor.execute({'id': 'test-job', 'graph': {'nodes': [
            {'id': 'web', 'plugin_id': 'unknown', 'config': {}}
        ]}}, lambda *_: None)


class SimulatedAnsible:
    """Deterministic command boundary, not live deployment evidence."""
    def __init__(self, fail_restore=False):
        self.calls = []
        self.services = {('app', 'web'): {'version': '1.0.0'}, ('branch-client', 'web'): {'version': '1.0.0'}}
        self.fail_restore = fail_restore

    def __call__(self, argv, **kwargs):
        import json
        from types import SimpleNamespace
        params = json.loads(Path(argv[argv.index('--extra-vars') + 1][1:]).read_text())
        request = params['kw_request']
        action, target, node = request['action'], request['target'], request['node_id']
        self.calls.append((action, target, node))
        key = (target, node)
        result = {}
        rc = 0
        if action == 'snapshot':
            result = {'exists': key in self.services, 'previous': self.services.get(key)}
        elif action == 'apply':
            self.services[key] = {'version': request['config']['version']}
            result = {'applied': True}
        elif action == 'verify':
            if request.get('inject_failure'):
                rc, result = 1, {'error': 'intentional healthcheck failure'}
            else:
                result = {'healthy': True, 'payload': self.services.get(key)}
        elif action == 'restore':
            if self.fail_restore:
                rc, result = 1, {'error': 'restore refused'}
            elif request['snapshot']['exists']:
                self.services[key] = request['snapshot']['previous']
                result = {'restored': True, 'payload': self.services[key]}
            else:
                self.services.pop(key, None)
                result = {'restored': True, 'absent': True}
        Path(params['kw_result_path']).write_text(json.dumps(result))
        return SimpleNamespace(returncode=rc, stdout='simulated ansible', stderr='')


def graph(version='2.0.0', failure='none'):
    return {'name': 'test-lab', 'environment': 'test', 'nodes': [
        {'id': 'web', 'plugin_id': 'nginx', 'config': {'version': version}}
    ], 'edges': [], 'strategy': {'canary': 1, 'batch_size': 1}, 'failure_mode': failure}


def test_rolling_failure_restores_successful_canary_and_failed_target(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    result = executor.execute({'id': 'rolling-failure', 'graph': graph(failure='rolling')}, lambda *_: None)
    assert result['status'] == 'failed'
    assert result['rollback']['status'] == 'rolled_back'
    assert [x[1] for x in runner.calls if x[0] == 'restore'] == ['branch-client', 'app']
    assert all(v['version'] == '1.0.0' for v in runner.services.values())
    assert len(result['snapshots']) == 2
    assert all(Path(s['path']).stat().st_mode & 0o777 == 0o600 for s in result['snapshots'])


def test_retry_partial_rollback_does_not_reject_already_restored_hosts(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    baseline = {'id': 'base', 'graph': graph('1.0.0')}
    assert executor.execute(baseline, lambda *_: None)['status'] == 'succeeded'
    update = {'id': 'update', 'graph': graph('2.0.0')}
    assert executor.execute(update, lambda *_: None)['status'] == 'succeeded'
    original = runner.__call__

    def fail_branch_restore(argv, **kwargs):
        import json
        from types import SimpleNamespace
        params = json.loads(Path(argv[argv.index('--extra-vars') + 1][1:]).read_text())
        if params['kw_request']['action'] == 'restore' and params['kw_request']['target'] == 'branch-client':
            Path(params['kw_result_path']).write_text(json.dumps({'error': 'transient restore failure'}))
            return SimpleNamespace(returncode=1, stdout='', stderr='')
        return original(argv, **kwargs)

    executor.runner = fail_branch_restore
    assert executor.rollback(update, lambda *_: None)['status'] == 'rollback_failed'
    executor.runner = runner
    retry = executor.rollback(update, lambda *_: None)
    assert retry['status'] == 'rolled_back'
    assert all(x['version'] == '1.0.0' for x in runner.services.values())


@pytest.mark.parametrize('plugin', ['dev-stack', 'cisco-ios', 'pfsense', 'cloud-vm', '../nginx'])
def test_planned_and_path_plugins_never_run_commands(tmp_path, plugin):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    config = graph()
    config['nodes'][0]['plugin_id'] = plugin
    with pytest.raises(ValueError):
        executor.execute({'id': 'denied', 'graph': config}, lambda *_: None)
    assert runner.calls == []


@pytest.mark.parametrize('config', [
    {'targets': ['operations']}, {'targets': ['app;id']}, {'port': 8080},
    {'port': 18110}, {'command': 'id'}, {'version': '1;id'},
    {'public_cidr': '0.0.0.0/0'}, {'targets': ['app', 'app']}, {'cpu': 100}, {'cpu': float('nan')},
])
def test_invalid_config_never_runs_commands(tmp_path, config):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    candidate = graph()
    candidate['nodes'][0]['config'].update(config)
    with pytest.raises(ValueError):
        executor.execute({'id': 'denied', 'graph': candidate}, lambda *_: None)
    assert runner.calls == []


def test_initial_absence_restored_after_canary_failure(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    runner.services.clear()
    executor.runner = runner
    result = executor.execute({'id': 'absence', 'graph': graph(failure='canary')}, lambda *_: None)
    assert result['status'] == 'failed'
    assert result['rollback']['evidence'][0]['absent']
    assert not runner.services
    assert not [x for x in runner.calls if x[0] == 'apply' and x[1] == 'branch-client']


def test_restore_failure_is_not_claimed_restored_and_attempts_all_hosts(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible(fail_restore=True)
    executor.runner = runner
    result = executor.execute({'id': 'rollback-failure', 'graph': graph(failure='rolling')}, lambda *_: None)
    assert result['status'] == result['rollback']['status'] == 'rollback_failed'
    assert len(result['rollback']['errors']) == 2
    assert [x[1] for x in runner.calls if x[0] == 'restore'] == ['branch-client', 'app']


def test_manual_rollback_reads_persisted_snapshot(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    job = {'id': 'update', 'graph': graph()}
    assert executor.execute(job, lambda *_: None)['status'] == 'succeeded'
    reloaded = load_executor()(tmp_path)
    reloaded.runner = runner
    result = reloaded.rollback({'id': 'update'}, lambda *_: None)
    assert result['status'] == 'rolled_back'
    assert all(value['version'] == '1.0.0' for value in runner.services.values())


def test_stale_manual_rollback_rejected_without_mutation(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    executor.execute({'id': 'older', 'graph': graph()}, lambda *_: None)
    executor.execute({'id': 'newer', 'graph': graph('3.0.0')}, lambda *_: None)
    calls = len(runner.calls)
    with pytest.raises(ValueError, match='Stale'):
        executor.rollback({'id': 'older'}, lambda *_: None)
    assert len(runner.calls) == calls
    assert all(value['version'] == '3.0.0' for value in runner.services.values())


def test_ownership_blocks_other_graph_on_port_or_node(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    executor.execute({'id': 'first', 'graph': graph()}, lambda *_: None)
    calls = len(runner.calls)
    other = graph()
    other['name'] = 'different-owner'
    with pytest.raises(ValueError, match='ownership'):
        executor.execute({'id': 'second', 'graph': other}, lambda *_: None)
    other['nodes'][0]['id'] = 'different-node'
    with pytest.raises(ValueError, match='ownership'):
        executor.execute({'id': 'third', 'graph': other}, lambda *_: None)
    assert len(runner.calls) == calls


def test_dependency_order_and_reverse_complete_restore(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    candidate = graph(failure='none')
    candidate['nodes'].insert(0, {'id': 'frontend', 'plugin_id': 'apache', 'config': {'port': 18091}})
    candidate['edges'] = [{'id': 'dependency', 'source': 'web', 'target': 'frontend'}]
    job = {'id': 'dag', 'graph': candidate}
    assert executor.execute(job, lambda *_: None)['status'] == 'succeeded'
    assert [x[2] for x in runner.calls if x[0] == 'apply'] == ['web', 'web', 'frontend', 'frontend']
    assert [x[0] for x in runner.calls[:4]] == ['snapshot'] * 4
    assert executor.rollback(job, lambda *_: None)['status'] == 'rolled_back'
    assert [(x[2], x[1]) for x in runner.calls if x[0] == 'restore'] == [
        ('frontend', 'branch-client'), ('frontend', 'app'), ('web', 'branch-client'), ('web', 'app')]


def test_cycle_and_duplicate_port_rejected_before_commands(tmp_path):
    executor = load_executor()(tmp_path)
    runner = SimulatedAnsible()
    executor.runner = runner
    candidate = graph()
    candidate['nodes'].append({'id': 'second', 'plugin_id': 'apache', 'config': {'port': 18091}})
    candidate['edges'] = [{'id': 'a', 'source': 'web', 'target': 'second'}, {'id': 'b', 'source': 'second', 'target': 'web'}]
    with pytest.raises(ValueError, match='cycle'):
        executor.execute({'id': 'cycle', 'graph': candidate}, lambda *_: None)
    candidate['edges'] = []
    candidate['nodes'][1]['config']['port'] = 18090
    with pytest.raises(ValueError, match='same target/port'):
        executor.execute({'id': 'duplicate', 'graph': candidate}, lambda *_: None)
    assert runner.calls == []


def test_new_manifest_plugin_uses_generic_lifecycle_without_kernel_edit(tmp_path):
    import shutil
    import yaml
    plugins = tmp_path / 'plugins'
    shutil.copytree(ROOT / 'plugins/nginx', plugins / 'custom-web')
    manifest_path = plugins / 'custom-web/manifest.yaml'
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest['id'] = 'custom-web'
    manifest_path.write_text(yaml.safe_dump(manifest))
    executor = load_executor()(tmp_path / 'state', plugins)
    runner = SimulatedAnsible()
    executor.runner = runner
    candidate = graph()
    candidate['nodes'][0]['plugin_id'] = 'custom-web'
    result = executor.execute({'id': 'new-plugin', 'graph': candidate}, lambda *_: None)
    assert result['status'] == 'succeeded'
    assert [x[0] for x in runner.calls] == ['snapshot', 'snapshot', 'apply', 'verify', 'apply', 'verify']


def test_remote_restore_verifies_actual_container_environment(tmp_path, monkeypatch):
    import copy
    import json
    spec = importlib.util.spec_from_file_location('guest_helper', ROOT / 'plugins/_shared/guest.py')
    assert spec and spec.loader
    guest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guest)
    state = {'Image': 'sha256:old', 'State': {'Running': False}, 'HostConfig': {},
             'Config': {'Labels': {'kw.managed': 'true', 'kw.owner': 'owner'}, 'Env': ['VERSION=1']}}
    actual = copy.deepcopy(state)
    actual['Config']['Env'] = ['VERSION=2']
    inspection = iter([None, actual])
    monkeypatch.setattr(guest, 'ROOT', tmp_path / 'services')
    monkeypatch.setattr(guest, 'inspect', lambda _: next(inspection))
    monkeypatch.setattr(guest, 'recreate', lambda *_: None)
    old = {'exists': True, 'config_exists': False, 'inspect': state, 'files': None, 'running': False}
    request = {'node_id': 'web', 'owner': 'owner', 'snapshot': old}
    with pytest.raises(RuntimeError, match='configuration differs'):
        guest.restore(request)


def test_manifest_schema_references_rejected(tmp_path):
    import shutil
    import yaml
    plugins = tmp_path / 'plugins'
    shutil.copytree(ROOT / 'plugins/nginx', plugins / 'nginx')
    path = plugins / 'nginx/manifest.yaml'
    manifest = yaml.safe_load(path.read_text())
    manifest['schema'] = {'$ref': 'file:///must-not-be-read'}
    path.write_text(yaml.safe_dump(manifest))
    executor = load_executor()(tmp_path / 'state', plugins)
    with pytest.raises(ValueError, match='references'):
        executor._plugin('nginx')

