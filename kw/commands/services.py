"""Owned services: my service requests, offering requests and governed retirement.

Thin wrapper over the Service Studio routes only:
- GET  /api/studio/services                    any user; ownership-scoped by the API
- POST /api/studio/offerings/{id}/request      admin/operator; no body, no approval bypass
- POST /api/studio/services/{id}/decommission  human admin only; queues retirement

A service request is an ordinary job: it lands in pending_approval and still
needs 'kw deploy approve'. Decommission is a governed mutation: the API queues
it on the one serialized worker under the pinned provider, never substitutes
rollback, and a failed retirement requires manual review. Follow the queued
retirement with 'kw deploy watch <job-id>'.
"""
from __future__ import annotations

import click

from kw.commands.common import api, client, out


@click.group()
def services():
    """My services: list owned service requests, request offerings, retire."""


@services.command("list")
@click.pass_context
@api
def list_cmd(ctx):
    """List my service requests (any user; the API scopes rows to the caller)."""
    c = client(ctx)
    data = c.get("/api/studio/services")
    c.close()
    out(ctx, data, columns=["job_id", "title", "status", "monthly_estimate"])


@services.command("request")
@click.argument("offering_id")
@click.pass_context
@api
def request_cmd(ctx, offering_id):
    """Request a published offering (admin/operator).

    Creates an ordinary job in pending_approval; approval stays an explicit
    administrator decision ('kw deploy approve <job-id>').
    """
    c = client(ctx)
    data = c.post(f"/api/studio/offerings/{offering_id}/request")
    c.close()
    out(ctx, data)


@services.command("decommission")
@click.argument("job_id")
@click.pass_context
@api
def decommission_cmd(ctx, job_id):
    """Retire one succeeded service request by job id (human admin only).

    Governed mutation: the API queues retirement on the serialized worker
    under the job's pinned provider; rollback is never substituted. Requires
    a succeeded service request whose provider declares decommission.
    Follow with 'kw deploy watch <job-id>' until decommissioned.
    """
    c = client(ctx)
    data = c.post(f"/api/studio/services/{job_id}/decommission")
    c.close()
    out(ctx, data, columns=["id", "status", "operation", "can_rollback", "can_decommission"])
