"""Service Studio: artifact metadata, immutable offerings and owned services.

Three deliberately narrow surfaces:

* an artifact register stores *metadata only* — no fetch, no probe, no job;
* publishing an offering copies a saved blueprint's graph and pins the graph
  hash plus the executable fingerprints that were reviewed at publication;
* a service request is an ordinary job created through the existing Jobs
  service, so approval stays an explicit administrator decision and the
  existing global job surface is never widened.

My Services is ownership-scoped: a caller only ever sees the service requests
that its own principal created.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field, ValidationError

from .jobs import digest
from .models import Graph, StrictModel
from .storage import now

MAX_REFERENCE = 512
MAX_PIN = 256

# Artifact locators are comparable strings, not documents: no whitespace, no
# control characters, and nothing that looks like embedded credentials.
_LOCATOR = re.compile(r'^[\x21-\x7e]+\Z')
_PIN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:+/=@-]*\Z')
_LABEL = re.compile(r'^[\x20-\x7e]+\Z')
# A userinfo segment before the first path separator is a credential carrier.
_USERINFO = re.compile(r'://[^/\s]*@')
# Query/fragment parameters named like secrets are refused outright. The value
# is never echoed back, so a rejected reference cannot leak through the API.
_SECRET_PARAMETER = re.compile(
    r'(?:^|[?&;\s,])(?:password|passwd|passphrase|token|secret|api[_-]?key|apikey'
    r'|access[_-]?key|private[_-]?key|credential|credentials|signature|sig)=', re.I)


class ArtifactRequest(StrictModel):
    kind: Literal['oci', 'vm-image', 'ansible-role']
    reference: str = Field(min_length=1, max_length=MAX_REFERENCE)
    pin: str | None = Field(default=None, max_length=MAX_PIN)


class OfferingRequest(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    blueprint_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')


def checked_reference(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= MAX_REFERENCE
            or not _LOCATOR.match(value)
            or _USERINFO.search(value) or _SECRET_PARAMETER.search(value)):
        raise HTTPException(422, 'Artifact reference must be a bounded, secret-free locator')
    return value


def checked_pin(value):
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_PIN or not _PIN.match(value):
        raise HTTPException(422, 'Artifact pin must be a bounded immutable locator')
    return value


def checked_title(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 160 \
            or value != value.strip() or not _LABEL.match(value):
        raise HTTPException(422, 'Offering title must be a bounded single-line label')
    return value


class Studio:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store
        self.jobs = app.state.jobs
        self.catalog = app.state.catalog
        self.store.execute('CREATE TABLE IF NOT EXISTS studio_artifacts('
                           'id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.store.execute('CREATE TABLE IF NOT EXISTS studio_offerings('
                           'id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    # -- artifact register -------------------------------------------------

    def register_artifact(self, spec, actor):
        kind = spec['kind']
        reference = checked_reference(spec.get('reference'))
        pin = checked_pin(spec.get('pin'))
        record = {'id': secrets.token_hex(16), 'kind': kind, 'reference': reference,
                  'pin': pin, 'created_at': now(), 'created_by': actor['username']}
        with self.store.lock:
            self.store.put('studio_artifacts', record['id'], record)
            stored = self._artifact(record['id'])
            self.store.audit(actor['username'], 'studio.artifact_registered', record['id'],
                             {'kind': kind, 'pinned': pin is not None})
            return stored

    def _artifact(self, identity):
        record = self.store.get('studio_artifacts', identity)
        if not isinstance(record, dict):
            raise HTTPException(503, 'Artifact registration was not recorded')
        return {key: record.get(key) for key in ('id', 'kind', 'reference', 'pin')}

    def artifacts(self):
        rows = []
        for row in self.store.all('SELECT body FROM studio_artifacts ORDER BY rowid DESC'):
            try:
                record = json.loads(row['body'])
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict):
                rows.append({key: record.get(key) for key in ('id', 'kind', 'reference', 'pin')})
        return rows

    # -- immutable offerings ----------------------------------------------

    def _pins(self, graph):
        """The reviewed graph hash and executable fingerprints, computed now."""
        manifests = self.catalog.discover()
        try:
            return {'graph_hash': digest(graph),
                    'manifest_hashes': {node['plugin_id']: digest(manifests[node['plugin_id']])
                                        for node in graph['nodes']},
                    'source_hashes': {node['plugin_id']: self.catalog.fingerprint(node['plugin_id'])
                                      for node in graph['nodes']}}
        except KeyError:
            raise HTTPException(422, 'Offering graph references a plugin outside the catalog') from None
        except (OSError, TypeError, ValueError):
            raise HTTPException(422, 'Offering graph sources cannot be fingerprinted') from None

    def publish(self, spec, actor):
        title = checked_title(spec.get('title'))
        blueprint = self.store.get('blueprints', spec['blueprint_id'])
        if not blueprint or not isinstance(blueprint.get('graph'), dict):
            raise HTTPException(404, 'Unknown blueprint')
        try:
            # A saved blueprint may predate a schema change; an offering is
            # pinned for much longer, so it is admitted only as a closed graph.
            graph = Graph.model_validate(blueprint['graph']).model_dump()
        except ValidationError:
            raise HTTPException(422, 'Saved blueprint graph is invalid') from None
        # Revalidation reuses the admission gate, so planned, missing, unsafe or
        # over-quota graphs are refused with the same violation codes as a job.
        report = self.jobs.check(graph, actor['role'])
        graph = self.catalog.normalize(graph)
        pins = self._pins(graph)
        with self.store.lock:
            version = self._next_version(title)
            record = {'id': secrets.token_hex(16), 'title': title, 'version': version,
                      'blueprint_id': blueprint.get('id') or spec['blueprint_id'],
                      'graph': graph, 'pins': pins, 'cost': report['cost'],
                      'created_at': now(), 'created_by': actor['username']}
            self.store.put('studio_offerings', record['id'], record)
            stored = self._offering(record['id'])
            self.store.audit(actor['username'], 'studio.offering_published', record['id'],
                             {'title': title, 'version': version,
                              'blueprint_id': record['blueprint_id']})
            return {key: stored[key] for key in ('id', 'title', 'version')}

    def _next_version(self, title):
        version = 0
        for row in self.store.all('SELECT body FROM studio_offerings'):
            try:
                record = json.loads(row['body'])
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict) and record.get('title') == title \
                    and type(record.get('version')) is int:
                version = max(version, record['version'])
        return version + 1

    def _offering(self, identity):
        record = self.store.get('studio_offerings', identity)
        if not isinstance(record, dict):
            raise HTTPException(404, 'Unknown offering')
        return record

    def offerings(self):
        rows = []
        for row in self.store.all('SELECT body FROM studio_offerings ORDER BY rowid DESC'):
            try:
                record = json.loads(row['body'])
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict):
                rows.append({key: record.get(key) for key in ('id', 'title', 'version')})
        return rows

    def _verify(self, offering):
        graph, pins = offering.get('graph'), offering.get('pins')
        if not isinstance(graph, dict) or not isinstance(pins, dict):
            raise HTTPException(409, 'Published offering is not executable; publish a new version')
        try:
            current = self._pins(graph)
        except HTTPException:
            raise HTTPException(409, 'Published offering sources are unavailable; '
                                     'publish a new version') from None
        # Republishing is the only way to adopt changed defaults or changed
        # plugin sources: a request never silently runs a different executable.
        if digest(graph) != pins.get('graph_hash') or current != pins \
                or self.catalog.normalize(graph) != graph:
            raise HTTPException(409, 'Published offering changed since publication; '
                                     'publish a new version')

    def request(self, identity, actor):
        with self.store.lock:
            offering = self._offering(identity)
            self._verify(offering)
            job = self.jobs.create(copy.deepcopy(offering['graph']), actor, attribution={
                'service_request': {
                    'offering_id': offering['id'],
                    'offering_version': offering['version'],
                    'pins': offering['pins'],
                    'title': offering['title'],
                    'blueprint_id': offering.get('blueprint_id'),
                    'requested_by': actor['id'],
                    'requested_by_username': actor['username'],
                    'requested_at': now()}})
            recorded = self.store.get('jobs', job['id'])
            attribution = (recorded or {}).get('service_request')
            if not isinstance(attribution, dict) or attribution.get('offering_id') != offering['id']:
                raise HTTPException(503, 'Service request attribution was not recorded')
            return {'job_id': job['id']}

    # -- owned services ----------------------------------------------------

    def services(self, actor):
        owner = actor.get('id')
        rows = []
        for job in self.store.list('jobs'):
            attribution = job.get('service_request')
            if not owner or not isinstance(attribution, dict) \
                    or attribution.get('requested_by') != owner:
                continue
            cost = job.get('cost') if isinstance(job.get('cost'), dict) else {}
            monthly = cost.get('monthly')
            if isinstance(monthly, bool) or not isinstance(monthly, (int, float)):
                monthly = None
            rows.append({'job_id': job['id'],
                         'title': attribution.get('title') or job.get('name'),
                         'status': job.get('status'),
                         'monthly_estimate': None if monthly is None else float(monthly)})
        return rows


def install_studio_api(app):
    if getattr(app.state, '_studio_api_installed', False):
        return
    service = app.state.studio = Studio(app)
    user, role = app.state.user_dependency, app.state.role_dependency
    original_count = len(app.router.routes)

    @app.get('/api/studio/artifacts')
    def artifacts(current=Depends(role('admin', 'operator'))):
        return service.artifacts()

    @app.post('/api/studio/artifacts', status_code=201)
    def register_artifact(data: ArtifactRequest, current=Depends(role('admin', 'operator'))):
        return service.register_artifact(data.model_dump(), current)

    @app.get('/api/studio/offerings')
    def offerings(current=Depends(user)):
        return service.offerings()

    @app.post('/api/studio/offerings', status_code=201)
    def publish(data: OfferingRequest, current=Depends(role('admin'))):
        return service.publish(data.model_dump(), current)

    @app.post('/api/studio/offerings/{identity}/request', status_code=201)
    def request_service(identity: str, current=Depends(role('admin', 'operator'))):
        return service.request(identity, current)

    @app.get('/api/studio/services')
    def services(current=Depends(user)):
        return service.services(current)

    # Tests and optional installers can run after the SPA catch-all exists.
    added = app.router.routes[original_count:]
    del app.router.routes[original_count:]
    before_spa = next((i for i, route in enumerate(app.router.routes)
                       if getattr(route, 'path', None) == '/{asset_path:path}'), len(app.router.routes))
    app.router.routes[before_spa:before_spa] = added
    app.state._studio_api_installed = True
