"""Cloudflare DNS: read zones/records, propose changes, approve/reject. Governed."""
from __future__ import annotations
import json
from pathlib import Path

import click

from kw.commands.common import api, client, out


def _load_record(record_file, record_json):
    if record_file and record_json:
        raise click.UsageError("provide at most one of --record-file / --record-json")
    raw = None
    if record_file:
        raw = Path(record_file).read_text()
    elif record_json:
        raw = record_json
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise click.UsageError(f"invalid record JSON: {err}") from None


@click.group()
def cloudflare():
    """Cloudflare DNS through vault-scoped tokens; changes need approval."""


@cloudflare.command("zones")
@click.option("--connector-id", required=True, help="Cloudflare connector id.")
@click.pass_context
@api
def zones_cmd(ctx, connector_id):
    """List zones visible to the connector's scoped token (read-only)."""
    c = client(ctx)
    data = c.get("/api/cloudflare/dns/zones", params={"connector_id": connector_id})
    c.close()
    out(ctx, data)


@cloudflare.command("records")
@click.option("--connector-id", required=True, help="Cloudflare connector id.")
@click.option("--zone-id", required=True, help="Cloudflare zone id.")
@click.option("--type", "record_type", default=None, help="Filter by record type.")
@click.option("--name", default=None, help="Filter by record name.")
@click.pass_context
@api
def records_cmd(ctx, connector_id, zone_id, record_type, name):
    """List DNS records in a zone (read-only)."""
    c = client(ctx)
    params = {"connector_id": connector_id, "zone_id": zone_id}
    if record_type:
        params["type"] = record_type
    if name:
        params["name"] = name
    data = c.get("/api/cloudflare/dns/records", params=params)
    c.close()
    out(ctx, data)


@cloudflare.command("request")
@click.option("--connector-id", required=True, help="Cloudflare connector id.")
@click.option("--zone-id", required=True, help="Cloudflare zone id.")
@click.option("--operation", required=True, type=click.Choice(["create", "update", "delete"]))
@click.option("--record-id", default=None, help="Record id (update/delete).")
@click.option("--record-file", type=click.Path(exists=True), default=None,
              help="JSON record (create) or patch (update).")
@click.option("--record-json", default=None, help="Inline JSON record or patch.")
@click.pass_context
@api
def request_cmd(ctx, connector_id, zone_id, operation, record_id, record_file, record_json):
    """Propose a DNS change; it lands in pending_approval, never auto-executed."""
    payload = {"connector_id": connector_id, "zone_id": zone_id, "operation": operation}
    if record_id:
        payload["record_id"] = record_id
    record = _load_record(record_file, record_json)
    if record is not None:
        payload["record"] = record
    c = client(ctx)
    data = c.post("/api/cloudflare/dns/requests", json=payload)
    c.close()
    out(ctx, data)


@cloudflare.command("requests")
@click.pass_context
@api
def requests_cmd(ctx):
    """List DNS change requests."""
    c = client(ctx)
    data = c.get("/api/cloudflare/dns/requests")
    c.close()
    out(ctx, data, columns=["id", "operation", "status", "zone_id", "created_by"])


@cloudflare.command("get")
@click.argument("identity")
@click.pass_context
@api
def get_cmd(ctx, identity):
    c = client(ctx)
    data = c.get(f"/api/cloudflare/dns/requests/{identity}")
    c.close()
    out(ctx, data)


@cloudflare.command("approve")
@click.argument("identity")
@click.pass_context
@api
def approve_cmd(ctx, identity):
    """Administrator approval; executes the pinned change against Cloudflare."""
    c = client(ctx)
    data = c.post(f"/api/cloudflare/dns/requests/{identity}/approve")
    c.close()
    out(ctx, data)


@cloudflare.command("reject")
@click.argument("identity")
@click.pass_context
@api
def reject_cmd(ctx, identity):
    c = client(ctx)
    data = c.post(f"/api/cloudflare/dns/requests/{identity}/reject")
    c.close()
    out(ctx, data)
