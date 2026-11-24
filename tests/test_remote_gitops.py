"""Remote publishing uses only explicit disposable test destinations."""
import importlib
import importlib.util
import json
import subprocess
import os
import shutil
import sys
import time

import pytest
from test_core_graph import api, graph


def git(repo, *args):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Test',
                           '-c', 'user.email=test@invalid', '-C', str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def configure(state, remote, **extra):
    path = state / 'config' / 'gitops.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'enabled': True, 'remote_url': str(remote),
                                'allow_local_test_path': True, 'branch': 'approved', **extra}))


def test_missing_config_is_disabled_without_git(tmp_path):
    assert importlib.util.find_spec('backend.gitops') is not None
    remote = importlib.import_module('backend.gitops')
    assert remote.publish(tmp_path, 'a' * 40) == {
        'status': 'disabled', 'required_before_execution': True, 'verified_commit': None}
    assert remote.safe_status(tmp_path)['status'] == 'disabled'


def test_approval_publishes_exact_local_commit(api, tmp_path):
    from backend.gitops import safe_status
    client, app = api
    remote = tmp_path / 'destination.git'
    git(tmp_path, 'init', '--bare', str(remote))
    configure(app.state.state_dir, remote)
    app.state.worker.pause.clear()
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    response = client.post(f"/api/jobs/{job['id']}/approve")
    assert response.status_code == 200, response.text
    approved = response.json()
    assert approved.get('remote_gitops', {}).get('status') == 'verified'
    sha = approved['git_commit']
    assert git(remote, 'rev-parse', 'refs/heads/approved') == sha
    artifact = json.loads(git(remote, 'show', sha + ':jobs/' + job['id'] + '.json'))
    assert artifact['graph'] == job['graph']
    assert approved['remote_gitops']['verified_commit'] == sha
    assert safe_status(app.state.state_dir)['status'] == 'configured'
    assert str(remote) not in json.dumps(approved)


@pytest.mark.parametrize('change', [
    {'remote_url': 'https://user:secret@example.org/private.git'},
    {'remote_url': 'ext::sh -c evil'}, {'remote_url': 'git@host:repo;evil'},
    {'remote_url': '/explicit/but/not/allowed', 'allow_local_test_path': False},
    {'remote_url': 'ssh://user:password@host/repo'},
    {'remote_url': 'ssh://host/repo?token=secret'},
    {'branch': '-evil'}, {'branch': 'main:other'}, {'branch': '../escape'},
    {'branch': 'main.lock'}, {'branch': 'a..b'}, {'branch': 'a@{b'},
    {'enabled': 'true'}, {'required_before_execution': 'false'},
    {'unexpected': 'secret'}, {'enabled': False, 'remote_url': 'https://bad'},
])
def test_invalid_configuration_fails_closed(tmp_path, change):
    from backend.gitops import safe_status, publish
    configure(tmp_path, tmp_path / 'remote.git', **change)
    expected = {'status': 'configuration_error', 'required_before_execution': True, 'verified_commit': None}
    assert safe_status(tmp_path) == expected
    assert publish(tmp_path, 'a' * 40) == expected


@pytest.mark.parametrize('url', ['ssh://git@gitops.example.org/team/plans.git', 'git@gitops.example.org:team/plans.git'])
def test_approved_ssh_configuration_is_safe_to_present(tmp_path, url):
    from backend.gitops import safe_status
    configure(tmp_path, url, allow_local_test_path=False)
    assert safe_status(tmp_path) == {'status': 'configured', 'required_before_execution': True, 'verified_commit': None}


@pytest.mark.parametrize('required', [True, False])
def test_divergent_remote_never_force_pushes_and_reports_failure(api, tmp_path, required):
    client, app = api
    remote = tmp_path / 'diverged.git'
    git(tmp_path, 'init', '--bare', str(remote))
    seed = tmp_path / 'seed'
    seed.mkdir()
    git(seed, 'init', '--quiet')
    git(seed, 'commit', '--allow-empty', '-m', 'Independent history')
    foreign = git(seed, 'rev-parse', 'HEAD')
    git(seed, 'push', str(remote), 'HEAD:refs/heads/approved')
    configure(app.state.state_dir, remote, required_before_execution=required)
    enqueued = []
    app.state.worker.enqueue = enqueued.append
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    response = client.post(f"/api/jobs/{job['id']}/approve")
    assert response.status_code == (503 if required else 200), response.text
    stored = client.get('/api/jobs/' + job['id']).json()
    assert stored['status'] == ('pending_approval' if required else 'queued')
    assert enqueued == ([] if required else [job['id']])
    assert stored['remote_gitops']['status'] == 'publish_failed'
    assert stored['remote_gitops']['verified_commit'] is None
    assert git(remote, 'rev-parse', 'refs/heads/approved') == foreign
    assert stored['git_commit']
    assert any(event['phase'] == 'gitops' and event['level'] == 'warning' for event in stored['events'])
    assert str(remote) not in json.dumps(stored) + response.text
    if required:
        # A later explicit local-only approval must not retain stale failure evidence.
        (app.state.state_dir / 'config' / 'gitops.json').write_text('{"enabled": false}')
        retried = client.post(f"/api/jobs/{job['id']}/approve")
        assert retried.status_code == 200, retried.text
        assert 'remote_gitops' not in retried.json()
        assert enqueued == [job['id']]


