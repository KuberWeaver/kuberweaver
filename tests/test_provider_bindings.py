"""Consumer-level replacement contracts: no core edits or builtin overrides."""
import json
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient
from test_core_graph import api, graph
from test_core_jobs import login, wait_job, RecordingExecutor, install_executor


def wait_idle(app, timeout=15.0):
    """Wait until the worker releases the queue.

    A job reaches its terminal status inside the engine, a moment before the
    worker clears its active identity. Provider and extension changes are
    refused while the queue is busy, so a test that changes either right after
    a job waits for the worker, not for the job status.
    """
    import time as _time

    from backend.runtime import queue_busy

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if not queue_busy(app):
            return True
        _time.sleep(0.02)
    return False


def install_extension(app, identity, code, capabilities):
    directory = app.state.extensions_dir / identity
    directory.mkdir()
    (directory / 'plugin.yaml').write_text(json.dumps({'id': identity, 'version': '1.0.0',
        'entrypoint': 'module.py', 'capabilities': capabilities}))
    (directory / 'module.py').write_text(textwrap.dedent(code))
    return directory


def test_selected_execution_is_approved_pinned_and_uses_real_worker(api):
    client, app = api
    install_extension(app, 'engine', '''
        class Engine:
            def execute(self, job, emit):
                emit('verify', 'info', 'Selected alternate engine ran')
                return {'status': 'succeeded', 'engine': 'alternate'}
            def rollback(self, job, emit):
                return {'status': 'rolled_back', 'engine': 'alternate'}
        def register(ctx):
            ctx.register('execution', 'engine', Engine())
    ''', ['providers:execution'])
    assert client.post('/api/extensions/engine/enable', json={'trusted': True}).status_code == 200
    assert client.patch('/api/providers', json={'bindings': {'execution': 'engine'}}).status_code == 200
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    result = wait_job(client, job['id'])
    assert result['status'] == 'succeeded', result
    assert result['result']['engine'] == 'alternate'
    assert result['approved_execution_provider']['name'] == 'engine'
    assert result['approved_execution_provider']['provenance']
    # Binding may change when idle; rollback must use the approved provider, not its replacement.
    assert wait_idle(app)
    assert client.patch('/api/providers', json={'bindings': {'execution': 'ansible-workload-v1'}}).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/rollback").status_code == 200
    assert wait_job(client, job['id'])['result']['engine'] == 'alternate'


def test_selected_inventory_is_used_persisted_and_disabled_fails_only_its_route(api):
    client, app = api
    install_extension(app, 'alternate', '''
        class Inventory:
            def read(self):
                return {'source': 'alternate', 'nodes': [{'id': 'extension-node'}], 'links': [], 'errors': []}
        def register(ctx):
            ctx.register('inventory', 'alternate', Inventory(), {'read_only': True})
    ''', ['providers:inventory'])
    assert client.post('/api/extensions/alternate/enable', json={'trusted': True}).status_code == 200
    selected = client.patch('/api/providers', json={'bindings': {'inventory': 'alternate'}})
    assert selected.status_code == 200, selected.text
    assert selected.json()['bindings']['inventory'] == 'alternate'
    assert client.get('/api/infrastructure').json()['nodes'] == [{'id': 'extension-node'}]
    assert json.loads((app.state.state_dir / 'config/providers.json').read_text())['bindings']['inventory'] == 'alternate'
    active = next(row for row in client.get('/api/integrations').json() if row['kind'] == 'inventory')
    assert active['id'] == 'alternate' and active['selected'] is True
    assert client.post('/api/extensions/alternate/disable').status_code == 200
    assert client.get('/api/infrastructure').status_code == 503
    assert client.get('/api/diagnostics').status_code == 200
    assert client.get('/api/jobs').status_code == 200
    assert client.get('/api/providers').json()['active']['inventory']['available'] is False
    from backend.app import create_app
    restarted = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir, testing=True)
    with TestClient(restarted) as other:
        login(other, app.state.state_dir, 'admin')
        assert other.get('/api/providers').json()['bindings']['inventory'] == 'alternate'
        assert other.get('/api/infrastructure').status_code == 503
        assert other.get('/api/jobs').status_code == 200


def test_consumer_protocol_rejects_wrong_method_signature(api):
    client, app = api
    install_extension(app, 'incompatible', """
        class Inventory:
            def read(self, mandatory_context):
                return {}
        def register(ctx):
            ctx.register('inventory', 'incompatible', Inventory())
    """, ['providers:inventory'])
    assert client.post('/api/extensions/incompatible/enable', json={'trusted': True}).status_code == 200
    response = client.patch('/api/providers', json={'bindings': {'inventory': 'incompatible'}})
    assert response.status_code == 422
    row = next(row for row in client.get('/api/providers').json()['available'] if row['name'] == 'incompatible')
    assert row['available'] is False


