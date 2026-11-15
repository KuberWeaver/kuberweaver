"""HTTP/worker contracts; every command boundary below is a tmp_path simulation."""
import copy
import json
import time

import pytest
from test_core_graph import api, graph, manifest
from test_core_jobs import RecordingExecutor, install_executor, wait_job
from test_provider_bindings import install_extension
from test_source_dependencies import queued_api, approve_queued


SIMPLE_ENGINE = '''
    class Engine:
        def execute(self, job, emit):
            return {'status': 'succeeded', 'engine': 'manifest-engine'}
        def rollback(self, job, emit):
            return {'status': 'rolled_back', 'engine': 'manifest-engine'}
    def register(ctx):
        ctx.register('execution', 'manifest-engine', Engine())
'''


def edit_manifest(app, identity='sample-service', **fields):
    path = app.state.plugins_dir / identity / 'manifest.yaml'
    value = json.loads(path.read_text())
    value.update(fields)
    path.write_text(json.dumps(value))
    return value


def enable_engine(client, app, code=SIMPLE_ENGINE, identity='manifest-engine'):
    package = install_extension(app, identity, code, ['providers:execution', 'data:read-write'])
    response = client.post('/api/extensions/' + identity + '/enable', json={'trusted': True})
    assert response.status_code == 200, response.text
    return package


def test_manifest_routes_execution_independently_of_global_binding(api):
    client, app = api
    default = install_executor(app, RecordingExecutor())
    enable_engine(client, app)
    edit_manifest(app, execution_provider='manifest-engine')
    submitted = client.post('/api/jobs', json={'graph': graph()})
    assert submitted.status_code == 201, submitted.text
    identity = submitted.json()['id']
    assert client.post(f'/api/jobs/{identity}/approve').status_code == 200
    result = wait_job(client, identity)
    assert result['status'] == 'succeeded', result
    assert result['approved_execution_provider']['name'] == 'manifest-engine'
    assert result['result']['engine'] == 'manifest-engine'
    assert not default.calls
    assert app.state.provider_bindings['execution'] == 'default'


@pytest.mark.parametrize('field,value', [('execution_provider', 'other-engine'),
                                       ('admission_profile', 'virtual-machine')])
def test_mixed_manifest_contracts_are_rejected_at_submission(api, field, value):
    client, app = api
    manifest(app.state.plugins_dir, 'another-service')
    if field == 'execution_provider':
        edit_manifest(app, execution_provider='manifest-engine')
    edit_manifest(app, 'another-service', **{field: value})
    candidate = graph()
    other = copy.deepcopy(candidate['nodes'][0])
    other.update(id='node-2', plugin_id='another-service')
    other['config']['port'] = 18091
    candidate['nodes'].append(other)
    response = client.post('/api/jobs', json={'graph': candidate})
    assert response.status_code == 422, response.text
    assert client.get('/api/jobs').json() == []


def vm_manifest(app, **changes):
    fields = {'admission_profile': 'virtual-machine',
              'defaults': {'cpu': 2, 'memory_mb': 1024, 'disk_gb': 8},
              'schema': {'type': 'object', 'additionalProperties': False,
                         'properties': {'cpu': {'type': 'number'},
                                        'memory_mb': {'type': 'integer'},
                                        'disk_gb': {'type': 'number'}}}}
    fields.update(changes)
    return edit_manifest(app, **fields)


def test_vm_profile_is_single_resource_without_container_strategy(api):
    client, app = api
    vm_manifest(app)
    candidate = graph()
    candidate['strategy'] = {'canary': 0, 'batch_size': 15}
    response = client.post('/api/plans/validate', json=candidate)
    report = response.json()
    assert report['allowed'], report
    assert report['cost']['breakdown'][0]['targets'] == 1
    assert report['cost']['breakdown'][0]['resources']['cpu'] == 2
    submitted = client.post('/api/jobs', json={'graph': candidate})
    assert submitted.status_code == 201, submitted.text


@pytest.mark.parametrize('change', ['environment', 'failure_mode', 'nodes', 'edges', 'open_schema'])
def test_vm_first_slice_rejects_unsupported_scope(api, change):
    client, app = api
    vm_manifest(app)
    candidate = graph()
    if change == 'environment':
        candidate['environment'] = 'staging'
    elif change == 'failure_mode':
        candidate['failure_mode'] = 'canary'
    elif change == 'nodes':
        other = copy.deepcopy(candidate['nodes'][0])
        other['id'] = 'node-2'
        candidate['nodes'].append(other)
    elif change == 'edges':
        candidate['edges'] = [{'id': 'edge-1', 'source': 'node-1', 'target': 'missing'}]
    else:
        value = vm_manifest(app)
        value['schema'].pop('additionalProperties')
        edit_manifest(app, schema=value['schema'])
    response = client.post('/api/jobs', json={'graph': candidate})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('name', ['', None, [], 'some.module:Engine', '../engine'])
