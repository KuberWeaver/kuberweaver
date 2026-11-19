"""Providers (fournisseurs): list and bind replaceable capability implementations.

Thin client over GET/PATCH /api/providers. Binding is an admin mutation; the
server enforces roles and validates each provider. No business logic here.
"""
from __future__ import annotations
import click

from kw.commands.common import api, client, out


@click.group()
def providers():
    """List and bind capability providers (inventory, execution, secrets, ...)."""


@providers.command("list")
@click.pass_context
@api
def list_cmd(ctx):
    """Show current bindings and available implementations per capability."""
    c = client(ctx)
    data = c.get("/api/providers")
    c.close()
    out(ctx, data)


@providers.command("bind")
@click.option("--kind", required=True,
              help="Capability kind: inventory, execution, secrets, diagnostics, notifications.")
@click.option("--name", required=True,
              help="Provider name to bind; empty string clears an optional kind.")
@click.pass_context
@api
def bind_cmd(ctx, kind, name):
    """Bind a capability to a provider (human admin only)."""
    c = client(ctx)
    data = c.patch("/api/providers", json={"bindings": {kind: name}})
    c.close()
    out(ctx, data)
