"""Adopt a virtual machine that already exists into a stack's durable state.

Adoption gives a stack that was never applied a first generation describing one
VM the portal already owns. It never creates, reconfigures, powers on or stops
anything: the only host operation is one read.

The ownership proof is the VM's UUID plus the exact annotation the portal wrote
when it created that VM. The hypervisor driver never returns the annotation, only
its SHA-256, because an annotation is free-form operator text; the portal hashes
the marker it expects and compares digests. A VM created by someone else, or one
whose annotation changed, is refused, and a second stack cannot adopt the same
UUID.

The read is supplied as an ``inspector(connection_id, uuid)`` callable so the
store stays independent of any hypervisor SDK. The kernel wires it to the ESXi
connection registry; a driver that does not report ownership evidence makes the
adoption path fail closed instead of adopting an unproven VM.
"""
from __future__ import annotations

import hashlib
import hmac
import re

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .environment_state import ENVIRONMENTS, StateConflict, StateError
from .storage import now

MARKER_PREFIX = 'kuberweaver-job:'
MARKER_PATTERN = re.escape(MARKER_PREFIX) + '[0-9a-f]{32}'
ADDRESS_PREFIX = 'esxi_vm.'
_POWER_STATES = ('poweredOn', 'poweredOff', 'suspended')
_UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
_CONNECTION = re.compile(r'[0-9a-f]{32}\Z')
_MARKER = re.compile(MARKER_PATTERN + r'\Z')
_MAX_NAME = 128


def marker_digest(marker):
    """The only form in which a marker leaves the hypervisor boundary."""
    return hashlib.sha256(marker.encode('utf-8')).hexdigest()


def resource_address(uuid):
    return ADDRESS_PREFIX + uuid


def live_ownership(row, uuid, marker):
    """Validate one live row and compare its annotation digest.

    Adoption and destruction share this proof so a VM that cannot be proven
    ours is refused by both, with the same sanitized reasons.
    """
    if not isinstance(row, dict):
        raise HTTPException(409, 'Virtual machine could not be read; ownership is unverified')
    live_uuid = row.get('uuid')
    if not isinstance(live_uuid, str) or live_uuid.lower() != uuid:
        raise HTTPException(409, 'Virtual machine UUID does not match the recorded resource')
    name = row.get('name')
    if (not isinstance(name, str) or not name or len(name) > _MAX_NAME
            or any(ord(character) < 32 or ord(character) == 127 for character in name)):
        raise HTTPException(409, 'Virtual machine inventory is inconsistent')
    if row.get('power_state') not in _POWER_STATES:
        raise HTTPException(409, 'Virtual machine inventory is inconsistent')
    for key in ('cpu', 'memory_mb'):
        if type(row.get(key)) is not int or row[key] < 1:
            raise HTTPException(409, 'Virtual machine inventory is inconsistent')
    digest = row.get('ownership_sha256')
    if digest is None:
        raise HTTPException(409, 'Virtual machine carries no KuberWeaver ownership marker')
    if not isinstance(digest, str) or not hmac.compare_digest(digest, marker_digest(marker)):
        raise HTTPException(409, 'Ownership marker does not match the virtual machine')
    return {'name': name, 'power_state': row['power_state'],
            'cpu': row['cpu'], 'memory_mb': row['memory_mb']}


class AdoptionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    environment: str = Field(pattern=r'^(dev|staging|production)$')
    stack: str = Field(min_length=1, max_length=160)
    connection_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    uuid: str = Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    marker: str = Field(pattern='^' + MARKER_PATTERN + '$')


def connection_inspector(app):
    """Read one VM through its recorded connection; never mutate the host."""
    def inspect(connection_id, uuid):
        connections = getattr(app.state, 'connections', None)
        if connections is None:
            raise RuntimeError('Connection registry is unavailable')
        with connections.access(connection_id) as (connection, credentials):
            driver = connections.driver(connection)
            return driver.read_resource(connection, credentials, uuid)
    return inspect


class AdoptionService:
    def __init__(self, environments, inspector=None, clock=now):
        self.environments = environments
        self.inspector = inspector
        self.clock = clock

    def _live(self, connection_id, uuid, marker):
        if not callable(self.inspector):
            raise HTTPException(503, 'Virtual machine ownership cannot be verified')
        try:
            row = self.inspector(connection_id, uuid)
        except HTTPException:
            raise
        except (Exception, SystemExit):
            raise HTTPException(409, 'Virtual machine could not be read; ownership is unverified') from None
        return live_ownership(row, uuid, marker)

    def adopt(self, environment, stack, connection_id, uuid, marker, actor):
        """Verify the live VM, then record it; a repeat is a no-op."""
        if environment not in ENVIRONMENTS:
            raise HTTPException(422, 'Expected dev, staging or production')
        try:
            self.environments.identity(environment, stack)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        if not isinstance(uuid, str):
            raise HTTPException(422, 'Expected a virtual machine UUID')
        uuid = uuid.lower()
        if not _UUID.match(uuid):
            raise HTTPException(422, 'Expected a virtual machine UUID')
        if not isinstance(marker, str) or not _MARKER.match(marker):
            raise HTTPException(422, 'Expected an ownership marker of the form kuberweaver-job:<job id>')
        if not isinstance(connection_id, str) or not _CONNECTION.match(connection_id):
            raise HTTPException(422, 'Expected a recorded connection identity')
        if not isinstance(actor, dict) or not isinstance(actor.get('username'), str) or not actor['username']:
            raise HTTPException(403, 'A signed-in administrator is required')
        live = self._live(connection_id, uuid, marker)
        stamp = self.clock()
        document = {'source': 'adopted-vm', 'environment': environment, 'stack': stack,
                    'uuid': uuid, 'marker': marker, 'name': live['name'],
                    'power_state': live['power_state'], 'cpu': live['cpu'],
                    'memory_mb': live['memory_mb'], 'connection_id': connection_id,
                    'adopted_at': stamp, 'adopted_by': actor['username']}
        resources = [{'address': resource_address(uuid), 'type': 'esxi_vm',
                      'name': uuid, 'mode': 'managed'}]
        try:
            return self.environments.adopt(environment, stack, uuid=uuid, marker=marker,
                                           name=live['name'], connection_id=connection_id,
                                           document=document, resources=resources,
                                           actor=actor['username'], stamp=stamp)
        except StateConflict as error:
            raise HTTPException(409, str(error)) from None
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None


def install_adoption_api(app, environments):
    """Register the administrator adoption routes before the state detail route.

    The request model is resolved from this module's globals, so the routes live
    beside it instead of being annotated through an import inside a function.
    """
    if getattr(app.state, '_adoption_api_installed', False):
        return
    adopter = app.state.vm_adoption = AdoptionService(environments, connection_inspector(app))

    @app.post('/api/environment-states/adoptions')
    def adopt_vm(data: AdoptionRequest,
                 current=Depends(app.state.role_dependency('admin'))):
        return adopter.adopt(data.environment, data.stack, data.connection_id,
                             data.uuid, data.marker, current)

    @app.get('/api/environment-states/adoptions')
    def adoption_listing(current=Depends(app.state.user_dependency)):
        try:
            return {'adoptions': environments.adoptions()}
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None

    app.state._adoption_api_installed = True
