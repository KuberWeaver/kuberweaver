"""Git-backed stack declarations and the CI plan built from one exact revision.

A stack's declaration lives in a Git repository, not in the portal database. CI
commits a bundle that pins one commit, one declaration file and one saved
OpenTofu plan by SHA-256. The portal reads those blobs from the repository's
object database at that commit, never from a working tree, and executes the plan
only while every digest still matches.

Nothing is fetched: the repository path is administrator configuration, every
Git command is local and read-only, and no destination, path from a request,
credential or raw command output is returned, stored or logged. Verification is
read-only. Execution writes one exact plan file into a private folder, takes the
stack's state claim, applies that file as-is (never ``init`` and never a new
``plan``) and records the resulting generation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import subprocess
import time
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .environment_state import StateConflict, StateError
from .models import Graph
from .storage import now

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = 'declarations.json'
MAX_CONFIG = 16384
MAX_BUNDLE = 65536
MAX_DECLARATION = 262144
MAX_PLAN = 8 * 1024 * 1024
MAX_PATH = 400
MAX_GIT_OUTPUT = 262144
GIT_TIMEOUT = 20.0
APPLY_TIMEOUT = 600.0
MAX_APPLY_OUTPUT = 8 * 1024 * 1024

_PLAN_MAGIC = b'PK\x03\x04'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_REVISION = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_RELATIVE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._/-]*\Z')
_EXECUTION = re.compile(r'[0-9a-f]{32}\Z')
_STATES = ('executing', 'executed', 'failed')
_BUNDLE_FIELDS = {'schema_version', 'engine', 'base_generation', 'declaration', 'plan'}
_FILE_FIELDS = {'path', 'sha256'}
_DECLARATION_FIELDS = {'schema_version', 'environment', 'stack', 'graph'}
_ARTIFACTS = {'declaration.tfplan', 'cli.tfrc', 'terraform.tfstate',
              'terraform.tfstate.backup', '.terraform.tfstate.lock.info'}


class DeclarationUnavailable(RuntimeError):
    """The configured repository or the plan applier cannot answer; nothing ran."""


class DeclarationConflict(RuntimeError):
    """A pin does not match the repository content; nothing is executed."""


class PlanExecutionError(RuntimeError):
    """The plan applier failed; the message is fixed and carries no provider text."""


def _unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate configuration field')
        value[key] = item
    return value


def _refuse_constant(value):
    raise ValueError('Non-finite number in declaration document')


def _config(state_dir):
    """Read the administrator file; it never comes from a request or a graph."""
    path = Path(state_dir) / 'config' / CONFIG_FILE
    if not path.exists() and not path.is_symlink():
        return {'schema_version': 1, 'enabled': False, 'repository': None}
    if path.is_symlink() or path.stat().st_size > MAX_CONFIG:
        raise ValueError('Invalid declarations configuration')
    value = json.loads(path.read_text(), object_pairs_hook=_unique_fields)
    if not isinstance(value, dict) or set(value) - {'schema_version', 'enabled', 'repository'}:
        raise ValueError('Invalid declarations configuration fields')
    config = {'schema_version': 1, 'enabled': False, 'repository': None, **value}
    if type(config['schema_version']) is not int or config['schema_version'] != 1:
        raise ValueError('Invalid declarations configuration version')
    if type(config['enabled']) is not bool:
        raise ValueError('Invalid declarations configuration flag')
    repository = config['repository']
    if repository is not None:
        if (not isinstance(repository, str) or not 1 <= len(repository) <= 4096
                or not repository.startswith('/') or any(ord(character) < 32 for character in repository)):
            raise ValueError('Invalid declaration repository path')
        candidate = Path(repository)
        try:
            canonical = candidate.resolve(strict=False)
        except OSError:
            raise ValueError('Invalid declaration repository path') from None
        if canonical != candidate:
            raise ValueError('The declaration repository path must be canonical')
        config['repository'] = canonical
    if config['enabled'] and config['repository'] is None:
        raise ValueError('An enabled declarations configuration needs a repository')
    return config


def repository_status(state_dir):
    """Safe configuration summary: never a path, never repository contents."""
    try:
        config = _config(state_dir)
    except Exception:
        return {'status': 'configuration_error'}
    return {'status': 'configured' if config['enabled'] else 'disabled'}


def _relative_path(value):
    """One repository-relative file path; no revision syntax and no traversal."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_PATH or not _RELATIVE.match(value):
        raise ValueError('Invalid repository path')
    parts = value.split('/')
    if any(part in ('', '.', '..') or part.startswith('.') or part.endswith('.lock') for part in parts):
        raise ValueError('Invalid repository path')
    return value


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_object(raw, label):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_fields,
                           parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError):
        raise DeclarationConflict(f'The {label} is not valid JSON') from None
    if not isinstance(value, dict):
        raise DeclarationConflict(f'The {label} is not a JSON object')
    return value


