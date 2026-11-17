"""Registry of external observability dashboards; KuberWeaver renders none of them.

A record is a bookmark with enough structure for the portal to link to the tool
and for the read-only probe to reach a health endpoint on the same origin.
Nothing here stores credentials, scrapes a dashboard or proxies its data. The
embed flag records that the operator asked for embedding; the embed page frames
the recorded URL only when that origin is on the deployment allowlist, and
answers with a deep link otherwise.
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .configuration import ConfigurationError
from .dashboard_health import (DISABLED, DRIFT_STATUSES, INTERVAL_VARIABLE, PROBE_STATUSES, UNKNOWN,
                               DashboardProbeLoop, probe_dashboard, probe_settings)
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
# The page frames exactly one origin, so frame-src is either that origin or
# 'none'. default-src 'none' keeps the document itself from loading anything
# remote; only the inline stylesheet is allowed, and the page is framable by the
# portal alone.
EMBED_PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                  "form-action 'none'; frame-src {source}; frame-ancestors 'self'")
_EMBED_STYLE = (
    'html,body{margin:0;height:100%;background:#0b0d10;color:#e6e8eb;'
    'font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}'
    'body.fallback{display:grid;place-items:center}'
    'iframe{display:block;width:100%;height:100%;border:0}'
    '.tab{position:fixed;right:.75rem;bottom:.75rem;padding:.3rem .55rem;'
    'border-radius:.375rem;background:rgb(17 19 23 / .85);color:#e6e8eb;'
    'font-size:12px;text-decoration:none}'
    '.tab:hover,.tab:focus{background:#111}'
    'main{max-width:34rem;padding:1.5rem}'
    'a.open{color:#7cc4ff}'
)


def embed_document(record, *, framed, reason=None):
    """Render the embed page; the record values are escaped into the markup."""
    label = html.escape(record['label'], quote=True)
    url = html.escape(record['url'], quote=True)
    head = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{label}</title>\n<style>{_EMBED_STYLE}</style>\n</head>\n')
    if framed:
        return head + (
            '<body>\n'
            f'<iframe src="{url}" title="{label}" loading="lazy" '
            'referrerpolicy="no-referrer"></iframe>\n'
            f'<a class="tab" href="{url}" target="_blank" rel="noopener noreferrer">'
            'Open in a new tab</a>\n</body>\n</html>\n')
    if reason is None:
        raise ValueError('A fallback embed page needs a refusal reason')
    return head + (
        '<body class="fallback">\n<main>\n'
        f'<h1>{label}</h1>\n'
        f'<p>This dashboard is not embedded: {html.escape(reason)}. '
        'Open it in a new tab instead.</p>\n'
        f'<p><a class="open" href="{url}" target="_blank" rel="noopener noreferrer">'
        f'Open {label}</a></p>\n</main>\n</body>\n</html>\n')


def _embed_refusal(record):
    if not record['embed']:
        return 'embedding is not requested for this record'
    if not record['enabled']:
        return 'the record is disabled'
    return 'its origin is not on the deployment allowlist'


def _health_view(status, *, observed_at=None, http_status=None, latency_ms=None,
                 detail=None, drift=False):
    """The status column has one shape whether or not an observation exists."""
    return {'status': status, 'observed_at': observed_at, 'http_status': http_status,
            'latency_ms': latency_ms, 'detail': detail, 'drift': bool(drift)}


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
    def __init__(self, app, *, embed_origins=(), embed_error=None, probe=None, fetch=None):
        self.app = app
        self.store = app.state.store
        self.embed_origins = tuple(embed_origins)
        self.embed_error = embed_error
        self.probe_policy = probe or probe_settings()
        self.fetch = fetch
        self.store.execute('CREATE TABLE IF NOT EXISTS dashboards(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.store.execute('CREATE TABLE IF NOT EXISTS dashboard_health('
                           'dashboard_id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def _stored(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise HTTPException(404, 'Unknown dashboard')
        row = self.store.one('SELECT body FROM dashboards WHERE id=?', (identity,))
        if row is None:
            raise HTTPException(404, 'Unknown dashboard')
        return json.loads(row['body'])

    def _probe_url(self, record):
        """A probe never leaves the registered origin; the path is validated on write."""
        return origin_of(record['url']) + (record['health_path']
                                           or DEFAULT_HEALTH_PATHS[record['kind']])

    def _health_row(self, identity):
        row = self.store.one('SELECT body FROM dashboard_health WHERE dashboard_id=?', (identity,))
        if row is None:
            return None
        try:
            return json.loads(row['body'])
        except (TypeError, ValueError):
            return None

    def _health(self, record, stored):
        if not record['enabled']:
            return _health_view(DISABLED, detail='the record is disabled and is not probed')
        if stored is None:
            return _health_view(UNKNOWN, detail='the record has not been probed yet')
        if stored.get('probe_url') != self._probe_url(record):
            # An observation of a previous URL would describe a different tool.
            return _health_view(UNKNOWN, observed_at=stored.get('observed_at'),
                                detail='the record changed since the last observation')
        state = stored.get('status')
        if state not in PROBE_STATUSES:
            state = UNKNOWN
        return _health_view(state, observed_at=stored.get('observed_at'),
                            http_status=stored.get('http_status'),
                            latency_ms=stored.get('latency_ms'),
                            detail=stored.get('detail'), drift=state in DRIFT_STATUSES)

    def present(self, record, health=None):
        url = record['url']
        origin = origin_of(url)
        return {**record, 'origin': origin,
                'probe_path': record['health_path'] or DEFAULT_HEALTH_PATHS[record['kind']],
                'embeddable': bool(record['embed']) and origin in self.embed_origins,
                'health': health if health is not None else self._health(
                    record, self._health_row(record['id']))}

    def get(self, identity):
        return self.present(self._stored(identity))

    def list(self):
        stored = {}
        for row in self.store.all('SELECT dashboard_id, body FROM dashboard_health'):
            try:
                stored[row['dashboard_id']] = json.loads(row['body'])
            except (TypeError, ValueError):
                continue
        records = []
        for row in self.store.all('SELECT body FROM dashboards ORDER BY rowid DESC'):
            record = json.loads(row['body'])
            records.append(self.present(record, self._health(record, stored.get(record['id']))))
        return records

    def collection(self):
        return {'dashboards': self.list(), 'embed_origins': list(self.embed_origins),
                'embed_error': self.embed_error,
                'probe': {'interval_seconds': self.probe_policy.interval,
                          'timeout_seconds': self.probe_policy.timeout,
                          'error': self.probe_policy.error}}

    def embed(self, identity):
        record = self.present(self._stored(identity))
        # A disabled record is not framed even when it is still stored as
        # embeddable: disabling it is the operator asking for the link only.
        framed = record['embeddable'] and record['enabled']
        return HTMLResponse(
            embed_document(record, framed=framed,
                           reason=None if framed else _embed_refusal(record)),
            headers={'Content-Security-Policy': EMBED_PAGE_CSP.format(
                         source=record['origin'] if framed else "'none'"),
                     'X-Frame-Options': 'SAMEORIGIN',
                     'Cache-Control': 'no-store'})

    def _observe(self, record):
        probe_url = self._probe_url(record)
        outcome = probe_dashboard(probe_url, record['kind'], timeout=self.probe_policy.timeout,
                                  fetch=self.fetch)
        return {**outcome, 'observed_at': now(), 'probe_url': probe_url,
                'revision': record['revision']}

    def _write_health(self, identity, observation, actor=None):
        """Store one observation only while its record still exists."""
        with self.store.lock:
            if self.store.one('SELECT 1 AS present FROM dashboards WHERE id=?',
                              (identity,)) is None:
                return False
            self.store.execute('INSERT OR REPLACE INTO dashboard_health(dashboard_id, body) '
                               'VALUES(?,?)', (identity, json.dumps(observation)))
            if actor is not None:
                self.store.audit(actor['username'], 'dashboard.probed', identity,
                                 {'status': observation['status'],
                                  'http_status': observation['http_status']})
            return True

    def probe(self, identity, actor=None):
        """Observe one enabled record now; the registry itself is left untouched."""
        record = self._stored(identity)
        if not record['enabled']:
            raise HTTPException(409, 'A disabled dashboard is not probed')
        observation = self._observe(record)
        if not self._write_health(record['id'], observation, actor):
            raise HTTPException(404, 'Unknown dashboard')
        return self.get(identity)

    def probe_enabled(self):
        """One sweep over enabled records; a failed record is recorded, never fixed."""
        probed = 0
        for row in self.store.all('SELECT body FROM dashboards'):
            try:
                record = json.loads(row['body'])
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict) or record.get('enabled') is not True:
                continue
            observation = self._observe(record)
            probed += int(self._write_health(record['id'], observation))
        return probed

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
            self.store.execute('DELETE FROM dashboard_health WHERE dashboard_id=?', (identity,))
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
    probe_policy = probe_settings()
    service = app.state.dashboards = Dashboards(app, embed_origins=embed_origins,
                                                embed_error=embed_error, probe=probe_policy)
    loop = app.state.dashboard_probe_loop = DashboardProbeLoop(service.probe_enabled,
                                                               interval=probe_policy.interval)
    # The default test suite must not open sockets. A test that exercises the
    # periodic loop opts in by naming an interval explicitly.
    if probe_policy.interval and (not getattr(app.state, 'testing', False)
                                  or os.environ.get(INTERVAL_VARIABLE)):
        app.state.background_services.append(loop)
    original_count = len(app.router.routes)

    @app.get('/api/dashboards')
    def listing(current=Depends(app.state.user_dependency)):
        return service.collection()

    @app.get('/api/dashboards/{identity}')
    def detail(identity: str, current=Depends(app.state.user_dependency)):
        return service.get(identity)

    @app.get('/api/dashboards/{identity}/embed', response_class=HTMLResponse)
    def embed(identity: str, current=Depends(app.state.user_dependency)):
        return service.embed(identity)

    @app.post('/api/dashboards/{identity}/probe')
    def probe(identity: str, current=Depends(app.state.role_dependency('admin'))):
        return service.probe(identity, current)

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
