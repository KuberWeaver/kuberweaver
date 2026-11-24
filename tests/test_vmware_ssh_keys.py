"""Managed public-key references are bound to the exact reviewed VM plan."""
import copy
import json
import pytest
from test_ovf_provisioning import executor, job, module

KEY_ID = 'd' * 32
PUBLIC_KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhY test'

class Keys:
    def __init__(self):
        self.enabled = True
        self.revision = 1
    def binding(self, identity):
        assert identity == KEY_ID
        if not self.enabled:
            raise ValueError('SSH key is disabled')
        return {'id': identity, 'revision': self.revision, 'public_key': PUBLIC_KEY,
                'fingerprint': 'SHA256:fixture', 'provider': {'name':'local-vault','provenance':'fixture'}}
    def private_key(self, identity):
        pytest.fail('VM planning must never resolve the private key')

def request():
    value = job()
    value['graph']['nodes'][0]['config'].pop('ssh_public_key', None)
    value['graph']['nodes'][0]['config']['ssh_key_id'] = KEY_ID
    return value


def test_managed_reference_resolves_public_key_without_exporting_private_material(tmp_path):
    ex = executor(tmp_path)
    ex.ssh_keys = Keys()
    value = request()
    review = ex.review(value)
    assert review['config']['ssh_public_key'] == PUBLIC_KEY
    assert review['ssh_key_binding']['id'] == KEY_ID
    assert 'ssh_public_key' not in value['graph']['nodes'][0]['config']
    prepared = ex.prepare_plan(value, lambda *args: None)
    assert prepared['summary']['ssh_key_id'] == KEY_ID
    assert prepared['summary']['ssh_key_fingerprint'] == 'SHA256:fixture'
    folder = ex.state_dir / value['id']
    assert 'PRIVATE KEY' not in (folder / 'terraform.tfvars.json').read_text()
    assert PUBLIC_KEY in (folder / 'terraform.tfvars.json').read_text()


def test_disabled_key_is_rejected_before_any_planning_command(tmp_path):
    ex = executor(tmp_path); ex.ssh_keys = Keys(); ex.ssh_keys.enabled = False
    ex.runner = lambda *args, **kw: pytest.fail('Disabled SSH key must reject before dispatch')
    with pytest.raises(ValueError, match='SSH key'):
        ex.prepare_plan(request(), lambda *args: None)
    assert not (ex.state_dir / ('a' * 32)).exists()


def test_key_revision_drift_invalidates_the_prepared_plan(tmp_path):
    ex = executor(tmp_path); ex.ssh_keys = Keys(); value = request()
    value['prepared_plan'] = ex.prepare_plan(value, lambda *args: None)
    ex.ssh_keys.revision += 1
    ex.runner = lambda *args, **kw: pytest.fail('Changed key binding must not run commands')
    with pytest.raises(ValueError):
        ex.validate_plan(value)


@pytest.mark.parametrize('environment', ['staging', 'production'])
def test_vmware_key_management_preserves_existing_environment_restrictions(tmp_path, environment):
    from backend.models import Graph
    ex = executor(tmp_path); ex.ssh_keys = Keys(); value = request()
    value['graph']['name'] = 'Managed key plan'
    value['graph']['environment'] = environment
    value['graph'] = Graph.model_validate(value['graph']).model_dump()
    with pytest.raises(ValueError, match='independent Ubuntu VM'):
        ex.review(value)
