"""Liveness and readiness evidence for local supervisors.

Readiness inspects only local, already-existing material: it never reads a secret
value, calls a provider endpoint, parses the catalogue, initializes storage or
writes state. The probe is unauthenticated, so a supervisor polling it must not
become a credential oracle, a remote load source or a mutation path. A missing
optional secret source is reported as ``unconfigured`` and does not block
traffic; a selected source that cannot be used does.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .module_paths import manifest_paths
from .providers import resolve_provider

OK = 'ok'
UNCONFIGURED = 'unconfigured'
UNAVAILABLE = 'unavailable'
READY_STATUSES = frozenset({OK, UNCONFIGURED})


def _database(store):
    backend = getattr(store, 'backend', 'unknown')
    try:
        row = store.one('SELECT 1 AS present')
        if not row or row.get('present') != 1:
            raise ValueError('probe query returned no evidence')
    except Exception:
        # A probe reports a dependency; it never surfaces driver text, DSNs or a 500.
        return {'status': UNAVAILABLE, 'backend': backend}
    return {'status': OK, 'backend': backend}


def _vault(app):
    bindings = getattr(app.state, 'provider_bindings', {})
    name = bindings.get('secrets')
    result = {'status': UNAVAILABLE, 'provider': name}
    if not isinstance(name, str):
        return result
    try:
        provider = resolve_provider(app, 'secrets')
    except Exception:
        return result
    descriptor = next((row for row in app.state.registry.list('secrets') if row.get('name') == name), {})
    verify = getattr(provider, 'verify', None)
    if callable(verify) and not inspect.iscoroutinefunction(verify) and not inspect.iscoroutinefunction(getattr(verify, '__call__', None)):
        try:
            inspect.signature(verify).bind()
            # Explicit False is a refusal; a hook that returns nothing signals success.
            if verify() is False:
                return result
        except Exception:
            return result
        return {**result, 'status': OK, 'verification': 'material'}
    declared = {'configured': OK, 'unconfigured': UNCONFIGURED}.get(descriptor.get('status'), UNAVAILABLE)
    return {**result, 'status': declared, **({'verification': 'registration'} if declared == OK else {})}


def _plugins(directory):
    try:
        root = Path(directory).absolute()
        if root.is_symlink() or not root.is_dir():
            raise ValueError('plugin directory is missing')
        manifests = sum(1 for path in manifest_paths(root)
                        if not path.parent.is_symlink() and path.is_file())
        if not manifests:
            raise ValueError('no plugin manifests')
    except (OSError, TypeError, ValueError):
        return {'status': UNAVAILABLE, 'manifests': 0}
    return {'status': OK, 'manifests': manifests}


def readiness(app):
    """Aggregate three local dependencies into one supervisor-facing body."""
    checks = {
        'database': _database(app.state.store),
        'vault': _vault(app),
        'plugins': _plugins(getattr(app.state, 'plugins_dir', None)),
    }
    ready = all(check['status'] in READY_STATUSES for check in checks.values())
    return {'status': 'ready' if ready else 'not_ready', 'service': 'kuberweaver', 'checks': checks}


def install_health(app: FastAPI):
    if getattr(app.state, '_health_installed', False):
        return
    app.state._health_installed = True

    @app.get('/readyz')
    def readyz():
        body = readiness(app)
        return JSONResponse(body, status_code=200 if body['status'] == 'ready' else 503,
                            headers={'Cache-Control': 'no-store'})
