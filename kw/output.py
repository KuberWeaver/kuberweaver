"""Output rendering: table by default, --json for automation, nonzero exit on error.

Exit codes: 0 success, 1 API refusal (404/422/409/...), 2 authentication
required or bad usage, 3 permission denied, 4 API unreachable. Errors go to
stderr as a single 'error: ...' line, never a traceback.
"""
from __future__ import annotations
import json
import sys

import click


def emit(data, as_json: bool, columns: list[str] | None = None) -> None:
    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
        return
    if data is None:
        click.echo("ok")
        return
    rows = data if isinstance(data, list) else [data]
    if not rows:
        click.echo("(empty)")
        return
    if columns is None:
        columns = list(rows[0].keys()) if isinstance(rows[0], dict) else None
    if columns is None:
        for row in rows:
            click.echo(str(row))
        return
    widths = {c: len(c) for c in columns}
    strrows = []
    for row in rows:
        sr = {c: _fmt(row.get(c)) for c in columns}
        for c in columns:
            widths[c] = max(widths[c], len(sr[c]))
        strrows.append(sr)
    header = "  ".join(c.upper().ljust(widths[c]) for c in columns)
    click.echo(header)
    click.echo("  ".join("-" * widths[c] for c in columns))
    for sr in strrows:
        click.echo("  ".join(sr[c].ljust(widths[c]) for c in columns))


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def fail(message: str, code: int = 1) -> None:
    click.echo(f"error: {message}", err=True)
    sys.exit(code)


def handle_api_error(err) -> None:
    from kw.client import ApiError
    if isinstance(err, ApiError):
        if err.status == 401:
            fail("authentication required; run 'kw login' (or set KW_AGENT_TOKEN)", 2)
        if err.status == 403:
            fail(f"permission denied (role or CSRF): {err.detail}", 3)
        fail(err.detail, 1)
    raise err


def handle_unreachable(err) -> None:
    """Clean one-line message when the API cannot be reached (no traceback)."""
    fail(f"{err}; check --url / KW_URL and that the server is running", 4)
