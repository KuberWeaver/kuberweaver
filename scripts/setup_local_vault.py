#!/usr/bin/env python3
"""Initialize optional Fernet storage. --install explicitly trusts only local-vault."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.local_vault import _atomic, _directory, _read, initialize
from backend.secrets import SecretError
from backend.extensions import validate_package


def _activation(fd):
    try:
        child = os.open('extensions.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return {'schema_version': 1, 'enabled': []}
    with os.fdopen(child, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) not in (0o600, 0o644)):
            raise SecretError('activation configuration not protected')
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError
    config = json.loads(raw)
    if (not isinstance(config, dict) or set(config) != {'schema_version', 'enabled'}
            or type(config['schema_version']) is not int or config['schema_version'] != 1
            or not isinstance(config['enabled'], list)
            or any(not isinstance(item, str) for item in config['enabled'])
            or len(set(config['enabled'])) != len(config['enabled'])):
        raise ValueError
    return config


@contextmanager
def _activation_target(data, data_fd):
    # Respect the host's single activation authority. Never create a competitor.
    preferred = data / 'config'
    has_preferred = False
    try:
        with _directory(preferred) as fd:
            try:
                os.stat('extensions.json', dir_fd=fd, follow_symlinks=False)
                has_preferred = True
            except FileNotFoundError:
                pass
    except SecretError:
        if preferred.exists() or preferred.is_symlink():
            raise
    if has_preferred:
        try:
            os.stat('extensions.json', dir_fd=data_fd, follow_symlinks=False)
        except FileNotFoundError:
            with _directory(preferred) as fd:
                yield fd
        else:
            raise SecretError('competing activation authorities')
    else:
        yield data_fd


def install(data):
    source = ROOT / 'extensions/local-vault'
    validate_package(source)
    with _directory(data) as data_fd:
        lock = os.open('.extensions.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                       0o600, dir_fd=data_fd)
        try:
            info = os.fstat(lock)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise ValueError
            fcntl.flock(lock, fcntl.LOCK_EX)
            with _activation_target(data, data_fd) as config_fd:
                config = _activation(config_fd)
                with _directory(data / 'extensions', create=True):
                    pass
                destination = data / 'extensions/local-vault'
                with _directory(destination, create=True, exclusive=True) as fd:
                    for name in ('module.py', 'plugin.yaml', 'README.md'):
                        content = (source / name).read_bytes()
                        try:
                            if _read(fd, name) != content:
                                raise SecretError('installed extension differs; review manually')
                        except FileNotFoundError:
                            _atomic(fd, name, content)
                        if _read(fd, name) != content:
                            raise ValueError
                validate_package(destination)
                changed = 'local-vault' not in config['enabled']
                if changed:
                    config['enabled'].append('local-vault')
                    _atomic(config_fd, 'extensions.json', (json.dumps(config) + '\n').encode())
                if _activation(config_fd) != config:
                    raise ValueError
        finally:
            os.close(lock)
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path,
                        default=Path(os.environ.get('KUBERWEAVER_DATA_DIR', ROOT / 'data')))
    parser.add_argument('--install', action='store_true',
                        help='install and enable this trusted extension only; restart the app')
    args = parser.parse_args(argv)
    try:
        data = args.data_dir.absolute()
        with _directory(data, create=True):
            pass
        with _directory(data / 'plugin-data', create=True):
            pass
        initialize(data / 'plugin-data/local-vault')
        changed = install(data) if args.install else False
        print(json.dumps({'status': 'ready', 'extension_activation_changed': changed,
                          'global_binding_changed': False, 'restart_required': args.install}))
        return 0
    except Exception:
        print(json.dumps({'status': 'error', 'error': 'local vault setup refused; inspect private state'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