def test_manifest_execution_name_is_not_normalized_or_imported(api, name):
    client, app = api
    edit_manifest(app, execution_provider=name)
    response = client.post('/api/jobs', json={'graph': graph()})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('config,code', [({'password': 'forbidden'}, 'unsafe_field'),
                                       ({'cpu': 10000}, 'quota_cpu'),
                                       ({'extra': 1}, 'config_schema')])
def test_vm_admission_keeps_secret_schema_and_quota_gates(api, config, code):
    client, app = api
    vm_manifest(app)
    candidate = graph()
    candidate['nodes'][0]['config'] = config
    report = client.post('/api/plans/validate', json=candidate).json()
    assert not report['allowed']
    assert code in {item['code'] for item in report['violations']}


SAVED_ENGINE = '''
    import hashlib
    import json
    class Engine:
        requires_saved_plan = True
        supports_rollback = False
        connections = None
        def __init__(self, directory):
            self.directory = directory
            self.runner = self.command
            self.validations = 0
        def command(self, operation, job):
            # Simulation command boundary: never subprocess, remote I/O or real targets.
            with (self.directory / 'commands.jsonl').open('a') as stream:
                stream.write(json.dumps({'operation': operation, 'job_id': job['id']}) + '\\n')
            if operation == 'plan':
                (self.directory / (job['id'] + '.plan')).write_bytes(b'reviewed simulation plan')
        def prepare_plan(self, job, emit):
            assert not job.get('approved_by') and not job.get('git_commit')
            self.runner('init', job)
            self.runner('plan', job)
            return {'plan_digest': hashlib.sha256((self.directory / (job['id'] + '.plan')).read_bytes()).hexdigest(),
                    'actions': [{'action': 'create', 'resource': 'vm'}],
                    'summary': {'add': 1, 'change': 0, 'destroy': 0}, 'bindings': {'simulation': True}}
        def validate_plan(self, job):
            # Artifact-only validation: no command or remote read is permitted here.
            self.validations += 1
            actual = hashlib.sha256((self.directory / (job['id'] + '.plan')).read_bytes()).hexdigest()
            if actual != job['prepared_plan']['plan_digest']:
                raise RuntimeError('private plan evidence must never reach HTTP')
        def execute(self, job, emit):
            self.runner('apply', job)
            return {'status': 'succeeded', 'simulation': True}
        def rollback(self, job, emit):
            raise RuntimeError('unsupported')
    def register(ctx):
        ctx.register('execution', 'manifest-engine', Engine(ctx.plugin_data_dir()))
'''


def saved_engine(api, code=SAVED_ENGINE):
    client, app = api
    package = enable_engine(client, app, code)
    edit_manifest(app, execution_provider='manifest-engine')
    provider = app.state.registry.get('execution', 'manifest-engine')
    return provider, package


def wait_status(client, identity, statuses):
    for _ in range(300):
        result = client.get('/api/jobs/' + identity).json()
        if result['status'] in statuses:
            return result
        time.sleep(.01)
    raise AssertionError(result)


