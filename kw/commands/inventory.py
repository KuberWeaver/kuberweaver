"""Inventory (inventaire): deployed resources/services and live infrastructure."""
from __future__ import annotations
import click

from kw.commands.common import api, client, out


@click.group()
def inventory():
    """Inspect deployed environment states and infrastructure."""


@inventory.command("list")
@click.pass_context
@api
def list_cmd(ctx):
    c = client(ctx)
    data = c.get("/api/environment-states")
    c.close()
    out(ctx, data)


@inventory.command("get")
@click.argument("identity")
@click.pass_context
@api
def get_cmd(ctx, identity):
    c = client(ctx)
    data = c.get(f"/api/environment-states/{identity}")
    c.close()
    out(ctx, data)


@inventory.command("infrastructure")
@click.pass_context
@api
def infrastructure_cmd(ctx):
    """Live topology from the bound inventory provider (may be unbound)."""
    c = client(ctx)
    data = c.get("/api/infrastructure")
    c.close()
    out(ctx, data)
