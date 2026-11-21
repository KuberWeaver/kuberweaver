"""Approval integrity through the real queue, with copied sources and no lab calls."""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_core_jobs import login, wait_job
from test_execution import SimulatedAnsible, graph

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def queued_api(tmp_path, monkeypatch):
    from kuberweaver.app import create_app

    plugins = tmp_path / 'plugins'
    shutil.copytree(ROOT / 'plugins', plugins)
    source = tmp_path / 'implementation' / 'execution.py'
    source.parent.mkdir()
    shutil.copyfile(ROOT / 'backend/kuberweaver/execution.py', source)
    name = 'isolated_approved_execution'
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    ssh_config = tmp_path / 'ssh_config'
    ssh_config.write_text('Host infra-app infra-branch-client\n  StrictHostKeyChecking yes\n')
    setattr(module, 'SSH_CONFIG', ssh_config)
    executor = module.Executor(tmp_path / 'state', plugins)
    runner = SimulatedAnsible()
    executor.runner = runner
    app = create_app(state_dir=tmp_path / 'state', plugins_dir=plugins,
                     executor=executor, testing=True)
    with TestClient(app) as client:
        login(client, app.state.state_dir, 'admin')
        app.state.worker.pause.clear()
        yield client, app, runner, source


def approve_queued(client):
    candidate = graph()
    candidate['environment'] = 'dev'
    response = client.post('/api/jobs', json={'graph': candidate})
    assert response.status_code == 201, response.text
    identity = response.json()['id']
    response = client.post(f'/api/jobs/{identity}/approve')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'queued'
    return identity


@pytest.mark.parametrize('dependency', ['guest.py', 'container.yml'])
def test_queued_shared_dependency_drift_rejected_before_runner(queued_api, dependency):
    client, app, runner, _ = queued_api
    identity = approve_queued(client)
    path = app.state.plugins_dir / '_shared' / dependency
    path.write_text(path.read_text() + '\n# Unapproved shared dependency change\n')
    app.state.worker.pause.set()
    result = wait_job(client, identity)
    assert result['status'] == 'failed', result
    assert runner.calls == []
    assert result['result'] is None
    assert not (app.state.state_dir / 'jobs' / identity / 'journal.json').exists()


def test_queued_default_executor_implementation_drift_rejected_before_runner(queued_api):
    client, app, runner, source = queued_api
    identity = approve_queued(client)
    source.write_text(source.read_text() + '\n# Unapproved actual executor change\n')
    app.state.worker.pause.set()
    result = wait_job(client, identity)
    assert result['status'] == 'failed', result
    assert runner.calls == []
    assert result['result'] is None
    report = client.get('/api/providers').json()
    assert report['active']['execution']['available'] is False


@pytest.mark.parametrize('relative', [False, True])
def test_generic_catalog_pins_shared_tree_with_canonical_root(tmp_path, monkeypatch, relative):
    from kuberweaver.catalog import Catalog

    plugins = tmp_path / 'plugins'
    service = plugins / 'arbitrary-blueprint'
    service.mkdir(parents=True)
    (service / 'lifecycle.yml').write_text('reviewed lifecycle')
    shared = plugins / '_shared'
    shared.mkdir()
    helper = shared / 'arbitrary-helper.py'
    helper.write_text('# reviewed helper\n')
    monkeypatch.chdir(tmp_path)
    catalog = Catalog(Path('plugins') if relative else plugins)
    before = catalog.fingerprint('arbitrary-blueprint')
    helper.write_text('# changed helper\n')
    assert catalog.fingerprint('arbitrary-blueprint') != before


@pytest.mark.parametrize('change', ['cache', 'unrelated'])
def test_queued_non_dependencies_do_not_invalidate_approval(queued_api, change):
    client, app, runner, source = queued_api
    identity = approve_queued(client)
    if change == 'cache':
        for directory in ('nginx', '_shared'):
            cache = app.state.plugins_dir / directory / '__pycache__'
            cache.mkdir(exist_ok=True)
            (cache / 'generated.pyc').write_bytes(b'generated cache, not reviewed source')
            (app.state.plugins_dir / directory / 'generated.pyc').write_bytes(b'cache')
    else:
        path = app.state.plugins_dir / 'apache' / 'manifest.yaml'
        path.write_text(path.read_text() + '\n# Unselected blueprint\n')
        (source.parent / 'unrelated.py').write_text('# Not an execution dependency\n')
    app.state.worker.pause.set()
    result = wait_job(client, identity)
    assert result['status'] == 'succeeded', result
    assert len(runner.calls) == 6


@pytest.mark.parametrize('dependency', ['_shared', '_shared/guest.py', 'nginx', 'nginx/iac'])
@pytest.mark.parametrize('external', [False, True])
def test_queued_dependency_symlinks_rejected_even_with_identical_bytes(queued_api, dependency, external):
    client, app, runner, source = queued_api
    identity = approve_queued(client)
    path = app.state.plugins_dir / dependency
    target = (source.parent if external else app.state.plugins_dir) / 'linked-source'
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    app.state.worker.pause.set()
    assert wait_job(client, identity)['status'] == 'failed'
    assert runner.calls == []


@pytest.mark.parametrize('dependency', ['guest.py', 'container.yml'])
def test_shared_source_drift_also_blocks_queued_rollback(queued_api, dependency):
    client, app, runner, _ = queued_api
    identity = approve_queued(client)
    app.state.worker.pause.set()
    assert wait_job(client, identity)['status'] == 'succeeded'
    calls = list(runner.calls)
    app.state.worker.pause.clear()
    assert client.post(f'/api/jobs/{identity}/rollback').status_code == 200
    path = app.state.plugins_dir / '_shared' / dependency
    path.write_text(path.read_text() + '\n# Unapproved restore implementation\n')
    app.state.worker.pause.set()
    assert wait_job(client, identity)['status'] == 'rollback_failed'
    assert runner.calls == calls


@pytest.mark.parametrize('dependency', ['guest.py', 'container.yml', 'execution.py'])
@pytest.mark.parametrize('timing', ['job_started', 'after_apply'])
def test_default_executor_rechecks_sources_at_each_command_boundary(queued_api, dependency, timing):
    client, app, runner, source = queued_api
    path = source if dependency == 'execution.py' else app.state.plugins_dir / '_shared' / dependency

    def drift(*_):
        path.write_text(path.read_text() + '\n# Drift after worker preflight\n')

    if timing == 'job_started':
        app.state.registry.add_hook('job_started', drift)
    else:
        def changing_runner(argv, **kwargs):
            result = runner(argv, **kwargs)
            if len(runner.calls) == 3:  # Two snapshots, then the first mutation.
                drift()
            return result
        app.state.executor.runner = changing_runner
    identity = approve_queued(client)
    app.state.worker.pause.set()
    result = wait_job(client, identity)
    if timing == 'job_started':
        assert result['status'] == 'failed', result
        assert runner.calls == []
    else:
        # Do not run changed restore code either: retain snapshots for manual recovery.
        assert result['status'] == 'rollback_failed', result
        assert [call[0] for call in runner.calls] == ['snapshot', 'snapshot', 'apply']
        assert len(result['result']['snapshots']) == 2
