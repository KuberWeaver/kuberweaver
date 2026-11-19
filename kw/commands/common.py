"""Shared helpers for command groups: client, output, API error handling."""
from __future__ import annotations
import functools

from kw.client import build_client, ApiError
from kw.output import emit, handle_api_error


def client(ctx):
    return build_client(ctx.obj["url"])


def out(ctx, data, columns=None):
    emit(data, ctx.obj["json"], columns)


def api(fn):
    """Translate ApiError into a clean stderr message and nonzero exit code."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError as err:
            handle_api_error(err)
    return wrapper
