"""Agent tokens: human-issued, hashed, short-lived machine principals.

Thin wrapper over the agent-token routes only (backend/agent_auth.py):
- GET    /api/agent-tokens              human admin; metadata, never the secret
- POST   /api/agent-tokens              human admin; the secret is returned once
- POST   /api/agent-tokens/{id}/rotate  human admin; new secret, same identity/expiry
- DELETE /api/agent-tokens/{id}         human admin; immediate, idempotent revocation

Automation mode (KW_AGENT_TOKEN): create/rotate write the one-time secret to
--token-file with 0600 permissions and never print it. Export it, e.g.
KW_AGENT_TOKEN="$(cat agent.token)", and every kw command authenticates with
'Authorization: Bearer' instead of a human session: no login, no CSRF, same
routes, roles and pins as the GUI. The token is limited to its role and scopes:
read/plan/submit automation can list, validate, request and follow jobs. The
API refuses approve and rollback unless a human administrator explicitly
issued those scopes to an admin-role token, and decommission is never a
machine path (human administrator session required). Agents cannot list,
issue, rotate or revoke tokens.
"""
from __future__ import annotations

import os
from pathlib import Path

import click

from kw.commands.common import api, client, out


def _write_secret(dest: str, secret: str) -> Path:
    """One-time secret to a 0600 file; never echoed, never in argv."""
    path = Path(dest)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(secret)
    os.chmod(path, 0o600)
    return path


@click.group()
def tokens():
    """Agent tokens for automation: list, create, rotate, revoke (human admin only).

    Automation mode: the secret is written once to --token-file (0600, never
    printed); export KW_AGENT_TOKEN="$(cat <token-file>)" and kw runs as the
    agent (Bearer auth, no login/CSRF). Automation is limited to the token's
    scopes: read/plan/submit covers listing, validation, requests and
    following jobs. Approve/rollback require scopes explicitly granted by a
    human admin to an admin-role token; decommission is never a machine path.
    Agents cannot manage tokens.
    """


@tokens.command("list")
@click.pass_context
@api
def list_cmd(ctx):
    """List token metadata (human admin only; secrets are never returned)."""
    c = client(ctx)
    data = c.get("/api/agent-tokens")
    c.close()
    out(ctx, data["tokens"],
        columns=["id", "label", "role", "scopes", "expires_at", "revoked_at"])


@tokens.command("create")
@click.option("--label", required=True,
              help="Token label, validated by the API (1-80 chars, no newlines).")
@click.option("--role", required=True,
              type=click.Choice(["viewer", "operator", "admin"]),
              help="Principal role (closed set, same as the API schema); caps the scopes.")
@click.option("--scopes", "scopes", multiple=True,
              help="Repeatable scope; must be permitted for the role (API-validated). "
                   "Omitted: the API default ['read'] applies.")
@click.option("--expires-in", "expires_in_seconds", required=True, type=int,
              help="Lifetime in seconds; the API enforces 60..2592000.")
@click.option("--token-file", required=True, type=click.Path(dir_okay=False),
              help="Destination for the one-time secret (written 0600, never printed).")
@click.pass_context
@api
def create_cmd(ctx, label, role, scopes, expires_in_seconds, token_file):
    """Issue an agent token (human admin only). The secret goes to --token-file only.

    Then automate with KW_AGENT_TOKEN="$(cat <token-file>)". Keep the scopes
    minimal: read/plan/submit for read-validate-request-follow automation.
    Approve/rollback scopes are an explicit human grant for admin-role tokens;
    decommission stays human-only regardless of scopes.
    """
    payload = {"label": label, "role": role, "expires_in_seconds": expires_in_seconds}
    if scopes:
        payload["scopes"] = list(scopes)
    c = client(ctx)
    data = c.post("/api/agent-tokens", json=payload)
    c.close()
    path = _write_secret(token_file, data["token"])
    out(ctx, {**data["metadata"], "token_file": str(path)})


@tokens.command("rotate")
@click.argument("identity")
@click.option("--token-file", required=True, type=click.Path(dir_okay=False),
              help="Destination for the new one-time secret (written 0600, never printed).")
@click.pass_context
@api
def rotate_cmd(ctx, identity, token_file):
    """Replace a token's secret (human admin only); identity, role, scopes and
    the original expiry are kept. Revoked or expired tokens are refused (409):
    issue a new token instead. The old secret stops working immediately.
    """
    c = client(ctx)
    data = c.post(f"/api/agent-tokens/{identity}/rotate")
    c.close()
    path = _write_secret(token_file, data["token"])
    out(ctx, {**data["metadata"], "token_file": str(path)})


@tokens.command("revoke")
@click.argument("identity")
@click.pass_context
@api
def revoke_cmd(ctx, identity):
    """Revoke a token (human admin only). Immediate and idempotent; the secret
    dies with it and can never be rotated back to life.
    """
    c = client(ctx)
    data = c.delete(f"/api/agent-tokens/{identity}")
    c.close()
    out(ctx, data)
