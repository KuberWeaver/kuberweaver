"""Catalog modules: Service Studio artifacts and immutable offerings.

Thin wrapper over /api/studio/artifacts and /api/studio/offerings only.
Backend gaps, marked unavailable and never stubbed client-side:
- get-by-id: the API has no GET /api/studio/artifacts/{id} nor
  GET /api/studio/offerings/{id} route;
- cost/month: neither ArtifactRequest nor OfferingRequest has a cost
  field, and no studio route returns one (offerings store an internal
  estimate at publication, but it is not exposed).
"""
from __future__ import annotations

import click

from kw.commands.common import api, client, out


@click.group()
def modules():
    """Catalog modules: studio artifacts and immutable offerings.

    Unavailable (backend gaps, not stubbed): get-by-id for artifacts and
    offerings (no GET .../{id} routes), and cost/month (the studio API
    exposes no cost field).
    """


@modules.group()
def artifacts():
    """Artifact register: metadata only, no fetch and no job."""


@artifacts.command("list")
@click.pass_context
@api
def artifacts_list(ctx):
    """List registered artifacts (admin/operator)."""
    c = client(ctx)
    data = c.get("/api/studio/artifacts")
    c.close()
    out(ctx, data, columns=["id", "kind", "reference", "pin"])


@artifacts.command("create")
@click.option("--kind", required=True,
              type=click.Choice(["oci", "vm-image", "ansible-role"]),
              help="Artifact kind (closed set, same as the API schema).")
@click.option("--reference", required=True,
              help="Secret-free locator: no whitespace, userinfo or secret query params.")
@click.option("--pin", default=None,
              help="Optional immutable pin, e.g. a sha256 digest.")
@click.pass_context
@api
def artifacts_create(ctx, kind, reference, pin):
    """Register artifact metadata (admin/operator).

    Cost/month: unavailable, the studio API has no cost field.
    """
    c = client(ctx)
    payload = {"kind": kind, "reference": reference}
    if pin is not None:
        payload["pin"] = pin
    data = c.post("/api/studio/artifacts", json=payload)
    c.close()
    out(ctx, data)


@artifacts.command("get")
@click.argument("identity")
def artifacts_get(identity):
    """Unavailable: the API has no GET /api/studio/artifacts/{id} route."""
    raise click.ClickException(
        "unavailable: the API has no GET /api/studio/artifacts/{id} route "
        "(backend gap; not stubbed client-side, use 'kw modules artifacts list')")


@modules.group()
def offerings():
    """Immutable offerings: a published blueprint, pinned at publication."""


@offerings.command("list")
@click.pass_context
@api
def offerings_list(ctx):
    """List published offerings (any authenticated user)."""
    c = client(ctx)
    data = c.get("/api/studio/offerings")
    c.close()
    out(ctx, data, columns=["id", "title", "version"])


@offerings.command("create")
@click.option("--title", required=True,
              help="Single-line label, 1..160 characters.")
@click.option("--blueprint-id", required=True,
              help="Saved blueprint whose graph is pinned and published.")
@click.pass_context
@api
def offerings_create(ctx, title, blueprint_id):
    """Publish an immutable offering from a saved blueprint (admin only).

    Cost/month: unavailable, the studio API exposes no cost field.
    """
    c = client(ctx)
    data = c.post("/api/studio/offerings",
                  json={"title": title, "blueprint_id": blueprint_id})
    c.close()
    out(ctx, data)


@offerings.command("get")
@click.argument("identity")
def offerings_get(identity):
    """Unavailable: the API has no GET /api/studio/offerings/{id} route."""
    raise click.ClickException(
        "unavailable: the API has no GET /api/studio/offerings/{id} route "
        "(backend gap; not stubbed client-side, use 'kw modules offerings list')")
