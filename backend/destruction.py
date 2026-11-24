"""Destroy one owned virtual machine, after proof and a cancellation window.

A destroy is irreversible, so the portal splits it into three explicit steps.
An administrator requests it against one recorded stack and proves ownership
twice: the stack's resource list must name the UUID, and the live VM must carry
the same ``kuberweaver-job:`` annotation digest that adoption required. The
request opens a quarantine window during which nothing touches the host and the
same administrator can cancel it. Only after that window can execution start,
and execution re-reads the VM before the driver is asked to destroy it, because
an annotation can change between the request and the window closing.

The host operation is supplied as a ``destroyer(connection_id, uuid, marker)``
callable and must return ``True`` only after it has confirmed the VM is gone.
Any other outcome leaves the destruction ``rollback_failed`` with
``requires_manual_review``: the portal never assumes either that the VM survived
or that it was deleted. ``GET /api/environment-states/{id}`` shows the record,
and a ``rollback_failed`` record is closed only by an administrator's observed
evidence through the resolve route.
"""
from __future__ import annotations

import re
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .adoption import (MARKER_PATTERN, connection_inspector, live_ownership,
                       resource_address)
from .environment_state import StateConflict, StateError
from .storage import now

QUARANTINE_SECONDS = 15 * 60
_UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
_CONNECTION = re.compile(r'[0-9a-f]{32}\Z')
_MARKER = re.compile(MARKER_PATTERN + r'\Z')


class DestroyUnavailable(RuntimeError):
    """The selected driver cannot delete a VM; nothing was attempted."""


class DestroyUnverified(RuntimeError):
    """The driver ran but did not confirm the VM is gone."""


def connection_destroyer(app):
    """Delete one VM through its recorded connection.

    The driver method is consulted at call time, so an extension that only
    implements discovery refuses the operation instead of appearing capable.
    """
    def destroy(connection_id, uuid, marker):
        connections = getattr(app.state, 'connections', None)
        if connections is None:
            raise DestroyUnavailable('Connection registry is unavailable')
        with connections.access(connection_id) as (connection, credentials):
            driver = connections.driver(connection)
            operation = getattr(driver, 'destroy_resource', None)
            if not callable(operation):
                raise DestroyUnavailable('Driver does not support destruction')
            result = operation(connection, credentials, uuid, marker)
        if result is not True:
            raise DestroyUnverified('Driver did not confirm the destruction')
        return True
    return destroy


class DestroyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    connection_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    uuid: str = Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    marker: str = Field(pattern='^' + MARKER_PATTERN + '$')


class DestroyAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    destroy_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class DestroyResolution(DestroyAction):
    outcome: Literal['intact', 'destroyed']


