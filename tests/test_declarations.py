"""Git-backed declarations: pinned revisions, digest refusals and plan execution."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.declarations import (DeclarationService, GitRepository, PlanApplier,
                                      PlanExecutionError, repository_status)
from backend.environment_state import EnvironmentStates
from backend.storage import Store
from test_postgresql_store import pg_file  # noqa: F401  fixture

PLAN = b'PK\x03\x04' + b'pinned plan bytes'
OTHER_PLAN = b'PK\x03\x04' + b'another revision plan'
BUNDLE = 'stacks/dev/vm-app/bundle.json'
DECLARATION = 'stacks/dev/vm-app/declaration.json'


def git_env():
    env = {key: os.environ[key] for key in ('PATH', 'HOME') if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_AUTHOR_NAME='fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    return env


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], env=git_env(),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def make_repository(parent):
    parent.mkdir(parents=True, exist_ok=True)
    repo = parent / 'repository'
    result = subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)],
                            env=git_env(), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return repo


def commit(repo, message='fixture'):
    git(repo, 'add', '--all')
    git(repo, 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD').strip()


def declaration(environment='dev', stack='vm-app', graph=None):
    return {'schema_version': 1, 'environment': environment, 'stack': stack,
            'graph': graph if graph is not None else {
                'name': stack, 'environment': environment,
                'nodes': [{'id': 'vm1', 'plugin_id': 'libvirt-vm',
                           'config': {'cpu': 1, 'memory_mb': 128}}]}}


def write_bundle(repo, *, plan=PLAN, environment='dev', stack='vm-app',
                 base_generation=0, document=None, mutate=None,
                 declaration_path=None, plan_path=None):
    folder = repo / 'stacks' / environment / stack
    folder.mkdir(parents=True, exist_ok=True)
    document = document if document is not None else declaration(environment, stack)
    raw_declaration = json.dumps(document, sort_keys=True).encode()
    (folder / 'declaration.json').write_bytes(raw_declaration)
    (folder / 'plan.tfplan').write_bytes(plan)
    bundle = {'schema_version': 1, 'engine': 'opentofu', 'base_generation': base_generation,
              'declaration': {'path': declaration_path or f'stacks/{environment}/{stack}/declaration.json',
                              'sha256': hashlib.sha256(raw_declaration).hexdigest()},
              'plan': {'path': plan_path or f'stacks/{environment}/{stack}/plan.tfplan',
                       'sha256': hashlib.sha256(plan).hexdigest()}}
    if mutate is not None:
        mutate(bundle)
    raw_bundle = json.dumps(bundle, sort_keys=True).encode()
    (folder / 'bundle.json').write_bytes(raw_bundle)
    return {'path': f'stacks/{environment}/{stack}/bundle.json',
            'sha256': hashlib.sha256(raw_bundle).hexdigest()}


class Recorder:
    """Replace the OpenTofu boundary only; the service path stays real."""

    def __init__(self):
        self.calls = []
        self.error = None
        self.resources = [
            {'address': 'libvirt_domain.vm', 'type': 'libvirt_domain', 'name': 'vm'},
            {'address': 'libvirt_volume.disk', 'type': 'libvirt_volume', 'name': 'disk'}]

    def read(self):
        return {'available': True}

    def __call__(self, prepared):
        self.calls.append(prepared)
        if self.error is not None:
            raise self.error
        return {'state': {'values': {'root_module': {'resources': self.resources}}},
                'resources': self.resources}


class World:
    def __init__(self, root):
        self.root = root
        self.repo = make_repository(root / 'git')
        self.store = Store(root / 'state')
        self.states = EnvironmentStates(self.store)
        self.applier = Recorder()
        self.service = DeclarationService(self.states, root / 'state',
                                          applier=self.applier,
                                          repository=GitRepository(self.repo))

    def close(self):
        self.store.conn.close()


@pytest.fixture
def world(tmp_path):
    value = World(tmp_path)
    yield value
    value.close()


def pin(world, *, plan=PLAN, base_generation=0, document=None, mutate=None,
        environment='dev', stack='vm-app'):
    bundle = write_bundle(world.repo, plan=plan, base_generation=base_generation,
                          document=document, mutate=mutate, environment=environment,
                          stack=stack)
    revision = commit(world.repo)
    return revision, bundle


def login(client, app, role='admin'):
    credentials = dict(line.split(':', 1) for line in
                       (app.state.state_dir / 'bootstrap-credentials.txt').read_text().splitlines())
    response = client.post('/api/auth/login', json={'username': role, 'password': credentials[role]})
    assert response.status_code == 200, response.text
    client.headers['x-csrf-token'] = response.json()['csrf_token']
    return response.json()['csrf_token']


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv('KUBERWEAVER_STATE_DIR', str(tmp_path / 'module-app'))
    from backend.app import create_app
    app = create_app(state_dir=tmp_path / 'web-state', testing=True)
    with TestClient(app) as client:
        login(client, app)
        yield app, client


def test_a_pinned_bundle_verifies_at_its_revision(world):
    revision, bundle = pin(world)
    changed = declaration(graph={'name': 'vm-app', 'environment': 'dev',
                                 'nodes': [{'id': 'vm2', 'plugin_id': 'libvirt-vm',
                                            'config': {'cpu': 2}}]})
    (world.repo / DECLARATION).write_text(json.dumps(changed))
    (world.repo / 'stacks/dev/vm-app/plan.tfplan').write_bytes(OTHER_PLAN)
    later = commit(world.repo)

    prepared = world.service.verify('dev', 'vm-app', revision, bundle['path'], bundle['sha256'])
    assert prepared['revision'] == revision
    assert prepared['revision'] != later
    assert prepared['plan']['sha256'] == hashlib.sha256(PLAN).hexdigest()
    assert prepared['plan_bytes'] == PLAN
    assert prepared['bundle'] == bundle
    assert prepared['declaration']['graph']['nodes'][0]['id'] == 'vm1'
    assert prepared['base_generation'] == 0
    assert prepared['identity'] == world.states.identity('dev', 'vm-app')


def test_the_working_tree_is_never_read(world):
    revision, bundle = pin(world)
    (world.repo / 'stacks/dev/vm-app/plan.tfplan').write_bytes(b'PK\x03\x04 uncommitted')
    (world.repo / DECLARATION).write_text(json.dumps(declaration()))
    assert git(world.repo, 'status', '--porcelain').strip()
    prepared = world.service.verify('dev', 'vm-app', revision, bundle['path'], bundle['sha256'])
    assert prepared['plan_bytes'] == PLAN


def test_execution_applies_the_pinned_plan_as_is(world):
    revision, bundle = pin(world)
    result = world.service.execute('dev', 'vm-app', revision, bundle['path'],
                                   bundle['sha256'], {'username': 'admin', 'role': 'admin'})
    assert result['executed'] is True and result['generation'] == 1
    assert result['plan_sha256'] == hashlib.sha256(PLAN).hexdigest()
    assert len(world.applier.calls) == 1
    call = world.applier.calls[0]
    assert call['plan_bytes'] == PLAN
    assert call['revision'] == revision and call['environment'] == 'dev'
    assert call['stack'] == 'vm-app'
    state = world.states.get('dev', 'vm-app')
    assert state['generation'] == 1 and state['job_id'] == call['execution_id']
    assert [row['address'] for row in state['resources']] == [
        'libvirt_domain.vm', 'libvirt_volume.disk']
    run = world.service.collection()['runs'][0]
    assert run['status'] == 'executed' and run['generation'] == 1
    assert run['plan_sha256'] == hashlib.sha256(PLAN).hexdigest()
    actions = [row['action'] for row in world.store.all('SELECT action FROM audit ORDER BY id')]
    assert actions.count('declaration.executed') == 1


def test_execution_uses_the_old_plan_after_a_newer_commit(world):
    revision, bundle = pin(world)
    write_bundle(world.repo, plan=OTHER_PLAN)
    commit(world.repo, 'newer plan')
    result = world.service.execute('dev', 'vm-app', revision, bundle['path'],
                                   bundle['sha256'], {'username': 'admin', 'role': 'admin'})
    assert result['executed'] is True
    assert world.applier.calls[0]['plan_bytes'] == PLAN


def test_a_bundle_digest_mismatch_is_refused(world):
    revision, bundle = pin(world)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, bundle['path'], 'f' * 64)
    assert error.value.status_code == 409
    assert 'bundle digest' in str(error.value.detail)
    assert world.applier.calls == []
    assert world.store.one('SELECT 1 AS present FROM declaration_run LIMIT 1') is None


def test_a_declaration_digest_mismatch_is_refused(world):
    def tamper(bundle):
        bundle['declaration']['sha256'] = 'a' * 64
    revision, bundle = pin(world, mutate=tamper)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, bundle['path'], bundle['sha256'])
    assert error.value.status_code == 409
    assert 'declaration digest' in str(error.value.detail)


def test_a_plan_digest_mismatch_is_refused(world):
    def tamper(bundle):
        bundle['plan']['sha256'] = 'b' * 64
    revision, bundle = pin(world, mutate=tamper)
    with pytest.raises(HTTPException) as error:
        world.service.execute('dev', 'vm-app', revision, bundle['path'],
                              bundle['sha256'], {'username': 'admin', 'role': 'admin'})
    assert error.value.status_code == 409
    assert 'plan digest' in str(error.value.detail)
    assert world.applier.calls == []
    assert world.store.one('SELECT 1 AS present FROM environment_state_claim LIMIT 1') is None


def test_a_plan_edited_after_the_bundle_is_refused(world):
    revision, bundle = pin(world)
    (world.repo / 'stacks/dev/vm-app/plan.tfplan').write_bytes(OTHER_PLAN)
    later = commit(world.repo, 'plans must not change under a pin')
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', later, bundle['path'], bundle['sha256'])
    assert error.value.status_code == 409
    assert 'plan digest' in str(error.value.detail)


@pytest.mark.parametrize('revision', ['a' * 8, 'main', 'HEAD~1', 'A' * 40])
def test_a_revision_that_is_not_a_full_commit_id_is_refused(world, revision):
    _, bundle = pin(world)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, bundle['path'], bundle['sha256'])
    assert error.value.status_code == 409


def test_an_unknown_commit_is_refused(world):
    _, bundle = pin(world)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', 'c' * 40, bundle['path'], bundle['sha256'])
    assert error.value.status_code == 409
    assert 'not a commit' in str(error.value.detail)


def test_a_symlinked_blob_is_refused(world):
    folder = world.repo / 'stacks/dev/vm-app'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'real.json').write_text('{}')
    (folder / 'link.json').symlink_to('real.json')
    bundle = {'schema_version': 1, 'engine': 'opentofu', 'base_generation': 0,
              'declaration': {'path': 'stacks/dev/vm-app/link.json',
                              'sha256': hashlib.sha256(b'real.json').hexdigest()},
              'plan': {'path': 'stacks/dev/vm-app/plan.tfplan',
                       'sha256': hashlib.sha256(PLAN).hexdigest()}}
    (folder / 'plan.tfplan').write_bytes(PLAN)
    raw = json.dumps(bundle, sort_keys=True).encode()
    (folder / 'bundle.json').write_bytes(raw)
    revision = commit(world.repo)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, BUNDLE,
                             hashlib.sha256(raw).hexdigest())
    assert error.value.status_code == 409
    assert 'regular file' in str(error.value.detail)


@pytest.mark.parametrize('path', ['/etc/passwd', '../outside.json', 'stacks/../bundle.json',
                                  'stacks/.hidden/bundle.json', 'stacks//bundle.json',
                                  'stacks/dev/vm-app/'])
def test_a_bundle_path_outside_the_repository_is_refused(world, path):
    revision, bundle = pin(world)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, path, bundle['sha256'])
    assert error.value.status_code == 409


def test_a_missing_path_is_refused(world):
    revision, bundle = pin(world)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, 'stacks/dev/vm-app/absent.json', bundle['sha256'])
    assert error.value.status_code == 409
    assert 'does not exist' in str(error.value.detail)


@pytest.mark.parametrize('mutate', [
    lambda bundle: bundle.update(extra=True),
    lambda bundle: bundle.pop('engine'),
    lambda bundle: bundle.update(schema_version=2),
    lambda bundle: bundle.update(engine='terraform'),
    lambda bundle: bundle.update(base_generation=-1),
    lambda bundle: bundle.update(base_generation='0'),
    lambda bundle: bundle['declaration'].update(extra='x'),
    lambda bundle: bundle['declaration'].update(path='../escaped.json'),
    lambda bundle: bundle['plan'].update(sha256='short'),
    lambda bundle: bundle['plan'].update(path='/absolute/plan'),
])
def test_an_invalid_bundle_is_refused(world, mutate):
    revision, bundle = pin(world, mutate=mutate)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, bundle['path'], bundle['sha256'])
    assert error.value.status_code == 409


@pytest.mark.parametrize('document', [
    dict(declaration(), extra=True),
    dict(declaration(), schema_version=2),
    dict(declaration(), environment='staging'),
    dict(declaration(), stack='vm-other'),
    {'schema_version': 1, 'environment': 'dev', 'stack': 'vm-app', 'graph': {}},
    dict(declaration(), graph={'name': 'vm-other', 'environment': 'dev',
                               'nodes': [{'id': 'vm1', 'plugin_id': 'libvirt-vm'}]}),
    dict(declaration(), graph={'name': 'vm-app', 'environment': 'staging',
                               'nodes': [{'id': 'vm1', 'plugin_id': 'libvirt-vm'}]}),
])
def test_an_invalid_declaration_is_refused(world, document):
    revision, bundle = pin(world, document=document)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, bundle['path'], bundle['sha256'])
    assert error.value.status_code == 409


def test_a_duplicate_json_field_is_refused(world):
    raw = (b'{"schema_version":1,"engine":"opentofu","base_generation":0,'
           b'"declaration":{"path":"stacks/dev/vm-app/declaration.json","sha256":"' + b'0' * 64 +
           b'"},"plan":{"path":"stacks/dev/vm-app/plan.tfplan","sha256":"' + b'0' * 64 +
           b'"},"engine":"opentofu"}')
    folder = world.repo / 'stacks/dev/vm-app'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'bundle.json').write_bytes(raw)
    revision = commit(world.repo)
    with pytest.raises(HTTPException) as error:
        world.service.verify('dev', 'vm-app', revision, BUNDLE,
                             hashlib.sha256(raw).hexdigest())
    assert error.value.status_code == 409
    assert 'not valid JSON' in str(error.value.detail)


def test_the_plan_artifact_must_be_a_saved_plan(world):
    revision, bundle = pin(world, plan=b'#!/bin/sh\nrm -rf /\n')
    with pytest.raises(HTTPException) as error:
        world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'],
                              {'username': 'admin', 'role': 'admin'})
    assert error.value.status_code == 409
    assert 'saved OpenTofu plan' in str(error.value.detail)
    assert world.applier.calls == []


def test_execution_requires_the_declared_base_generation(world):
    revision, bundle = pin(world)
    world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'],
                          {'username': 'admin', 'role': 'admin'})
    with pytest.raises(HTTPException) as error:
        world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'],
                              {'username': 'admin', 'role': 'admin'})
    assert error.value.status_code == 409
    assert 'generation changed' in str(error.value.detail)
    assert len(world.applier.calls) == 1


def test_execution_refuses_a_held_lease(world):
    revision, bundle = pin(world)
    lease = world.states.acquire('dev', 'vm-app', 'a' * 32)
    with pytest.raises(HTTPException) as error:
        world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'],
                              {'username': 'admin', 'role': 'admin'})
    assert error.value.status_code == 409
    assert world.applier.calls == []
    assert world.states.release(lease) is True


@pytest.mark.parametrize('actor', [{}, {'username': ''},
                                   {'username': 'agent:x', 'role': 'admin',
                                    'principal_type': 'agent'}])
def test_execution_requires_a_human_administrator(world, actor):
    revision, bundle = pin(world)
    with pytest.raises(HTTPException) as error:
        world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'], actor)
    assert error.value.status_code == 403
    assert world.applier.calls == []


def test_verification_writes_nothing(world):
    revision, bundle = pin(world)
    world.service.verify('dev', 'vm-app', revision, bundle['path'], bundle['sha256'])
    assert world.store.one('SELECT 1 AS present FROM declaration_run LIMIT 1') is None
    assert world.store.one('SELECT 1 AS present FROM environment_state LIMIT 1') is None
    assert world.store.one('SELECT 1 AS present FROM environment_state_claim LIMIT 1') is None


def test_an_unavailable_applier_refuses_before_any_claim(world):
    revision, bundle = pin(world)
    world.service.applier = PlanApplier(world.root / 'state', binary=world.root / 'missing-tofu')
    with pytest.raises(HTTPException) as error:
        world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'],
                              {'username': 'admin', 'role': 'admin'})
    assert error.value.status_code == 503
    assert world.store.one('SELECT 1 AS present FROM environment_state_claim LIMIT 1') is None
    assert world.store.one('SELECT 1 AS present FROM declaration_run LIMIT 1') is None


def config(state_dir, value):
    folder = Path(state_dir) / 'config'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'declarations.json'
    path.write_text(json.dumps(value))
    return path


@pytest.mark.parametrize('value,status', [
    ({'schema_version': 1, 'enabled': True}, 'configuration_error'),
    ({'schema_version': 1, 'enabled': True, 'repository': 'relative/repo'}, 'configuration_error'),
    ({'schema_version': 1, 'enabled': True, 'repository': '/tmp/repo', 'extra': 1}, 'configuration_error'),
    ({'schema_version': 2, 'enabled': False}, 'configuration_error'),
    ({'schema_version': 1, 'enabled': 'yes'}, 'configuration_error'),
    ({'schema_version': 1, 'enabled': True, 'repository': '/nonexistent/declarations'}, 'configured'),
])
def test_the_repository_configuration_is_reported(tmp_path, value, status):
    assert repository_status(tmp_path) == {'status': 'disabled'}
    config(tmp_path, value)
    assert repository_status(tmp_path) == {'status': status}


def test_a_symlinked_or_malformed_configuration_is_refused(tmp_path):
    path = config(tmp_path, {'schema_version': 1, 'enabled': True,
                             'repository': str(tmp_path / 'repo')})
    path.unlink()
    path.symlink_to(tmp_path / 'elsewhere.json')
    assert repository_status(tmp_path) == {'status': 'configuration_error'}
    path.unlink()
    path.write_text('{not json')
    assert repository_status(tmp_path) == {'status': 'configuration_error'}


def test_a_configured_repository_is_read_end_to_end(tmp_path):
    root = tmp_path / 'configured'
    repo = make_repository(root)
    bundle = write_bundle(repo)
    revision = commit(repo)
    store = Store(root / 'state')
    try:
        service = DeclarationService(EnvironmentStates(store), root / 'state',
                                     applier=Recorder())
        config(root / 'state', {'schema_version': 1, 'enabled': True,
                                'repository': str(repo)})
        assert service.collection()['repository'] == {'status': 'configured'}
        prepared = service.verify('dev', 'vm-app', revision, bundle['path'], bundle['sha256'])
        assert prepared['plan_bytes'] == PLAN
        assert str(repo) not in json.dumps(service.collection())
    finally:
        store.conn.close()


def test_a_bare_repository_is_read_from_its_object_database(world):
    revision, _ = pin(world)
    bare = world.root / 'bare.git'
    result = subprocess.run(['git', 'clone', '-q', '--bare', str(world.repo), str(bare)],
                            env=git_env(), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    reader = GitRepository(bare)
    assert reader.commit(revision) == revision
    assert reader.blob(revision, 'stacks/dev/vm-app/plan.tfplan', 1024) == PLAN


def test_an_unconfigured_repository_refuses_verification(tmp_path):
    store = Store(tmp_path / 'state')
    try:
        service = DeclarationService(EnvironmentStates(store), tmp_path / 'state')
        with pytest.raises(HTTPException) as error:
            service.verify('dev', 'vm-app', 'a' * 40, BUNDLE, 'b' * 64)
        assert error.value.status_code == 503
        assert service.collection()['repository'] == {'status': 'disabled'}
    finally:
        store.conn.close()


class TofuBoundary:
    """Only the process boundary is fake; staging, modes and digests are real."""

    def __init__(self):
        self.calls = []
        self.folder = None
        self.plan_at_apply = None
        self.extra = None
        self.failure = None
        self.state = {'values': {'root_module': {'resources': [
            {'address': 'libvirt_domain.vm', 'type': 'libvirt_domain', 'name': 'vm',
             'mode': 'managed'},
            {'address': 'libvirt_volume.disk', 'type': 'libvirt_volume', 'name': 'disk',
             'mode': 'managed'}]}}}

    def __call__(self, argv, **kwargs):
        from types import SimpleNamespace
        folder = Path(kwargs['cwd'])
        self.calls.append(list(argv))
        if 'apply' in argv:
            self.folder = folder
            self.plan_at_apply = (folder / 'declaration.tfplan').read_bytes()
            self.staged = sorted(path.name for path in folder.iterdir())
            (folder / 'terraform.tfstate').write_text(json.dumps(self.state))
            if self.extra:
                (folder / self.extra).write_text('unexpected')
            if self.failure:
                return SimpleNamespace(returncode=1, stdout='SECRET provider error',
                                       stderr='SECRET provider error')
        if 'show' in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.state), stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')


def test_the_plan_applier_stages_and_applies_one_exact_file(world):
    revision, bundle = pin(world)
    binary = world.root / 'fake-tofu'
    binary.write_text('#!/bin/sh\nexit 0\n')
    boundary = TofuBoundary()
    world.service.applier = PlanApplier(world.root / 'state', binary=binary, runner=boundary)
    result = world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'],
                                   {'username': 'admin', 'role': 'admin'})
    assert result['executed'] is True and result['generation'] == 1
    assert len(boundary.calls) == 2
    apply_call = boundary.calls[0]
    assert apply_call[1:] == ['apply', '-input=false', '-no-color', '-lock=true',
                              '-lock-timeout=5s', 'declaration.tfplan']
    assert 'plan' not in apply_call[1:]
    assert not any(token.startswith('-out=') for token in apply_call[1:])
    assert boundary.plan_at_apply == PLAN
    assert boundary.staged == ['cli.tfrc', 'declaration.tfplan']
    folder = boundary.folder
    assert folder is not None and folder.parent == world.root / 'state' / 'declarations'
    assert folder.stat().st_mode & 0o777 == 0o700
    assert (folder / 'declaration.tfplan').stat().st_mode & 0o777 == 0o600
    assert (folder / 'declaration.tfplan').read_bytes() == PLAN
    state = world.states.get('dev', 'vm-app')
    assert state['generation'] == 1
    assert [row['address'] for row in state['resources']] == [
        'libvirt_domain.vm', 'libvirt_volume.disk']


def test_the_plan_applier_refuses_an_unverified_artifact_before_staging(world):
    binary = world.root / 'fake-tofu'
    binary.write_text('#!/bin/sh\nexit 0\n')
    applier = PlanApplier(world.root / 'state', binary=binary, runner=TofuBoundary())
    with pytest.raises(PlanExecutionError):
        applier({'execution_id': 'a' * 32, 'plan_bytes': PLAN,
                 'plan_sha256': 'b' * 64})
    assert not (world.root / 'state' / 'declarations').exists()


def test_the_plan_applier_refuses_an_unexpected_artifact(world):
    revision, bundle = pin(world)
    binary = world.root / 'fake-tofu'
    binary.write_text('#!/bin/sh\nexit 0\n')
    boundary = TofuBoundary()
    boundary.extra = 'injected.sh'
    world.service.applier = PlanApplier(world.root / 'state', binary=binary, runner=boundary)
    with pytest.raises(HTTPException) as error:
        world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'],
                              {'username': 'admin', 'role': 'admin'})
    assert error.value.status_code == 502
    assert world.states.get('dev', 'vm-app')['generation'] == 0
    assert world.states.get('dev', 'vm-app')['lease'] is not None
    run = world.service.collection()['runs'][0]
    assert run['status'] == 'failed' and run['requires_manual_review'] is True
    assert run['reason'] == 'execution_failed'


def test_a_failed_apply_keeps_the_claim_and_records_review(world):
    revision, bundle = pin(world)
    world.applier.error = RuntimeError('SECRET provider failure')
    with pytest.raises(HTTPException) as error:
        world.service.execute('dev', 'vm-app', revision, bundle['path'], bundle['sha256'],
                              {'username': 'admin', 'role': 'admin'})
    assert error.value.status_code == 502
    state = world.states.get('dev', 'vm-app')
    assert state['generation'] == 0 and state['lease'] is not None
    run = world.service.collection()['runs'][0]
    assert run['status'] == 'failed' and run['requires_manual_review'] is True
    assert 'SECRET' not in json.dumps(world.service.collection())


def test_the_kernel_wires_declarations_and_their_routes(web):
    app, client = web
    assert isinstance(app.state.declarations, DeclarationService)
    assert app.state._declarations_api_installed is True
    paths = app.openapi()['paths']
    assert paths['/api/declarations']['get']
    assert paths['/api/declarations/verify']['post']
    assert paths['/api/declarations/execute']['post']


def test_the_routes_require_a_human_administrator(web, tmp_path):
    app, client = web
    body = {'environment': 'dev', 'stack': 'vm-app', 'revision': 'a' * 40,
            'bundle_path': BUNDLE, 'bundle_sha256': 'b' * 64}
    for role in ('operator', 'viewer'):
        login(client, app, role)
        assert client.post('/api/declarations/verify', json=body).status_code == 403
        assert client.post('/api/declarations/execute', json=body).status_code == 403
    login(client, app, 'viewer')
    assert client.get('/api/declarations').status_code == 200
    world = World(tmp_path / 'web-world')
    try:
        revision, bundle = pin(world)
        body.update(revision=revision, bundle_sha256=bundle['sha256'])
        login(client, app)
        app.state.declarations.repository = GitRepository(world.repo)
        app.state.declarations.applier = world.applier
        response = client.post('/api/declarations/verify', json=body)
        assert response.status_code == 200, response.text
        assert 'plan_bytes' not in response.json()
        assert str(world.repo) not in response.text
        assert client.post('/api/declarations/execute', json=body).status_code == 200
    finally:
        world.close()


def test_the_routes_require_a_session_and_a_csrf_token(web):
    app, client = web
    body = {'environment': 'dev', 'stack': 'vm-app', 'revision': 'a' * 40,
            'bundle_path': BUNDLE, 'bundle_sha256': 'b' * 64}
    token = client.headers.pop('x-csrf-token')
    assert client.post('/api/declarations/verify', json=body).status_code == 403
    client.headers['x-csrf-token'] = token
    client.cookies.clear()
    assert client.post('/api/declarations/verify', json=body).status_code == 401
    assert client.get('/api/declarations').status_code == 401


def test_agent_tokens_have_no_declaration_scope(web):
    app, client = web
    issued = client.post('/api/agent-tokens', json={
        'label': 'declaration-reader', 'role': 'viewer', 'scopes': ['read:infrastructure'],
        'expires_in_seconds': 3600})
    assert issued.status_code == 201, issued.text
    headers = {'authorization': 'Bearer ' + issued.json()['token']}
    client.cookies.clear()
    client.headers.pop('x-csrf-token', None)
    assert client.get('/api/declarations', headers=headers).status_code == 403
    body = {'environment': 'dev', 'stack': 'vm-app', 'revision': 'a' * 40,
            'bundle_path': BUNDLE, 'bundle_sha256': 'b' * 64}
    assert client.post('/api/declarations/verify', json=body, headers=headers).status_code == 403


def test_postgresql_registers_the_declaration_run_table(tmp_path, pg_file):
    world = World(tmp_path / 'pg-world')
    try:
        assert world.store.backend == 'postgresql'
        revision, bundle = pin(world)
        result = world.service.execute('dev', 'vm-app', revision, bundle['path'],
                                       bundle['sha256'], {'username': 'admin', 'role': 'admin'})
        assert result['generation'] == 1
        row = world.store.one('SELECT body FROM declaration_run')
        assert row is not None and json.loads(row['body'])['status'] == 'executed'
        assert world.states.get('dev', 'vm-app')['generation'] == 1
    finally:
        world.close()
