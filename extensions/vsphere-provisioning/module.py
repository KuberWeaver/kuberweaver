"""Trusted opt-in saved-plan executor; registration performs no command/network.

The host injects ``connections`` after provider resolution. The SDK exposes no
application object, and no graph value is an executable or filesystem path.
"""
try:
    from kuberweaver.provisioning import ROOT, SavedPlanExecutor
except ModuleNotFoundError:
    from backend.kuberweaver.provisioning import ROOT, SavedPlanExecutor


def register(ctx):
    provider = SavedPlanExecutor(ctx.plugin_data_dir() / 'states', ROOT / 'plugins/vsphere-vm')
    ctx.register('execution', 'vsphere-provisioner', provider, {
        'label': 'vSphere / OpenTofu (stopped-create blocked)',
        'available': False, 'status': 'blocked', 'requires_saved_plan': True,
        'supports_rollback': False,
        'description': 'Private exact-plan protocol; strict PKI required. Official provider 2.18.0 powers on a bare-created VM, so this stopped-VM contract is blocked.',
    })
