"""Deployment jobs: request, validate (dry-run), approve, reject, watch. Governed."""
from __future__ import annotations
import json
import time
from pathlib import Path

import click

from kw.commands.common import api, client, out

TERMINAL = {"succeeded", "failed", "rolled_back", "rollback_failed", "rejected",
            "decommissioned", "decommission_failed"}


def _load_graph(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError as err:
        raise click.UsageError(f"invalid JSON in {path}: {err}") from None


@click.group()
def deploy():
    """Request, validate, approve and follow deployments."""


@deploy.command("request")
@click.option("--graph-file", type=click.Path(exists=True), help="Deployment graph JSON.")
@click.option("--blueprint-id", help="Deploy an existing blueprint's graph.")
@click.option("--offering-id", help="Request a published service offering.")
@click.pass_context
@api
def request_cmd(ctx, graph_file, blueprint_id, offering_id):
    """Submit a deployment; it lands in pending_approval, never auto-executed."""
    c = client(ctx)
    if offering_id:
        data = c.post(f"/api/studio/offerings/{offering_id}/request")
    else:
        if blueprint_id:
            graph = c.get(f"/api/blueprints/{blueprint_id}")["graph"]
        elif graph_file:
            graph = _load_graph(graph_file)
        else:
            raise click.UsageError("provide --graph-file, --blueprint-id or --offering-id")
        data = c.post("/api/jobs", json={"graph": graph})
    c.close()
    out(ctx, data)


@deploy.command("validate")
@click.option("--graph-file", required=True, type=click.Path(exists=True))
@click.pass_context
@api
def validate_cmd(ctx, graph_file):
    """Dry-run admission + execution readiness; no job is created."""
    c = client(ctx)
    data = c.post("/api/plans/validate", json=_load_graph(graph_file))
    c.close()
    out(ctx, data)


@deploy.command("list")
@click.pass_context
@api
def list_cmd(ctx):
    c = client(ctx)
    data = c.get("/api/jobs")
    c.close()
    out(ctx, data, columns=["id", "name", "status", "environment"])


@deploy.command("get")
@click.argument("identity")
@click.pass_context
@api
def get_cmd(ctx, identity):
    c = client(ctx)
    data = c.get(f"/api/jobs/{identity}")
    c.close()
    out(ctx, data)


@deploy.command("approve")
@click.argument("identity")
@click.pass_context
@api
def approve_cmd(ctx, identity):
    """Human administrator approval; required before execution."""
    c = client(ctx)
    data = c.post(f"/api/jobs/{identity}/approve")
    c.close()
    out(ctx, data)


@deploy.command("reject")
@click.argument("identity")
@click.pass_context
@api
def reject_cmd(ctx, identity):
    c = client(ctx)
    data = c.post(f"/api/jobs/{identity}/reject")
    c.close()
    out(ctx, data)


@deploy.command("watch")
@click.argument("identity")
@click.option("--timeout", default=180, help="Seconds to wait for a terminal state.")
@click.pass_context
@api
def watch_cmd(ctx, identity, timeout):
    """Poll the job until a terminal state, printing status transitions."""
    c = client(ctx)
    deadline = time.monotonic() + timeout
    last = None
    try:
        while time.monotonic() < deadline:
            job = c.get(f"/api/jobs/{identity}")
            if job["status"] != last:
                last = job["status"]
                if not ctx.obj["json"]:
                    click.echo(f"status: {last}", err=True)
            if job["status"] in TERMINAL:
                out(ctx, job)
                if job["status"] not in {"succeeded", "rolled_back", "decommissioned"}:
                    raise SystemExit(1)
                return
            time.sleep(1)
    finally:
        c.close()
    raise SystemExit("timed out waiting for a terminal state")
