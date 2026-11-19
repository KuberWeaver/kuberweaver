"""FinOps (optimisation des coûts): cost analysis from retained deployment data."""
from __future__ import annotations
import click

from kw.commands.common import api, client, out


@click.group(invoke_without_command=True)
@click.pass_context
@api
def finops(ctx):
    """Estimated monthly cost of active deployments (not metered billing)."""
    if ctx.invoked_subcommand is None:
        c = client(ctx)
        data = c.get("/api/finops")
        c.close()
        out(ctx, data)


@finops.command("reporting")
@click.pass_context
@api
def reporting_cmd(ctx):
    c = client(ctx)
    data = c.get("/api/reporting")
    c.close()
    out(ctx, data)


@finops.command("overview")
@click.pass_context
@api
def overview_cmd(ctx):
    c = client(ctx)
    data = c.get("/api/overview")
    c.close()
    out(ctx, data)
