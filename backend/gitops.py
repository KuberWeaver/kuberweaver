"""Explicit remote GitOps; public results never contain destinations or tool output."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

TIMEOUT_SECONDS = 20
MAX_OUTPUT_BYTES = 65536


def _unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate configuration field')
        value[key] = item
    return value


def _config(state_dir):
    path = Path(state_dir) / 'config' / 'gitops.json'
    if not path.exists() and not path.is_symlink():
        return {'enabled': False, 'required_before_execution': True}
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError('Invalid configuration file')
    value = json.loads(path.read_text(), object_pairs_hook=_unique_fields)
    allowed = {'schema_version', 'enabled', 'remote_url', 'branch', 'required_before_execution', 'allow_local_test_path'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Invalid configuration fields')
    config = {'schema_version': 1, 'required_before_execution': True, 'allow_local_test_path': False, **value}
    if type(config['schema_version']) is not int or config['schema_version'] != 1:
        raise ValueError('Invalid schema version')
    if any(type(config.get(key)) is not bool for key in ('enabled', 'required_before_execution', 'allow_local_test_path')):
        raise ValueError('Invalid boolean')
    if config['enabled'] or 'remote_url' in config or 'branch' in config:
        url, branch = config.get('remote_url'), config.get('branch')
        if not isinstance(branch, str) or len(branch) > 200 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch):
            raise ValueError('Invalid branch')
        if '..' in branch or '//' in branch or branch.endswith(('.', '/')) or any(part.startswith('.') or part.endswith('.lock') for part in branch.split('/')):
            raise ValueError('Invalid branch')
        if not isinstance(url, str) or len(url) > 2048:
            raise ValueError('Invalid destination')
        if url.startswith('/'):
            destination = Path(url)
            if not config['allow_local_test_path'] or destination.resolve() != destination or destination == (Path(state_dir) / 'gitops').resolve():
                raise ValueError('Invalid local destination')
        else:
            # Deliberately narrow grammar: no credentials, quoting, options,
            # percent encoding, shell syntax, query strings or remote helpers.
            user = r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?'
            host = r'[A-Za-z0-9][A-Za-z0-9.-]*'
            location = r'[A-Za-z0-9_][A-Za-z0-9_./-]*'
            if url.startswith('ssh://'):
                if not re.fullmatch(r'ssh://' + user + host + r'(?::[0-9]{1,5})?/' + location, url):
                    raise ValueError('Invalid SSH destination')
                parsed = urlsplit(url)
                if parsed.port is not None and not 1 <= parsed.port <= 65535:
                    raise ValueError('Invalid SSH port')
            elif not re.fullmatch(user + host + ':' + location, url):
                raise ValueError('Invalid SSH destination')
    return config


def _status(status, required=True, commit=None):
    return {'status': status, 'required_before_execution': required, 'verified_commit': commit}


def safe_status(state_dir):
    """Read administrator config without leaking its URL, paths or exception text."""
    try:
        config = _config(state_dir)
        return _status('configured' if config['enabled'] else 'disabled', config['required_before_execution'])
    except Exception:
        return _status('configuration_error')


class _Failure(Exception):
    pass


def _run(args, env, deadline):
    """Bound elapsed time and combined output; never return raw errors to callers."""
    process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True, shell=False)
    assert process.stdout is not None
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Failure('timeout')
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > MAX_OUTPUT_BYTES:
                            raise _Failure('output_limit')
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if process.returncode:
            raise _Failure('publish_failed')
        return output.decode('utf-8', errors='replace').strip()
    except subprocess.TimeoutExpired:
        raise _Failure('timeout') from None
    finally:
        # Terminate descendants as well as Git (including stalled SSH children).
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()


def publish(state_dir, commit):
    """Push exactly the approved SHA, then independently read the destination ref."""
    try:
        config = _config(state_dir)
    except Exception:
        return _status('configuration_error')
    required = config['required_before_execution']
    if not config['enabled']:
        return _status('disabled', required)
    try:
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
            raise _Failure('publish_failed')
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'SSH_AUTH_SOCK', 'SYSTEMROOT') if key in os.environ}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
                   GIT_SSH_COMMAND='ssh -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10',
                   GIT_SSH_VARIANT='ssh', GIT_PROTOCOL_FROM_USER='0')
        deadline = time.monotonic() + TIMEOUT_SECONDS
        # Isolate publishing from local repository remotes, URL rewrites and hooks.
        # Only objects are shared; no remote is ever added to the local repository.
        with tempfile.TemporaryDirectory(prefix='kw-gitops-') as directory:
            base = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never',
                    '-c', 'protocol.ssh.allow=always', '-c', 'protocol.file.allow=always',
                    '-c', 'gc.auto=0', '--git-dir=' + directory]
            _run(base + ['init', '--bare', '--quiet'], env, deadline)
            env['GIT_OBJECT_DIRECTORY'] = str((Path(state_dir) / 'gitops' / '.git' / 'objects').resolve())
            ref = 'refs/heads/' + config['branch']
            _run(base + ['push', '--no-verify', '--porcelain',
                         '--receive-pack=git -c core.hooksPath=/dev/null receive-pack',
                         '--', config['remote_url'], commit + ':' + ref], env, deadline)
            actual = _run(base + ['ls-remote', '--refs', '--', config['remote_url'], ref], env, deadline)
            if actual != commit + '\t' + ref:
                raise _Failure('verification_failed')
        return _status('verified', required, commit)
    except _Failure as exc:
        return _status(str(exc), required)
    except Exception:
        return _status('publish_failed', required)