def _gitdir(path):
    """The object database of a worktree or a bare repository, without following links."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise DeclarationUnavailable('The declaration repository path is not a directory')
    if not path.is_dir():
        raise DeclarationUnavailable('The declaration repository is unavailable')
    dot = path / '.git'
    if dot.is_dir() and not dot.is_symlink():
        return dot
    if (path / 'HEAD').is_file() and (path / 'objects').is_dir() and (path / 'refs').is_dir():
        return path
    raise DeclarationUnavailable('The declaration repository is unavailable')


class GitRepository:
    """Read blobs from one local repository; the worktree is never consulted."""

    def __init__(self, path, *, binary='git', timeout=GIT_TIMEOUT):
        self.path = Path(path)
        self.gitdir = _gitdir(self.path)
        self.binary = binary
        self.timeout = timeout

    def _run(self, args, *, limit):
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                   GIT_TERMINAL_PROMPT='0', GIT_PROTOCOL_FROM_USER='0',
                   GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
        argv = [self.binary, '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never',
                '-c', 'gc.auto=0', '--git-dir=' + str(self.gitdir), *args]
        process = subprocess.Popen(argv, cwd=str(self.gitdir), env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True,
                                   shell=False)
        assert process.stdout is not None
        output = bytearray()
        deadline = time.monotonic() + self.timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DeclarationUnavailable('The declaration repository did not answer in time')
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) > limit:
                            raise DeclarationConflict('The pinned repository file is larger than the accepted limit')
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise DeclarationUnavailable('The declaration repository did not answer in time') from None
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
        if process.returncode:
            raise DeclarationUnavailable('The declaration repository could not be read')
        return bytes(output)

    def commit(self, revision):
        """Resolve a full commit id and prove the pin names that exact object."""
        if not isinstance(revision, str) or not _REVISION.match(revision):
            raise DeclarationConflict('The pinned revision must be a full commit id')
        try:
            output = self._run(['rev-parse', '--verify', '--end-of-options',
                                revision + '^{commit}'], limit=1024)
        except DeclarationUnavailable:
            raise DeclarationConflict(
                'The pinned revision is not a commit in the declaration repository') from None
        value = output.strip().decode('ascii', 'replace')
        if value != revision:
            raise DeclarationConflict('The pinned revision is not a commit in the declaration repository')
        return value

    def blob(self, commit, path, limit):
        """Read one regular file blob at one commit, with its mode and size checked."""
        try:
            path = _relative_path(path)
        except ValueError:
            raise DeclarationConflict('The bundle names an invalid repository path') from None
        output = self._run(['ls-tree', '-z', '-l', '--full-tree', commit, '--', path],
                           limit=MAX_GIT_OUTPUT)
        entries = [entry for entry in output.split(b'\0') if entry]
        if not entries:
            raise DeclarationConflict('The pinned path does not exist at that revision')
        if len(entries) != 1:
            raise DeclarationConflict('The pinned path is ambiguous at that revision')
        match = re.fullmatch(rb'(\d{6}) (\w+) ([0-9a-f]{40,64}) +(\d+)\t(.+)', entries[0], re.S)
        if match is None:
            raise DeclarationUnavailable('The declaration repository returned an unexpected entry')
        mode, kind, sha, size, name = match.groups()
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise DeclarationConflict('The pinned path is not a regular file at that revision')
        if name != path.encode('utf-8'):
            raise DeclarationConflict('The pinned path does not exist at that revision')
        size = int(size)
        if size > limit:
            raise DeclarationConflict('The pinned repository file is larger than the accepted limit')
        raw = self._run(['cat-file', 'blob', sha.decode('ascii')], limit=limit + 1)
        if len(raw) != size:
            raise DeclarationUnavailable('The declaration repository returned an inconsistent object')
        return raw


def _parse_bundle(raw):
    value = _json_object(raw, 'bundle')
    if set(value) != _BUNDLE_FIELDS:
        raise DeclarationConflict('The bundle has an unsupported shape')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise DeclarationConflict('The bundle has an unsupported schema version')
    if value['engine'] != 'opentofu':
        raise DeclarationConflict('The bundle names an unsupported plan engine')
    if type(value['base_generation']) is not int or not 0 <= value['base_generation'] <= 2 ** 31 - 1:
        raise DeclarationConflict('The bundle carries an invalid base generation')
    for key in ('declaration', 'plan'):
        entry = value[key]
        if not isinstance(entry, dict) or set(entry) != _FILE_FIELDS:
            raise DeclarationConflict(f'The bundle names an invalid {key}')
        try:
            _relative_path(entry['path'])
        except ValueError:
            raise DeclarationConflict(f'The bundle names an invalid {key} path') from None
        if not isinstance(entry['sha256'], str) or not _SHA.match(entry['sha256']):
            raise DeclarationConflict(f'The bundle carries an invalid {key} digest')
    return value


def _parse_declaration(raw, *, environment, stack):
    value = _json_object(raw, 'declaration')
    if set(value) != _DECLARATION_FIELDS:
        raise DeclarationConflict('The declaration has an unsupported shape')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise DeclarationConflict('The declaration has an unsupported schema version')
    if value['environment'] != environment:
        raise DeclarationConflict('The declaration names another environment')
    if value['stack'] != stack:
        raise DeclarationConflict('The declaration names another stack')
    try:
        graph = Graph.model_validate(value['graph'])
    except ValidationError:
        raise DeclarationConflict('The declaration is not a valid stack graph') from None
    if graph.environment != environment or graph.name != stack:
        raise DeclarationConflict('The declaration graph does not match its environment and stack')
    document = graph.model_dump()
    canonical = json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return {'graph': document, 'graph_sha256': _sha256(canonical.encode())}


def _applied(outcome):
    """The applier's contract: one engine state document and its resource rows."""
    if not isinstance(outcome, dict) or set(outcome) != {'state', 'resources'}:
        raise PlanExecutionError('The plan applier returned no state')
    document, resources = outcome['state'], outcome['resources']
    if not isinstance(document, dict) or not isinstance(resources, list):
        raise PlanExecutionError('The plan applier returned no state')
    rows = []
    for row in resources:
        if (not isinstance(row, dict) or set(row) - {'address', 'type', 'name', 'mode'}
                or not isinstance(row.get('address'), str) or not row['address']):
            raise PlanExecutionError('The applied state has an invalid resource list')
        mode = row.get('mode', 'managed')
        if mode not in ('managed', 'data'):
            raise PlanExecutionError('The applied state has an invalid resource list')
        rows.append({'address': row['address'], 'type': row.get('type'),
                     'name': row.get('name'), 'mode': mode})
    return document, rows


