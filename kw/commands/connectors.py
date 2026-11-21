"""Connectors (connecteurs): hypervisor / cloud / network targets. Secrets go to the vault."""
from __future__ import annotations
import click

from kw import config
from kw.commands.common import api, client, out


@click.group()
def connectors():
    """Add, configure, test and discover connectors."""


@connectors.command("drivers")
@click.pass_context
@api
def drivers_cmd(ctx):
    """List registered connector drivers."""
    c = client(ctx)
    data = c.get("/api/connection-drivers")
    c.close()
    out(ctx, data)


@connectors.command("list")
@click.pass_context
@api
def list_cmd(ctx):
    c = client(ctx)
    data = c.get("/api/connections")
    c.close()
    out(ctx, data, columns=["id", "label", "driver", "endpoint", "enabled"])


@connectors.command("add")
@click.option("--label", required=True, help="Human-readable connector name.")
@click.option("--driver", required=True, help="Registered driver id (see 'kw connectors drivers').")
@click.option("--endpoint", required=True, help="Connection address / URL.")
@click.option("--secret-ref", required=True, help="Logical vault reference for the credentials.")
@click.option("--secrets-provider", required=True, help="Vault provider id (e.g. local vault / OpenBao).")
@click.option("--tls-sha256", default=None, help="Pinned TLS certificate SHA-256 digest.")
@click.option("--disabled", is_flag=True, help="Create the connector disabled.")
@click.pass_context
@api
def add_cmd(ctx, label, driver, endpoint, secret_ref, secrets_provider, tls_sha256, disabled):
    """Register a connector. Connection secrets are stored in the vault, not here."""
    body = {"label": label, "driver": driver, "endpoint": endpoint,
            "secret_ref": secret_ref, "secrets_provider": secrets_provider,
            "enabled": not disabled}
    if tls_sha256:
        body["tls_sha256"] = tls_sha256
    c = client(ctx)
    data = c.post("/api/connections", json=body)
    c.close()
    out(ctx, data)


@connectors.command("get")
@click.argument("identity")
@click.pass_context
@api
def get_cmd(ctx, identity):
    c = client(ctx)
    data = c.get(f"/api/connections/{identity}")
    c.close()
    out(ctx, data)


@connectors.command("credentials")
@click.argument("identity")
@click.option("--username", required=True, help="Connector username.")
@click.pass_context
@api
def credentials_cmd(ctx, identity, username):
    """Store connector credentials in the vault (human admin only). Password via env/file/prompt."""
    password = config.read_password(username)
    c = client(ctx)
    data = c.put(f"/api/connections/{identity}/credentials",
                 json={"username": username, "password": password})
    c.close()
    out(ctx, data)


@connectors.command("test")
@click.argument("identity")
@click.pass_context
@api
def test_cmd(ctx, identity):
    """Probe the connector (same endpoint as discover)."""
    c = client(ctx)
    data = c.post(f"/api/connections/{identity}/test")
    c.close()
    out(ctx, data)


@connectors.command("discover")
@click.argument("identity")
@click.pass_context
@api
def discover_cmd(ctx, identity):
    c = client(ctx)
    data = c.post(f"/api/connections/{identity}/discover")
    c.close()
    out(ctx, data)
