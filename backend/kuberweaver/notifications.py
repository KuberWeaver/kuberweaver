"""Durable webhook observer. Configuration and credential URLs are never public."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import ipaddress
import math
import re
from urllib.parse import urlsplit
import json
import os
from pathlib import Path
import sqlite3

import ssl
from urllib.error import HTTPError
from urllib.request import (Request, HTTPRedirectHandler, HTTPSHandler,
                            ProxyHandler, build_opener)


from .providers import resolve_provider
from .storage import now


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NotificationError(RuntimeError):
    """Fixed safe messages only; never propagate upstream exceptions."""


class WebhookNotifications:
    def __init__(self, app, config):
        self.app = app
        try:
            allowed = {'enabled', 'transport', 'endpoint_secret_ref', 'allow_loopback_http', 'timeout', 'max_attempts'}
            if not isinstance(config, dict) or config.keys() - allowed:
                raise ValueError
            merged = {'allow_loopback_http': False, 'timeout': 5.0, 'max_attempts': 3, **config}
            if (type(merged['enabled']) is not bool or type(merged['allow_loopback_http']) is not bool
                    or merged['transport'] not in ('json', 'discord', 'teams')
                    or not isinstance(merged['endpoint_secret_ref'], str)
                    or len(merged['endpoint_secret_ref']) > 128
                    or not re.fullmatch(r'[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)*', merged['endpoint_secret_ref'])
                    or type(merged['timeout']) not in (int, float)
                    or not math.isfinite(merged['timeout']) or not 0 < merged['timeout'] <= 60
                    or type(merged['max_attempts']) is not int or not 1 <= merged['max_attempts'] <= 5):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise NotificationError('Invalid notification configuration') from None
        self.config = merged
        directory = Path(app.state.state_dir) / 'notifications'
        try:
            if directory.is_symlink():
                raise OSError
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            self.path = directory / 'deliveries.sqlite3'
            if self.path.is_symlink():
                raise OSError
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            os.close(fd)
            with self._locked(), self._connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL, error TEXT, updated TEXT NOT NULL)')
                db.execute("UPDATE deliveries SET status='uncertain', error='Interrupted delivery; remote acceptance unknown', updated=? WHERE status='in_flight'", (now(),))
        except (OSError, sqlite3.Error):
            raise NotificationError('Notification storage unavailable') from None

    @contextmanager
    def _locked(self):
        fd = None
        try:
            fd = os.open(self.path.parent / 'delivery.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except OSError:
            raise NotificationError('Notification storage unavailable or busy') from None
        finally:
            if fd is not None:
                os.close(fd)

    @contextmanager
    def _connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        except sqlite3.Error:
            raise NotificationError('Notification storage unavailable') from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ('id', 'status', 'attempts', 'error', 'updated')}

    def read(self):
        with self._connect() as db:
            rows = db.execute('SELECT * FROM deliveries ORDER BY rowid').fetchall()
        return {'deliveries': [self._public(row) for row in rows]}

    @staticmethod
    def _payload(payload):
        keys = ('event', 'job_id', 'status', 'operation', 'environment', 'time')
        if not isinstance(payload, dict):
            raise NotificationError('Invalid notification payload')
        clean = {key: payload.get(key) for key in keys}
        validation = (clean['operation'] == 'validate' and clean['event'] == 'before_plan'
                      and clean['job_id'] is None and clean['status'] == 'planning')
        if (clean['event'] not in ('before_plan', 'after_approval', 'job_started', 'job_finished')
                or (clean['operation'] not in ('execute', 'rollback') and not validation)
                or any(value is not None and (not isinstance(value, str) or len(value) > 128
                       or not re.fullmatch(r'[A-Za-z0-9_.:+-]+', value)) for value in clean.values())
                or not clean['status'] or not clean['time']):
            raise NotificationError('Invalid notification payload')
        return clean

    def _endpoint(self, endpoint):
        parsed = urlsplit(endpoint)
        if (len(endpoint) > 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in endpoint)
                or parsed.scheme not in ('https', 'http') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise NotificationError('Invalid endpoint')
        parsed.port
        if parsed.scheme == 'http' and not (self.config['allow_loopback_http']
                and ipaddress.ip_address(parsed.hostname).is_loopback):
            raise NotificationError('Invalid endpoint')
        return endpoint

    def _body(self, payload):
        text = json.dumps(payload, separators=(',', ':'))
        if self.config['transport'] == 'discord':
            return {'content': text, 'allowed_mentions': {'parse': []}}
        if self.config['transport'] == 'teams':
            return {'text': text}
        return payload

    @contextmanager
    def _post(self, endpoint, body, identity):
        # urllib does not log credential-bearing request URLs (httpx does at INFO).
        opener = build_opener(ProxyHandler({}), _NoRedirect(),
                              HTTPSHandler(context=ssl.create_default_context()))
        request = Request(endpoint, data=json.dumps(body).encode(), method='POST',
                          headers={'Content-Type': 'application/json',
                                   'Idempotency-Key': identity})
        try:
            response = opener.open(request, timeout=self.config['timeout'])
        except HTTPError as response_error:
            try:
                yield response_error.code
            finally:
                response_error.close()
        else:
            with response:
                yield response.status  # Never consume/log/store the response body.

    def send(self, payload):
        if not self.config['enabled']:
            return {'status': 'disabled'}
        payload = self._payload(payload)
        identity = hashlib.sha256(json.dumps([payload.get(key) for key in ('event', 'job_id', 'operation', 'status', 'time')], separators=(',', ':')).encode()).hexdigest()
        with self._connect() as db:
            db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?,0,NULL,?)',
                       (identity, json.dumps(payload), 'pending', now()))
        with self._connect() as db:
            return self._public(db.execute('SELECT * FROM deliveries WHERE id=?', (identity,)).fetchone())

    def _deliver(self, identity, *, include_uncertain=False):
        with self._locked():
            return self._deliver_locked(identity, include_uncertain=include_uncertain)

    def _deliver_locked(self, identity, *, include_uncertain=False):
        with self._connect() as db:
            row = db.execute('SELECT * FROM deliveries WHERE id=?', (identity,)).fetchone()
            if row['status'] in ('delivered', 'exhausted') or (row['status'] in ('uncertain', 'in_flight') and not include_uncertain):
                return self._public(row)
            if row['attempts'] >= self.config['max_attempts']:
                db.execute("UPDATE deliveries SET status='exhausted', updated=? WHERE id=?", (now(), identity))
                return self._public(db.execute('SELECT * FROM deliveries WHERE id=?', (identity,)).fetchone())
            db.execute('UPDATE deliveries SET status=?, attempts=attempts+1, updated=? WHERE id=?', ('in_flight', now(), identity))
        status, error = 'delivered', None
        attempted_http = False
        try:
            with resolve_provider(self.app, 'secrets').resolve(self.config['endpoint_secret_ref']) as secret:
                endpoint = self._endpoint(secret.bytes().decode('utf-8'))
                attempted_http = True
                with self._post(endpoint, self._body(json.loads(row['payload'])), identity) as code:
                    if not 200 <= code < 300:
                        status, error = 'pending', 'Remote delivery rejected'
        except Exception:
            status, error = (('uncertain', 'Remote acceptance unknown') if attempted_http
                             else ('pending', 'Delivery unavailable'))
        if status == 'pending' and row['attempts'] + 1 >= self.config.get('max_attempts', 3):
            status = 'exhausted'
        with self._connect() as db:
            db.execute('UPDATE deliveries SET status=?, error=?, updated=? WHERE id=?', (status, error, now(), identity))
            return self._public(db.execute('SELECT * FROM deliveries WHERE id=?', (identity,)).fetchone())

    def retry_pending(self, *, limit=100, include_uncertain=False):
        if type(limit) is not int or not 1 <= limit <= 100 or type(include_uncertain) is not bool:
            raise NotificationError('Invalid retry request')
        if not self.config['enabled']:
            return {'deliveries': []}
        with self._connect() as db:
            identities = [row['id'] for row in db.execute("SELECT id FROM deliveries WHERE status='pending' OR (? AND status='uncertain') ORDER BY rowid LIMIT ?", (include_uncertain, limit))]
        return {'deliveries': [self._deliver(identity, include_uncertain=include_uncertain) for identity in identities]}

    def describe(self):
        return {'id': 'webhook', 'kind': 'notifications', 'status': 'configured' if self.config['enabled'] else 'disabled',
                'external_delivery': True, 'transport': self.config['transport']}


def register_notifications(registry, app):
    """Optional builtin, fail closed without disturbing other providers."""
    provider = None
    metadata = {'status': 'unavailable', 'external_delivery': True,
                'error': 'Notification configuration unavailable or invalid'}
    try:
        target = Path(app.state.state_dir) / 'config' / 'notifications.json'
        if target.is_symlink() or target.stat().st_size > 16384:
            raise ValueError
        config = json.loads(target.read_text())
        candidate = WebhookNotifications(app, config)
        if candidate.config['enabled']:
            provider = candidate
            metadata = {'status': 'configured', 'external_delivery': True,
                        'transport': candidate.config['transport']}
        else:
            metadata = {'status': 'disabled', 'external_delivery': True}
    except Exception:
        pass  # No paths, credential references, upstream errors or config contents.
    registry.register('notifications', 'webhook', provider, metadata)