def test_saved_plan_is_prepared_by_same_worker_before_explicit_approval(api):
    from kuberweaver.jobs import digest
    from kuberweaver.providers import resolve_provider
    client, app = api
    provider, _ = saved_engine(api)
    app.state.connections = object()  # Injection contract only; no connection or remote mutation.
    assert resolve_provider(app, 'execution', 'manifest-engine').connections is app.state.connections
    boundary = provider.runner
    boundaries = []
    def simulated_command(operation, job):
        boundaries.append((operation, app.state.store.lock._is_owned()))
        return boundary(operation, job)
    provider.runner = simulated_command
    notifications = []
    app.state.registry.add_hook('job_finished', lambda payload: notifications.append(payload))
    app.state.worker.pause.clear()
    submitted = client.post('/api/jobs', json={'graph': graph()})
    assert submitted.status_code == 201, submitted.text
    job = submitted.json()
    assert job['status'] == 'queued'
    assert job['operation'] == 'prepare'
    assert job['approved_by'] is None and job['git_commit'] is None
    assert job['preparation_execution_provider']['name'] == 'manifest-engine'
    assert job['preparation_graph_hash'] == digest(job['graph'])
    assert job['preparation_source_hashes']
    assert 'preparation_credentials' in job
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 409
    assert not boundaries and not (app.state.state_dir / 'gitops').exists()
    app.state.worker.pause.set()
    ready = wait_status(client, job['id'], {'pending_approval', 'failed'})
    assert ready['status'] == 'pending_approval', ready
    assert ready['operation'] == 'execute' and ready['result'] is None
    assert ready['approved_by'] is None and ready['git_commit'] is None
    assert set(ready['prepared_plan']) == {'plan_digest', 'actions', 'summary', 'bindings'}
    assert ready['prepared_plan_hash'] == digest(ready['prepared_plan'])
    assert boundaries == [('init', False), ('plan', False)]
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    result = wait_job(client, job['id'])
    assert result['status'] == 'succeeded', result
    assert result['approved_prepared_plan'] == digest(ready['prepared_plan'])
    assert boundaries == [('init', False), ('plan', False), ('apply', False)]
    import subprocess
    artifact = subprocess.run(['git', '-C', str(app.state.state_dir / 'gitops'), 'show',
                               result['git_commit'] + ':jobs/' + job['id'] + '.json'],
                              capture_output=True, text=True, check=True)
    committed = json.loads(artifact.stdout)
    assert committed['prepared_plan'] == ready['prepared_plan']
    assert committed['approved_prepared_plan'] == result['approved_prepared_plan']
    for _ in range(100):
        if len(notifications) == 2:
            break
        time.sleep(.01)
    assert [item['status'] for item in notifications] == ['pending_approval', 'succeeded']
    # The existing notification schema supports execute/rollback, never prepare.
    from kuberweaver.notifications import WebhookNotifications
    for payload in notifications:
        assert WebhookNotifications._payload(payload)['operation'] == 'execute'


@pytest.mark.parametrize('declaration', [
    '',
    'def prepare_plan(self, job, emit): return {}',
    'async def prepare_plan(self, job, emit): return {}\n    def validate_plan(self, job): pass',
    'def prepare_plan(self, job, emit): return {}\n    def validate_plan(self, job, mandatory): pass',
    'def prepare_plan(self, job, emit): return {}\n    async def validate_plan(self, job): pass',
])
def test_saved_plan_provider_requires_synchronous_compatible_methods(api, declaration):
    client, app = api
    code = ('class Engine:\n'
            '    requires_saved_plan = True\n'
            '    def execute(self, job, emit): return {"status": "succeeded"}\n'
            '    def rollback(self, job, emit): return {"status": "rolled_back"}\n'
            '    ' + declaration + '\n'
            'def register(ctx): ctx.register("execution", "manifest-engine", Engine())\n')
    enable_engine(client, app, code)
    row = next(item for item in client.get('/api/providers').json()['available']
               if item['name'] == 'manifest-engine')
    assert row['available'] is False
    assert client.patch('/api/providers', json={'bindings': {'execution': 'manifest-engine'}}).status_code == 422


def test_provider_can_explicitly_disable_historical_rollback(api):
    client, app = api
    enable_engine(client, app, SIMPLE_ENGINE.replace('class Engine:', 'class Engine:\n        supports_rollback = False'))
    edit_manifest(app, execution_provider='manifest-engine')
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    result = wait_job(client, job['id'])
    assert result['status'] == 'succeeded', result
    assert result['can_rollback'] is False
    assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 409


def test_worker_propagates_provider_manual_review_to_top_level(api):
    client, app = api
    code = SIMPLE_ENGINE.replace("{'status': 'succeeded', 'engine': 'manifest-engine'}",
                                 "{'status': 'failed', 'requires_manual_review': True}")
    enable_engine(client, app, code)
    edit_manifest(app, execution_provider='manifest-engine')
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    result = wait_job(client, job['id'])
    assert result['status'] == 'failed'
    assert result['requires_manual_review'] is True
    assert result['can_rollback'] is False


@pytest.mark.parametrize('status', ['queued', 'planning'])
def test_interrupted_preparation_is_never_replayed_after_restart(api, status):
    from fastapi.testclient import TestClient
    from kuberweaver.app import create_app
    from test_core_jobs import login
    client, app = api
    provider, _ = saved_engine(api)
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    job['status'] = status
    app.state.store.put('jobs', job['id'], job)
    restarted = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir, testing=True)
    with TestClient(restarted) as other:
        login(other, app.state.state_dir, 'admin')
        recovered = other.get('/api/jobs/' + job['id']).json()
        assert recovered['status'] == 'failed', recovered
        assert recovered['requires_manual_review'] is True
        assert other.post(f"/api/jobs/{job['id']}/approve").status_code == 409
        assert not (provider.directory / 'commands.jsonl').exists()


