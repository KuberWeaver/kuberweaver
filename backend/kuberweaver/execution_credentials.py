"""Administrator-only SSH credential resolution with operation-scoped key files."""
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import tempfile


class ExecutionCredentialError(RuntimeError):
    """A deliberately sanitized error; upstream messages must not escape."""


def credential_configuration(state_dir, known_targets):
    """Read validated reference-only configuration. Missing alone means legacy SSH."""
    path = Path(state_dir) / 'config' / 'execution-credentials.json'
    try:
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink():
            raise ValueError
        config = json.loads(path.read_text())
        if (not isinstance(config, dict) or set(config) != {'enabled', 'targets'}
                or type(config['enabled']) is not bool or not isinstance(config['targets'], dict)):
            raise ValueError
        for target, selected in config['targets'].items():
            if (target not in known_targets or not isinstance(selected, dict)
                    or set(selected) - {'private_key_ref', 'username'}):
                raise ValueError
            ref = selected.get('private_key_ref')
            if (not isinstance(ref, str) or len(ref) > 128
                    or not re.fullmatch(r'[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)*', ref)):
                raise ValueError
            if 'username' in selected and (not isinstance(selected['username'], str)
                    or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', selected['username'])):
                raise ValueError
        return config
    except Exception:
        raise ExecutionCredentialError('Invalid execution credential configuration') from None


def credential_configuration_fingerprint(state_dir, known_targets, secrets_binding=None):
    """Pin references and selected provider metadata, never resolve or hash values.

    The caller supplies the selected provider name AND source provenance in
    ``secrets_binding`` and compares this digest at approval and each boundary.
    """
    payload = {'configuration': credential_configuration(state_dir, known_targets),
               'secrets_provider': secrets_binding}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@contextmanager
def operation_credentials(state_dir, target, known_targets, resolver, *, result_path=None, ssh_config=None):
    config = credential_configuration(state_dir, known_targets)
    if config is None or not config['enabled']:
        yield {}
        return
    try:
        selected = config['targets'][target]
        if not callable(resolver):
            raise ValueError
        with resolver(selected['private_key_ref']) as secret:
            value = secret.bytes()
            if not isinstance(value, bytes) or not value or len(value) > 1024 * 1024:
                raise ValueError
            with tempfile.TemporaryDirectory(prefix='kw-ssh-') as folder:
                os.chmod(folder, 0o700)
                key = Path(folder) / 'identity'
                fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(value)
                # Compare decoded JSON strings as well as serialized output: JSON
                # escaping must not turn a credential leak into acceptable evidence.
                sensitive = [line.decode('utf-8', errors='replace')
                             for line in value.splitlines() if len(line) >= 16]
                del value
                isolated_config = Path(folder) / 'ansible.cfg'
                config_fd = os.open(isolated_config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(config_fd, 'w') as stream:
                    stream.write('[defaults]\n')
                # IdentityFile is additive in OpenSSH. IdentitiesOnly alone does
                # NOT disable an identity in the existing -F configuration.
                # Preserve routing/hostkey directives but remove all credential
                # directives from this operation-only copy, including Host blocks.
                ssh_lines = []
                for line in Path(ssh_config).read_text().splitlines():
                    option = re.split(r'[\s=]+', line.strip(), maxsplit=1)[0].lower()
                    if option == 'include':
                        raise ValueError('Included SSH configs cannot be isolated safely')
                    if option not in {'identityfile', 'identityagent', 'certificatefile'}:
                        ssh_lines.append(line)
                isolated_ssh = Path(folder) / 'ssh_config'
                ssh_fd = os.open(isolated_ssh, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(ssh_fd, 'w') as stream:
                    stream.write('\n'.join(ssh_lines) + '\n')
                import shlex
                env = {'ANSIBLE_PRIVATE_KEY_FILE': str(key), 'ANSIBLE_CONFIG': str(isolated_config),
                       'ANSIBLE_SSH_COMMON_ARGS': f'-F {shlex.quote(str(isolated_ssh))} -o BatchMode=yes -o ConnectTimeout=15',
                       'ANSIBLE_SSH_ARGS': '-o ControlMaster=no -o ControlPersist=no '
                                           '-o IdentitiesOnly=yes -o IdentityAgent=none -o CertificateFile=none '
                                           '-o PreferredAuthentications=publickey -o StrictHostKeyChecking=yes'}
                if 'username' in selected:
                    env['ANSIBLE_REMOTE_USER'] = selected['username']
                yield env
                if result_path is not None and result_path.exists():
                    normalized = json.dumps(json.loads(result_path.read_text()))
                    if any(json.dumps(fragment)[1:-1] in normalized for fragment in sensitive):
                        raise ValueError
    except Exception:
        if result_path is not None:
            result_path.unlink(missing_ok=True)
        raise ExecutionCredentialError('Execution credential operation unavailable or failed') from None
