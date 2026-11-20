#!/usr/bin/env python3
"""Reverse SSH tunnel: supervision-VM Grafana -> host-loopback PostgreSQL.

The platform database publishes 127.0.0.1:15433 only (docs/POSTGRESQL.md) and
the host runs no sshd, so a guest->host forward is impossible without new host
packages. Instead this supervisor opens host->guest SSH and reverse-forwards
the guest's loopback 15433 to the host's loopback 15433: Grafana's datasource
points at 127.0.0.1:15433 ON THE GUEST, the database stays loopback-only on
both ends, and pg_hba keeps seeing host-loopback connections.

Foreground script for the systemd user unit kw-reporting-tunnel.service
(ops/kuberweaver-bao-tunnel.service pattern). Each start: read the admin
password from data/bootstrap-credentials.txt (never argv, never printed),
log into kw, export the managed key into a private tmpfs directory, run
ssh -N -R, verify the guest-side listener, and shred the key on every exit.
No guest authorized_keys change, no new persistent secret.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API = 'http://127.0.0.1:8088'
KEY_ID = 'fd4c8e2e58442577948f3545f90856bf'   # managed key for the supervision VM
GUEST = 'ubuntu@192.168.10.63'                # supervision VM (LAN_pfSense)
PORT = 15433                                  # host-loopback PostgreSQL publish

GUEST_PROBE = (
    'import socket\n'
    f's = socket.create_connection(("127.0.0.1", {PORT}), 3)\n'
    's.close()\n'
    'print("guest-loopback-forward:ok")\n'
)


class TunnelError(RuntimeError):
    """Secret-free tunnel failure."""


def admin_password() -> str:
    path = ROOT / 'data' / 'bootstrap-credentials.txt'
    if path.is_symlink() or (path.stat().st_mode & 0o777) != 0o600:
        raise TunnelError('bootstrap credentials must be a private 0600 file')
    for line in path.read_text().splitlines():
        if line.startswith('admin:'):
            value = line.split(':', 1)[1]
            if value:
                return value
    raise TunnelError('admin credential line missing')


def stage_key(password: str) -> tuple[Path, Path]:
    """Export the managed key through kw into a private tmpfs directory."""
    from kw.client import Client
    client = Client(API, use_session=False)
    try:
        client.login('admin', password)
        response = client.request('POST', f'/api/ssh-keys/{KEY_ID}/export',
                                  json={'password': password})
        material = response.content
    finally:
        client.close()
    if not material.startswith(b'-----BEGIN'):
        raise TunnelError('unexpected exported key format')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
    directory = Path(tempfile.mkdtemp(prefix='kw-reporting-tunnel-', dir=runtime))
    os.chmod(directory, 0o700)
    key_path = directory / 'managed-key'
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(material)
        stream.flush()
        os.fsync(stream.fileno())
    return directory, key_path


def shred(directory: Path) -> None:
    try:
        for path in directory.iterdir():
            if path.is_file():
                size = path.stat().st_size
                with open(path, 'r+b') as stream:
                    stream.write(b'\0' * size)
                    stream.flush()
                    os.fsync(stream.fileno())
                path.unlink()
        directory.rmdir()
    except OSError:
        pass  # tmpfs disappears at logout/reboot regardless


def ssh_base(key_path: Path) -> list[str]:
    return ['ssh', '-i', str(key_path),
            '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=3']


def guest_probe(key_path: Path) -> bool:
    result = subprocess.run(ssh_base(key_path) + [GUEST, 'python3 -'],
                            input=GUEST_PROBE, text=True, capture_output=True, timeout=30)
    return result.returncode == 0 and 'guest-loopback-forward:ok' in result.stdout


def main() -> None:
    with socket.socket() as probe:
        probe.settimeout(3)
        if probe.connect_ex(('127.0.0.1', PORT)) != 0:
            raise TunnelError('host-loopback PostgreSQL not listening; start the platform first')
    password = admin_password()
    directory, key_path = stage_key(password)
    del password
    process = None

    def stop(signum, frame):
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        shred(directory)
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if guest_probe(key_path):
            raise TunnelError('guest 15433 already forwarded; refusing a duplicate tunnel')
        command = ssh_base(key_path) + ['-o', 'ExitOnForwardFailure=yes', '-N',
                                        '-R', f'127.0.0.1:{PORT}:127.0.0.1:{PORT}', GUEST]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL)
        for _ in range(60):
            if process.poll() is not None:
                raise TunnelError('ssh exited before the reverse forward came up')
            if guest_probe(key_path):
                break
            time.sleep(0.5)
        else:
            raise TunnelError('guest never reached the forwarded port')
        print(f'reporting tunnel up: guest 127.0.0.1:{PORT} -> host 127.0.0.1:{PORT}', flush=True)
        returncode = process.wait()
        raise TunnelError(f'ssh exited with status {returncode}')
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        shred(directory)


if __name__ == '__main__':
    try:
        main()
    except TunnelError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Transport/driver errors can carry URLs or values; never stringify.
        print('reporting tunnel failed; no credentials printed.', file=sys.stderr)
        sys.exit(1)
