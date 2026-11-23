"""Opt-in compound execution provider: local guest, verified SSH, pinned Ansible.

Registration is deliberately inert unless a reviewed adapter next to this file
supplies the ``local-runtime-v1`` provisioner. Without it the provider registers
as unavailable and every preparation is refused: no silent fallback.
"""
import importlib.util
from pathlib import Path

from kuberweaver import configuration
from kuberweaver.local_vm_ansible import LocalVmAnsibleProvider

ADAPTER = 'runtime_adapter.py'


def _adapter():
    """Load the reviewed sibling glue, if the operator installed one.

    The adapter module must expose ``runtime()`` returning an object whose
    ``contract`` is ``local-runtime-v1`` and which implements ``provision`` or
    ``handoff``. Nothing is imported from the environment or from job data.
    """
    path = Path(__file__).with_name(ADAPTER)
    if not path.is_file() or path.is_symlink():
        return None
    spec = importlib.util.spec_from_file_location('kuberweaver_local_vm_ansible_adapter', path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, 'runtime', None)
    return factory() if callable(factory) else None


def register(ctx):
    runtime = None
    try:
        runtime = _adapter()
    except Exception:
        runtime = None
    try:
        plugins = configuration.plugins_dir()
    except Exception:
        plugins = None
    provider = LocalVmAnsibleProvider(ctx.plugin_data_dir(), plugins_dir=plugins, runtime=runtime)
    state = provider.read()
    ctx.register('execution', provider.executor_id, provider, {
        'status': state['status'],
        'requires_saved_plan': True,
        'supports_rollback': False,
        'description': ('Local-runtime-v1 guest, attested SSH host identity, '
                        'pinned apply/verify playbooks; no fetch, no remote target.'),
        'limitations': state['limitations'],
    })