def install_git_boundary(tmp_path, monkeypatch, behavior):
    """Exercise real subprocess supervision while changing only one Git boundary."""
    real_git = shutil.which('git')
    directory = tmp_path / 'bin'
    directory.mkdir()
    wrapper = directory / 'git'
    wrapper.write_text(f'#!{sys.executable}\nimport os,sys,time\n'
                       + behavior + '\nos.execv(' + repr(real_git) + ', [' + repr(real_git) + '] + sys.argv[1:])\n')
    wrapper.chmod(0o700)
    monkeypatch.setenv('PATH', str(directory) + os.pathsep + os.environ['PATH'])


@pytest.mark.parametrize('mode,expected', [('timeout', 'timeout'), ('output', 'output_limit'), ('readback', 'verification_failed')])
def test_remote_process_failures_never_queue_or_leak(api, tmp_path, monkeypatch, mode, expected):
    import backend.gitops as module
    client, app = api
    remote = tmp_path / 'boundary.git'
    git(tmp_path, 'init', '--bare', str(remote))
    configure(app.state.state_dir, remote)
    behavior = {
        'timeout': "if 'push' in sys.argv: time.sleep(30)",
        'output': "if 'push' in sys.argv:\n print('SECRET_REMOTE_ERROR' * 10000, flush=True)\n sys.exit(1)",
        'readback': "if 'ls-remote' in sys.argv:\n print('0' * 40 + '\\trefs/heads/approved')\n sys.exit(0)",
    }[mode]
    install_git_boundary(tmp_path, monkeypatch, behavior)
    if mode == 'timeout':
        monkeypatch.setattr(module, 'TIMEOUT_SECONDS', 0.5)
    enqueued = []
    app.state.worker.enqueue = enqueued.append
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    started = time.monotonic()
    response = client.post(f"/api/jobs/{job['id']}/approve")
    assert response.status_code == 503, response.text
    assert time.monotonic() - started < 5
    stored = client.get('/api/jobs/' + job['id']).json()
    assert stored['status'] == 'pending_approval'
    assert stored['remote_gitops']['status'] == expected
    assert not enqueued
    assert 'SECRET_REMOTE_ERROR' not in response.text + json.dumps(stored)


def test_explicitly_disabled_preserves_local_artifact_and_job_shape(api, tmp_path):
    client, app = api
    config = app.state.state_dir / 'config' / 'gitops.json'
    config.write_text('{"enabled": false}')
    enqueued = []
    app.state.worker.enqueue = enqueued.append
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    approved = client.post(f"/api/jobs/{job['id']}/approve").json()
    assert approved['status'] == 'queued'
    assert 'remote_gitops' not in approved
    assert enqueued == [job['id']]
    artifact = json.loads(git(app.state.state_dir / 'gitops', 'show', approved['git_commit'] + ':jobs/' + job['id'] + '.json'))
    assert set(artifact) == {'id', 'graph', 'cost', 'approved_by', 'approved_at'}
    assert artifact['graph'] == job['graph']


def test_duplicate_config_fields_are_not_an_approval_authority(tmp_path):
    from backend.gitops import safe_status
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'gitops.json').write_text('{"enabled": true, "enabled": false}')
    assert safe_status(tmp_path)['status'] == 'configuration_error'