@pytest.mark.parametrize('status', ['planning', 'approving'])
def test_registry_changes_block_durable_inflight_states_without_active_thread(api, status):
    client, app = api
    enable_engine(client, app)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    job['status'] = status
    app.state.store.put('jobs', job['id'], job)
    assert app.state.worker.active_identity is None
    assert client.patch('/api/providers', json={'bindings': {'execution': 'default'}}).status_code == 409
    assert client.post('/api/extensions/manifest-engine/disable').status_code == 409


def test_consumer_validated_provider_can_reuse_provenance_without_global_binding(api, tmp_path):
    from kuberweaver.providers import provider_provenance, CONSUMER_METHODS
    client, app = api
    dependency = tmp_path / 'driver_helper.py'
    dependency.write_text('# reviewed simulation helper\n')
    package = install_extension(app, 'sim-driver', '''
        class Driver:
            def test(self, connection, secret): return {'status': 'simulated'}
        def register(ctx): ctx.register('hypervisor', 'sim-driver', Driver())
    ''', ['providers:hypervisor'])
    assert client.post('/api/extensions/sim-driver/enable', json={'trusted': True}).status_code == 200
    driver = app.state.registry.get('hypervisor', 'sim-driver')
    type(driver).source_dependencies = (dependency,)
    original = provider_provenance(app, 'hypervisor', 'sim-driver', provider=driver)
    dependency.write_text('# changed simulation helper\n')
    changed = provider_provenance(app, 'hypervisor', 'sim-driver', provider=driver)
    assert changed != original
    path = package / 'module.py'
    path.write_text(path.read_text() + '# changed driver source\n')
    assert provider_provenance(app, 'hypervisor', 'sim-driver', provider=driver) != changed
    assert 'hypervisor' not in CONSUMER_METHODS
    assert 'hypervisor' not in app.state.provider_bindings


def ready_job(api):
    client, _ = api
    response = client.post('/api/jobs', json={'graph': graph()})
    assert response.status_code == 201, response.text
    ready = wait_status(client, response.json()['id'], {'pending_approval', 'failed'})
    assert ready['status'] == 'pending_approval', ready
    return ready


