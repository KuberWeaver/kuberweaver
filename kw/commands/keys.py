"""SSH keys (clés SSH): generate, list, revoke, export. Private material stays in the vault."""
from __future__ import annotations
import os
from pathlib import Path

import click

from kw import config
from kw.commands.common import api, client, out


@click.group()
def keys():
    """Generate and manage SSH keys used by Ansible / OpenTofu."""


@keys.command("list")
@click.pass_context
@api
def list_cmd(ctx):
    c = client(ctx)
    data = c.get("/api/ssh-keys")
    c.close()
    out(ctx, data, columns=["id", "name", "enabled", "fingerprint"])


@keys.command("generate")
@click.option("--name", required=True, help="Key name.")
@click.pass_context
@api
def generate_cmd(ctx, name):
    """Generate a key pair; the private key is stored in the vault (human admin only)."""
    c = client(ctx)
    data = c.post("/api/ssh-keys", json={"name": name})
    c.close()
    out(ctx, data)


@keys.command("get")
@click.argument("identity")
@click.pass_context
@api
def get_cmd(ctx, identity):
    c = client(ctx)
    data = c.get(f"/api/ssh-keys/{identity}")
    c.close()
    out(ctx, data)


@keys.command("revoke")
@click.argument("identity")
@click.pass_context
@api
def revoke_cmd(ctx, identity):
    """Disable a key (revocation). It is no longer injected into new resources."""
    c = client(ctx)
    data = c.patch(f"/api/ssh-keys/{identity}", json={"enabled": False})
    c.close()
    out(ctx, data)


@keys.command("enable")
@click.argument("identity")
@click.pass_context
@api
def enable_cmd(ctx, identity):
    c = client(ctx)
    data = c.patch(f"/api/ssh-keys/{identity}", json={"enabled": True})
    c.close()
    out(ctx, data)


@keys.command("export")
@click.argument("identity")
@click.option("--output", "out_path", required=True, type=click.Path(),
              help="Destination file for the private key (written 0600, never printed).")
@click.pass_context
@api
def export_cmd(ctx, identity, out_path):
    """Export the private key after admin reauthentication. Binary download, saved 0600."""
    password = config.read_password("admin password (reauthentication)")
    c = client(ctx)
    response = c.request("POST", f"/api/ssh-keys/{identity}/export", json={"password": password})
    c.close()
    path = Path(out_path)
    path.write_bytes(response.content)
    os.chmod(path, 0o600)
    out(ctx, {"exported": identity, "path": str(path)})