def test_selected_notification_consumer_sends_and_missing_provider_warns_without_fallback(api):
    client, app = api
    install_extension(app, 'delivery', r"""
        import json
        class Delivery:
            def __init__(self, path):
                self.path = path
            def send(self, payload):
                with self.path.open('a') as stream:
                    stream.write(json.dumps(payload) + '\n')
        def register(ctx):
            ctx.register('notifications', 'delivery', Delivery(ctx.plugin_data_dir() / 'sent.jsonl'),
                         {'status': 'configured', 'external_delivery': False})
    """, ['providers:notifications', 'data:read-write'])
    assert client.post('/api/extensions/delivery/enable', json={'trusted': True}).status_code == 200
    assert client.patch('/api/providers', json={'bindings': {'notifications': 'delivery'}}).status_code == 200
    integrations = [row for row in client.get('/api/integrations').json() if row['kind'] == 'notifications']
    assert len(integrations) == 1 and integrations[0]['id'] == 'delivery'
    install_executor(app, RecordingExecutor())
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    assert wait_job(client, job['id'])['status'] == 'succeeded'
    import time
    sent = app.state.state_dir / 'plugin-data/delivery/sent.jsonl'
    records = []
    for _ in range(100):
        records = [json.loads(row) for row in sent.read_text().splitlines()]
        if len(records) == 4:
            break
        time.sleep(.01)
    assert [row['event'] for row in records] == ['before_plan', 'after_approval', 'job_started', 'job_finished']
    assert records[-1]['job_id'] == job['id'] and records[-1]['status'] == 'succeeded'
    assert wait_idle(app)
    assert client.post('/api/extensions/delivery/disable').status_code == 200
    # Disabling an extension rebuilds the registry, so the review fixture that
    # this suite registered directly has to be installed again.
    install_executor(app, RecordingExecutor())
    second = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{second['id']}/approve").status_code == 200
    terminal = wait_job(client, second['id'])
    assert terminal['status'] == 'succeeded'
    assert any(row['phase'] == 'observers' and row['level'] == 'warning' for row in terminal['events'])
    assert not (app.state.state_dir / 'outbox/notifications.jsonl').exists()
    assert client.get('/api/providers').json()['active']['notifications']['available'] is False


def test_invalid_secret_configuration_does_not_take_down_other_capabilities(api):
    client, app = api
    (app.state.state_dir / 'config/secrets.json').write_text('{invalid-json')
    from backend.app import create_app
    restarted = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir, testing=True)
    with TestClient(restarted) as other:
        login(other, app.state.state_dir, 'admin')
        report = other.get('/api/providers').json()
        assert report['active']['secrets']['available'] is False
        assert report['active']['secrets']['status'] == 'unavailable'
        assert other.get('/api/jobs').status_code == 200
        assert other.get('/api/diagnostics').status_code == 200


def test_execution_source_drift_is_reported_unavailable_and_approval_denied(api):
    client, app = api
    package = install_extension(app, 'drifting', """
        class Engine:
            def execute(self, job, emit):
                return {'status': 'succeeded'}
            def rollback(self, job, emit):
                return {'status': 'rolled_back'}
        def register(ctx):
            ctx.register('execution', 'drifting', Engine())
    """, ['providers:execution'])
    assert client.post('/api/extensions/drifting/enable', json={'trusted': True}).status_code == 200
    assert client.patch('/api/providers', json={'bindings': {'execution': 'drifting'}}).status_code == 200
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    with (package / 'module.py').open('a') as stream:
        stream.write('# Source changed without reloading registered implementation\n')
    report = client.get('/api/providers').json()
    assert report['active']['execution']['available'] is False
    assert client.patch('/api/providers', json={'bindings': {'execution': 'drifting'}}).status_code == 422
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 503
    assert client.get(f"/api/jobs/{job['id']}").json()['status'] == 'pending_approval'
    assert not (app.state.state_dir / 'gitops').exists()
    assert client.get('/api/diagnostics').status_code == 200


def test_invalid_binding_schema_fails_capabilities_closed_without_disabling_auth_or_repair(api):
    client, app = api
    (app.state.state_dir / 'config/providers.json').write_text(json.dumps({'schema_version': True, 'bindings': {}}))
    from backend.app import create_app
    restarted = create_app(state_dir=app.state.state_dir, plugins_dir=app.state.plugins_dir, testing=True)
    from backend.providers import provider_provenance
    from test_core_jobs import RecordingExecutor
    other_name = 'recording'
    restarted.state.registry.register('execution', other_name, RecordingExecutor(), {'status': 'configured'})
    restarted.state.provider_loaded_sources[('execution', other_name)] = provider_provenance(restarted, 'execution', other_name)
    restarted.state.provider_bindings['execution'] = other_name
    with TestClient(restarted) as other:
        login(other, app.state.state_dir, 'admin')
        assert other.get('/api/jobs').status_code == 200
        assert other.get('/api/providers').json()['errors']
        assert other.get('/api/diagnostics').status_code == 503
        assert other.get('/api/infrastructure').status_code == 503
        job = other.post('/api/jobs', json={'graph': graph()}).json()
        assert other.post(f"/api/jobs/{job['id']}/approve").status_code == 503
        repaired = other.patch('/api/providers', json={'bindings': {'inventory': 'gns3'}})
        assert repaired.status_code == 200 and repaired.json()['errors'] == []
        assert other.get('/api/diagnostics').status_code == 200


