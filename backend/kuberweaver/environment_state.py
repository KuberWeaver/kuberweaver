"""One durable state document per environment and stack, encrypted at rest.

The portal records what a stack owns instead of trusting an execution
provider's per-job working copy. A record carries a monotonic generation and the
resource list declared by the last applied document; the document itself is
encrypted with a local key before it reaches the database, so a copied database
alone does not disclose provider attributes.

Only an execution provider writes state: no HTTP route creates or replaces a
document. An apply takes an exclusive claim on one (environment, stack) pair and
commits against the generation it observed, so a second apply is refused while
the claim is held and a commit from a stale generation is refused.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .storage import now

ENVIRONMENTS = ('dev', 'staging', 'production')
MAX_RESOURCES = 256
MAX_DOCUMENT = 4 * 1024 * 1024
KEY_NAME = 'state.key'
_ENVELOPE = 1
_STACK = re.compile(r'[^\x00-\x1f\x7f]{1,160}\Z')
_ADDRESS = re.compile(r'[A-Za-z0-9_.\[\]":-]{1,200}\Z')
_WORD = re.compile(r'[A-Za-z0-9_.\[\]":-]{1,120}\Z')


class StateError(RuntimeError):
    """Sanitized failure: never a document, key byte or provider value."""


class StateConflict(StateError):
    """The claim or generation moved; a blind retry would overwrite evidence."""


def _key_error():
    return StateError('State encryption key is unavailable or not protected')


def _read_key(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise
        raise _key_error() from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size != 44):
            raise _key_error()
        raw = os.read(fd, 45)
    finally:
        os.close(fd)
    if len(raw) != 44:
        raise _key_error()
    return raw


def _create_key(directory):
    """Publish a new key atomically; an existing key is never replaced."""
    raw = Fernet.generate_key()
    temporary = directory / ('.pending-' + secrets.token_hex(12))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, directory / KEY_NAME)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise _key_error() from None
    return raw


class EnvironmentStates:
    def __init__(self, store, directory=None, *, key=None, clock=now):
        self.store = store
        self.directory = Path(directory) if directory is not None else store.path / 'environment-state'
        self.key = key
        self.clock = clock
        self.store.execute('CREATE TABLE IF NOT EXISTS environment_state('
                           'id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.store.execute('CREATE TABLE IF NOT EXISTS environment_state_documents('
                           'id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.store.execute('CREATE TABLE IF NOT EXISTS environment_state_claim('
                           'id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def _has_documents(self):
        return self.store.one('SELECT 1 AS present FROM environment_state_documents LIMIT 1') is not None

    def _cipher(self, *, create):
        if self.key is not None:
            return Fernet(self.key)
        directory = self.directory
        if any(path.is_symlink() for path in (directory, *directory.parents)):
            raise _key_error()
        if create:
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        if directory.is_symlink() or not directory.is_dir():
            raise _key_error()
        try:
            raw = _read_key(directory / KEY_NAME)
        except FileNotFoundError:
            # A fresh key would make every stored document unreadable, so the
            # only safe answer is to name the missing material.
            if not create or self._has_documents():
                raise StateError('State encryption key is missing; restore it with the database') from None
            raw = _create_key(directory)
        try:
            return Fernet(raw)
        except (TypeError, ValueError):
            raise _key_error() from None

    @staticmethod
    def _identity(environment, stack):
        if environment not in ENVIRONMENTS:
            raise ValueError('Expected dev, staging or production')
        if (not isinstance(stack, str) or not _STACK.match(stack) or stack != stack.strip()):
            raise ValueError('Expected a stack name of 1 to 160 characters without '
                             'surrounding spaces or control characters')
        return hashlib.sha256((environment + '\0' + stack).encode()).hexdigest()[:32]

    def _row(self, identity):
        row = self.store.one('SELECT body FROM environment_state WHERE id=?', (identity,))
        if row is None:
            return None
        try:
            record = json.loads(row['body'])
        except (TypeError, ValueError):
            raise StateError('Environment state record is unreadable') from None
        if not isinstance(record, dict) or record.get('id') != identity:
            raise StateError('Environment state record is unreadable')
        return record

    def _claim(self, identity):
        row = self.store.one('SELECT body FROM environment_state_claim WHERE id=?', (identity,))
        if row is None:
            return None
        try:
            claim = json.loads(row['body'])
        except (TypeError, ValueError):
            raise StateError('Environment state claim is unreadable') from None
        if not isinstance(claim, dict) or claim.get('id') != identity:
            raise StateError('Environment state claim is unreadable')
        return claim

    def _empty(self, environment, stack, identity):
        return {'id': identity, 'environment': environment, 'stack': stack, 'generation': 0,
                'resources': [], 'digest': None, 'job_id': None,
                'created_at': self.clock(), 'updated_at': None}

    def _view(self, record, claim):
        return {'id': record['id'], 'environment': record['environment'],
                'stack': record['stack'], 'generation': record['generation'],
                'resource_count': len(record['resources']), 'resources': record['resources'],
                'digest': record['digest'], 'job_id': record['job_id'],
                'created_at': record['created_at'], 'updated_at': record['updated_at'],
                'encrypted': True,
                'lease': None if claim is None else {'job_id': claim['job_id'],
                                                     'acquired_at': claim['acquired_at']}}

    def collection(self):
        return {'states': self.list()}

    def list(self, environment=None):
        claims = {}
        for row in self.store.all('SELECT id, body FROM environment_state_claim'):
            try:
                claims[row['id']] = json.loads(row['body'])
            except (TypeError, ValueError):
                continue
        records = []
        for row in self.store.all('SELECT body FROM environment_state ORDER BY rowid DESC'):
            try:
                record = json.loads(row['body'])
            except (TypeError, ValueError):
                raise StateError('Environment state record is unreadable') from None
            if not isinstance(record, dict) or not isinstance(record.get('id'), str):
                raise StateError('Environment state record is unreadable')
            if environment is not None and record.get('environment') != environment:
                continue
            records.append(self._view(record, claims.get(record['id'])))
        return records

    def get(self, environment, stack):
        identity = self._identity(environment, stack)
        with self.store.lock:
            record = self._row(identity)
            if record is None:
                return None
            if record['environment'] != environment or record['stack'] != stack:
                raise StateError('Environment state identity collision')
            return self._view(record, self._claim(identity))

    def present(self, identity):
        with self.store.lock:
            record = self._row(identity) if isinstance(identity, str) else None
            if record is None:
                raise HTTPException(404, 'Unknown environment state')
            return self._view(record, self._claim(identity))

    def document(self, environment, stack):
        identity = self._identity(environment, stack)
        with self.store.lock:
            if self._row(identity) is None:
                raise StateError('Unknown environment state')
            return json.loads(self._plaintext(identity))

    def _plaintext(self, identity):
        row = self.store.one('SELECT body FROM environment_state_documents WHERE id=?', (identity,))
        if row is None:
            raise StateError('Environment state document is missing')
        try:
            envelope = json.loads(row['body'])
            if (not isinstance(envelope, dict) or envelope.get('version') != _ENVELOPE
                    or envelope.get('algorithm') != 'fernet'
                    or not isinstance(envelope.get('token'), str)):
                raise ValueError
            raw = self._cipher(create=False).decrypt(envelope['token'].encode('ascii'))
            if hashlib.sha256(raw).hexdigest() != envelope.get('digest'):
                raise ValueError
        except (InvalidToken, ValueError, TypeError, UnicodeError):
            raise StateError('Environment state document is unavailable or changed') from None
        if len(raw) > MAX_DOCUMENT:
            raise StateError('Environment state document is unavailable or changed')
        return raw

    def acquire(self, environment, stack, job_id, *, expected_generation=None):
        """Take the exclusive claim for one stack, or refuse the caller."""
        identity = self._identity(environment, stack)
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise ValueError('Invalid job identity')
        with self.store.lock:
            record = self._row(identity)
            if record is None:
                record = self._empty(environment, stack, identity)
            if record['environment'] != environment or record['stack'] != stack:
                raise StateError('Environment state identity collision')
            if expected_generation is not None and expected_generation != record['generation']:
                raise StateConflict('Environment state generation changed')
            claim = self._claim(identity)
            if claim is not None:
                if claim.get('job_id') != job_id:
                    raise StateConflict('Another apply holds the state lease for this stack')
                return claim
            # The generation-0 row makes a claimed but unapplied stack visible
            # and keeps the record's identity stable before the first commit.
            self.store.execute('INSERT OR IGNORE INTO environment_state(id,body) VALUES(?,?)',
                               (identity, json.dumps(record, sort_keys=True)))
            lease = {'id': identity, 'environment': environment, 'stack': stack,
                     'job_id': job_id, 'base_generation': record['generation'],
                     'token': secrets.token_hex(16), 'acquired_at': self.clock()}
            try:
                self.store.execute('INSERT INTO environment_state_claim(id,body) VALUES(?,?)',
                                   (identity, json.dumps(lease, sort_keys=True)))
            except Exception:
                # The primary key is the cross-process arbiter; a row means
                # someone else won, never that the claim can be shared.
                try:
                    # SQLite keeps the failed statement's transaction open.
                    self.store.conn.rollback()
                except Exception:
                    pass
                current = self._claim(identity)
                if current is not None and current.get('job_id') == job_id:
                    return current
                if current is not None:
                    raise StateConflict('Another apply holds the state lease for this stack') from None
                raise StateError('State lease could not be recorded') from None
            return lease

    def _drop_claim(self, identity, token):
        claim = self._claim(identity)
        if claim is None or claim.get('token') != token:
            return False
        self.store.execute('DELETE FROM environment_state_claim WHERE id=?', (identity,))
        return self._claim(identity) is None

    def release(self, lease):
        """Drop a claim held by the matching token; a lost claim is not an error."""
        if not isinstance(lease, dict) or not isinstance(lease.get('id'), str) \
                or not isinstance(lease.get('token'), str):
            raise ValueError('Invalid state lease')
        with self.store.lock:
            return self._drop_claim(lease['id'], lease['token'])

    @staticmethod
    def _resources(rows):
        if not isinstance(rows, list) or len(rows) > MAX_RESOURCES:
            raise ValueError('Expected at most 256 resource rows')
        cleaned, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or set(row) - {'address', 'type', 'name', 'mode'}:
                raise ValueError('Invalid resource row')
            address = row.get('address')
            if not isinstance(address, str) or not _ADDRESS.match(address) or address in seen:
                raise ValueError('Invalid or duplicate resource address')
            seen.add(address)
            mode = row.get('mode', 'managed')
            if mode not in ('managed', 'data'):
                raise ValueError('Invalid resource mode')
            kind = row.get('type') or address.split('.')[0]
            name = row.get('name') or address.rsplit('.', 1)[-1]
            for value in (kind, name):
                if not isinstance(value, str) or not _WORD.match(value):
                    raise ValueError('Invalid resource type or name')
            cleaned.append({'address': address, 'type': kind, 'name': name, 'mode': mode})
        return sorted(cleaned, key=lambda item: item['address'])

    @staticmethod
    def _encode(document):
        if not isinstance(document, dict):
            raise ValueError('State document must be a JSON object')
        try:
            raw = json.dumps(document, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode()
        except (TypeError, ValueError):
            raise ValueError('State document must contain JSON values only') from None
        if len(raw) > MAX_DOCUMENT:
            raise ValueError('State document is too large')
        return raw

    def commit(self, lease, document, resources):
        """Store one new generation and clear the claim that produced it."""
        if (not isinstance(lease, dict) or not isinstance(lease.get('id'), str)
                or not isinstance(lease.get('token'), str)):
            raise ValueError('Invalid state lease')
        raw = self._encode(document)
        resources = self._resources(resources)
        digest = hashlib.sha256(raw).hexdigest()
        identity = lease['id']
        with self.store.lock:
            claim = self._claim(identity)
            if claim is None or claim.get('token') != lease['token']:
                raise StateConflict('State lease is no longer held')
            record = self._row(identity)
            if record is None:
                raise StateError('Environment state record is missing')
            if record['generation'] != claim['base_generation']:
                raise StateConflict('Environment state generation changed')
            token = self._cipher(create=True).encrypt(raw)
            envelope = json.dumps({'version': _ENVELOPE, 'algorithm': 'fernet',
                                   'digest': digest, 'token': token.decode('ascii')},
                                  sort_keys=True)
            self.store.execute('INSERT OR REPLACE INTO environment_state_documents(id,body) '
                               'VALUES(?,?)', (identity, envelope))
            updated = {**record, 'generation': record['generation'] + 1,
                       'resources': resources, 'digest': digest,
                       'job_id': claim['job_id'], 'updated_at': self.clock()}
            self.store.execute('INSERT OR REPLACE INTO environment_state(id,body) VALUES(?,?)',
                               (identity, json.dumps(updated, sort_keys=True)))
            self._drop_claim(identity, lease['token'])
            stored = self._row(identity)
            if (stored is None or stored.get('generation') != updated['generation']
                    or stored.get('digest') != digest or self._plaintext(identity) != raw):
                raise StateError('Environment state write could not be confirmed')
            self.store.audit('system', 'environment_state.applied', identity,
                             {'environment': record['environment'], 'stack': record['stack'],
                              'generation': updated['generation'], 'job_id': claim['job_id'],
                              'resources': len(resources)})
            return self._view(stored, None)

    def release_lease(self, identity, job_id, actor):
        """Administrator recovery for an interrupted apply; never a silent retry."""
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise HTTPException(422, 'Invalid job identity')
        with self.store.lock:
            record = self._row(identity) if isinstance(identity, str) else None
            if record is None:
                raise HTTPException(404, 'Unknown environment state')
            claim = self._claim(identity)
            if claim is None:
                raise HTTPException(409, 'No state lease is held')
            if claim.get('job_id') != job_id:
                raise HTTPException(409, 'The state lease is held by another job')
            self.store.execute('DELETE FROM environment_state_claim WHERE id=?', (identity,))
            if self._claim(identity) is not None:
                raise HTTPException(503, 'State lease release could not be confirmed')
            self.store.audit(actor['username'], 'environment_state.lease_released', identity,
                             {'job_id': job_id, 'environment': record['environment'],
                              'stack': record['stack'],
                              'generation': record['generation']})
            return self._view(record, None)


class LeaseRelease(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: str = Field(pattern=r'^[0-9a-f]{32}$')


def install_environment_state_api(app):
    if getattr(app.state, '_environment_state_api_installed', False):
        return
    service = app.state.environment_states = EnvironmentStates(app.state.store)
    original_count = len(app.router.routes)

    @app.get('/api/environment-states')
    def listing(current=Depends(app.state.user_dependency)):
        return service.collection()

    @app.get('/api/environment-states/{identity}')
    def detail(identity: str, current=Depends(app.state.user_dependency)):
        return service.present(identity)

    @app.post('/api/environment-states/{identity}/lease/release')
    def release(identity: str, data: LeaseRelease,
                current=Depends(app.state.role_dependency('admin'))):
        return service.release_lease(identity, data.job_id, current)

    added = app.router.routes[original_count:]
    del app.router.routes[original_count:]
    before_spa = next((i for i, route in enumerate(app.router.routes)
                       if getattr(route, 'path', None) == '/{asset_path:path}'), len(app.router.routes))
    app.router.routes[before_spa:before_spa] = added
    app.state._environment_state_api_installed = True
