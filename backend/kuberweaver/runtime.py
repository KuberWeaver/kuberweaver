"""Trusted optional runtime extensions and validated administrator config files."""
from __future__ import annotations
import json
import os
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict
from fastapi import HTTPException
from .extensions import ExtensionRegistry, inspect_directory, read_enabled
from .models import Policies, Pricing
from .providers import register_builtins, DEFAULT_BINDINGS, resolve_provider, provider_listing
from typing import Literal, Annotated
from pydantic import Field

ProviderKind = Literal['inventory', 'diagnostics', 'execution', 'notifications', 'secrets']
ProviderName = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')]


class ProviderPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    bindings: dict[ProviderKind, ProviderName] = Field(min_length=1)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    bindings: dict[ProviderKind, ProviderName]


def load_provider_bindings(app):
    directory = app.state.state_dir / 'config'
    present = [directory / ('providers' + ext) for ext in ('.json', '.yaml', '.yml') if (directory / ('providers' + ext)).exists()]
    app.state.provider_bindings = dict(DEFAULT_BINDINGS)
    app.state.provider_config_error = None
    try:
        if len(present) > 1:
            raise ValueError('Competing binding sources')
        if present:
            value = ProviderConfig.model_validate(yaml.safe_load(present[0].read_text()))
            app.state.provider_bindings.update(value.bindings)
    except Exception:
        app.state.provider_config_error = 'Invalid provider configuration; selected capabilities unavailable'


def queue_busy(app):
    return app.state.worker.active_identity is not None or any(
        job['status'] in {'queued', 'planning', 'approving', 'running', 'rolling_back'} for job in app.state.store.list('jobs'))


def change_provider_bindings(app, patch, actor):
    with app.state.store.lock:
        if queue_busy(app):
            raise HTTPException(409, 'Wait for the execution queue to become idle before changing providers')
        # Validate explicit names against the registered allowlist, never import config strings.
        previous_error = app.state.provider_config_error
        app.state.provider_config_error = None
        try:
            for kind, name in patch.bindings.items():
                resolve_provider(app, kind, name)
                if kind == 'execution':
                    from .providers import execution_binding
                    execution_binding(app, name)
        except HTTPException:
            app.state.provider_config_error = previous_error
            raise HTTPException(422, 'Requested provider is missing or has an incompatible consumer protocol')
        value = {'schema_version': 1, 'bindings': {**app.state.provider_bindings, **patch.bindings}}
        try:
            persist_settings(app, 'providers', value)
            load_provider_bindings(app)
        except Exception:
            app.state.provider_config_error = previous_error
            raise
        app.state.store.audit(actor['username'], 'providers.updated', detail={'bindings': patch.bindings})
        return provider_listing(app)


class TrustRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    trusted: bool


def persist_settings(app, section, value):
    directory = app.state.state_dir / 'config'
    present = [directory / (section + extension) for extension in ('.yaml', '.yml', '.json') if (directory / (section + extension)).exists()]
    if len(present) > 1:
        raise HTTPException(409, 'Multiple administrator config sources; resolve them before updating')
    path = present[0] if present else directory / (section + '.json')
    temporary = path.with_suffix(path.suffix + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)
    if json.loads(path.read_text()) != value:
        raise HTTPException(500, 'Administrator config readback failed')
    app.state.store.put('settings', section, value)


