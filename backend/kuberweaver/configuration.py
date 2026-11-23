"""Process configuration read from the environment.

Every setting has a documented default so a checkout runs without exports.
Explicit path values must be absolute: a relative path would resolve against
whatever working directory the supervisor happens to use.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(ValueError):
    """A setting is missing or unusable; the message always names the variable."""


def _raw(name: str) -> str:
    return (os.environ.get(name) or '').strip()


def _path(name: str, default: Path) -> Path:
    value = _raw(name)
    if not value:
        return Path(default).expanduser()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ConfigurationError(f'{name} must be an absolute path, got {value!r}')
    return candidate


def state_dir() -> Path:
    """Durable state and the database selection: ``KUBERWEAVER_STATE_DIR``."""
    return _path('KUBERWEAVER_STATE_DIR', ROOT / 'data')


def plugins_dir() -> Path:
    """Reviewed module manifests: ``KUBERWEAVER_PLUGINS_DIR``."""
    return _path('KUBERWEAVER_PLUGINS_DIR', ROOT / 'modules')


def frontend_dir() -> Path:
    """Built portal assets: ``KUBERWEAVER_FRONTEND_DIR``."""
    return _path('KUBERWEAVER_FRONTEND_DIR', ROOT / 'frontend' / 'dist')


def ansible_playbook() -> Path:
    """Ansible executable for workload bricks: ``KUBERWEAVER_ANSIBLE_PLAYBOOK``."""
    return _path('KUBERWEAVER_ANSIBLE_PLAYBOOK', Path(sys.executable).parent / 'ansible-playbook')


def ssh_config() -> Path:
    """OpenSSH client configuration for lab targets: ``KUBERWEAVER_SSH_CONFIG``."""
    return _path('KUBERWEAVER_SSH_CONFIG', Path.home() / '.ssh' / 'config')


def require_ssh_config(path: Path | str | None = None) -> Path:
    """Return the selected OpenSSH file, or refuse before any remote command."""
    selected = Path(path).expanduser() if path is not None else ssh_config()
    if not selected.is_file():
        raise ConfigurationError(
            f'OpenSSH client configuration not found at {selected}; '
            f'set KUBERWEAVER_SSH_CONFIG to the absolute path of the SSH client config used for lab targets')
    return selected
