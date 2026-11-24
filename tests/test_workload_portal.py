"""A workload brick through the portal: prepared, approved, executed, refused.

Every playbook below is a stub runner: no SSH, no guest and no remote command is
reached. The target is the deployment evidence a succeeded provisioning job
recorded, exactly as the provider resolves it in production.
"""
from contextlib import contextmanager
import json
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_core_auth import bootstrap

UUID = '6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b'
KEY_ID = 'b' * 32
CONNECTION_ID = 'c' * 32
ADDRESS = '192.168.10.62'

MANIFEST = {
    'id': 'web-nginx',
    'name': 'NGINX on a guest',
    'version': '1.0.0',
    'description': 'Stub workload brick driven by a stub playbook.',
    'kind': 'workload',
    'capability': 'live',
    'engine': 'ansible',
    'executor': 'ansible-workload-v1',
    'execution_provider': 'ansible-workload-v1',
    'requires': 'compute.vm',
    'data_class': 'config',
    'implementation': {'role': 'geerlingguy.nginx', 'version': '3.3.1', 'license': 'MIT'},
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'required': ['server_name', 'target_uuid', 'ssh_key_id'],
        'properties': {
            'server_name': {'type': 'string', 'pattern': '^[a-z0-9]([a-z0-9.-]{0,251})$'},
            'target_uuid': {'type': 'string',
                            'pattern': '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$'},
            'ssh_key_id': {'type': 'string', 'pattern': '^[0-9a-f]{32}$'},
            'listen_port': {'type': 'integer', 'minimum': 1, 'maximum': 65535, 'default': 80},
        },
    },
    'defaults': {'listen_port': 80},
    'lifecycle': {'snapshot': 'iac/snapshot.yml', 'apply': 'iac/apply.yml',
                  'verify': 'iac/verify.yml', 'rollback': 'iac/rollback.yml',
                  'remove': 'iac/remove.yml'},
    'verify': {'assertions': [{'id': 'service-active'}, {'id': 'http-200'}]},
}


def write_brick(plugins, manifest=None):
    brick = plugins / 'web-nginx'
    (brick / 'iac').mkdir(parents=True, exist_ok=True)
    (brick / 'manifest.yaml').write_text(json.dumps(manifest or MANIFEST))
    for name in ('snapshot', 'apply', 'verify', 'rollback', 'remove'):
        (brick / 'iac' / f'{name}.yml').write_text('---\n- hosts: all\n  tasks: []\n')
    (brick / 'roles.lock.json').write_text(json.dumps(
        {'geerlingguy.nginx': {'version': '3.3.1', 'sha256': 'a' * 64, 'license': 'MIT'}}))
    return brick


def graph(**config):
    value = {'server_name': 'app.lab', 'target_uuid': UUID, 'ssh_key_id': KEY_ID}
    value.update(config)
    return {'name': 'Workload lab', 'environment': 'dev',
            'nodes': [{'id': 'node-1', 'plugin_id': 'web-nginx', 'config': value}],
            'edges': [], 'strategy': {'canary': 1, 'batch_size': 1}, 'failure_mode': 'none'}


