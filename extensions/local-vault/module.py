"""Trusted optional local provider; never changes a global provider binding."""
from backend.local_vault import LocalVaultProvider, _cipher, _directory


def register(ctx):
    provider, status = None, 'unavailable'
    try:
        directory = ctx.plugin_data_dir()
        # Registration probes only local existing material, never initializes it.
        with _directory(directory) as fd:
            _cipher(fd)
        provider = LocalVaultProvider(directory, allow_write=True)
        status = 'configured'
    except Exception:
        pass
    ctx.register('secrets', 'local-vault', provider, {
        'label': 'Coffre local chiffré', 'reference_only': True,
        'available': provider is not None, 'status': status,
        'description': 'Fernet local optionnel pour nouvelles connexions ; aucun binding global.',
    })