class DestroyService:
    def __init__(self, environments, inspector=None, destroyer=None, clock=now,
                 quarantine_seconds=QUARANTINE_SECONDS):
        self.environments = environments
        self.inspector = inspector
        self.destroyer = destroyer
        self.clock = clock
        self.quarantine_seconds = quarantine_seconds

    @staticmethod
    def _actor(actor):
        if (not isinstance(actor, dict) or not isinstance(actor.get('username'), str)
                or not actor['username']):
            raise HTTPException(403, 'A signed-in administrator is required')
        if actor.get('principal_type') == 'agent':
            raise HTTPException(403, 'A human administrator session is required')
        return actor['username']

    @staticmethod
    def _uuid(value):
        if not isinstance(value, str):
            raise HTTPException(422, 'Expected a virtual machine UUID')
        value = value.lower()
        if not _UUID.match(value):
            raise HTTPException(422, 'Expected a virtual machine UUID')
        return value

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

    def request(self, identity, connection_id, uuid, marker, actor):
        """Prove ownership and open the quarantine window; no host change."""
        username = self._actor(actor)
        uuid = self._uuid(uuid)
        if not isinstance(marker, str) or not _MARKER.match(marker):
            raise HTTPException(422, 'Expected an ownership marker of the form kuberweaver-job:<job id>')
        if not isinstance(connection_id, str) or not _CONNECTION.match(connection_id):
            raise HTTPException(422, 'Expected a recorded connection identity')
        try:
            record = self.environments.present(identity)
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None
        rows = record['resources']
        if (len(rows) != 1 or rows[0]['address'] != resource_address(uuid)
                or rows[0]['mode'] != 'managed'):
            raise HTTPException(409, 'Virtual machine is not owned by this stack')
        adopted = record.get('adopted') or {}
        if adopted and (adopted.get('uuid') != uuid or adopted.get('marker') != marker):
            raise HTTPException(409, 'Ownership marker does not match the recorded adoption')
        live = self._live(connection_id, uuid, marker)
        try:
            result = self.environments.quarantine_destroy(
                identity, uuid=uuid, marker=marker, connection_id=connection_id,
                name=live['name'], actor=username, stamp=self.clock(),
                quarantine_seconds=self.quarantine_seconds)
        except StateConflict as error:
            raise HTTPException(409, str(error)) from None
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None
        return {'state': self.environments.present(identity), 'destroy': result['destroy'],
                'changed': not result['unchanged']}

    def cancel(self, identity, destroy_id, actor):
        """Cancel a quarantined destruction before it touches the host."""
        username = self._actor(actor)
        try:
            stored = self.environments.cancel_destroy(identity, destroy_id,
                                                      actor=username, stamp=self.clock())
        except StateConflict as error:
            raise HTTPException(409, str(error)) from None
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None
        return {'state': self.environments.present(identity), 'destroy': stored,
                'cancelled': True}

    def execute(self, identity, destroy_id, actor):
        """Run a destruction whose window has closed, re-proving the VM first."""
        username = self._actor(actor)
        try:
            running = self.environments.start_destroy(identity, destroy_id,
                                                      actor=username, stamp=self.clock())
        except StateConflict as error:
            raise HTTPException(409, str(error)) from None
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None
        try:
            live = self._live(running['connection_id'], running['uuid'], running['marker'])
        except HTTPException as error:
            # Nothing has been sent to the host, but the stack's evidence no
            # longer matches the VM; a human must look before any retry.
            self._fail(identity, destroy_id, 'failed', 'ownership_unverified', username, True)
            raise error
        if not callable(self.destroyer):
            self._fail(identity, destroy_id, 'failed', 'destruction_unavailable', username, False)
            raise HTTPException(503, 'Virtual machine destruction is unavailable')
        try:
            self.destroyer(running['connection_id'], running['uuid'], running['marker'])
        except DestroyUnavailable:
            self._fail(identity, destroy_id, 'failed', 'destruction_unsupported', username, False)
            raise HTTPException(503, 'Virtual machine destruction is not supported by the driver') from None
        except (Exception, SystemExit):
            self._fail(identity, destroy_id, 'rollback_failed', 'provider_error', username, True)
            raise HTTPException(502, 'Destruction failed; the virtual machine state is unverified') from None
        try:
            stamp = self.clock()
            result = self.environments.complete_destroy(
                identity, destroy_id, actor=username, stamp=stamp,
                document=self._document(running, username, live['name'], stamp))
        except (StateConflict, StateError):
            self._fail(identity, destroy_id, 'rollback_failed', 'state_unconfirmed', username, True)
            raise HTTPException(503, 'Destruction succeeded but could not be recorded; '
                                     'manual review is required') from None
        return {'state': result['state'], 'destroy': result['destroy'], 'destroyed': True}

    def resolve(self, identity, destroy_id, outcome, actor):
        """Close an unverified destruction with an administrator's observation."""
        username = self._actor(actor)
        try:
            record = self.environments.present(identity)
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None
        destroy = record.get('destroy')
        if destroy is None:
            raise HTTPException(409, 'No destruction is recorded for this stack')
        if destroy.get('destroy_id') != destroy_id:
            raise HTTPException(409, 'Destruction request changed')
        if destroy.get('status') != 'rollback_failed':
            raise HTTPException(409, 'Only an unverified destruction can be resolved')
        if outcome == 'intact':
            # The administrator says the VM survived; verify before believing it.
            self._live(destroy['connection_id'], destroy['uuid'], destroy['marker'])
        stamp = self.clock()
        document = None
        if outcome == 'destroyed':
            document = self._document(destroy, username, destroy.get('name'), stamp)
        try:
            result = self.environments.resolve_destroy(
                identity, destroy_id, outcome=outcome, actor=username,
                stamp=stamp, document=document)
        except StateConflict as error:
            raise HTTPException(409, str(error)) from None
        except StateError:
            raise HTTPException(503, 'Environment state is unavailable') from None
        return {'state': result['state'], 'destroy': result['destroy'], 'resolved': outcome}

    def _fail(self, identity, destroy_id, status, reason, actor, review):
        try:
            self.environments.fail_destroy(identity, destroy_id, status=status,
                                           reason=reason, actor=actor,
                                           stamp=self.clock(),
                                           requires_manual_review=review)
        except (StateConflict, StateError):
            # The record already moved or cannot be written; the route still
            # reports the refusal, and the detail view shows the stored status.
            pass

    @staticmethod
    def _document(destroy, actor, name, stamp):
        # The plaintext document replaces the previous one; it records the
        # evidence of the destruction and no provider attributes.
        return {'source': 'destroyed', 'environment': destroy['environment'],
                'stack': destroy['stack'], 'uuid': destroy['uuid'],
                'marker': destroy['marker'], 'name': name,
                'connection_id': destroy['connection_id'],
                'destroyed_at': stamp, 'destroyed_by': actor}


def install_destroy_api(app, environments):
    """Register the administrator destruction routes on the state kernel."""
    if getattr(app.state, '_destroy_api_installed', False):
        return
    service = app.state.vm_destruction = DestroyService(
        environments, connection_inspector(app), connection_destroyer(app))

    @app.post('/api/environment-states/{identity}/destroy')
    def request_destroy(identity: str, data: DestroyRequest,
                        current=Depends(app.state.role_dependency('admin'))):
        return service.request(identity, data.connection_id, data.uuid, data.marker, current)

    @app.post('/api/environment-states/{identity}/destroy/cancel')
    def cancel_destroy(identity: str, data: DestroyAction,
                       current=Depends(app.state.role_dependency('admin'))):
        return service.cancel(identity, data.destroy_id, current)

    @app.post('/api/environment-states/{identity}/destroy/execute')
    def execute_destroy(identity: str, data: DestroyAction,
                        current=Depends(app.state.role_dependency('admin'))):
        return service.execute(identity, data.destroy_id, current)

    @app.post('/api/environment-states/{identity}/destroy/resolve')
    def resolve_destroy(identity: str, data: DestroyResolution,
                        current=Depends(app.state.role_dependency('admin'))):
        return service.resolve(identity, data.destroy_id, data.outcome, current)

    app.state._destroy_api_installed = True