def configure(app):
    state_dir = app.state.state_dir
    config_dir = state_dir / 'config'
    config_dir.mkdir(mode=0o700, exist_ok=True)
    config_dir.chmod(0o700)
    app.state.config_errors = []
    for name, model in [('policies', Policies), ('pricing', Pricing)]:
        paths = [config_dir / (name + extension) for extension in ('.yaml', '.yml', '.json')]
        present = [path for path in paths if path.exists()]
        if present:
            try:
                if len(present) > 1:
                    raise ValueError('Multiple config sources')
                value = model.model_validate(yaml.safe_load(present[0].read_text())).model_dump()
                app.state.store.put('settings', name, value)
            except Exception:
                app.state.config_errors.append({'section': name, 'error': 'Invalid configuration; deny execution until corrected'})
    app.state.admission.config_errors = app.state.config_errors
    app.state.extensions_dir = state_dir / 'extensions'
    app.state.extensions_dir.mkdir(mode=0o700, exist_ok=True)
    app.state.extensions_dir.chmod(0o700)
    # The SDK CLI uses data/extensions.json. Also accept data/config/extensions.json;
    # never merge competing activation authorities.
    preferred = config_dir / 'extensions.json'
    app.state.extension_config = preferred if preferred.exists() else state_dir / 'extensions.json'
    rebuild_registry(app)
    load_provider_bindings(app)


def rebuild_registry(app):
    registry = ExtensionRegistry()
    register_builtins(registry, app)
    try:
        enabled = read_enabled(app.state.extension_config)
        if (app.state.state_dir / 'config' / 'extensions.json').exists() and (app.state.state_dir / 'extensions.json').exists():
            raise ValueError('Competing extension activation files')
        reports = registry.load_directory(app.state.extensions_dir, enabled)
        discovered = {row.get('id') for row in reports}
        reports += [{'id': identity, 'status': 'error', 'error': 'Enabled extension package is missing'} for identity in sorted(enabled - discovered)]
    except Exception:
        reports = [{'status': 'error', 'error': 'Extension activation config invalid; no optional code loaded'}]
    app.state.registry = registry
    app.state.extension_reports = reports
    from .providers import provider_provenance
    # Snapshot registered execution source; approval cannot bless unloaded on-disk edits.
    app.state.provider_loaded_sources = {}
    for row in registry.list('execution'):
        try:
            app.state.provider_loaded_sources[('execution', row['name'])] = provider_provenance(app, 'execution', row['name'])
        except Exception:
            pass  # Exposed as unavailable at the consumer; never substitute another provider.


def extension_listing(app):
    active = {row.get('id'): row for row in app.state.extension_reports if row.get('id')}
    rows = inspect_directory(app.state.extensions_dir)
    for row in rows:
        if row.get('id') in active and active[row['id']]['status'] in {'loaded', 'error'}:
            row.update(active[row['id']])
    rows += [row for row in app.state.extension_reports if not row.get('id') or not any(other.get('id') == row.get('id') for other in rows)]
    return {'protocol_version': 1, 'extensions': rows, 'providers': app.state.registry.list(),
            'config_errors': app.state.config_errors,
            'security': 'Extensions are trusted Python, not sandboxed. Core admission and approval gates remain mandatory.'}


def change_extension(app, identity, enabled, actor, trusted=False):
    with app.state.store.lock:
        if queue_busy(app):
            raise HTTPException(409, 'Wait for the execution queue to become idle before changing extensions')
        rows = inspect_directory(app.state.extensions_dir)
        if not any(row.get('id') == identity and row['status'] != 'error' for row in rows):
            raise HTTPException(404, 'Valid installed extension not found')
        if enabled and trusted is not True:
            raise HTTPException(422, 'Explicit trusted=true is required; extensions execute trusted Python')
        try:
            selected = read_enabled(app.state.extension_config)
        except Exception:
            raise HTTPException(409, 'Correct the extension activation config first')
        selected.add(identity) if enabled else selected.discard(identity)
        path = app.state.extension_config
        temporary = path.with_suffix('.new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'schema_version': 1, 'enabled': sorted(selected)}, stream, indent=2)
        temporary.chmod(0o600)
        temporary.replace(path)
        if read_enabled(path) != selected:
            raise HTTPException(500, 'Activation config readback failed')
        rebuild_registry(app)
        app.state.store.audit(actor['username'], 'extension.enabled' if enabled else 'extension.disabled', identity, {'trusted': enabled})
        return extension_listing(app)
