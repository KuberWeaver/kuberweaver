#!/usr/bin/env python3
"""Synchronize the flat runtime catalogue without touching installed dependencies."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def tracked(prefix):
    output = subprocess.check_output(['git', 'ls-files', '-z', '--', prefix], cwd=ROOT)
    return {Path(path) for path in output.decode().split('\0') if path}


def main():
    if sys.argv[1:] not in ([], ['--check']):
        raise SystemExit('usage: sync_compat_catalog.py [--check]')
    check = sys.argv[1:] == ['--check']
    expected = {}
    for path in sorted(tracked('modules/')):
        if len(path.parts) < 4 or path.parts[1] not in {'resources', 'services'}:
            raise SystemExit(f'Unexpected catalogue path: {path}')
        target = Path('plugins', *path.parts[2:])
        if target in expected:
            raise SystemExit(f'Ambiguous runtime path: {target}')
        expected[target] = path
    extra = tracked('plugins/') - expected.keys()
    if extra:
        raise SystemExit('Remove obsolete tracked runtime files explicitly: ' + ', '.join(map(str, sorted(extra))))
    for target, source in expected.items():
        src, dst = ROOT / source, ROOT / target
        if src.is_symlink() or any(parent.is_symlink() for parent in src.parents if parent.is_relative_to(ROOT)):
            raise SystemExit(f'Unsafe canonical path: {source}')
        if dst.is_symlink() or any(parent.is_symlink() for parent in dst.parents if parent.is_relative_to(ROOT)):
            raise SystemExit(f'Unsafe runtime path: {target}')
        if not src.is_file() or (dst.exists() and (not dst.is_file() or dst.stat().st_nlink != 1)):
            raise SystemExit(f'Expected regular, independent catalogue files: {source}, {target}')
        same = (dst.is_file() and dst.read_bytes() == src.read_bytes()
                and bool(dst.stat().st_mode & 0o111) == bool(src.stat().st_mode & 0o111))
        if not same:
            if check:
                raise SystemExit(f'Stale runtime catalogue: {target}; run {Path(__file__).name}')
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    if check and tracked('plugins/') != expected.keys():
        raise SystemExit('Stage the generated runtime catalogue files')
    print(f'Runtime catalogue synchronized: {len(expected)} files')


if __name__ == '__main__':
    main()