def commands(provider):
    path = provider.directory / 'commands.jsonl'
    return [json.loads(line)['operation'] for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize('summary', ["{'password': 'SIMULATION-SENSITIVE'}",
                                     "{'detail': '-----BEGIN PRIVATE KEY-----SIMULATION-SENSITIVE'}",
                                     "{'detail': " + "{'nested': " * 12 + "'end'" + '}' * 12 + '}',
                                     "{'detail': 'x' * 70000}"])
def test_preparation_persists_only_safe_bounded_json_summary(api, summary):
    client, app = api
    code = SAVED_ENGINE.replace("{'add': 1, 'change': 0, 'destroy': 0}", summary)
    provider, _ = saved_engine(api, code)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    result = wait_status(client, job['id'], {'pending_approval', 'failed'})
    assert result['status'] == 'failed', result
    assert result['requires_manual_review'] is True
    assert 'prepared_plan' not in result
    assert 'SIMULATION-SENSITIVE' not in json.dumps(result)
    assert not (app.state.state_dir / 'gitops').exists()
    assert commands(provider) == ['init', 'plan']


def test_provider_raw_dump_is_not_persisted_as_prepared_plan(api):
    client, app = api
    code = SAVED_ENGINE.replace("'bindings': {'simulation': True}}",
                                "'bindings': {'simulation': True}, 'raw_output': 'SIMULATION-SENSITIVE'}")
    saved_engine(api, code)
    ready = ready_job(api)
    assert 'SIMULATION-SENSITIVE' not in json.dumps(ready)
    assert set(ready['prepared_plan']) == {'plan_digest', 'actions', 'summary', 'bindings'}


@pytest.mark.parametrize('stage', ['prepare', 'execute'])
@pytest.mark.parametrize('field,value', [(None, None), ('id', 'unreviewed-job'),
                                       ('status', 'pending_approval'), ('status', 'rejected')])
def test_durable_job_drift_blocks_the_next_simulation_command(api, stage, field, value):
    client, app = api
    code = SAVED_ENGINE.replace("self.runner('apply', job)", "self.runner('verify', job)\n            self.runner('apply', job)")
    provider, _ = saved_engine(api, code)
    boundary = provider.runner
    trigger = 'init' if stage == 'prepare' else 'verify'
    def simulated_command(operation, job):
        boundary(operation, job)
        if operation == trigger:
            with app.state.store.lock:
                current = app.state.jobs.get(job['id'])
                if field is not None:
                    current[field] = value
                elif stage == 'prepare':
                    current['graph']['name'] = 'Unreviewed graph'
                else:
                    current['prepared_plan']['summary']['add'] = 2
                app.state.store.put('jobs', job['id'], current)
    provider.runner = simulated_command
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    if stage == 'execute':
        assert wait_status(client, job['id'], {'pending_approval', 'failed'})['status'] == 'pending_approval'
        assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    result = wait_job(client, job['id'])
    assert commands(provider) == (['init'] if stage == 'prepare' else ['init', 'plan', 'verify'])
    assert result['status'] == 'failed', result
    assert result['requires_manual_review'] is True
    assert provider.runner is simulated_command


@pytest.mark.parametrize('field,value', [
    ('id', 'unreviewed-job'),
    ('status', 'pending_approval'),
    ('status', 'rejected'),
    ('graph', 'Unreviewed durable graph'),
    ('approved_by', None),
    ('git_commit', None),
    ('operation', 'rollback'),
    ('requires_manual_review', True),
    ('approved_execution_provider', {'name': 'default', 'provenance': 'unreviewed'}),
    ('approved_graph_hash', 'unreviewed'),
    ('approved_manifest_hashes', {}),
    ('approved_source_hashes', {}),
    ('approved_credentials', 'unreviewed'),
])
def test_default_executor_reloads_durable_evidence_before_next_command(queued_api, field, value):
    client, app, runner, _ = queued_api
    assert not hasattr(app.state.registry.get('execution', 'default'), 'runner')

    def changing_runner(argv, **kwargs):
        result = runner(argv, **kwargs)
        if len(runner.calls) == 1:  # First snapshot, before any mutation.
            with app.state.store.lock:
                current = app.state.jobs.get(identity)
                if field == 'graph':
                    current['graph']['name'] = value
                else:
                    current[field] = value
                app.state.store.put('jobs', identity, current)
        return result

    app.state.executor.runner = changing_runner
    identity = approve_queued(client)
    app.state.worker.pause.set()
    result = wait_job(client, identity)
    assert [call[0] for call in runner.calls] == ['snapshot']
    assert result['status'] == 'failed', result
    assert result['requires_manual_review'] is True
    assert result['can_rollback'] is False
    assert result['result'] is None
    if field != 'status':
        assert (result['graph']['name'] if field == 'graph' else result[field]) == value
    assert app.state.executor.runner is changing_runner


@pytest.mark.parametrize('operation', ['execute', 'rollback'])
@pytest.mark.parametrize('field,value', [('approved_by', None), ('id', 'unreviewed-job'),
                                       ('status', 'pending_approval'), ('status', 'rejected')])
def test_default_executor_retains_manual_review_when_restore_catches_trust_error(queued_api, operation, field, value):
    client, app, runner, _ = queued_api
    trigger = 'apply' if operation == 'execute' else 'restore'
    changed = []

    def changing_runner(argv, **kwargs):
        result = runner(argv, **kwargs)
        if runner.calls[-1][0] == trigger and not changed:
            with app.state.store.lock:
                current = app.state.jobs.get(identity)
                current[field] = value
                app.state.store.put('jobs', identity, current)
            changed.append(True)
        return result

    app.state.executor.runner = changing_runner
    identity = approve_queued(client)
    app.state.worker.pause.set()
    result = wait_job(client, identity)
    if operation == 'rollback':
        assert result['status'] == 'succeeded', result
        assert client.post(f'/api/jobs/{identity}/rollback').status_code == 200
        result = wait_job(client, identity)

    expected = ['snapshot', 'snapshot', 'apply']
    if operation == 'rollback':
        expected += ['verify', 'apply', 'verify', 'restore']
    assert [call[0] for call in runner.calls] == expected
    assert result['status'] == 'rollback_failed', result
    assert result['requires_manual_review'] is True
    assert result['can_rollback'] is False
    assert len(result['result']['snapshots']) == 2
    assert app.state.executor.runner is changing_runner
    journal = json.loads((app.state.state_dir / 'jobs' / identity / 'journal.json').read_text())
    assert journal['status'] == 'rollback_failed'
    assert journal['restored'] == ([1] if operation == 'rollback' else [])
    restoration = result['result'] if operation == 'rollback' else result['result']['rollback']
    assert restoration['status'] == 'rollback_failed'
    assert len(restoration['errors']) == 1
    assert len(restoration['evidence']) == (1 if operation == 'rollback' else 0)


@pytest.mark.parametrize('dependency', ['implementation', 'source', 'manifest', 'credentials'])
def test_default_executor_keeps_dependency_pins_at_each_command(queued_api, dependency):
    client, app, runner, source = queued_api

    def changing_runner(argv, **kwargs):
        result = runner(argv, **kwargs)
        if len(runner.calls) == 1:
            if dependency in {'implementation', 'source'}:
                path = source if dependency == 'implementation' else app.state.plugins_dir / '_shared/guest.py'
                path.write_text(path.read_text() + '\n# Unapproved simulation dependency\n')
            elif dependency == 'manifest':
                path = app.state.plugins_dir / 'nginx/manifest.yaml'
                value = app.state.catalog.discover()['nginx']
                value['description'] = 'Changed after first snapshot'
                path.write_text(json.dumps(value))
            else:
                path = app.state.state_dir / 'config/execution-credentials.json'
                path.write_text(json.dumps({'enabled': False, 'targets': {}}))
        return result

    app.state.executor.runner = changing_runner
    identity = approve_queued(client)
    app.state.worker.pause.set()
    result = wait_job(client, identity)
    assert result['status'] == 'failed', result
    assert result['requires_manual_review'] is True
    assert [call[0] for call in runner.calls] == ['snapshot']
    assert app.state.executor.runner is changing_runner


def test_default_executor_allows_appended_events_during_commands_and_rollback(queued_api):
    client, app, runner, _ = queued_api

    def observed_runner(argv, **kwargs):
        assert not app.state.store.lock._is_owned()
        app.state.jobs.emit(identity, 'simulation', 'info', 'Command-boundary event appended')
        return runner(argv, **kwargs)

    app.state.executor.runner = observed_runner
    identity = approve_queued(client)
    app.state.worker.pause.set()
    executed = wait_job(client, identity)
    assert executed['status'] == 'succeeded', executed
    assert executed['requires_manual_review'] is False
    assert client.post(f'/api/jobs/{identity}/rollback').status_code == 200
    restored = wait_job(client, identity)
    assert restored['status'] == 'rolled_back', restored
    assert restored['requires_manual_review'] is False
    assert [call[0] for call in runner.calls] == [
        'snapshot', 'snapshot', 'apply', 'verify', 'apply', 'verify', 'restore', 'restore']
    assert len([event for event in restored['events'] if event['phase'] == 'simulation']) == 8
    assert app.state.executor.runner is observed_runner


@pytest.mark.parametrize('stage', ['approval', 'dequeue', 'command'])
def test_saved_artifact_drift_never_replans_or_reaches_apply(api, stage):
    client, app = api
    code = SAVED_ENGINE.replace("self.runner('apply', job)", "self.runner('verify', job)\n            self.runner('apply', job)")
    provider, _ = saved_engine(api, code)
    job = ready_job(api)
    plan = provider.directory / (job['id'] + '.plan')
    if stage == 'approval':
        plan.write_bytes(b'changed simulation artifact')
        response = client.post(f"/api/jobs/{job['id']}/approve")
        assert response.status_code == 409, response.text
        assert 'private plan evidence' not in response.text
        assert not (app.state.state_dir / 'gitops').exists()
    else:
        if stage == 'dequeue':
            app.state.worker.pause.clear()
        else:
            boundary = provider.runner
            def simulated_command(operation, current):
                boundary(operation, current)
                if operation == 'verify':
                    plan.write_bytes(b'changed simulation artifact')
            provider.runner = simulated_command
        assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
        if stage == 'dequeue':
            plan.write_bytes(b'changed simulation artifact')
            app.state.worker.pause.set()
        result = wait_job(client, job['id'])
        assert result['status'] == 'failed', result
        assert result['requires_manual_review'] is True
    assert commands(provider) == (['init', 'plan', 'verify'] if stage == 'command' else ['init', 'plan'])


@pytest.mark.parametrize('change', ['missing', 'summary', 'graph', 'credentials', 'source', 'provider_name'])
def test_prepared_plan_cannot_be_blessed_after_preparation_drift(api, change):
    client, app = api
    provider, package = saved_engine(api)
    job = ready_job(api)
    if change == 'credentials':
        (app.state.state_dir / 'config/execution-credentials.json').write_text(json.dumps({'enabled': False, 'targets': {}}))
    elif change == 'source':
        path = package / 'module.py'
        path.write_text(path.read_text() + '# unapproved simulation source\n')
    elif change == 'provider_name':
        edit_manifest(app, execution_provider='default')
    else:
        current = app.state.jobs.get(job['id'])
        if change == 'missing':
            current.pop('prepared_plan')
        elif change == 'summary':
            current['prepared_plan']['summary']['add'] = 7
        else:
            current['graph']['name'] = 'Changed since preparation'
        app.state.store.put('jobs', job['id'], current)
    response = client.post(f"/api/jobs/{job['id']}/approve")
    assert response.status_code == 409, response.text
    assert commands(provider) == ['init', 'plan']
    assert not (app.state.state_dir / 'gitops').exists()


def test_saved_plan_revalidation_occurs_after_actual_remote_publication_without_shared_lock(api, tmp_path, monkeypatch):
    import kuberweaver.gitops as gitops
    from test_remote_gitops import git, configure
    client, app = api
    provider, _ = saved_engine(api)
    job = ready_job(api)
    remote = tmp_path / 'saved-approval.git'
    git(tmp_path, 'init', '--bare', str(remote))
    configure(app.state.state_dir, remote)
    boundary = gitops._run
    checked = []
    def actual_git_command(args, env, deadline):
        if 'push' in args or 'ls-remote' in args:
            checked.append('push' if 'push' in args else 'ls-remote')
            assert not app.state.store.lock._is_owned()
        result = boundary(args, env, deadline)
        if 'ls-remote' in args:
            (provider.directory / (job['id'] + '.plan')).write_bytes(b'changed after publishing simulation artifact')
        return result
    monkeypatch.setattr(gitops, '_run', actual_git_command)
    response = client.post(f"/api/jobs/{job['id']}/approve")
    assert response.status_code == 409, response.text
    current = app.state.jobs.get(job['id'])
    assert current['requires_manual_review'] is True
    assert current['approval_claim']['state'] == 'conflict'
    assert current['status'] == 'approving'
    assert git(remote, 'rev-parse', 'refs/heads/approved') == current['git_commit']
    assert checked == ['push', 'ls-remote']
    assert commands(provider) == ['init', 'plan']


def test_preparation_interrupt_is_failed_sanitized_and_not_replayed(api):
    client, app = api
    code = SAVED_ENGINE.replace("self.runner('plan', job)", "raise SystemExit('SIMULATION-SENSITIVE')")
    provider, _ = saved_engine(api, code)
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    try:
        # Dispatch the real claimed Worker operation deterministically while its
        # background consumer is paused. It must contain provider process exits.
        app.state.worker.process(job['id'])
    except BaseException as exc:
        pytest.fail('Worker leaked provider interruption: ' + type(exc).__name__)
    result = client.get('/api/jobs/' + job['id']).json()
    assert result['status'] == 'failed'
    assert result['requires_manual_review'] is True
    assert 'SIMULATION-SENSITIVE' not in json.dumps(result)
    app.state.worker.process(job['id'])
    assert commands(provider) == ['init']


def test_preparation_uses_single_worker_releases_lock_and_observes_each_operation_once(api):
    import threading
    client, app = api
    provider, _ = saved_engine(api)
    entered, release = threading.Event(), threading.Event()
    boundary = provider.runner
    threads, lock_ownership, completed = [], [], []
    def simulated_command(operation, job):
        threads.append(threading.get_ident())
        lock_ownership.append(app.state.store.lock._is_owned())
        if operation == 'init' and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return boundary(operation, job)
    provider.runner = simulated_command
    app.state.registry.add_hook('job_finished', lambda payload: completed.append(payload))
    def broken_observer(payload):
        raise RuntimeError('SIMULATION-SENSITIVE')
    app.state.registry.add_hook('job_finished', broken_observer)
    first = client.post('/api/jobs', json={'graph': graph()}).json()
    try:
        assert entered.wait(3)
        assert client.get('/api/jobs/' + first['id']).json()['status'] == 'planning'
        # Both HTTP submission and reads finish while actual init is paused.
        second = client.post('/api/jobs', json={'graph': graph()}).json()
        assert second['operation'] == 'prepare' and second['status'] == 'queued'
        assert client.post(f"/api/jobs/{first['id']}/approve").status_code == 409
        assert client.patch('/api/providers', json={'bindings': {'execution': 'default'}}).status_code == 409
        assert client.post('/api/extensions/manifest-engine/disable').status_code == 409
    finally:
        release.set()
    for identity in (first['id'], second['id']):
        assert wait_status(client, identity, {'pending_approval', 'failed'})['status'] == 'pending_approval'
        app.state.worker.enqueue(identity)  # Stale duplicate queue entries are not new operations.
    assert client.post(f"/api/jobs/{first['id']}/approve").status_code == 200
    assert wait_job(client, first['id'])['status'] == 'succeeded'
    app.state.worker.enqueue(first['id'])
    for _ in range(100):
        if app.state.worker.active_identity is None and app.state.worker.queue.empty():
            break
        time.sleep(.01)
    assert [item['status'] for item in completed] == ['pending_approval', 'pending_approval', 'succeeded']
    assert len(set(threads)) == 1 and not any(lock_ownership)
    assert commands(provider) == ['init', 'plan', 'init', 'plan', 'apply']
    assert 'SIMULATION-SENSITIVE' not in json.dumps(client.get('/api/jobs').json())
    failures = [item for item in client.get('/api/audit').json() if item['action'] == 'lifecycle.observer_failed']
    assert len(failures) == 3


@pytest.mark.parametrize('change', ['source', 'manifest', 'credentials', 'graph'])
def test_preparation_initial_pins_are_revalidated_before_any_command(api, change):
    client, app = api
    provider, package = saved_engine(api)
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    if change == 'source':
        path = package / 'module.py'
        path.write_text(path.read_text() + '# unapproved simulation engine\n')
    elif change == 'manifest':
        edit_manifest(app, description='Changed manifest after queue admission')
    elif change == 'credentials':
        (app.state.state_dir / 'config/execution-credentials.json').write_text(json.dumps({'enabled': False, 'targets': {}}))
    else:
        current = app.state.jobs.get(job['id'])
        current['graph']['name'] = 'Changed graph after queue admission'
        app.state.store.put('jobs', job['id'], current)
    app.state.worker.pause.set()
    result = wait_job(client, job['id'])
    assert result['status'] == 'failed' and result['requires_manual_review'] is True
    assert not commands(provider)


@pytest.mark.parametrize('change', ['source', 'credentials'])
def test_preparation_initial_pins_are_revalidated_at_every_command(api, change):
    client, app = api
    provider, package = saved_engine(api)
    boundary = provider.runner
    def simulated_command(operation, job):
        boundary(operation, job)
        if operation == 'init':
            if change == 'source':
                path = package / 'module.py'
                path.write_text(path.read_text() + '# changed after init simulation boundary\n')
            else:
                (app.state.state_dir / 'config/execution-credentials.json').write_text(json.dumps({'enabled': False, 'targets': {}}))
    provider.runner = simulated_command
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    result = wait_job(client, job['id'])
    assert result['status'] == 'failed' and result['requires_manual_review'] is True
    assert commands(provider) == ['init']


def test_approval_cannot_repin_new_blueprint_source_over_the_prepared_source(api, monkeypatch):
    client, app = api
    provider, _ = saved_engine(api)
    job = ready_job(api)
    fingerprint = app.state.catalog.fingerprint
    changed = []
    def source_changes_after_initial_validation(identity):
        value = fingerprint(identity)
        if not changed:
            (app.state.plugins_dir / identity / 'simulation-helper.py').write_text('# source changed during approval\n')
            changed.append(identity)
        return value
    monkeypatch.setattr(app.state.catalog, 'fingerprint', source_changes_after_initial_validation)
    response = client.post(f"/api/jobs/{job['id']}/approve")
    assert response.status_code == 409, response.text
    current = app.state.jobs.get(job['id'])
    assert current['requires_manual_review'] is True
    assert current['approved_source_hashes'] == job['preparation_source_hashes']
    assert commands(provider) == ['init', 'plan']


def test_vm_profile_and_manifest_saved_executor_share_the_existing_job_lifecycle(api):
    client, app = api
    provider, _ = saved_engine(api)
    vm_manifest(app)
    candidate = graph()
    candidate['strategy'] = {'canary': 0, 'batch_size': 15}
    response = client.post('/api/jobs', json={'graph': candidate})
    assert response.status_code == 201, response.text
    job = wait_status(client, response.json()['id'], {'pending_approval', 'failed'})
    assert job['status'] == 'pending_approval', job
    assert 'targets' not in job['graph']['nodes'][0]['config']
    assert job['cost']['breakdown'][0]['targets'] == 1
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    result = wait_job(client, job['id'])
    assert result['status'] == 'succeeded', result
    assert result['can_rollback'] is False
    assert commands(provider) == ['init', 'plan', 'apply']
