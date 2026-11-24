"""The built-in catalogue executes directly from the grouped source tree."""
from pathlib import Path

import pytest

from backend import configuration
from backend.catalog import Catalog
from backend.local_execution import LocalExecutor
from backend.local_vm_ansible import LocalVmAnsibleProvider
from backend.ovf_provisioning import OVFExecutor
from backend.provisioning import SavedPlanExecutor

from backend.workload_execution import WorkloadExecutor


ROOT = Path(__file__).resolve().parents[1]


def test_default_runtime_uses_grouped_modules(monkeypatch, tmp_path):
    monkeypatch.delenv('KUBERWEAVER_PLUGINS_DIR', raising=False)
    root = configuration.plugins_dir()
    assert root == ROOT / 'modules'
    assert not (ROOT / 'plugins').exists()
    assert {'local-oci', 'local-qemu', 'web-nginx'} <= set(Catalog(root).discover())
    assert LocalExecutor(tmp_path)._manifest('local-oci')['id'] == 'local-oci'
    assert LocalExecutor(tmp_path)._manifest('local-qemu')['id'] == 'local-qemu'
    assert LocalVmAnsibleProvider(tmp_path).brick()['id'] == 'local-vm-ansible'
    assert WorkloadExecutor(tmp_path).brick('web-nginx')['_directory'] == root / 'services/web-nginx'


@pytest.mark.parametrize('executor, identity', [(OVFExecutor, 'vmware-vm'),
                                                (SavedPlanExecutor, 'vsphere-vm')])
def test_installed_legacy_extension_uses_the_current_template(executor, identity, tmp_path):
    assert executor(tmp_path, ROOT / 'plugins' / identity).template_dir == ROOT / 'modules/resources' / identity
    custom = tmp_path / 'plugins' / identity
    assert executor(tmp_path, custom).template_dir == custom
