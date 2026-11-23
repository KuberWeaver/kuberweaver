"""Compatibility of reviewed module source pins across a catalogue move."""
import shutil

from kuberweaver.catalog import Catalog


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
