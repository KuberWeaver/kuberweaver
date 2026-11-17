"""Human accounts with fixed roles, policy-checked passwords and single-use bootstrap access.

Accounts are created by an administrator or seeded once at first startup. The
seeded passwords are written to a private file; rotating an account through this
module removes that account's plaintext line, so the file cannot outlive the
credential it describes.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import stat

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .passwords import PasswordPolicy, WeakPassword, check_password, hash_password, needs_rehash

ROLES = ('admin', 'operator', 'viewer')
USERNAME_PATTERN = re.compile(r'^[a-z][a-z0-9._-]{2,63}$')
BOOTSTRAP_FILE = 'bootstrap-credentials.txt'


class UserError(ValueError):
    status = 422


class InvalidUsername(UserError):
    status = 422


class UnknownRole(UserError):
    status = 422


class DuplicateUser(UserError):
    status = 409


class UnknownUser(UserError):
    status = 404


class RefusedPassword(UserError):
    status = 422

    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


class ReusedPassword(UserError):
    status = 422


class WrongPassword(UserError):
    status = 403


class Forbidden(UserError):
    status = 403


class LastAdmin(UserError):
    status = 409


class BootstrapFile(UserError):
    status = 409


def refused(error: WeakPassword) -> 'RefusedPassword':
    return RefusedPassword(error.reason, str(error))


class UserStore:
    def __init__(self, store, policy=None):
        self.store = store
        self.policy = policy or PasswordPolicy()

    @staticmethod
    def metadata(row):
        return {key: row[key] for key in ('id', 'username', 'role')}

    def find(self, username):
        if not isinstance(username, str) or not username.strip():
            return None
        return self.store.one('SELECT * FROM users WHERE lower(username)=lower(?)', (username.strip(),))

    def by_id(self, identity):
        return self.store.one('SELECT * FROM users WHERE id=?', (identity,))

    def list(self):
        return [self.metadata(row) for row in self.store.all('SELECT * FROM users ORDER BY lower(username)')]

    def create(self, username, password, role, actor):
        name = username.strip().lower() if isinstance(username, str) else ''
        if not USERNAME_PATTERN.fullmatch(name):
            raise InvalidUsername('Username must be 3-64 lowercase characters, starting with a letter')
        if role not in ROLES:
            raise UnknownRole('Unknown role')
        try:
            self.policy.validate(password, name)
        except WeakPassword as error:
            raise refused(error) from None
        encoded = hash_password(password)
        identity = secrets.token_hex(12)
        with self.store.lock:
            if self.find(name):
                raise DuplicateUser('Username already exists')
            self.store.execute('INSERT INTO users VALUES(?,?,?,?)', (identity, name, role, encoded))
        self.store.audit(actor, 'user.created', identity, {'username': name, 'role': role})
        return self.metadata(self.by_id(identity))

    def set_role(self, identity, role, actor):
        if role not in ROLES:
            raise UnknownRole('Unknown role')
        with self.store.lock:
            row = self.by_id(identity)
            if not row:
                raise UnknownUser('Unknown user')
            if row['role'] == role:
                return self.metadata(row)
            if row['role'] == 'admin' and role != 'admin':
                count = self.store.one('SELECT count(*) AS total FROM users WHERE role=?', ('admin',))['total']
                if count <= 1:
                    raise LastAdmin('At least one administrator must remain')
            self.store.execute('UPDATE users SET role=? WHERE id=?', (role, identity))
        self.store.audit(actor, 'user.role_changed', identity, {'username': row['username'], 'role': role})
        return self.metadata(self.by_id(identity))

    def rotate(self, identity, new_password, actor, current_password=None):
        with self.store.lock:
            row = self.by_id(identity)
            if not row:
                raise UnknownUser('Unknown user')
            is_self = actor.get('id') == identity
            if is_self and (not current_password or not check_password(current_password, row['password_hash'])):
                raise WrongPassword('Current password is incorrect')
            if not is_self and actor.get('role') != 'admin':
                raise Forbidden('Only an administrator can change another account')
            if check_password(new_password, row['password_hash']):
                raise ReusedPassword('New password must differ from the current one')
            try:
                self.policy.validate(new_password, row['username'])
            except WeakPassword as error:
                raise refused(error) from None
            # Any rotation invalidates the seeded plaintext; a file that cannot
            # be retired is a refusal, not a silent leftover.
            self._retire_bootstrap(row['username'])
            self.store.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(new_password), identity))
            if is_self:
                self.store.execute('DELETE FROM sessions WHERE user_id=?', (identity,))
                self._retire_bootstrap(row['username'])
        self.store.audit(actor.get('username', 'anonymous'), 'user.password_rotated', identity,
                         {'username': row['username'], 'self': is_self})
        return self.metadata(self.by_id(identity))

    def authenticate(self, username, password):
        account = self.find(username)
        encoded = account['password_hash'] if account else self.store.dummy_hash
        if not check_password(password, encoded):
            return None
        if account and needs_rehash(encoded):
            self.store.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(password), account['id']))
        return account

    def _retire_bootstrap(self, username):
        """Drop one plaintext line from the private bootstrap file, then the file when empty."""
        root = getattr(self.store, 'path', None)
        if root is None:
            return
        target = Path(root) / BOOTSTRAP_FILE
        try:
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        except OSError:
            raise BootstrapFile('Bootstrap credentials file could not be retired') from None
        try:
            with os.fdopen(descriptor) as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                    raise BootstrapFile('Bootstrap credentials file could not be retired')
                lines = source.read().splitlines()
            kept = [line for line in lines if line.split(':', 1)[0].strip() != username]
            if len(kept) == len(lines):
                return
            if not kept:
                target.unlink()
                return
            temporary = target.with_name(f'.{BOOTSTRAP_FILE}.{secrets.token_hex(6)}')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, 'w') as output:
                    output.write('\n'.join(kept) + '\n')
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        except OSError:
            raise BootstrapFile('Bootstrap credentials file could not be retired') from None


class NewUser(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=512)
    role: str = Field(min_length=1, max_length=16)


class RoleChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: str = Field(min_length=1, max_length=16)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    new_password: str = Field(min_length=1, max_length=512)
    current_password: str | None = Field(default=None, max_length=512)


def install_users(app):
    users = app.state.users

    def human(*roles):
        def dependency(current=Depends(app.state.role_dependency(*roles))):
            if current.get('principal_type') == 'agent':
                raise HTTPException(403, 'Human administrator session required')
            return current
        return dependency

    @app.get('/api/users')
    def listing(current=Depends(human('admin'))):
        return {'users': users.list(), 'roles': list(ROLES)}

    @app.post('/api/users', status_code=201)
    def create(data: NewUser, current=Depends(human('admin'))):
        try:
            user = users.create(data.username, data.password, data.role, current['username'])
        except UserError as error:
            raise HTTPException(error.status, str(error)) from None
        return {'user': user}

    @app.patch('/api/users/{identity}')
    def change_role(identity: str, data: RoleChange, current=Depends(human('admin'))):
        try:
            user = users.set_role(identity, data.role, current['username'])
        except UserError as error:
            raise HTTPException(error.status, str(error)) from None
        return {'user': user}

    @app.post('/api/users/{identity}/password')
    def rotate(identity: str, data: PasswordChange, request: Request, response: Response,
               current=Depends(app.state.user_dependency)):
        if current.get('principal_type') == 'agent':
            raise HTTPException(403, 'Human administrator session required')
        try:
            user = users.rotate(identity, data.new_password, current, data.current_password)
        except UserError as error:
            raise HTTPException(error.status, str(error)) from None
        if current['id'] == identity:
            token, csrf = app.state.store.create_session(user)
            response.set_cookie('kw_session', token, httponly=True, samesite='strict',
                                secure=request.url.scheme == 'https', max_age=8 * 3600, path='/')
            return {'user': user, 'csrf_token': csrf}
        return {'user': user}
