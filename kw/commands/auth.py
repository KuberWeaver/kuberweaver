"""Authentication: human session (cookie + CSRF) or scoped agent token."""
from __future__ import annotations
import click

from kw import config
from kw.client import build_client
from kw.commands.common import api, client, out


@click.group()
def auth():
    """Login, logout, and identity."""


@auth.command("login")
@click.option("--username", "-u", prompt=True, help="Account username.")
@click.pass_context
@api
def login_cmd(ctx, username):
    """Authenticate a human session; stores cookie + CSRF in a 0600 file."""
    password = config.read_password(username)
    c = build_client(ctx.obj["url"], use_session=False)
    data = c.login(username, password)
    c.close()
    out(ctx, {"user": data["user"]["username"], "role": data["user"]["role"],
              "url": config.resolve_url(ctx.obj["url"])})
    if not ctx.obj["json"]:
        click.echo("session saved; you are logged in", err=True)


@auth.command("logout")
@click.pass_context
@api
def logout_cmd(ctx):
    """Revoke the session server-side and clear the local session file."""
    c = client(ctx)
    try:
        c.post("/api/auth/logout")
    finally:
        c.close()
    config.clear_session()
    out(ctx, {"logged_out": True})


@auth.command("whoami")
@click.pass_context
@api
def whoami_cmd(ctx):
    """Show the current identity (human session or agent token)."""
    c = client(ctx)
    data = c.get("/api/auth/me")
    c.close()
    user = data["user"]
    out(ctx, {"username": user.get("username"), "role": user.get("role"),
              "principal_type": user.get("principal_type", "human")})