class StubKeys:
    """Local stand-in for the managed key registry; no key material leaves tmp."""

    material = b'stub-openssh-key-material-not-a-real-key'

    def __init__(self):
        self.reads = []

    def binding(self, identity):
        return {'id': identity, 'fingerprint': 'SHA256:workloadstub', 'revision': 1,
                'public_key': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIStub stub'}

    @contextmanager
    def private_key(self, identity):
        self.reads.append(identity)
        yield SimpleNamespace(bytes=lambda: self.material)


class Playbook:
    """Stub ansible-playbook: records each action and writes the brick result file."""

    def __init__(self):
        self.calls = []
        self.fail = None

    def __call__(self, argv, **kwargs):
        action = Path(argv[3]).stem
        self.calls.append(action)
        variables = json.loads(Path(argv[5][1:]).read_text())
        report = {'snapshot': {'site_present': False},
                  'apply': {'changed': 3}}.get(action, {'assertions': {'service-active': True,
                                                                      'http-200': True}})
        Path(variables['kw_result_path']).write_text(json.dumps(report))
        code = 2 if self.fail == action else 0
        return SimpleNamespace(returncode=code, stdout='changed=3' if action == 'apply' else 'ok=2',
                               stderr='stub failure' if code else '')


def record_evidence(app, *, uuid=UUID, address=ADDRESS, environment='dev', job_id='deploy-1',
                    name='kw-lab-01', power='poweredOn'):
    app.state.store.put('jobs', job_id, {
        'id': job_id, 'name': 'vm', 'status': 'succeeded', 'operation': 'execute',
        'environment': environment, 'approved_by': 'admin', 'approved_at': '2026-11-17T09:01:00+00:00',
        'created_at': '2026-11-17T09:00:00+00:00', 'finished_at': '2026-11-17T09:05:00+00:00',
        'graph': {'name': 'vm', 'environment': environment, 'edges': [], 'nodes': [
            {'id': 'vm', 'plugin_id': 'vmware-vm', 'config': {'connection_id': CONNECTION_ID}}]},
        'result': {'status': 'succeeded', 'evidence': [
            {'uuid': uuid, 'guest_ip': address, 'power_state': power, 'name': name,
             'cpu': 2, 'memory_mb': 1024}]},
    })


@pytest.fixture
def portal(tmp_path):
    from backend.app import create_app
    plugins = tmp_path / 'plugins'
    write_brick(plugins)
    app = create_app(state_dir=tmp_path / 'data', plugins_dir=plugins, testing=True)
    with TestClient(app) as client:
        credentials = bootstrap(tmp_path / 'data')
        response = client.post('/api/auth/login',
                               json={'username': 'admin', 'password': credentials['admin']})
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        provider = app.state.registry.get('execution', 'ansible-workload-v1')
        provider.playbook = Path('/bin/true')
        provider.ssh_keys = app.state.ssh_keys = StubKeys()
        provider.inspector = lambda connection_id, uuid: {'uuid': uuid, 'name': 'kw-lab-01',
                                                          'power_state': 'poweredOn'}
        provider.runner = Playbook()
        yield SimpleNamespace(client=client, app=app, provider=provider, plugins=plugins,
                              state=tmp_path / 'data')


def wait_status(client, identity, statuses):
    result = None
    for _ in range(400):
        result = client.get('/api/jobs/' + identity).json()
        if result['status'] in statuses:
            return result
        time.sleep(.01)
    raise AssertionError(result)


def submit(client):
    response = client.post('/api/jobs', json={'graph': graph()})
    assert response.status_code == 201, response.text
    return response.json()


def prepared(portal):
    job = submit(portal.client)
    ready = wait_status(portal.client, job['id'], {'pending_approval', 'failed'})
    return job, ready


def test_a_workload_brick_is_prepared_approved_and_executed(portal):
    record_evidence(portal.app)
    job, ready = prepared(portal)
    assert ready['status'] == 'pending_approval', ready
    plan = ready['prepared_plan']
    assert set(plan) == {'plan_digest', 'actions', 'summary', 'bindings'}
    assert plan['actions'] == {'web-nginx': ['snapshot', 'apply', 'verify']}
    assert plan['summary']['role'] == 'geerlingguy.nginx'
    assert plan['summary']['role_version'] == '3.3.1'
    assert plan['summary']['address'] == ADDRESS
    assert plan['summary']['data_class'] == 'config'
    assert plan['bindings']['target_job'] == 'deploy-1'
    assert plan['bindings']['ssh_key_id'] == KEY_ID
    assert plan['bindings']['evidence_digest']
    assert 'BEGIN OPENSSH PRIVATE KEY' not in json.dumps(ready)
    assert portal.provider.runner.calls == []

    assert portal.client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    done = wait_status(portal.client, job['id'], {'succeeded', 'failed', 'rollback_failed'})
    assert done['status'] == 'succeeded', done
    assert portal.provider.runner.calls == ['snapshot', 'apply', 'verify']
    evidence = done['result']['evidence'][0]
    assert evidence['assertions'] == {'service-active': True, 'http-200': True}
    assert evidence['address'] == ADDRESS and evidence['changed'] == 3
    assert done['can_rollback'] is False
    assert 'BEGIN OPENSSH PRIVATE KEY' not in json.dumps(done)

    folder = portal.state / 'workloads' / job['id']
    assert stat.S_IMODE((folder / 'review.json').stat().st_mode) == 0o600
    assert stat.S_IMODE((folder / 'evidence.json').stat().st_mode) == 0o600
    assert not list((portal.state / 'workloads' / 'keys').glob('*'))
    artifact = portal.app.state.store.get('jobs', job['id'])
    assert artifact['approved_prepared_plan'] == artifact['prepared_plan_hash']


def test_unpinned_role_is_refused_before_approval(portal):
    manifest = json.loads(json.dumps(MANIFEST))
    manifest['implementation'] = {'role': 'geerlingguy.nginx'}
    write_brick(portal.plugins, manifest)
    record_evidence(portal.app)
    job = submit(portal.client)
    failed = wait_status(portal.client, job['id'], {'pending_approval', 'failed'})
    assert failed['status'] == 'failed'
    assert failed['requires_manual_review'] is True
    assert failed.get('prepared_plan') is None
    assert portal.client.post(f"/api/jobs/{job['id']}/approve").status_code == 409
    assert portal.provider.runner.calls == []


def test_installed_role_drift_is_refused_before_approval(portal):
    record_evidence(portal.app)
    (portal.plugins / 'web-nginx' / 'roles.lock.json').write_text(json.dumps(
        {'geerlingguy.nginx': {'version': '3.0.0'}}))
    job = submit(portal.client)
    failed = wait_status(portal.client, job['id'], {'pending_approval', 'failed'})
    assert failed['status'] == 'failed' and failed['requires_manual_review'] is True
    assert portal.provider.runner.calls == []


def test_target_without_recorded_evidence_is_refused(portal):
    job = submit(portal.client)
    failed = wait_status(portal.client, job['id'], {'pending_approval', 'failed'})
    assert failed['status'] == 'failed'
    assert 'prepared_plan' not in failed or failed.get('prepared_plan') is None


def test_ambiguous_deployment_evidence_is_refused(portal):
    record_evidence(portal.app, address=ADDRESS, job_id='deploy-1')
    record_evidence(portal.app, address='192.168.10.63', job_id='deploy-2')
    job = submit(portal.client)
    failed = wait_status(portal.client, job['id'], {'pending_approval', 'failed'})
    assert failed['status'] == 'failed'


def test_live_target_identity_change_is_refused(portal):
    record_evidence(portal.app)
    portal.provider.inspector = lambda connection_id, uuid: {
        'uuid': '11111111-2222-3333-4444-555555555555', 'name': 'kw-lab-01',
        'power_state': 'poweredOn'}
    job = submit(portal.client)
    failed = wait_status(portal.client, job['id'], {'pending_approval', 'failed'})
    assert failed['status'] == 'failed'


def test_a_failed_apply_rolls_back_and_reports_it(portal):
    record_evidence(portal.app)
    job, ready = prepared(portal)
    assert ready['status'] == 'pending_approval'
    portal.provider.runner.fail = 'apply'
    assert portal.client.post(f"/api/jobs/{job['id']}/approve").status_code == 200
    done = wait_status(portal.client, job['id'], {'succeeded', 'failed', 'rollback_failed'})
    assert done['status'] == 'failed', done
    assert portal.provider.runner.calls == ['snapshot', 'apply', 'rollback']
    assert 'rollback completed' in done['error']
    assert done['requires_manual_review'] is False


def test_workload_scope_is_enforced_at_submission(portal):
    other = json.loads(json.dumps(MANIFEST))
    other.update(id='second-brick')
    write_brick(portal.plugins, other)
    candidate = graph()
    second = json.loads(json.dumps(candidate['nodes'][0]))
    second.update(id='node-2', plugin_id='second-brick')
    candidate['nodes'].append(second)
    response = portal.client.post('/api/plans/validate', json=candidate)
    assert response.status_code == 200
    assert 'workload_scope' in {row['code'] for row in response.json()['violations']}
    assert portal.client.post('/api/jobs', json={'graph': candidate}).status_code == 422


def test_a_container_node_cannot_share_a_workload_job(portal):
    container = {'id': 'sample-service', 'name': 'Sample', 'version': '1.0.0', 'capability': 'live',
                 'engine': 'ansible', 'defaults': {'targets': ['app'], 'port': 18090},
                 'schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                     'targets': {'type': 'array', 'items': {'type': 'string'}},
                     'port': {'type': 'integer'}}}}
    (portal.plugins / 'sample-service').mkdir()
    (portal.plugins / 'sample-service' / 'manifest.yaml').write_text(json.dumps(container))
    candidate = graph()
    candidate['nodes'].append({'id': 'node-2', 'plugin_id': 'sample-service',
                               'config': {'targets': ['app'], 'port': 18090}})
    assert portal.client.post('/api/jobs', json={'graph': candidate}).status_code == 422
