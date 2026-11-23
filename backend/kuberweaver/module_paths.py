"""Locate catalogue modules in the repository or a legacy flat catalogue."""
from pathlib import Path
import re


LAYOUT_SOURCE = Path(__file__).resolve()
MODULES_DIR = LAYOUT_SOURCE.parents[2] / 'modules'


def module_roots(root):
    root = Path(root)
    yield root
    for name in ('resources', 'services'):
        group = root / name
        if not group.is_symlink() and group.is_dir() and not (group / 'manifest.yaml').exists():
            yield group


def manifest_paths(root):
    for directory in module_roots(root):
        yield from directory.glob('*/manifest.yaml')


def module_directory(root, identity):
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identity):
        raise ValueError('Invalid module directory name')
    matches = [directory / identity for directory in module_roots(root)
               if (directory / identity).exists() or (directory / identity).is_symlink()]
    if not matches:
        raise FileNotFoundError(identity)
    if len(matches) > 1:
        raise ValueError('Ambiguous module source directory')
    directory = matches[0]
    if directory.is_symlink() or directory.resolve().parent != directory.parent.resolve() or not directory.is_dir():
        raise ValueError('Invalid module source directory')
    return directory
