"""Resource creation and import: pinned OpenTofu plans and owned-VM adoption.

Thin wrapper over the real creation/import routes only:
- POST /api/declarations/verify              human admin; read-only pin check
- POST /api/declarations/execute             human admin; applies one exact saved plan
- GET  /api/declarations                     any user; repository/applier status + runs
- POST /api/environment-states/adoptions     human admin; adopt one proven owned VM
- GET  /api/environment-states/adoptions     any user; adoption index

Backend gaps, marked unavailable and never stubbed client-side:
- default-form creation (--default): the API has no form route; governed
  creation goes through 'kw deploy request' + 'kw deploy approve';
- add: no route adds resources to an existing stack outside a new apply.
SSH key injection is OpenTofu's job at apply time, never the CLI's.
"""
from __future__ import annotations

import click

from kw.commands.common import api, client, out

ENVIRONMENTS = ["dev", "staging", "production"]


@click.group()
def resources():
    """Create or import resources into a stack's durable state.

    Unavailable (backend gaps, not stubbed): default-form creation
    (--default, no form route; use 'kw deploy request' + 'kw deploy approve')
    and 'add' (no route adds resources to an existing stack).
    """


@resources.command("create")
@click.option("--default", "default_form", is_flag=True,
              help="Unavailable: the API has no default-form creation route.")
@click.option("--import", "import_plan", is_flag=True,
              help="Import proven OpenTofu code: apply one pinned saved plan (human admin).")
@click.option("--environment", type=click.Choice(ENVIRONMENTS), default=None,
              help="Target environment (import).")
@click.option("--stack", default=None, help="Target stack name, 1..160 characters (import).")
@click.option("--revision", default=None,
              help="Full Git commit id pinning the bundle (import).")
@click.option("--bundle-path", default=None,
              help="Repository-relative bundle path at that revision (import).")
@click.option("--bundle-sha256", default=None,
              help="SHA-256 of the bundle file (import).")
@click.option("--dry-run", is_flag=True,
              help="Verify the pins only (POST /api/declarations/verify); execute nothing.")
@click.pass_context
@api
def create_cmd(ctx, default_form, import_plan, environment, stack, revision,
               bundle_path, bundle_sha256, dry_run):
    """Create a resource: --default (unavailable) or --import (pinned plan).

    --import applies the exact saved plan the bundle pins; the API re-verifies
    every digest before execution and refuses a moved generation. Security and
    configuration of imported code are the user's responsibility. SSH keys are
    injected by OpenTofu at apply time, never by the CLI.
    """
    if default_form and import_plan:
        raise click.UsageError("choose either --default or --import, not both")
    if default_form:
        raise click.ClickException(
            "unavailable: the API has no default-form resource creation route "
            "(backend gap; not stubbed client-side). Governed creation goes through "
            "'kw deploy request --graph-file ...' then 'kw deploy approve'; "
            "SSH keys are injected by OpenTofu, not the CLI")
    if not import_plan:
        raise click.UsageError("choose --default or --import")
    missing = [name for name, value in (
        ("--environment", environment), ("--stack", stack), ("--revision", revision),
        ("--bundle-path", bundle_path), ("--bundle-sha256", bundle_sha256)) if value is None]
    if missing:
        raise click.UsageError("--import requires " + ", ".join(missing))
    payload = {"environment": environment, "stack": stack, "revision": revision,
               "bundle_path": bundle_path, "bundle_sha256": bundle_sha256}
    c = client(ctx)
    path = "/api/declarations/verify" if dry_run else "/api/declarations/execute"
    data = c.post(path, json=payload)
    c.close()
    out(ctx, data)


@resources.command("status")
@click.pass_context
@api
def status_cmd(ctx):
    """Declaration repository/applier availability and past runs (any user)."""
    c = client(ctx)
    data = c.get("/api/declarations")
    c.close()
    out(ctx, data)


@resources.command("adopt")
@click.option("--environment", required=True, type=click.Choice(ENVIRONMENTS),
              help="Environment of the empty stack to fill.")
@click.option("--stack", required=True, help="Stack name, 1..160 characters.")
@click.option("--connection-id", required=True,
              help="Recorded connection identity (32 hex chars) holding the VM.")
@click.option("--uuid", required=True, help="UUID of the virtual machine to adopt.")
@click.option("--marker", required=True,
              help="Ownership marker kuberweaver-job:<job id> written at creation.")
@click.pass_context
@api
def adopt_cmd(ctx, environment, stack, connection_id, uuid, marker):
    """Import one existing VM the portal already owns (human admin only).

    The API reads the live VM through its connection and compares the ownership
    marker digest; an unproven VM is refused. Adoption fills an empty stack with
    one generation, never replaces applied state, and a repeat is a no-op.
    """
    c = client(ctx)
    data = c.post("/api/environment-states/adoptions",
                  json={"environment": environment, "stack": stack,
                        "connection_id": connection_id, "uuid": uuid, "marker": marker})
    c.close()
    out(ctx, data)


@resources.command("adoptions")
@click.pass_context
@api
def adoptions_cmd(ctx):
    """List recorded VM adoptions (any user)."""
    c = client(ctx)
    data = c.get("/api/environment-states/adoptions")
    c.close()
    out(ctx, data)


@resources.command("add")
def add_cmd():
    """Unavailable: no API route adds resources to an existing stack."""
    raise click.ClickException(
        "unavailable: the API has no route to add resources to an existing stack "
        "(backend gap; not stubbed client-side). New resources go through a new "
        "governed apply ('kw deploy request') or a pinned declaration "
        "('kw resources create --import')")
