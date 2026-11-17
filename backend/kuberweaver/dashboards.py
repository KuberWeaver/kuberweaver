"""Registry of external observability dashboards; KuberWeaver renders none of them.

A record is a bookmark with enough structure for the portal to link to the tool
and for a later read-only probe to reach a health endpoint on the same origin.
Nothing here stores credentials, scrapes a dashboard or frames a page: the embed
flag only records that the operator asked for embedding, and it is accepted only
when the URL origin is on the deployment allowlist.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .configuration import ConfigurationError
from .storage import now

Kind = Literal['grafana', 'uptime-kuma', 'prometheus', 'netdata', 'custom']
DEFAULT_HEALTH_PATHS = {
    'grafana': '/api/health',
    'uptime-kuma': '/',
    'prometheus': '/-/healthy',
    'netdata': '/api/v1/info',
    'custom': '/',
}
EMBED_ORIGINS_VARIABLE = 'KUBERWEAVER_DASHBOARD_EMBED_ORIGINS'


def origin_of(url):
    """Return the scheme/host/port origin of an http(s) URL, lowercased."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    port = parsed.port
    if scheme not in ('http', 'https') or not host:
        raise ValueError('Expected an http or https origin')
    if ':' in host:
        host = f'[{host}]'
    default = 443 if scheme == 'https' else 80
    return f'{scheme}://{host.lower()}' + ('' if port in (None, default) else f':{port}')


def _allowlisted_origin(value):
    try:
        # A trailing slash is tolerated; a path, query, fragment or credentials
        # would make the entry a URL rather than the origin it claims to be.
        parsed = urlsplit(value)
        if (not value or len(value) > 300 or any(ord(c) < 33 or ord(c) > 126 for c in value)
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or parsed.username is not None or parsed.password is not None):
            raise ValueError
        return origin_of(value)
    except (TypeError, ValueError):
        raise ConfigurationError(
            f'{EMBED_ORIGINS_VARIABLE} must list origins such as https://grafana.example, '
            f'got {value[:80]!r}') from None


def configured_embed_origins():
    """Read the comma-separated origin allowlist; an unusable value is reported."""
    raw = (os.environ.get(EMBED_ORIGINS_VARIABLE) or '').strip()
    if not raw:
        return ()
    origins = [_allowlisted_origin(entry.strip()) for entry in raw.split(',')]
    if len(set(origins)) != len(origins):
        raise ConfigurationError(f'{EMBED_ORIGINS_VARIABLE} lists an origin more than once')
    return tuple(origins)


class DashboardSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    label: str = Field(min_length=1, max_length=80)
    kind: Kind
    url: str = Field(min_length=8, max_length=2048)
    health_path: str | None = Field(default=None, max_length=512)
    embed: bool = False
    enabled: bool = True

    @field_validator('label')
    @classmethod
    def plain_label(cls, value):
        if value.strip() != value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Expected a label without control characters or surrounding spaces')
        return value

    @field_validator('url')
    @classmethod
    def http_url(cls, value):
        try:
            if any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError
            parsed = urlsplit(value)
            if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
                raise ValueError
            if parsed.username is not None or parsed.password is not None:
                raise ValueError
            if parsed.fragment or parsed.netloc.endswith(':'):
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('Expected an absolute http or https URL without credentials or fragment') from None
        return value

    @field_validator('health_path')
    @classmethod
    def same_origin_path(cls, value):
        if value is None:
            return value
        if (not value.startswith('/') or value.startswith('//')
                or any(ord(c) < 33 or ord(c) > 126 for c in value)
                or '?' in value or '#' in value
                or any(segment in ('.', '..') for segment in value.split('/'))):
            raise ValueError('Expected an absolute path on the dashboard origin')
        return value


class DashboardPatch(DashboardSpec):
    """Unset fields are absent changes; only health_path accepts an explicit null."""
    revision: int = Field(ge=1)
    label: str = Field(default=None, min_length=1, max_length=80)  # type: ignore[assignment]
    kind: Kind = Field(default=None)  # type: ignore[assignment]
    url: str = Field(default=None, min_length=8, max_length=2048)  # type: ignore[assignment]
    embed: bool = Field(default=None)  # type: ignore[assignment]
    enabled: bool = Field(default=None)  # type: ignore[assignment]


_BASE_FIELDS = tuple(DashboardSpec.model_fields)