def test_disabled_selected_execution_blocks_approval_and_rollback_but_not_job_views(api):
    client, app = api
    install_extension(app, 'removable', """
        class Engine:
            def execute(self, job, emit):
                return {'status': 'succeeded'}
            def rollback(self, job, emit):
                return {'status': 'rolled_back'}
        def register(ctx):
            ctx.register('execution', 'removable', Engine())
    """, ['providers:execution'])
    assert client.post('/api/extensions/removable/enable', json={'trusted': True}).status_code == 200
    assert client.patch('/api/providers', json={'bindings': {'execution': 'removable'}}).status_code == 200
    first = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{first['id']}/approve").status_code == 200
    assert wait_job(client, first['id'])['status'] == 'succeeded'
    assert wait_idle(app)
    assert client.post('/api/extensions/removable/disable').status_code == 200
    assert client.get('/api/jobs').status_code == 200
    assert client.get(f"/api/jobs/{first['id']}").json()['can_rollback'] is False
    assert client.post(f"/api/jobs/{first['id']}/rollback").status_code == 409
    second = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{second['id']}/approve").status_code == 503
    assert client.get(f"/api/jobs/{second['id']}").json()['status'] == 'pending_approval'
    assert client.get('/api/diagnostics').status_code == 200


def test_provider_binding_edits_require_admin_csrf_and_allowlisted_names(api):
    client, app = api
    expected = client.get('/api/providers').json()['bindings']
    for username in ('operator', 'viewer'):
        login(client, app.state.state_dir, username)
        assert client.get('/api/providers').status_code == 200
        assert client.patch('/api/providers', json={'bindings': {'inventory': 'gns3'}}).status_code == 403
    login(client, app.state.state_dir, 'admin')
    token = client.headers.pop('X-CSRF-Token')
    assert client.patch('/api/providers', json={'bindings': {'inventory': 'gns3'}}).status_code == 403
    client.headers['X-CSRF-Token'] = token
    for bindings in ({'unknown-kind': 'gns3'}, {'inventory': 'some.module:factory'},
                     {'inventory': 'missing'}, {'execution': '../default'}, {'inventory': 1}):
        assert client.patch('/api/providers', json={'bindings': bindings}).status_code == 422
        assert client.get('/api/providers').json()['bindings'] == expected
    assert client.patch('/api/providers', json={'bindings': {'inventory': 'gns3'}}).status_code == 200
    assert any(row['action'] == 'providers.updated' for row in client.get('/api/audit').json())
    client.post('/api/auth/logout')
    assert client.get('/api/providers').status_code == 401


def test_queued_provider_binding_and_extension_changes_block_and_worker_rechecks_source(api):
    client, app = api
    package = install_extension(app, 'queued-engine', """
        class Engine:
            def __init__(self, path):
                self.path = path
            def execute(self, job, emit):
                self.path.write_text('applied')
                return {'status': 'succeeded'}
            def rollback(self, job, emit):
                return {'status': 'rolled_back'}
        def register(ctx):
            ctx.register('execution', 'queued-engine', Engine(ctx.plugin_data_dir() / 'executed'))
    """, ['providers:execution', 'data:read-write'])
    assert client.post('/api/extensions/queued-engine/enable', json={'trusted': True}).status_code == 200
    assert client.patch('/api/providers', json={'bindings': {'execution': 'queued-engine'}}).status_code == 200
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    assert client.patch('/api/providers', json={'bindings': {'execution': 'ansible-workload-v1'}}).status_code == 409
    assert client.post('/api/extensions/queued-engine/disable').status_code == 409
    with (package / 'module.py').open('a') as stream:
        stream.write('# Unapproved implementation drift\n')
    app.state.worker.pause.set()
    assert wait_job(client, job['id'])['status'] == 'failed'
    assert not (app.state.state_dir / 'plugin-data/queued-engine/executed').exists()


def test_provider_changes_wait_for_worker_completion_observers_not_just_terminal_status(api):
    import threading
    client, app = api
    install_executor(app, RecordingExecutor())
    entered, release = threading.Event(), threading.Event()
    def finishing(payload):
        entered.set()
        release.wait(5)
    app.state.registry.add_hook('job_finished', finishing)
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    try:
        assert entered.wait(2)
        assert client.get(f"/api/jobs/{job['id']}").json()['status'] == 'succeeded'
        assert client.patch('/api/providers', json={'bindings': {'execution': 'ansible-workload-v1'}}).status_code == 409
    finally:
        release.set()
