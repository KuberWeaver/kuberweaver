"""Registration performs no command, SDK login or remote mutation."""
from backend.ovf_provisioning import ROOT, OVFExecutor


def register(ctx):
    provider = OVFExecutor(ctx.plugin_data_dir() / 'states', ROOT / 'modules/resources/vmware-vm')
    ctx.register('execution', 'esxi-ovf', provider, {
        'label': 'ESXi Ubuntu / OpenTofu + OVF Tool',
        'available': provider.read()['status'] == 'configured', 'status': provider.read()['status'], 'requires_saved_plan': True,
        'supports_rollback': False,
        'description': 'Create-only composition, OS cloud image included. Requires exact saved-plan approval. Guest readiness is verified separately.',
    })
