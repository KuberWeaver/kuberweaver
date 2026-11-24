"""Reviewed trusted extension: installed disabled; no subprocess at registration."""
import json
from pathlib import Path

from backend.tofu_execution import TofuExecutor


def register(ctx):
    root = ctx.plugin_data_dir()
    # Local administrator configuration ONLY, never a field in a graph or API request.
    path = root / 'runtime.json'
    config = json.loads(path.read_text()) if path.is_file() else {}
    if set(config) - {'binary'}:
        raise ValueError('Unknown OpenTofu administrator runtime configuration')
    binary = config.get('binary')
    if binary is not None and (not isinstance(binary, str) or not Path(binary).is_absolute()):
        raise ValueError('Administrator binary path must be absolute')
    provider = TofuExecutor(root / 'states', binary=binary)
    ctx.register('execution', 'opentofu', provider, {
        'label': 'OpenTofu / libvirt lab (planned)', 'available': provider.read()['binary_present'],
        'status': 'planned', 'requires_plan_approval': True,
        'description': 'One stopped blank-disk VM; separately approved owned-resource removal, not universal rollback',
    })
