"""Regenerate the declarative local blueprint catalog (no deployment)."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'targets': {'type': 'array', 'items': {'enum': ['app', 'branch-client']}, 'minItems': 1, 'maxItems': 2, 'uniqueItems': True, 'default': ['app', 'branch-client']},
        'port': {'type': 'integer', 'minimum': 18090, 'maximum': 18109, 'default': 18090},
        'version': {'type': 'string', 'pattern': r'^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$', 'default': '1.0.0'},
        'cpu': {'type': 'number', 'minimum': 0.1, 'maximum': 2, 'default': 1},
        'memory_mb': {'type': 'integer', 'minimum': 64, 'maximum': 1024, 'default': 256},
        'disk_gb': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'default': 2},
        'public_cidr': {'const': '127.0.0.1/32', 'default': '127.0.0.1/32'},
    },
}
DEFAULTS = {k: v['default'] for k, v in SCHEMA['properties'].items()}
CATALOG = [
    ('nginx', 'NGINX', 'web', 'globe', 5, 'nginx:1.28-alpine', '/usr/share/nginx/html'),
    ('apache', 'Apache HTTP Server', 'web', 'server', 5, 'httpd:2.4-alpine', '/usr/local/apache2/htdocs'),
    ('dev-stack', 'Development Stack', 'development', 'code', 15, None, None),
    ('cisco-ios', 'Cisco IOS', 'network', 'network', 25, None, None),
    ('pfsense', 'pfSense', 'security', 'shield', 15, None, None),
    ('cloud-vm', 'Cloud VM', 'compute', 'cloud', 20, None, None),
]


def main():
    for ident, name, category, icon, price, image, docroot in CATALOG:
        directory = ROOT / 'plugins' / ident
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            'id': ident, 'name': name, 'version': '1.0.0',
            'description': ('Real container on allowlisted GNS3 guests, deployed by Ansible.' if image else 'Planned blueprint; deployment is disabled.'),
            'category': category, 'icon': icon, 'price_monthly': price,
            'capability': 'live' if image else 'planned', 'engine': 'ansible' if image else 'opentofu',
            'schema': SCHEMA, 'defaults': DEFAULTS, 'resources': {'cpu': 1, 'memory_mb': 256, 'disk_gb': 2},
            'security': {'status': 'local_checks', 'checks': ['allowlisted_targets', 'management_binding', 'strict_schema', 'owned_resources_only'] if image else []},
            'executor': 'ansible-container-v1' if image else 'unsupported',
        }
        if image:
            manifest['service'] = {'image': image, 'document_root': docroot, 'container_port': 80, 'health_path': '/version.json'}
            manifest['iac'] = {key: 'iac/' + filename for key, filename in [('snapshot', 'snapshot.yml'), ('apply', 'playbook.yml'), ('verify', 'verify.yml'), ('restore', 'rollback.yml')]}
            (directory / 'iac').mkdir(exist_ok=True)
            for filename in manifest['iac'].values():
                (directory / filename).write_text('---\n- import_playbook: ../../_shared/container.yml\n')
        (directory / 'manifest.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False))


if __name__ == '__main__':
    main()
