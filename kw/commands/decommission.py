"""Decommission: governed destruction of one deployed stack by its state id.

Thin wrapper over the destruction routes (destruction.py), same gates as the GUI:
- POST /api/environment-states/{id}/destroy          human admin; prove ownership, open quarantine
- POST /api/environment-states/{id}/destroy/cancel   human admin; cancel inside the window
- POST /api/environment-states/{id}/destroy/execute  human admin; destroy after the window
- POST /api/environment-states/{id}/destroy/resolve  human admin; close an unverified destruction
- GET  /api/environment-states/{id}                  any user; state + destruction record

Every gate lives in the API: ownership proof against the live VM, the 15-minute
quarantine window, human-administrator sessions (agent tokens refused), and one
owned VM per stack. The CLI adds no logic, never bypasses a gate, and only ever
targets the given state id. Job-based service retirement is 'kw services
decommission'; snapshot restore is 'kw rollback'.
"""
from __future__ import annotations

import click

from kw.commands.common import api, client, out


@click.group()
def decommission():
    """Destroy one deployed stack by its environment-state id (human admin).

    Governed flow: 'request' proves ownership and opens a 15-minute quarantine
    window (server-enforced); 'cancel' aborts inside the window; 'execute'
    destroys the VM after the window, re-proving ownership first; 'resolve'
    closes an unverified (rollback_failed) destruction with observed evidence.
    Agent tokens are refused. The API destroys one owned VM per stack and never
    touches another stack. IDENTITY comes from 'kw inventory list'.
    """


@decommission.command("request")
@click.argument("identity")
@click.option("--connection-id", required=True,
              help="Recorded connection identity (32 hex chars) holding the VM.")
@click.option("--uuid", required=True, help="UUID of the virtual machine to destroy.")
@click.option("--marker", required=True,
              help="Ownership marker kuberweaver-job:<job id> recorded at adoption.")
@click.pass_context
@api
def request_cmd(ctx, identity, connection_id, uuid, marker):
    """Prove ownership and open the quarantine window; no host change yet.

    The API checks this stack owns exactly the named VM and the live annotation
    matches the marker, then records a destruction executable only after the
    window. A repeat request keeps the same window.
    """
    c = client(ctx)
    data = c.post(f"/api/environment-states/{identity}/destroy",
                  json={"connection_id": connection_id, "uuid": uuid, "marker": marker})
    c.close()
    out(ctx, data)


@decommission.command("cancel")
@click.argument("identity")
@click.option("--destroy-id", required=True,
              help="Current destruction id (32 hex chars) from 'request' or 'status'.")
@click.pass_context
@api
def cancel_cmd(ctx, identity, destroy_id):
    """Cancel a quarantined destruction before it touches the host.

    Only the current destroy id is accepted, and only while the destruction is
    still quarantined; the API refuses once execution has started.
    """
    c = client(ctx)
    data = c.post(f"/api/environment-states/{identity}/destroy/cancel",
                  json={"destroy_id": destroy_id})
    c.close()
    out(ctx, data)


@decommission.command("execute")
@click.argument("identity")
@click.option("--destroy-id", required=True,
              help="Current destruction id (32 hex chars) from 'request' or 'status'.")
@click.pass_context
@api
def execute_cmd(ctx, identity, destroy_id):
    """Destroy the VM once the quarantine window has closed (irreversible).

    The API re-proves ownership against the live VM before the driver runs; an
    early call is refused (the CLI never waits out or bypasses the window). A
    driver failure leaves the record rollback_failed for 'resolve'.
    """
    c = client(ctx)
    data = c.post(f"/api/environment-states/{identity}/destroy/execute",
                  json={"destroy_id": destroy_id})
    c.close()
    out(ctx, data)


@decommission.command("resolve")
@click.argument("identity")
@click.option("--destroy-id", required=True,
              help="Current destruction id (32 hex chars) from 'request' or 'status'.")
@click.option("--outcome", required=True, type=click.Choice(["intact", "destroyed"]),
              help="Administrator's observed evidence: the VM survived or is gone.")
@click.pass_context
@api
def resolve_cmd(ctx, identity, destroy_id, outcome):
    """Close an unverified (rollback_failed) destruction with observed evidence.

    'intact' is believed only after the API re-reads the live VM and restores
    the retry path; 'destroyed' commits the emptied generation. No other status
    can be resolved.
    """
    c = client(ctx)
    data = c.post(f"/api/environment-states/{identity}/destroy/resolve",
                  json={"destroy_id": destroy_id, "outcome": outcome})
    c.close()
    out(ctx, data)


@decommission.command("status")
@click.argument("identity")
@click.pass_context
@api
def status_cmd(ctx, identity):
    """Show one stack's state and its destruction record (any signed-in user)."""
    c = client(ctx)
    data = c.get(f"/api/environment-states/{identity}")
    c.close()
    out(ctx, data)
