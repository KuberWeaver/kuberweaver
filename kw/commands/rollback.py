"""Explicit administrator rollback of a deployment. Governed human mutation.

Thin wrapper over POST /api/jobs/{id}/rollback only: the API decides
eligibility (a succeeded job, or a rollback_failed journal it can verify),
re-checks the approved execution provider pin, and queues the restore on the
one serialized worker. The CLI adds no logic and never bypasses approval.
"""
from __future__ import annotations

import click

from kw.commands.common import api, client, out


@click.command("rollback")
@click.argument("identity")
@click.pass_context
@api
def rollback_cmd(ctx, identity):
    """Roll back a succeeded deployment by job id (human admin only).

    Governed mutation: the API queues the restore under the job's pinned
    execution provider; interrupted or drifted jobs require manual review.
    Follow with 'kw deploy watch <job-id>' until rolled_back.
    """
    c = client(ctx)
    data = c.post(f"/api/jobs/{identity}/rollback")
    c.close()
    out(ctx, data, columns=["id", "status", "operation", "rollback_requested_by"])
