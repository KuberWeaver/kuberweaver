"""Data-only manifest discovery. Loading catalog entries never imports plugin code."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import re
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry


class Catalog:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.errors = []

    def discover(self):
        items = {}
        self.errors = []
        for path in sorted(self.directory.glob('*/manifest.yaml')):
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
                    raise ValueError('Manifest must remain inside plugins directory')
                if path.stat().st_size > 256_000:
                    raise ValueError('Manifest too large')
                body = yaml.safe_load(path.read_text())
                if not isinstance(body, dict):
                    raise ValueError('Manifest must be a mapping')
                identity = body['id']
                if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identity):
                    raise ValueError('Invalid plugin ID')
                if identity in items:
                    raise ValueError('Duplicate plugin ID')
                schema = body.get('schema', body.get('config_schema', {'type': 'object', 'additionalProperties': False}))
                Draft202012Validator.check_schema(schema)
                # Never retrieve remote/local JSON-schema refs from untrusted documents.
                if '$ref' in json.dumps(schema) or '$dynamicRef' in json.dumps(schema):
                    raise ValueError('Schema references are unsupported; embed the schema')
                defaults = body.get('defaults', {})
                if not isinstance(defaults, dict):
                    raise ValueError('Defaults must be an object')
                price = float(body.get('price_monthly', 0))
                if not math.isfinite(price) or price < 0:
                    raise ValueError('Price must be finite and nonnegative')
                item = {**body, 'id': identity, 'name': body.get('name', identity),
                        'version': str(body.get('version', '0.0.0')), 'description': body.get('description', ''),
                        'category': body.get('category', 'infrastructure'), 'icon': body.get('icon', 'box'),
                        'price_monthly': price, 'capability': body.get('capability', 'planned'),
                        'engine': body.get('engine', 'unconfigured'), 'schema': schema, 'defaults': defaults,
                        'resources': body.get('resources', {}), 'security': body.get('security', {}),
                        'executor': body.get('executor', {})}
                items[identity] = item
            except Exception as exc:
                self.errors.append({'manifest': str(path.relative_to(self.directory)), 'error': type(exc).__name__ + ': ' + str(exc)[:300]})
        return items

    def fingerprint(self, plugin_id):
        import hashlib
        root = self.directory.resolve()
        directory = root / plugin_id
        if directory.is_symlink() or directory.resolve().parent != root or not directory.is_dir():
            raise ValueError('Invalid plugin source directory')
        digest = hashlib.sha256()
        # Every blueprint conservatively pins the reviewed shared lifecycle tree,
        # including guest helpers loaded by Ansible outside its own directory.
        # Missing shared assets are pinned too, so adding them invalidates approval.
        for tree in (directory, root / '_shared'):
            if tree.is_symlink() or tree.resolve().parent != root:
                raise ValueError('Invalid plugin source directory')
            digest.update(tree.name.encode() + b'\0')
            digest.update(b'present\0' if tree.exists() else b'absent\0')
            for path in sorted(tree.rglob('*')):
                relative = path.relative_to(root)
                if '__pycache__' in relative.parts or path.suffix in {'.pyc', '.pyo'}:
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError('Plugin source symlinks are forbidden')
                if path.is_file():
                    digest.update(str(relative).encode() + b'\0')
                    digest.update(path.read_bytes())
                    digest.update(b'\0')
        return digest.hexdigest()

    def normalize(self, graph):
        result = copy.deepcopy(graph)
        manifests = self.discover()
        for node in result['nodes']:
            plugin = manifests.get(node['plugin_id'])
            if plugin:
                schema_defaults = {key: copy.deepcopy(spec['default']) for key, spec in plugin['schema'].get('properties', {}).items() if isinstance(spec, dict) and 'default' in spec}
                node['config'] = {**schema_defaults, **copy.deepcopy(plugin['defaults']), **node['config']}
        return result