class PlanApplier:
    """Apply one saved plan file in an isolated folder; never plan, never init."""

    def __init__(self, state_dir, *, binary=None, runner=None, timeout=APPLY_TIMEOUT):
        self.state_dir = Path(state_dir).absolute()
        self.binary = Path(binary or ROOT / 'tools' / 'tofu').absolute()
        self.runner = runner or subprocess.run
        self.timeout = timeout
        self.directory = self.state_dir / 'declarations'

    def read(self):
        present = self.binary.is_file()
        return {'available': present, 'binary_present': present,
                'limitations': ['applies one saved plan file as-is',
                                'no init, no new plan and no configuration files',
                                'provider plugins must be available to the service account']}

    def _run(self, folder, *args):
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'SSH_AUTH_SOCK',
                                                'TF_PLUGIN_CACHE_DIR') if key in os.environ}
        env.update(TF_IN_AUTOMATION='1', TF_INPUT='0',
                   TF_CLI_CONFIG_FILE=str(folder / 'cli.tfrc'), CHECKPOINT_DISABLE='1')
        try:
            result = self.runner([str(self.binary), *args], cwd=folder, env=env,
                                 capture_output=True, text=True, timeout=self.timeout)
        except Exception:
            raise PlanExecutionError('OpenTofu did not answer') from None
        finally:
            self._restrict(folder)
        if result.returncode:
            raise PlanExecutionError('OpenTofu did not accept the saved plan')
        if len(result.stdout or '') > MAX_APPLY_OUTPUT:
            raise PlanExecutionError('OpenTofu output is larger than the accepted limit')
        return result

    @staticmethod
    def _restrict(folder):
        for path in folder.iterdir():
            if path.is_symlink() or path.name not in _ARTIFACTS or path.is_dir():
                raise PlanExecutionError('Unexpected plan artifact; inspect the private folder')
            path.chmod(0o600)

    def __call__(self, prepared):
        plan = prepared.get('plan_bytes')
        digest = prepared.get('plan_sha256')
        execution_id = prepared.get('execution_id')
        if not isinstance(plan, bytes) or not plan.startswith(_PLAN_MAGIC):
            raise PlanExecutionError('The plan artifact is not a saved OpenTofu plan')
        if not isinstance(digest, str) or not _SHA.match(digest) or _sha256(plan) != digest:
            raise PlanExecutionError('The plan artifact changed before execution')
        if not isinstance(execution_id, str) or not _EXECUTION.match(execution_id):
            raise PlanExecutionError('Invalid execution identity')
        if not self.binary.is_file():
            raise PlanExecutionError('OpenTofu binary is unavailable')
        for candidate in (self.state_dir, *self.state_dir.parents):
            if candidate.is_symlink():
                raise PlanExecutionError('Symlinked state path is forbidden')
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)
        folder = self.directory / execution_id
        try:
            folder.mkdir(mode=0o700)
        except FileExistsError:
            raise PlanExecutionError('Execution folder already exists') from None
        target = folder / 'declaration.tfplan'
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(plan)
            stream.flush()
            os.fsync(stream.fileno())
        if _sha256(target.read_bytes()) != digest:
            raise PlanExecutionError('The staged plan changed before execution')
        cli = folder / 'cli.tfrc'
        fd = os.open(cli, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('disable_checkpoint = true\n')
        # Apply the exact file that was verified; no plan is produced here.
        self._run(folder, 'apply', '-input=false', '-no-color', '-lock=true',
                  '-lock-timeout=5s', 'declaration.tfplan')
        if _sha256(target.read_bytes()) != digest:
            raise PlanExecutionError('The saved plan changed during execution')
        shown = self._run(folder, 'show', '-json')
        if len(shown.stdout) > MAX_APPLY_OUTPUT:
            raise PlanExecutionError('The applied state is larger than the accepted limit')
        try:
            document = json.loads(shown.stdout)
        except (TypeError, ValueError):
            raise PlanExecutionError('The applied state is unreadable') from None
        if not isinstance(document, dict):
            raise PlanExecutionError('The applied state is unreadable')
        values = document.get('values')
        resources = []
        if values is not None:
            module = values.get('root_module') if isinstance(values, dict) else None
            resources = module.get('resources') if isinstance(module, dict) else None
            if not isinstance(resources, list):
                raise PlanExecutionError('The applied state has no resource list')
            resources = [{'address': row.get('address'), 'type': row.get('type'),
                          'name': row.get('name'), 'mode': row.get('mode', 'managed')}
                         for row in resources if isinstance(row, dict)]
        return {'state': document, 'resources': resources}


class BundleRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    environment: Literal['dev', 'staging', 'production']
    stack: str = Field(min_length=1, max_length=160)
    revision: str = Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')
    bundle_path: str = Field(min_length=1, max_length=MAX_PATH)
    bundle_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def _applier_status(applier):
    if not callable(applier):
        return {'available': False}
    read = getattr(applier, 'read', None)
    if callable(read):
        try:
            value = read()
        except Exception:
            return {'available': False}
        if isinstance(value, dict):
            status = {'available': bool(value.get('available'))}
            if 'binary_present' in value:
                status['binary_present'] = bool(value.get('binary_present'))
            return status
    return {'available': True}


class DeclarationService:
    """Verify one pinned bundle and execute its plan; nothing else."""

    def __init__(self, environments, state_dir, *, applier=None, clock=now, repository=None):
        self.environments = environments
        self.state_dir = Path(state_dir)
        self.applier = applier
        self.clock = clock
        self.repository = repository
        self.store = environments.store
        self.store.execute('CREATE TABLE IF NOT EXISTS declaration_run('
                           'id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    @staticmethod
    def _actor(actor):
        if (not isinstance(actor, dict) or not isinstance(actor.get('username'), str)
                or not actor['username']):
            raise HTTPException(403, 'A signed-in administrator is required')
        if actor.get('principal_type') == 'agent':
            raise HTTPException(403, 'A human administrator session is required')
        return actor['username']

    def _repository(self):
        if self.repository is not None:
            return self.repository
        try:
            config = _config(self.state_dir)
        except Exception:
            raise HTTPException(503, 'Git-backed declarations are not configured') from None
        if not config['enabled']:
            raise HTTPException(503, 'Git-backed declarations are disabled')
        try:
            return GitRepository(config['repository'])
        except DeclarationUnavailable as error:
            raise HTTPException(503, str(error)) from None

    def verify(self, environment, stack, revision, bundle_path, bundle_sha256):
        try:
            identity = self.environments.identity(environment, stack)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        repository = self._repository()
        try:
            commit = repository.commit(revision)
            raw_bundle = repository.blob(commit, bundle_path, MAX_BUNDLE)
            if _sha256(raw_bundle) != bundle_sha256:
                raise DeclarationConflict('The bundle digest does not match the pinned digest')
            bundle = _parse_bundle(raw_bundle)
            raw_declaration = repository.blob(commit, bundle['declaration']['path'], MAX_DECLARATION)
            if _sha256(raw_declaration) != bundle['declaration']['sha256']:
                raise DeclarationConflict('The declaration digest does not match the bundle')
            declaration = _parse_declaration(raw_declaration, environment=environment, stack=stack)
            raw_plan = repository.blob(commit, bundle['plan']['path'], MAX_PLAN)
            if _sha256(raw_plan) != bundle['plan']['sha256']:
                raise DeclarationConflict('The plan digest does not match the bundle')
            if not raw_plan.startswith(_PLAN_MAGIC):
                raise DeclarationConflict('The plan artifact is not a saved OpenTofu plan')
        except DeclarationConflict as error:
            raise HTTPException(409, str(error)) from None
        except DeclarationUnavailable as error:
            raise HTTPException(503, str(error)) from None
        return {'identity': identity, 'environment': environment, 'stack': stack,
                'revision': commit,
                'bundle': {'path': bundle_path, 'sha256': bundle_sha256},
                'declaration': {'path': bundle['declaration']['path'],
                                'sha256': bundle['declaration']['sha256'],
                                'graph': declaration['graph'],
                                'graph_sha256': declaration['graph_sha256']},
                'plan': {'path': bundle['plan']['path'],
                         'sha256': bundle['plan']['sha256'],
                         'size': len(raw_plan), 'engine': bundle['engine']},
                'base_generation': bundle['base_generation'],
                'verified_at': self.clock(), 'plan_bytes': raw_plan}

    @staticmethod
    def view(prepared):
        """The public verification result: no artifact bytes and no private path."""
        return {key: prepared[key] for key in
                ('identity', 'environment', 'stack', 'revision', 'bundle', 'declaration',
                 'plan', 'base_generation', 'verified_at')}

    def execute(self, environment, stack, revision, bundle_path, bundle_sha256, actor):
        username = self._actor(actor)
        prepared = self.verify(environment, stack, revision, bundle_path, bundle_sha256)
        applier = self.applier
        if not callable(applier) or not _applier_status(applier)['available']:
            raise HTTPException(503, 'Plan execution is unavailable')
        identity = prepared['identity']
        execution_id = secrets.token_hex(16)
        try:
            lease = self.environments.acquire(environment, stack, execution_id)
        except StateConflict as error:
            raise HTTPException(409, str(error)) from None
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None
        try:
            point = self.environments.get(environment, stack)
        except StateError:
            self._release(lease)
            raise HTTPException(503, 'Environment state is unavailable') from None
        generation = 0 if point is None else point['generation']
        if generation != prepared['base_generation']:
            self._release(lease)
            raise HTTPException(409, 'The stack generation changed since the plan was produced')
        record = {'id': identity, 'environment': environment, 'stack': stack,
                  'status': 'executing', 'revision': prepared['revision'],
                  'bundle': prepared['bundle'], 'declaration_sha256':
                  prepared['declaration']['sha256'], 'plan_sha256': prepared['plan']['sha256'],
                  'plan_bytes': prepared['plan']['size'],
                  'base_generation': prepared['base_generation'], 'generation': generation,
                  'execution_id': execution_id, 'started_at': self.clock(),
                  'finished_at': None, 'executed_by': username,
                  'requires_manual_review': False, 'reason': None}
        try:
            self._record(record)
        except HTTPException:
            # Nothing has been attempted on the host; a stuck claim would be a
            # false signal and would block every later execution of this stack.
            self._release(lease)
            raise
        try:
            outcome = applier({'execution_id': execution_id, 'environment': environment,
                               'stack': stack, 'revision': prepared['revision'],
                               'plan_bytes': prepared['plan_bytes'],
                               'plan_sha256': prepared['plan']['sha256']})
            document, resources = _applied(outcome)
        except (Exception, SystemExit):
            self._fail(record, 'execution_failed')
            raise HTTPException(502, 'Plan execution failed; the stack state is unverified '
                                     'and the claim is held') from None
        try:
            state = self.environments.commit(lease, document, resources)
        except (StateConflict, StateError, ValueError):
            self._fail(record, 'state_unconfirmed')
            raise HTTPException(503, 'The plan ran but the state could not be recorded; '
                                     'manual review is required') from None
        record.update(status='executed', generation=state['generation'],
                      finished_at=self.clock())
        self.store.audit(username, 'declaration.executed', identity,
                         {'environment': environment, 'stack': stack,
                          'revision': prepared['revision'],
                          'plan_sha256': prepared['plan']['sha256'],
                          'execution_id': execution_id, 'generation': state['generation']})
        try:
            self._record(record)
        except HTTPException:
            raise HTTPException(503, 'The plan ran but its run record could not be written; '
                                     'inspect the audit trail') from None
        return {'executed': True, 'identity': identity, 'environment': environment,
                'stack': stack, 'revision': prepared['revision'],
                'bundle_sha256': prepared['bundle']['sha256'],
                'declaration_sha256': prepared['declaration']['sha256'],
                'plan_sha256': prepared['plan']['sha256'],
                'execution_id': execution_id, 'generation': state['generation']}

    def _release(self, lease):
        try:
            self.environments.release(lease)
        except Exception:
            pass

    def _record(self, record):
        body = json.dumps(record, sort_keys=True)
        self.store.execute('INSERT OR REPLACE INTO declaration_run(id,body) VALUES(?,?)',
                           (record['id'], body))
        row = self.store.one('SELECT body FROM declaration_run WHERE id=?', (record['id'],))
        if row is None or row['body'] != body:
            raise HTTPException(503, 'The declaration run record could not be written')

    def _fail(self, record, reason):
        record.update(status='failed', finished_at=self.clock(),
                      requires_manual_review=True, reason=reason)
        try:
            self._record(record)
            self.store.audit(record['executed_by'], 'declaration.execution_failed', record['id'],
                             {'environment': record['environment'], 'stack': record['stack'],
                              'revision': record['revision'], 'reason': reason,
                              'execution_id': record['execution_id']})
        except Exception:
            # The refusal is still reported; the stored record shows the last write.
            pass

    def _runs(self):
        runs = []
        for row in self.store.all('SELECT body FROM declaration_run ORDER BY id'):
            try:
                value = json.loads(row['body'])
            except (TypeError, ValueError):
                raise HTTPException(503, 'Declaration run records are unreadable') from None
            if (not isinstance(value, dict) or not isinstance(value.get('id'), str)
                    or value.get('status') not in _STATES):
                raise HTTPException(503, 'Declaration run records are unreadable')
            runs.append(value)
        return sorted(runs, key=lambda item: (item.get('environment') or '', item.get('stack') or ''))

    def collection(self):
        repository = (repository_status(self.state_dir) if self.repository is None
                      else {'status': 'configured'})
        return {'repository': repository, 'applier': _applier_status(self.applier),
                'runs': self._runs()}


def install_declarations_api(app, environments):
    """Register the declaration routes; the kernel supplies state and applier."""
    if getattr(app.state, '_declarations_api_installed', False):
        return
    service = app.state.declarations = DeclarationService(
        environments, app.state.state_dir, applier=PlanApplier(app.state.state_dir))

    @app.get('/api/declarations')
    def declarations(current=Depends(app.state.user_dependency)):
        return service.collection()

    @app.post('/api/declarations/verify')
    def verify(data: BundleRequest,
               current=Depends(app.state.role_dependency('admin'))):
        return service.view(service.verify(data.environment, data.stack, data.revision,
                                           data.bundle_path, data.bundle_sha256))

    @app.post('/api/declarations/execute')
    def execute(data: BundleRequest,
                current=Depends(app.state.role_dependency('admin'))):
        return service.execute(data.environment, data.stack, data.revision,
                               data.bundle_path, data.bundle_sha256, current)

    app.state._declarations_api_installed = True
