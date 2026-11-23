"""Registration performs no command, SDK login or remote mutation."""
try:
    from kuberweaver.ovf_provisioning import ROOT, OVFExecutor
except ModuleNotFoundError:
    from backend.kuberweaver.ovf_provisioning import ROOT, OVFExecutor


def register(ctx):
    provider = OVFExecutor(ctx.plugin_data_dir() / 'states', ROOT / 'plugins/vmware-vm')
    ctx.register('execution', 'esxi-ovf', provider, {
        'label': 'ESXi Ubuntu / OpenTofu + OVF Tool',
        'available': provider.read()['status'] == 'configured', 'status': provider.read()['status'], 'requires_saved_plan': True,
        'supports_rollback': False,
        'description': 'Create-only composition, OS cloud image included. Requires exact saved-plan approval. Guest readiness is verified separately.',
    })
