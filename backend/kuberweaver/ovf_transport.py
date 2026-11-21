"""Force native OVF Tool into SHA256-pinned trust, without changing host CAs.

The fixed CLI forbids PEM/insecure overrides. Its filesystem namespace hides
ambient CA bundles, while a private profile prevents inherited host approvals.
This is transport isolation, not a sandbox for untrusted executable plugins.
"""
from pathlib import Path
import os
import re
import stat

BWRAP = Path('/usr/bin/bwrap')
CA_DIRS = ('/etc/ssl/certs', '/etc/pki/tls/certs', '/usr/local/share/ca-certificates')
CA_FILES = ('/etc/pki/tls/cert.pem', '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem')


def private_directory(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe OVF transport directory')
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('OVF transport directory must be private')
    return path


def require_transport():
    import subprocess
    if not BWRAP.is_file() or not os.access(BWRAP, os.X_OK):
        raise ValueError('Pin-only OVF transport is unavailable')
    result = subprocess.run([str(BWRAP), '--unshare-user', '--ro-bind', '/', '/', '--', '/usr/bin/true'],
                            capture_output=True, timeout=10, env={'PATH': '/usr/bin:/bin'})
    if result.returncode:
        raise ValueError('Pin-only OVF transport requires working user namespaces')


def pinned_command(ovftool, folder, args):
    if not BWRAP.is_file() or not os.access(BWRAP, os.X_OK):
        raise ValueError('Pin-only OVF transport is unavailable')
    folder = Path(folder).absolute()
    home = private_directory(folder / 'ovf-home')
    temporary = private_directory(folder / 'ovf-tmp')
    ca = private_directory(folder / 'ovf-trust')
    if any(ca.iterdir()):
        raise ValueError('OVF CA directory must remain empty')
    if any(arg.lower().startswith(('--targetpem', '--sourcepem', '--nosslverify', '--config', '--privatekey')) for arg in args):
        raise ValueError('OVF trust override is forbidden')
    pins = [arg.partition('=')[2] for arg in args if arg.startswith('--targetSSLThumbprint=')]
    if len(pins) != 1 or not re.fullmatch(r'(?:[0-9A-F]{2}:){31}[0-9A-F]{2}', pins[0]):
        raise ValueError('One explicit SHA256 target fingerprint required')
    command = [str(BWRAP), '--unshare-user', '--ro-bind', '/', '/',
               '--bind', str(folder), str(folder), '--dev-bind', '/dev', '/dev']
    for path in CA_DIRS:
        if Path(path).is_dir():
            command += ['--ro-bind', str(ca), path]
    for path in CA_FILES:
        if Path(path).exists():
            command += ['--ro-bind', '/dev/null', path]
    command += ['--setenv', 'HOME', str(home), '--setenv', 'TMPDIR', str(temporary),
                '--setenv', 'SSL_CERT_DIR', str(ca), '--setenv', 'SSL_CERT_FILE', '/dev/null',
                '--', str(ovftool), *args]
    return command
