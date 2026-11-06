"""Redis tracer bullets; subprocess boundary is injected, never a live lab."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


@pytest.fixture(autouse=True)
def reviewed_redis_candidate(tmp_path, monkeypatch):
    """Only isolated test copies are promoted; shipped blueprint stays planned."""
    import shutil
    import yaml
    from kuberweaver import execution
    staged = tmp_path / 'reviewed-candidate'
    plugin = staged / 'plugins/redis'
    shutil.copytree(ROOT / 'plugins/redis', plugin, ignore=shutil.ignore_patterns('__pycache__'))
    manifest = yaml.safe_load((plugin / 'manifest.yaml').read_text())
    manifest['capability'] = 'live'
    (plugin / 'manifest.yaml').write_text(yaml.safe_dump(manifest))
    monkeypatch.setattr(execution, 'ROOT', staged)
    return staged / 'plugins'


def executor_type():
    from kuberweaver.redis_execution import RedisExecutor
    return RedisExecutor


def graph(**config):
    return {'name': 'redis-lab', 'environment': 'dev', 'nodes': [
        {'id': 'cache', 'plugin_id': 'redis', 'config': config}], 'edges': [],
        'strategy': {'canary': 1, 'batch_size': 1}, 'failure_mode': 'none'}


def test_reviewed_redis_only_before_any_command(tmp_path):
    executor = executor_type()(tmp_path)
    calls = []
    executor.runner = lambda *a, **k: calls.append(a)
    mixed = graph()
    mixed['nodes'].append({'id': 'web', 'plugin_id': 'nginx', 'config': {}})
    with pytest.raises(ValueError, match='Redis|redis'):
        executor.execute({'id': 'mixed', 'graph': mixed}, lambda *_: None)
    assert not calls
    prepared, _, _ = executor._prepare({'id': 'reviewed', 'graph': graph()})
    assert prepared[0]['manifest']['service']['image'] == 'redis:7.4.2-alpine'
    assert prepared[0]['config']['isolated_lab'] is True


@pytest.mark.parametrize('config', [dict(password='secret'), dict(image='evil'),
    dict(targets=['outside']), dict(port=6379), dict(port=True),
    dict(isolated_lab=False), dict(public_cidr='0.0.0.0/0')])
def test_schema_rejects_unsafe_inputs(tmp_path, config):
    executor = executor_type()(tmp_path)
    calls = []
    executor.runner = lambda *a, **k: calls.append(a)
    with pytest.raises(ValueError):
        executor.execute({'id': 'unsafe', 'graph': graph(**config)}, lambda *_: None)
    assert not calls


class RedisBoundary:
    def __init__(self):
        self.calls = []
        self.services = {}

    def __call__(self, argv, **kwargs):
        from types import SimpleNamespace
        params = json.loads(Path(argv[argv.index('--extra-vars') + 1][1:]).read_text())
        assert Path(params['kw_helper_path']).parts[-2:] == ('redis', 'guest.py')
        assert Path(argv[3]).parts[-3:] == ('redis', 'iac', 'lifecycle.yml')
        r = params['kw_request']
        action, key = r['action'], (r['node_id'], r['target'])
        self.calls.append((action, key))
        if action == 'snapshot':
            result = {'exists': key in self.services, 'previous': self.services.get(key)}
        elif action == 'apply':
            self.services[key] = r['config']['version']
            result = {'applied': True}
        elif action == 'verify':
            result = {'error': 'injected Redis probe failure'} if r['inject_failure'] else {'healthy': True, 'protocol': 'resp2', 'ping': 'PONG', 'set_get': True}
        else:
            if r['snapshot']['exists']:
                self.services[key] = r['snapshot']['previous']
            else:
                self.services.pop(key, None)
            result = {'restored': True, 'absent': not r['snapshot']['exists']}
        Path(params['kw_result_path']).write_text(json.dumps(result))
        return SimpleNamespace(returncode=int('error' in result), stdout='', stderr='')


def test_real_orchestration_own_protocol_and_reverse_absence_restore(tmp_path):
    e = executor_type()(tmp_path)
    runner = e.runner = RedisBoundary()
    job = {'id': 'initial', 'graph': graph()}
    result = e.execute(job, lambda *_: None)
    assert result['status'] == 'succeeded'
    assert [a for a, _ in runner.calls] == ['snapshot', 'snapshot', 'apply', 'verify', 'apply', 'verify']
    assert [p['phase'] for p in result['evidence']] == ['canary', 'rolling']
    assert all(p['url'].startswith('redis://') for p in result['endpoints'])
    assert e.rollback(job, lambda *_: None)['status'] == 'rolled_back'
    assert not runner.services
    assert [key[1] for a, key in runner.calls if a == 'restore'] == ['branch-client', 'app']


def test_rolling_probe_failure_restores_persistent_baseline(tmp_path):
    e = executor_type()(tmp_path)
    runner = e.runner = RedisBoundary()
    assert e.execute({'id': 'base', 'graph': graph()}, lambda *_: None)['status'] == 'succeeded'
    changed = graph(version='2.0.0')
    changed['failure_mode'] = 'rolling'
    result = e.execute({'id': 'changed', 'graph': changed}, lambda *_: None)
    assert result['status'] == 'failed'
    assert result['rollback']['status'] == 'rolled_back'
    assert set(runner.services.values()) == {'1.0.0'}


def guest_module():
    path = ROOT / 'plugins/redis/guest.py'
    assert path.exists(), 'Redis guest lifecycle is missing'
    spec = importlib.util.spec_from_file_location('redis_guest', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_local_redis_ping_set_get_and_persistence(tmp_path):
    import shutil
    import socket
    import subprocess
    import time
    executable = shutil.which('redis-server')
    if not executable:
        pytest.skip('redis-server is not installed; no installation attempted')
    guest = guest_module()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    argv = [executable, '--bind', '127.0.0.1', '--port', str(port), '--dir', str(tmp_path),
            '--appendonly', 'yes', '--appendfsync', 'always', '--save', '', '--loglevel', 'warning']
    for iteration in range(2):
        process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    proof = guest.probe('127.0.0.1', port, 'probe-key', 'reviewed-v1')
                    break
                except OSError:
                    time.sleep(.03)
            else:
                pytest.fail('Local Redis did not start')
            assert proof == {'ping': 'PONG', 'set_get': True}
            if iteration == 0:
                assert guest.resp('127.0.0.1', port, 'SET', 'durable-test', 'persistent-data') == 'OK'
            else:
                assert guest.resp('127.0.0.1', port, 'GET', 'durable-test') == 'persistent-data'
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_resp_probe_refuses_wrong_readback(monkeypatch):
    guest = guest_module()
    monkeypatch.setattr(guest, 'resp', lambda ip, port, *args: {'PING': 'PONG', 'SET': 'OK', 'GET': 'wrong'}[args[0]])
    with pytest.raises(RuntimeError, match='readback'):
        guest.probe('127.0.0.1', 1, 'probe-key', 'expected')


def request(action='snapshot'):
    import yaml
    manifest = yaml.safe_load((ROOT / 'plugins/redis/manifest.yaml').read_text())
    return {'action': action, 'job_id': 'guest-job', 'node_id': 'cache', 'target': 'app',
            'management_ip': '192.168.122.40', 'plugin_id': 'redis', 'owner': 'a' * 24,
            'config': manifest['defaults'], 'service': manifest['service'], 'inject_failure': False}


class GuestBoundary:
    def __init__(self):
        self.calls = []
        self.state = None
        self.image = 'sha256:' + 'b' * 64
        self.firewall = True

    def __call__(self, argv, **kwargs):
        import copy
        from types import SimpleNamespace
        self.calls.append(argv)
        out, error, rc = '', '', 0
        if argv[:3] == ['iptables', '-S', 'DOCKER-USER']:
            out = '-N DOCKER-USER\n-A DOCKER-USER ! -s 192.168.122.1/32 -j DROP\n' if self.firewall else '-N DOCKER-USER\n'
        elif argv[1:3] == ['image', 'inspect']:
            out = json.dumps([{'Id': self.image}])
        elif argv[1:3] == ['container', 'inspect']:
            if self.state is None:
                rc, error = 1, 'No such container'
            else:
                out = json.dumps([self.state])
        elif argv[1] == 'stop':
            self.state['State']['Running'] = False
        elif argv[1] == 'start':
            self.state['State']['Running'] = True
        elif argv[1] == 'rm':
            self.state = None
        elif argv[1] in ('run', 'create'):
            self.state = self.make_state(argv)
            self.state['State']['Running'] = argv[1] == 'run'
        else:
            raise AssertionError(argv)
        return SimpleNamespace(returncode=rc, stdout=out, stderr=error)

    def make_state(self, argv):
        def option(name):
            return argv[argv.index(name) + 1]
        labels = dict(v.split('=', 1) for i, v in enumerate(argv) if i and argv[i-1] == '--label')
        mount = dict(v.split('=', 1) for v in option('--mount').split(','))
        ip, port, _ = option('-p').split(':')
        return {'Image': self.image, 'State': {'Running': True},
                'Config': {'Labels': labels, 'Cmd': argv[argv.index(self.image) + 1:] if self.image in argv else argv[argv.index('redis:7.4.2-alpine') + 1:],
                           'User': '999:999', 'Entrypoint': ['docker-entrypoint.sh']},
                'Mounts': [{'Type': 'bind', 'Source': mount['src'], 'Destination': '/data', 'RW': True}],
                'HostConfig': {'PortBindings': {'6379/tcp': [{'HostIp': ip, 'HostPort': port}]},
                  'Privileged': False, 'NetworkMode': 'bridge', 'ReadonlyRootfs': True,
                  'CapDrop': ['ALL'], 'CapAdd': None, 'Binds': None, 'Devices': [],
                  'SecurityOpt': ['no-new-privileges:true'], 'NanoCpus': int(float(option('--cpus')) * 1e9),
                  'Memory': int(option('--memory')[:-1]) * 1024 * 1024, 'PidsLimit': 128,
                  'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}}}


def service(tmp_path, monkeypatch, action='snapshot'):
    guest = guest_module()
    monkeypatch.setattr(guest, 'ROOT', tmp_path / 'services')
    monkeypatch.setattr(guest.Service, 'available', lambda *_: None)  # No lab interfaces in isolated unit tests.
    monkeypatch.setattr(guest.os, 'chown', lambda *_: None)
    monkeypatch.setattr(guest, 'resp', lambda ip, port, *a: 'PONG' if a[0] == 'PING' else '1.0.0')
    runner = GuestBoundary()
    return guest, guest.Service(request(action), runner=runner), runner


def test_guest_stopped_snapshot_and_coherent_data_restore(tmp_path, monkeypatch):
    guest, s, runner = service(tmp_path, monkeypatch)
    assert s.snapshot()['exists'] is False
    s.apply()
    data = s.path / 'data' / 'appendonly.aof'
    data.write_bytes(b'durable redis data\x00')
    old = s.snapshot()
    assert old['running'] and old['exists']
    assert [c[1] for c in runner.calls if c[0] == 'docker' and c[1] in ('stop', 'start')] == ['stop', 'start']
    assert runner.state['State']['Running'] is True
    data.write_bytes(b'new data')
    s.request['snapshot'] = old
    assert s.restore()['restored'] is True
    assert data.read_bytes() == b'durable redis data\x00'
    assert runner.state['State']['Running'] is True
    assert s.restore()['restored'] is True


def test_guest_restores_absence(tmp_path, monkeypatch):
    _, s, runner = service(tmp_path, monkeypatch)
    old = s.snapshot()
    s.apply()
    s.request['snapshot'] = old
    assert s.restore() == {'restored': True, 'absent': True, 'data_verified': True}
    assert runner.state is None and not s.path.exists()


def test_guest_refuses_unauthenticated_lan_before_mutation(tmp_path, monkeypatch):
    _, s, runner = service(tmp_path, monkeypatch)
    runner.firewall = False
    with pytest.raises(RuntimeError, match='controller-only firewall'):
        s.snapshot()
    assert not any(c[0] == 'docker' and c[1] in ('stop', 'start', 'run', 'rm') for c in runner.calls)
    assert not s.path.exists()


def test_guest_refuses_foreign_mount_before_stopping(tmp_path, monkeypatch):
    _, s, runner = service(tmp_path, monkeypatch)
    s.apply()
    runner.state['Mounts'][0]['Source'] = '/home/foreign'
    before = len(runner.calls)
    with pytest.raises(RuntimeError, match='mount|owned|configuration'):
        s.snapshot()
    assert not any(c[1] == 'stop' for c in runner.calls[before:] if c[0] == 'docker')


def test_source_drift_after_apply_blocks_every_later_command_including_restore(tmp_path, reviewed_redis_candidate):
    import shutil
    copied = tmp_path / 'plugins'
    shutil.copytree(reviewed_redis_candidate / 'redis', copied / 'redis', ignore=shutil.ignore_patterns('__pycache__'))
    e = executor_type()(tmp_path / 'state', copied)
    boundary = RedisBoundary()
    # Adapt fixture path assertions to copied source without modifying live source.
    def runner(argv, **kwargs):
        from types import SimpleNamespace
        params = json.loads(Path(argv[-1][1:]).read_text())
        r = params['kw_request']
        boundary.calls.append(r['action'])
        result = {'exists': False} if r['action'] == 'snapshot' else {'applied': True}
        Path(params['kw_result_path']).write_text(json.dumps(result))
        if r['action'] == 'apply':
            with (copied / 'redis/guest.py').open('a') as stream:
                stream.write('\n# drift after canary mutation\n')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    e.runner = runner
    result = e.execute({'id': 'drift', 'graph': graph()}, lambda *_: None)
    assert result['status'] == 'rollback_failed'
    assert boundary.calls == ['snapshot', 'snapshot', 'apply']


def test_rollback_rejects_mixed_journal_before_any_commands(tmp_path):
    e = executor_type()(tmp_path)
    boundary = e.runner = RedisBoundary()
    job = {'id': 'mixed-journal', 'graph': graph()}
    assert e.execute(job, lambda *_: None)['status'] == 'succeeded'
    path = tmp_path / 'jobs/mixed-journal/journal.json'
    journal = json.loads(path.read_text())
    journal['entries'][0]['plugin_id'] = 'nginx'
    path.write_text(json.dumps(journal))
    count = len(boundary.calls)
    with pytest.raises(ValueError, match='Redis|redis'):
        e.rollback(job, lambda *_: None)
    assert len(boundary.calls) == count


def test_main_dispatch_has_defined_resp_functions():
    import ast
    nodes = ast.parse((ROOT / 'plugins/redis/guest.py').read_text()).body
    assert isinstance(nodes[-1], ast.If), 'Executable entrypoint must run after all protocol definitions'


def test_declarative_catalog_and_independent_disabled_extension(tmp_path, reviewed_redis_candidate):
    import shutil
    from kuberweaver.catalog import Catalog
    from kuberweaver.extensions import ExtensionRegistry
    from kuberweaver.policy import Admission
    from kuberweaver.storage import Store
    catalog = Catalog(reviewed_redis_candidate)
    manifest = catalog.discover()['redis']
    assert manifest['schema']['additionalProperties'] is False
    assert manifest['executor'] == 'redis-container-v1'
    admission = Admission(catalog, Store(tmp_path / 'state'))
    assert admission.validate(graph())['allowed']
    copied = tmp_path / 'extensions/redis-runtime'
    shutil.copytree(ROOT / 'examples/extensions/redis-runtime', copied)
    registry = ExtensionRegistry()
    reports = registry.load_directory(copied.parent, enabled=set())
    assert not registry.list('execution')
    reports = registry.load_directory(copied.parent, enabled={'redis-runtime'})
    assert reports[0]['status'] == 'loaded', reports
    provider = registry.get('execution', 'redis')
    assert isinstance(provider, executor_type())
    assert provider.state_dir == tmp_path.resolve()
    assert registry.list('execution')[0]['isolated_lab_only'] is True


def test_empty_or_unproven_guest_result_is_not_success(tmp_path):
    from types import SimpleNamespace
    e = executor_type()(tmp_path)
    calls = []
    def runner(argv, **kwargs):
        params = json.loads(Path(argv[-1][1:]).read_text())
        calls.append(params['kw_request']['action'])
        Path(params['kw_result_path']).write_text(json.dumps({'unproven': True}))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    e.runner = runner
    with pytest.raises(RuntimeError, match='evidence'):
        e.execute({'id': 'unproven', 'graph': graph()}, lambda *_: None)
    assert calls == ['snapshot']


def test_guest_stopped_container_stays_stopped_after_restore(tmp_path, monkeypatch):
    _, s, runner = service(tmp_path, monkeypatch)
    s.apply()
    runner.state['State']['Running'] = False
    old = s.snapshot()
    assert old['running'] is False
    s.apply()
    s.request['snapshot'] = old
    assert s.restore()['restored'] is True
    assert runner.state['State']['Running'] is False


def test_guest_snapshot_archive_failure_restarts_owned_container(tmp_path, monkeypatch):
    guest, s, runner = service(tmp_path, monkeypatch)
    s.apply()
    def fail_archive(*a, **k):
        raise RuntimeError('injected archive failure')
    monkeypatch.setattr(guest.tarfile, 'open', fail_archive)
    with pytest.raises(RuntimeError, match='archive failure'):
        s.snapshot()
    assert runner.state['State']['Running'] is True


def test_guest_unsafe_archive_cannot_remove_current_workload(tmp_path, monkeypatch):
    import base64
    import io
    import tarfile
    _, s, runner = service(tmp_path, monkeypatch)
    s.apply()
    old = s.snapshot()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as stream:
        member = tarfile.TarInfo('service/../../escape')
        member.size = 1
        stream.addfile(member, io.BytesIO(b'x'))
    old['archive'] = base64.b64encode(buffer.getvalue()).decode()
    s.request['snapshot'] = old
    count = len(runner.calls)
    with pytest.raises(RuntimeError, match='Unsafe Redis snapshot'):
        s.restore()
    assert not any(c[1] in ('stop', 'rm') for c in runner.calls[count:] if c[0] == 'docker')
    assert runner.state['State']['Running'] is True


def test_trusted_extension_through_authenticated_approved_worker(tmp_path, reviewed_redis_candidate):
    import shutil
    from fastapi.testclient import TestClient
    from kuberweaver.app import create_app
    from test_core_auth import bootstrap
    from test_core_jobs import wait_job
    data = tmp_path / 'data'
    app = create_app(state_dir=data, plugins_dir=reviewed_redis_candidate, testing=True)
    with TestClient(app) as client:
        credentials = bootstrap(data)
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        shutil.copytree(ROOT / 'examples/extensions/redis-runtime', app.state.extensions_dir / 'redis-runtime')
        assert client.post('/api/extensions/redis-runtime/enable', json={'trusted': True}).status_code == 200
        assert client.patch('/api/providers', json={'bindings': {'execution': 'redis'}}).status_code == 200
        e = app.state.registry.get('execution', 'redis')
        boundary = e.runner = RedisBoundary()
        submitted = client.post('/api/jobs', json={'graph': graph()})
        assert submitted.status_code == 201, submitted.text
        identity = submitted.json()['id']
        assert client.post(f'/api/jobs/{identity}/approve').status_code == 200
        deployed = wait_job(client, identity)
        assert deployed['status'] == 'succeeded', deployed
        assert deployed['approved_execution_provider']['name'] == 'redis'
        assert [p['phase'] for p in deployed['result']['evidence']] == ['canary', 'rolling']
        assert client.post(f'/api/jobs/{identity}/rollback').status_code == 200
        assert wait_job(client, identity)['status'] == 'rolled_back'
        assert not boundary.services
        assert client.post('/api/extensions/redis-runtime/disable').status_code == 200
        assert client.get('/api/providers').json()['active']['execution']['available'] is False
        assert client.get('/api/jobs').status_code == 200


def test_shipped_candidate_is_planned_until_real_lab_validation(tmp_path):
    import yaml
    manifest = yaml.safe_load((ROOT / 'plugins/redis/manifest.yaml').read_text())
    assert manifest['capability'] == 'planned'
    e = executor_type()(tmp_path / 'unpromoted', ROOT / 'plugins')
    e.runner = lambda *a, **kw: pytest.fail('Unvalidated candidate must never run guest commands')
    with pytest.raises(ValueError):
        e.execute({'id': 'unpromoted', 'graph': graph()}, lambda *_: None)


def test_imported_executable_dependency_is_exposed_for_host_pinning(tmp_path):
    e = executor_type()(tmp_path)
    assert isinstance(e.source_dependencies, tuple)
    assert any(path.name == 'execution_credentials.py' for path in e.source_dependencies)
    assert all(isinstance(path, Path) and path.is_file() for path in e.source_dependencies)


@pytest.mark.parametrize('drift_before_retry', [False, True])
def test_authenticated_redis_partial_restore_retry_skips_restored_and_retains_source_guard(
        tmp_path, reviewed_redis_candidate, drift_before_retry):
    import shutil
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from kuberweaver.app import create_app
    from test_core_auth import bootstrap
    from test_core_jobs import wait_job
    data = tmp_path / 'retry-data'
    app = create_app(state_dir=data, plugins_dir=reviewed_redis_candidate, testing=True)
    with TestClient(app) as client:
        credentials = bootstrap(data)
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        shutil.copytree(ROOT / 'examples/extensions/redis-runtime', app.state.extensions_dir / 'redis-runtime')
        assert client.post('/api/extensions/redis-runtime/enable', json={'trusted': True}).status_code == 200
        assert client.patch('/api/providers', json={'bindings': {'execution': 'redis'}}).status_code == 200
        executor = app.state.registry.get('execution', 'redis')
        boundary = RedisBoundary()
        failed = []

        def runner(argv, **kwargs):
            params = json.loads(Path(argv[-1][1:]).read_text())
            request = params['kw_request']
            if request['action'] == 'restore' and request['target'] == 'branch-client' and not failed:
                failed.append(True)
                boundary.calls.append(('restore', (request['node_id'], request['target'])))
                Path(params['kw_result_path']).write_text(json.dumps({'error': 'temporary restore failure'}))
                return SimpleNamespace(returncode=1, stdout='', stderr='')
            return boundary(argv, **kwargs)

        executor.runner = runner
        submitted = client.post('/api/jobs', json={'graph': graph()})
        assert submitted.status_code == 201, submitted.text
        identity = submitted.json()['id']
        assert client.post(f'/api/jobs/{identity}/approve').status_code == 200
        assert wait_job(client, identity)['status'] == 'succeeded'
        assert client.post(f'/api/jobs/{identity}/rollback').status_code == 200
        partial = wait_job(client, identity)
        assert partial['status'] == 'rollback_failed', partial
        assert partial['can_rollback'] is True
        path = data / 'jobs' / identity / 'journal.json'
        assert json.loads(path.read_text())['restored'] == [0]
        assert list(boundary.services) == [('cache', 'branch-client')]
        before = list(boundary.calls)
        app.state.worker.pause.clear()
        try:
            assert client.post(f'/api/jobs/{identity}/rollback').status_code == 200
            assert client.post(f'/api/jobs/{identity}/rollback').status_code == 409
            if drift_before_retry:
                helper = reviewed_redis_candidate / 'redis/guest.py'
                helper.write_text(helper.read_text() + '\n# private candidate drift before retry\n')
        finally:
            app.state.worker.pause.set()
        retried = wait_job(client, identity)
        if drift_before_retry:
            assert retried['status'] == 'rollback_failed', retried
            assert boundary.calls == before
            assert json.loads(path.read_text())['restored'] == [0]
        else:
            assert retried['status'] == 'rolled_back', retried
            assert not boundary.services
            assert [key[1] for action, key in boundary.calls if action == 'restore'] == [
                'branch-client', 'app', 'branch-client']
            assert sorted(json.loads(path.read_text())['restored']) == [0, 1]


def credential_executor(tmp_path):
    from test_execution_credentials import configure, controlled_provider
    state = tmp_path / 'credential-state'
    configure(state)
    source, provider = controlled_provider(tmp_path)
    executor = executor_type()(state)
    executor.secret_resolver = provider.resolve
    return executor, source


def test_consumed_redis_credentials_override_old_inventory_and_strip_environment(tmp_path, monkeypatch):
    import shlex
    import stat
    from kuberweaver.redis_execution import SSH_CONFIG
    executor, source = credential_executor(tmp_path)
    monkeypatch.setenv('SSH_AUTH_SOCK', '/untrusted/agent')
    monkeypatch.setenv('PROVIDER_TOKEN', 'must-not-forward')
    monkeypatch.setenv('ANSIBLE_LOG_PATH', '/untrusted/log')
    boundary = RedisBoundary()
    seen = []

    def runner(argv, **kwargs):
        env = kwargs['env']
        key = Path(env['ANSIBLE_PRIVATE_KEY_FILE'])
        seen.append(key)
        assert key.read_bytes() == source.read_bytes()
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
        assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
        assert not {'SSH_AUTH_SOCK', 'PROVIDER_TOKEN', 'ANSIBLE_LOG_PATH'} & env.keys()
        assert 'IdentityAgent=none' in env['ANSIBLE_SSH_ARGS']
        assert 'IdentitiesOnly=yes' in env['ANSIBLE_SSH_ARGS']
        assert env['ANSIBLE_REMOTE_USER'] == 'lab-user'
        # Persisted inventory is intentionally legacy. Extra vars must outrank it.
        inventory = Path(argv[argv.index('-i') + 1]).read_text()
        assert str(SSH_CONFIG) in inventory
        extra = json.loads(argv[argv.index('-e') + 1])
        assert extra['ansible_ssh_common_args'] == env['ANSIBLE_SSH_COMMON_ARGS']
        isolated = Path(shlex.split(extra['ansible_ssh_common_args'])[1])
        assert isolated != SSH_CONFIG and isolated.is_file()
        assert not any(line.strip().lower().startswith(('identityfile', 'identityagent', 'certificatefile'))
                       for line in isolated.read_text().splitlines())
        result = boundary(argv, **kwargs)
        result.stdout = result.stderr = source.read_text()
        return result

    executor.runner = runner
    result = executor.execute({'id': 'credential-consumer', 'graph': graph(targets=['app'])}, lambda *_: None)
    assert result['status'] == 'succeeded'
    assert executor.rollback({'id': 'credential-consumer', 'graph': graph(targets=['app'])},
                             lambda *_: None)['status'] == 'rolled_back'
    assert len(seen) == 4
    assert all(not path.parent.exists() for path in seen)
    assert all(source.read_bytes() not in path.read_bytes()
               for path in executor.state_dir.rglob('*') if path.is_file())


@pytest.mark.parametrize('mode', ['missing-resolver', 'missing-reference', 'missing-target'])
def test_consumed_redis_credentials_fail_closed_without_ssh_fallback(tmp_path, mode):
    from kuberweaver.execution_credentials import ExecutionCredentialError
    executor, _ = credential_executor(tmp_path)
    if mode == 'missing-resolver':
        executor.secret_resolver = None
    elif mode == 'missing-reference':
        path = executor.state_dir / 'config/execution-credentials.json'
        config = json.loads(path.read_text())
        config['targets']['app']['private_key_ref'] = 'ssh/unavailable'
        path.write_text(json.dumps(config))
    else:
        path = executor.state_dir / 'config/execution-credentials.json'
        path.write_text(json.dumps({'enabled': True, 'targets': {}}))
    calls = []
    executor.runner = lambda *args, **kwargs: calls.append((args, kwargs))
    with pytest.raises(ExecutionCredentialError):
        executor.execute({'id': 'no-fallback', 'graph': graph(targets=['app'])}, lambda *_: None)
    assert not calls
    assert not list(executor.state_dir.rglob('*.vars.json'))


@pytest.mark.parametrize('outcome', ['result-leak', 'escaped-result-leak', 'parse-failure', 'timeout'])
def test_consumed_redis_secret_evidence_rejected_and_keys_cleaned(tmp_path, outcome):
    import subprocess
    from kuberweaver.execution_credentials import ExecutionCredentialError
    executor, source = credential_executor(tmp_path)
    seen = []
    events = []
    boundary = RedisBoundary()

    def runner(argv, **kwargs):
        seen.append(Path(kwargs['env']['ANSIBLE_PRIVATE_KEY_FILE']))
        params = json.loads(Path(argv[-1][1:]).read_text())
        result = boundary(argv, **kwargs)
        path = Path(params['kw_result_path'])
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(argv, 300, output=source.read_text())
        if outcome == 'parse-failure':
            path.write_text('{invalid-json')
        else:
            secret = source.read_text()
            serialized = json.dumps({'exists': False, 'unexpected': secret})
            if outcome == 'escaped-result-leak':
                serialized = serialized.replace('BEGIN', '\\u0042EGIN')
            path.write_text(serialized)
        result.stdout = result.stderr = source.read_text()
        return result

    executor.runner = runner
    with pytest.raises(ExecutionCredentialError) as failure:
        executor.execute({'id': 'bad-evidence', 'graph': graph(targets=['app'])},
                         lambda *event: events.append(event))
    assert str(failure.value) == 'Execution credential operation unavailable or failed'
    assert seen and all(not key.parent.exists() for key in seen)
    assert not list(executor.state_dir.rglob('*.vars.json'))
    assert not list(executor.state_dir.rglob('*.result.json'))
    assert not list(executor.state_dir.rglob('journal.json'))
    assert source.read_text() not in json.dumps(events)
    assert all(source.read_bytes() not in path.read_bytes()
               for path in executor.state_dir.rglob('*') if path.is_file())