def test_fast_forward_ignores_local_rewrites_and_never_runs_hooks(api, tmp_path, monkeypatch):
    client, app = api
    remote = tmp_path / 'isolated.git'
    git(tmp_path, 'init', '--bare', str(remote))
    configure(app.state.state_dir, remote)
    app.state.worker.enqueue = lambda identity: None
    first = client.post('/api/jobs', json={'graph': graph()}).json()
    first = client.post(f"/api/jobs/{first['id']}/approve").json()
    repo = app.state.state_dir / 'gitops'
    sentinel = tmp_path / 'HOOK_EXECUTED'
    for hook in [repo / '.git/hooks/pre-push', remote / 'hooks/pre-receive']:
        hook.write_text('#!/bin/sh\ntouch ' + str(sentinel) + '\nexit 1\n')
        hook.chmod(0o700)
    git(repo, 'config', 'url.' + str(tmp_path / 'WRONG_DESTINATION') + '.insteadOf', str(remote))
    capture = tmp_path / 'commands.jsonl'
    install_git_boundary(tmp_path, monkeypatch,
                         "if 'push' in sys.argv or 'ls-remote' in sys.argv:\n"
                         " import json\n"
                         f" with open({str(capture)!r}, 'a') as stream: stream.write(json.dumps({{'args': sys.argv, 'env': dict(os.environ)}}) + '\\n')")
    second = client.post('/api/jobs', json={'graph': graph()}).json()
    response = client.post(f"/api/jobs/{second['id']}/approve")
    assert response.status_code == 200, response.text
    second = response.json()
    assert second['remote_gitops']['status'] == 'verified'
    assert git(remote, 'rev-parse', 'refs/heads/approved') == second['git_commit']
    assert git(repo, 'merge-base', first['git_commit'], second['git_commit']) == first['git_commit']
    assert not sentinel.exists()
    commands = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(commands) == 2
    for command in commands:
        assert '-o BatchMode=yes' in command['env']['GIT_SSH_COMMAND']
        assert '-o StrictHostKeyChecking=yes' in command['env']['GIT_SSH_COMMAND']
        assert '--force' not in command['args']
        assert all(not argument.startswith('+') for argument in command['args'])
        assert str(remote) in command['args']

@pytest.mark.parametrize('via_chatops', [False, True])
def test_real_remote_commands_never_hold_shared_store_lock(api, tmp_path, monkeypatch, via_chatops):
    import backend.gitops as module
    from test_chatops import setup_chatops, issue, signed
    client, app = api
    remote = tmp_path / 'unlocked.git'
    git(tmp_path, 'init', '--bare', str(remote))
    configure(app.state.state_dir, remote)
    app.state.worker.enqueue = lambda identity: None
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    seen = []
    original = module._run
    def boundary(args, env, deadline):
        if 'push' in args or 'ls-remote' in args:
            seen.append((next(cmd for cmd in ('push', 'ls-remote') if cmd in args),
                         app.state.store.lock._is_owned()))
        return original(args, env, deadline)
    monkeypatch.setattr(module, '_run', boundary)
    if via_chatops:
        _, callback = setup_chatops(app)
        response = signed(callback, issue(client, job))
    else:
        response = client.post(f"/api/jobs/{job['id']}/approve")
    assert response.status_code == 200, response.text
    assert seen == [('push', False), ('ls-remote', False)]

@pytest.mark.parametrize('crash_point', ['publication', 'revalidation'])
def test_interrupted_approval_is_durable_and_never_retried(api, monkeypatch, crash_point):
    import backend.jobs as module
    from backend.storage import Store
    from fastapi import HTTPException
    client, app = api
    enqueued = []
    app.state.worker.enqueue = enqueued.append
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    class ProcessLost(BaseException):
        pass
    if crash_point == 'publication':
        def crash(*args):
            raise ProcessLost()
        monkeypatch.setattr(module, 'publish_remote', crash)
    else:
        original = app.state.catalog.discover
        def crash():
            current = app.state.jobs.get(job['id'])
            if current.get('approval_claim'):
                raise ProcessLost()
            return original()
        monkeypatch.setattr(app.state.catalog, 'discover', crash)
    actor = {'username': 'admin', 'role': 'admin'}
    with pytest.raises(ProcessLost):
        app.state.jobs.approve(job['id'], actor)
    # Read through a fresh SQLite connection, not the interrupted stack's copy.
    reopened = Store(app.state.state_dir)
    try:
        stored = reopened.get('jobs', job['id'])
    finally:
        reopened.conn.close()
    assert stored['requires_manual_review'] is True
    assert stored['approval_claim']['state'] == 'inflight'
    app.state.worker.recover()
    with pytest.raises(HTTPException) as denied:
        app.state.jobs.approve(job['id'], actor)
    assert denied.value.status_code == 409
    assert not enqueued