class Dashboards:
    def __init__(self, app, *, embed_origins=(), embed_error=None):
        self.app = app
        self.store = app.state.store
        self.embed_origins = tuple(embed_origins)
        self.embed_error = embed_error
        self.store.execute('CREATE TABLE IF NOT EXISTS dashboards(id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def _stored(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise HTTPException(404, 'Unknown dashboard')
        row = self.store.one('SELECT body FROM dashboards WHERE id=?', (identity,))
        if row is None:
            raise HTTPException(404, 'Unknown dashboard')
        return json.loads(row['body'])

    def present(self, record):
        url = record['url']
        origin = origin_of(url)
        return {**record, 'origin': origin,
                'probe_path': record['health_path'] or DEFAULT_HEALTH_PATHS[record['kind']],
                'embeddable': bool(record['embed']) and origin in self.embed_origins}

    def get(self, identity):
        return self.present(self._stored(identity))

    def list(self):
        return [self.present(json.loads(row['body']))
                for row in self.store.all('SELECT body FROM dashboards ORDER BY rowid DESC')]

    def collection(self):
        return {'dashboards': self.list(), 'embed_origins': list(self.embed_origins),
                'embed_error': self.embed_error}

    def _checked(self, spec):
        value = DashboardSpec.model_validate(spec).model_dump()
        if value['embed'] and origin_of(value['url']) not in self.embed_origins:
            raise HTTPException(422, 'Embedding requires an allowlisted origin')
        return value

    def create(self, spec, actor):
        try:
            value = self._checked(spec)
        except ValidationError:
            raise HTTPException(422, 'Invalid dashboard specification') from None
        record = {**value, 'id': secrets.token_hex(16), 'revision': 1,
                  'created_at': now(), 'created_by': actor['username']}
        with self.store.lock:
            self.store.execute('INSERT INTO dashboards(id,body) VALUES(?,?)',
                               (record['id'], json.dumps(record)))
            self.store.audit(actor['username'], 'dashboard.created', record['id'],
                             {'label': record['label'], 'kind': record['kind'], 'embed': record['embed']})
            return self.get(record['id'])

    def update(self, identity, spec, actor):
        with self.store.lock:
            current = self._stored(identity)
            try:
                changes = DashboardPatch.model_validate(spec).model_dump(exclude_unset=True)
                patch = {key: value for key, value in changes.items() if key != 'revision'}
                value = DashboardSpec.model_validate(
                    {**{key: current[key] for key in _BASE_FIELDS}, **patch}).model_dump()
            except ValidationError:
                raise HTTPException(422, 'Invalid dashboard specification') from None
            if changes['revision'] != current['revision']:
                raise HTTPException(409, 'Dashboard revision changed')
            if value['embed'] and origin_of(value['url']) not in self.embed_origins:
                raise HTTPException(422, 'Embedding requires an allowlisted origin')
            record = {**current, **value, 'revision': current['revision'] + 1}
            self.store.execute('UPDATE dashboards SET body=? WHERE id=?', (json.dumps(record), identity))
            self.store.audit(actor['username'], 'dashboard.updated', identity,
                             {'label': record['label'], 'kind': record['kind'], 'embed': record['embed']})
            return self.get(identity)

    def delete(self, identity, revision, actor):
        if type(revision) is not int or revision < 1:
            raise HTTPException(422, 'Invalid dashboard revision')
        with self.store.lock:
            current = self._stored(identity)
            if revision != current['revision']:
                raise HTTPException(409, 'Dashboard revision changed')
            self.store.execute('DELETE FROM dashboards WHERE id=?', (identity,))
            if self.store.one('SELECT 1 AS present FROM dashboards WHERE id=?', (identity,)) is not None:
                raise HTTPException(503, 'Dashboard deletion could not be confirmed')
            self.store.audit(actor['username'], 'dashboard.deleted', identity,
                             {'label': current['label'], 'kind': current['kind']})
            return {'deleted': True, 'id': identity}


def install_dashboards_api(app):
    if getattr(app.state, '_dashboards_api_installed', False):
        return
    # A typo in an optional allowlist must not brick the portal: the registry
    # keeps working, embedding stays refused and the collection reports why.
    try:
        embed_origins, embed_error = configured_embed_origins(), None
    except ConfigurationError as error:
        embed_origins, embed_error = (), str(error)
    service = app.state.dashboards = Dashboards(app, embed_origins=embed_origins,
                                                embed_error=embed_error)
    original_count = len(app.router.routes)

    @app.get('/api/dashboards')
    def listing(current=Depends(app.state.user_dependency)):
        return service.collection()

    @app.get('/api/dashboards/{identity}')
    def detail(identity: str, current=Depends(app.state.user_dependency)):
        return service.get(identity)

    @app.post('/api/dashboards', status_code=201)
    def create(data: DashboardSpec, current=Depends(app.state.role_dependency('admin'))):
        return service.create(data.model_dump(), current)

    @app.patch('/api/dashboards/{identity}')
    def update(identity: str, data: DashboardPatch,
               current=Depends(app.state.role_dependency('admin'))):
        return service.update(identity, data.model_dump(exclude_unset=True), current)

    @app.delete('/api/dashboards/{identity}')
    def delete(identity: str, revision: int, current=Depends(app.state.role_dependency('admin'))):
        return service.delete(identity, revision, current)

    # Tests and optional installers can run after the SPA catch-all exists.
    added = app.router.routes[original_count:]
    del app.router.routes[original_count:]
    before_spa = next((i for i, route in enumerate(app.router.routes)
                       if getattr(route, 'path', None) == '/{asset_path:path}'), len(app.router.routes))
    app.router.routes[before_spa:before_spa] = added
    app.state._dashboards_api_installed = True
