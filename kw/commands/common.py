"""Shared helpers for command groups: client, output, API error handling."""
from __future__ import annotations
import functools

from kw.client import build_client, ApiError, UnreachableError
from kw.output import emit, handle_api_error, handle_unreachable


def client(ctx):
    return build_client(ctx.obj["url"])


def out(ctx, data, columns=None):
    emit(data, ctx.obj["json"], columns)


def api(fn):
    """Translate API errors into clean stderr messages and nonzero exit codes."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError as err:
            handle_api_error(err)
        except UnreachableError as err:
            handle_unreachable(err)
    return wrapper
