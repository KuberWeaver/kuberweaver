"""Compatibility of reviewed module source pins across a catalogue move."""
from pathlib import Path
import shutil
import subprocess
import sys

from kuberweaver.catalog import Catalog
from kuberweaver import configuration


ROOT = Path(__file__).resolve().parents[1]


def test_release_keeps_the_original_runtime_catalogue_and_template_paths(monkeypatch):
    monkeypatch.delenv('KUBERWEAVER_PLUGINS_DIR', raising=False)
    assert configuration.plugins_dir() == ROOT / 'plugins'
    for path in ('vmware-vm/main.tf', 'vmware-vm/manifest.yaml',
                 'vsphere-vm/main.tf', 'vsphere-vm/manifest.yaml',
                 'vsphere-vm/.terraform.lock.hcl'):
        old = ROOT / 'plugins' / path
        assert old.is_file() and not old.is_symlink()
        assert old.read_bytes() == (ROOT / 'modules' / 'resources' / path).read_bytes()


def test_runtime_catalogue_matches_tracked_source():
    subprocess.run([sys.executable, str(ROOT / "scripts/sync_compat_catalog.py"), "--check"],
                   cwd=ROOT, check=True)


def test_moving_source_into_a_group_preserves_reviewed_fingerprint(tmp_path):
    root = tmp_path / 'catalogue'
    original = root / 'sample'
    original.mkdir(parents=True)
    (original / 'manifest.yaml').write_text('id: sample\n')
    (original / 'apply.yml').write_text('- name: original\n')
    shared = root / '_shared'
    shared.mkdir()
    (shared / 'helper.yml').write_text('- name: common\n')
    catalogue = Catalog(root)
    before = catalogue.fingerprint('sample')

    group = root / 'services'
    group.mkdir()
    relocated = group / 'sample'
    shutil.move(original, relocated)
    assert catalogue.fingerprint('sample') == before

    (relocated / 'apply.yml').write_text('- name: changed\n')
    assert catalogue.fingerprint('sample') != before
    (relocated / 'apply.yml').write_text('- name: original\n')
    (shared / 'helper.yml').write_text('- name: changed helper\n')
    assert catalogue.fingerprint('sample') != before
