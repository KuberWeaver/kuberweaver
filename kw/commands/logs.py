"""Installation logs and audit trail. Verbosity via global -v; logs are retained server-side."""
from __future__ import annotations
import time

import click

from kw.commands.common import api, client, out
from kw.commands.deploy import TERMINAL


@click.command("logs")
@click.argument("identity")
@click.option("--follow", is_flag=True, help="Poll and stream new events until a terminal state.")
@click.option("--timeout", default=180, help="Seconds to wait when following.")
@click.pass_context
@api
def logs_cmd(ctx, identity, follow, timeout):
    """Show installation events for a deployment job."""
    c = client(ctx)
    try:
        if not follow:
            job = c.get(f"/api/jobs/{identity}")
            out(ctx, job.get("events", []))
            return
        deadline = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < deadline:
            job = c.get(f"/api/jobs/{identity}")
            events = job.get("events", [])
            for event in events[seen:]:
                click.echo(_line(event))
            seen = len(events)
            if job["status"] in TERMINAL:
                click.echo(f"status: {job['status']}", err=True)
                return
            time.sleep(1)
        raise SystemExit("timed out following logs")
    finally:
        c.close()


def _line(event: dict) -> str:
    stamp = event.get("at") or event.get("timestamp") or ""
    message = event.get("message") or event.get("event") or str(event)
    return f"[{stamp}] {message}" if stamp else str(message)


@click.command("audit")
@click.option("--limit", default=50, help="Maximum rows to show.")
@click.pass_context
@api
def audit_cmd(ctx, limit):
    """Read the audit trail (who did what)."""
    c = client(ctx)
    data = c.get("/api/audit")
    c.close()
    out(ctx, data[:limit], columns=["id", "actor", "action", "target", "at"])
