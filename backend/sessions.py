"""Browser sessions: hashed identifiers, absolute and idle expiry, bound CSRF.

A session token carries its issue time in whole seconds followed by a random
secret. The store keeps only the SHA-256 of the whole token, so the issue time
cannot be altered without invalidating the row, and the absolute deadline stays
derivable after a restart without a second timestamp column. The row's `expires`
is the idle deadline: it moves forward when a request lands in the last minute
of the window, which keeps writes to roughly one per idle period instead of one
per request.
"""
from __future__ import annotations

import hashlib
import secrets
import time

SESSION_COOKIE = 'kw_session'
ABSOLUTE_TIMEOUT = 8 * 3600
IDLE_TIMEOUT = 30 * 60
REFRESH_MARGIN = 60
SEPARATOR = '.'


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issued_at(token):
    """Issue time from a token, or None when the token predates the format."""
    if not isinstance(token, str):
        return None
    prefix, separator, _ = token.partition(SEPARATOR)
    if not separator or not prefix.isdigit():
        return None
    return float(prefix)


class Sessions:
    COOKIE = SESSION_COOKIE

    def __init__(self, store, *, absolute_timeout=ABSOLUTE_TIMEOUT, idle_timeout=IDLE_TIMEOUT,
                 refresh_margin=REFRESH_MARGIN, clock=time.time):
        self.store = store
        self.absolute_timeout = absolute_timeout
        self.idle_timeout = idle_timeout
        self.refresh_margin = refresh_margin
        self.clock = clock

    def create(self, account):
        current = self.clock()
        token = f'{int(current)}{SEPARATOR}{secrets.token_urlsafe(32)}'
        csrf = secrets.token_urlsafe(32)
        self.store.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                           (digest(token), account['id'], csrf, current + self.idle_timeout))
        return token, csrf

    def resolve(self, token):
        issued = issued_at(token)
        if issued is None:
            return None
        current = self.clock()
        row = self.store.one(
            'SELECT users.id,users.username,users.role,sessions.csrf,sessions.expires '
            'FROM sessions JOIN users ON user_id=users.id WHERE token_hash=?',
            (digest(token),))
        if not row:
            return None
        absolute_deadline = issued + self.absolute_timeout
        if current >= row['expires'] or current >= absolute_deadline:
            self.store.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))
            return None
        if row['expires'] - current <= self.refresh_margin:
            self.store.execute('UPDATE sessions SET expires=? WHERE token_hash=?',
                               (min(current + self.idle_timeout, absolute_deadline), digest(token)))
        return {key: row[key] for key in ('id', 'username', 'role', 'csrf')}

    def revoke(self, token):
        if token:
            self.store.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def revoke_user(self, identity):
        self.store.execute('DELETE FROM sessions WHERE user_id=?', (identity,))

    def prune(self):
        self.store.execute('DELETE FROM sessions WHERE expires<=?', (self.clock(),))

    def set_cookie(self, response, token, *, secure):
        response.set_cookie(self.COOKIE, token, max_age=self.absolute_timeout, path='/',
                            httponly=True, samesite='strict', secure=secure)

    def clear_cookie(self, response, *, secure):
        response.delete_cookie(self.COOKIE, path='/', httponly=True, samesite='strict', secure=secure)
