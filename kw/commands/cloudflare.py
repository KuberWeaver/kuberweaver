"""Cloudflare DNS and tunnels: read zones/records/tunnels, propose changes,
approve/reject. Governed."""
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


@cloudflare.group("tunnel")
def tunnel_group():
    """Cloudflare named tunnels through vault-scoped tokens; changes need approval."""


@tunnel_group.command("list")
@click.option("--connector-id", required=True, help="Cloudflare connector id (tunnel purpose).")
@click.pass_context
@api
def tunnel_list_cmd(ctx, connector_id):
    """List named tunnels visible to the connector (read-only)."""
    c = client(ctx)
    data = c.get("/api/cloudflare/tunnel/tunnels", params={"connector_id": connector_id})
    c.close()
    out(ctx, data)


@tunnel_group.command("show")
@click.option("--connector-id", required=True, help="Cloudflare connector id.")
@click.option("--tunnel-id", required=True, help="Named tunnel id.")
@click.pass_context
@api
def tunnel_show_cmd(ctx, connector_id, tunnel_id):
    """Show one named tunnel and its ingress configuration (read-only)."""
    c = client(ctx)
    data = c.get(f"/api/cloudflare/tunnel/tunnels/{tunnel_id}",
                 params={"connector_id": connector_id})
    c.close()
    out(ctx, data)


@tunnel_group.command("request")
@click.option("--connector-id", required=True, help="Cloudflare connector id.")
@click.option("--operation", required=True, type=click.Choice(["create", "configure", "delete"]))
@click.option("--name", default=None, help="Tunnel name (create).")
@click.option("--tunnel-id", default=None, help="Tunnel id (configure/delete).")
@click.option("--ingress-file", type=click.Path(exists=True), default=None,
              help="JSON ingress rules (create/configure).")
@click.option("--ingress-json", default=None, help="Inline JSON ingress rules.")
@click.option("--zone-id", default=None, help="Zone for the DNS CNAME route.")
@click.option("--record-id", default=None, help="DNS record id to remove (delete).")
@click.option("--secret-ref", default=None, help="Vault reference for the tunnel credential.")
@click.pass_context
@api
def tunnel_request_cmd(ctx, connector_id, operation, name, tunnel_id, ingress_file,
                       ingress_json, zone_id, record_id, secret_ref):
    """Propose a tunnel change; it lands in pending_approval, never auto-executed."""
    if ingress_file and ingress_json:
        raise click.UsageError("provide at most one of --ingress-file / --ingress-json")
    payload = {"connector_id": connector_id, "operation": operation}
    if name:
        payload["name"] = name
    if tunnel_id:
        payload["tunnel_id"] = tunnel_id
    raw = None
    if ingress_file:
        raw = Path(ingress_file).read_text()
    elif ingress_json:
        raw = ingress_json
    if raw is not None:
        try:
            payload["ingress"] = json.loads(raw)
        except json.JSONDecodeError as err:
            raise click.UsageError(f"invalid ingress JSON: {err}") from None
    if zone_id:
        route = {"zone_id": zone_id}
        if record_id:
            route["record_id"] = record_id
        payload["route"] = route
    if secret_ref:
        payload["tunnel_secret_ref"] = secret_ref
    c = client(ctx)
    data = c.post("/api/cloudflare/tunnel/requests", json=payload)
    c.close()
    out(ctx, data)


@tunnel_group.command("requests")
@click.pass_context
@api
def tunnel_requests_cmd(ctx):
    """List tunnel change requests."""
    c = client(ctx)
    data = c.get("/api/cloudflare/tunnel/requests")
    c.close()
    out(ctx, data, columns=["id", "operation", "status", "name", "created_by"])


@tunnel_group.command("get")
@click.argument("identity")
@click.pass_context
@api
def tunnel_get_cmd(ctx, identity):
    """Show one tunnel change request and its sanitized result."""
    c = client(ctx)
    data = c.get(f"/api/cloudflare/tunnel/requests/{identity}")
    c.close()
    out(ctx, data)


@tunnel_group.command("approve")
@click.argument("identity")
@click.pass_context
@api
def tunnel_approve_cmd(ctx, identity):
    """Administrator approval; executes the pinned change against Cloudflare."""
    c = client(ctx)
    data = c.post(f"/api/cloudflare/tunnel/requests/{identity}/approve")
    c.close()
    out(ctx, data)


@tunnel_group.command("reject")
@click.argument("identity")
@click.pass_context
@api
def tunnel_reject_cmd(ctx, identity):
    c = client(ctx)
    data = c.post(f"/api/cloudflare/tunnel/requests/{identity}/reject")
    c.close()
    out(ctx, data)
