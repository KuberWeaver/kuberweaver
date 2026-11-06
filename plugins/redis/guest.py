#!/usr/bin/env python3
"""Fixed Redis guest protocol. No graph-supplied executables, images or paths."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time

ROOT = Path('/opt/kuberweaver/redis')
IMAGE = 'redis:7.4.2-alpine'
LIMIT = 16777216
COMMAND = ['redis-server', '--bind', '0.0.0.0', '--port', '6379', '--protected-mode', 'no',
           '--appendonly', 'yes', '--appendfsync', 'always', '--save', '', '--dir', '/data']
TARGETS = {'app': '192.168.122.40', 'branch-client': '192.168.122.60'}


def validate_config(config):
    if set(config) != {'targets', 'port', 'version', 'cpu', 'memory_mb', 'disk_gb', 'public_cidr', 'isolated_lab'}:
        raise RuntimeError('Unsupported Redis config fields')
    targets = config['targets']
    if not isinstance(targets, list) or not 1 <= len(targets) <= 2 or any(t not in TARGETS for t in targets) or len(set(targets)) != len(targets):
        raise RuntimeError('Invalid Redis targets')
    for key, lo, hi in [('port', 18090, 18109), ('memory_mb', 64, 1024), ('disk_gb', 1, 10)]:
        if type(config[key]) is not int or not lo <= config[key] <= hi:
            raise RuntimeError('Invalid Redis numeric config')
    if type(config['cpu']) not in (float, int) or not math.isfinite(config['cpu']) or not .1 <= config['cpu'] <= 2:
        raise RuntimeError('Invalid Redis CPU config')
    if not isinstance(config['version'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,31}', config['version']):
        raise RuntimeError('Invalid Redis version')
    if config['isolated_lab'] is not True or config['public_cidr'] != '127.0.0.1/32':
        raise RuntimeError('Redis is restricted to the isolated lab')


def tree(path):
    if not path.exists():
        return None
    result, size = {}, 0
    for child in [path, *sorted(path.rglob('*'))]:
        info = child.lstat()
        if child.is_symlink() or (child.is_file() and info.st_nlink > 1) or not (child.is_dir() or child.is_file()):
            raise RuntimeError('Unsafe Redis data file type or link')
        item = {'mode': info.st_mode & 0o777, 'uid': info.st_uid, 'gid': info.st_gid, 'directory': child.is_dir()}
        if child.is_file():
            size += info.st_size
            if size > LIMIT:
                raise RuntimeError('Redis persistent snapshot exceeds 16 MiB limit')
            item['sha256'] = hashlib.sha256(child.read_bytes()).hexdigest()
        result[str(child.relative_to(path))] = item
    if len(result) > 4096:
        raise RuntimeError('Redis persistent snapshot exceeds file-count limit')
    return result


class Service:
    def __init__(self, request, runner=subprocess.run):
        self.request, self.runner = request, runner
        for key in ('node_id', 'job_id'):
            if not isinstance(request.get(key), str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', request[key]):
                raise RuntimeError('Invalid Redis identity')
        if not isinstance(request.get('owner'), str) or not re.fullmatch(r'[a-f0-9]{24}', request['owner']):
            raise RuntimeError('Invalid Redis owner')
        if request.get('plugin_id') != 'redis' or request.get('management_ip') != TARGETS.get(request.get('target')):
            raise RuntimeError('Invalid Redis target or plugin')
        validate_config(request['config'])
        if request['target'] not in request['config']['targets']:
            raise RuntimeError('Redis target is not configured')
        if request.get('service') != {'image': IMAGE, 'container_port': 6379, 'protocol': 'resp2',
                'persistence': 'appendonly-always', 'snapshot_limit_bytes': LIMIT}:
            raise RuntimeError('Unsupported Redis service protocol')
        self.path = ROOT / request['node_id']
        self.name = 'kw-redis-' + request['node_id']
        for parent in [*ROOT.parents, ROOT, self.path]:
            if parent.is_symlink():
                raise RuntimeError('Unsafe Redis service path')

    def run(self, argv, check=True):
        result = self.runner(argv, capture_output=True, text=True, timeout=120)
        if check and result.returncode:
            raise RuntimeError('Redis runtime prerequisite or Docker operation failed')
        return result

    def docker(self, *args, check=True):
        return self.run(['docker', *args], check=check)

    def inspect(self):
        result = self.docker('container', 'inspect', self.name, check=False)
        if result.returncode:
            if 'No such container' in result.stderr or 'No such object' in result.stderr:
                return None
            raise RuntimeError('Cannot inspect Redis container')
        return json.loads(result.stdout)[0]

    def gates(self):
        # Read-only, explicit operator prerequisite. Docker's DNAT bypasses UFW;
        # require the FIRST DOCKER-USER rule to drop every non-controller source.
        rules = self.run(['iptables', '-S', 'DOCKER-USER']).stdout.splitlines()
        rules = [line for line in rules if line.startswith('-A ')]
        if not rules or rules[0] != '-A DOCKER-USER ! -s 192.168.122.1/32 -j DROP':
            raise RuntimeError('Missing first-rule controller-only firewall; Redis would expose unauthenticated LAN access')

    def marker(self):
        files = tree(self.path)
        if files is None:
            return None
        marker = self.path / 'owner.json'
        if not marker.is_file():
            raise RuntimeError('Redis data path is not owned')
        value = json.loads(marker.read_text())
        if value.get('owner') != self.request['owner']:
            raise RuntimeError('Redis data path is not owned by this graph/node')
        validate_config(value['config'])
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', value.get('image_id', '')):
            raise RuntimeError('Unsafe Redis recorded image')
        return value

    def owned(self, state, marker):
        if not state:
            return
        if not marker:
            raise RuntimeError('Redis container has no owned persistent data')
        labels = state['Config'].get('Labels') or {}
        if labels.get('kw.managed') != 'true' or labels.get('kw.owner') != self.request['owner'] or labels.get('kw.plugin') != 'redis':
            raise RuntimeError('Redis container is not owned by this graph/node')
        if state['Image'] != marker['image_id'] or state['Config'].get('Cmd') != COMMAND or state['Config'].get('User') != '999:999' or state['Config'].get('Entrypoint') != ['docker-entrypoint.sh']:
            raise RuntimeError('Unsupported owned Redis container configuration')
        mounts = state.get('Mounts', [])
        if len(mounts) != 1 or any(m.get('Type') != 'bind' or m.get('Source') != str(self.path / 'data') or m.get('Destination') != '/data' or m.get('RW') is not True for m in mounts):
            raise RuntimeError('Unsupported foreign Redis mount')
        hc = state['HostConfig']
        config = marker['config']
        expected = {'PortBindings': {'6379/tcp': [{'HostIp': self.request['management_ip'], 'HostPort': str(config['port'])}]},
                    'NetworkMode': 'bridge', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                    'SecurityOpt': ['no-new-privileges:true'], 'NanoCpus': int(config['cpu'] * 1e9),
                    'Memory': config['memory_mb'] * 1024 * 1024, 'PidsLimit': 128,
                    'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}}
        if any(hc.get(key) != value for key, value in expected.items()) or any(hc.get(k) for k in ('Privileged', 'CapAdd', 'Binds', 'Devices', 'VolumesFrom', 'Links', 'DeviceRequests')):
            raise RuntimeError('Unsupported owned Redis runtime configuration')

    def available(self, state, port):
        if state:
            bindings = state['HostConfig']['PortBindings']['6379/tcp']
            if any(int(b['HostPort']) == port for b in bindings):
                return
        with socket.socket() as sock:
            try:
                sock.bind((self.request['management_ip'], port))
            except OSError as exc:
                raise RuntimeError('Redis management port unavailable') from exc

    def wait(self, port, expected=None):
        for _ in range(25):
            try:
                if resp(self.request['management_ip'], port, 'PING') != 'PONG':
                    raise RuntimeError('Redis PING readback failed')
                if expected is not None and resp(self.request['management_ip'], port, 'GET', '__kuberweaver_probe:' + self.request['owner']) != expected:
                    raise RuntimeError('Restored Redis probe value differs')
                return
            except (OSError, RuntimeError):
                time.sleep(.2)
        raise RuntimeError('Redis protocol health/readback failed')

    def snapshot(self):
        self.gates()
        marker, state = self.marker(), self.inspect()
        self.owned(state, marker)
        self.available(state, self.request['config']['port'])
        running = bool(state and state['State']['Running'])
        old_probe = None
        if running:
            self.wait(marker['config']['port'])
            old_probe = resp(self.request['management_ip'], marker['config']['port'], 'GET', '__kuberweaver_probe:' + self.request['owner'])
            self.docker('stop', '--time', '30', self.name)
            if self.inspect()['State']['Running']:
                raise RuntimeError('Redis must be stopped before persistent backup')
        try:
            files = tree(self.path)
            archive = None
            if files is not None:
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode='w:gz') as stream:
                    stream.add(self.path, arcname='service', recursive=True)
                if len(buffer.getvalue()) > LIMIT:
                    raise RuntimeError('Redis persistent archive exceeds 16 MiB limit')
                archive = base64.b64encode(buffer.getvalue()).decode('ascii')
            return {'protocol': 'redis-snapshot-v1', 'owner': self.request['owner'],
                    'node_id': self.request['node_id'], 'exists': state is not None,
                    'running': running, 'inspect': state, 'marker': marker,
                    'files': files, 'archive': archive, 'probe_value': old_probe}
        finally:
            if running:
                self.docker('start', self.name)
                self.wait(marker['config']['port'])

    def create(self, config, image, start=True):
        argv = ['run' if start else 'create', '--pull', 'never', '--name', self.name,
                '--label', 'kw.managed=true', '--label', 'kw.owner=' + self.request['owner'],
                '--label', 'kw.plugin=redis', '--restart', 'unless-stopped', '--network', 'bridge',
                '--user', '999:999', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges:true', '--pids-limit', '128',
                '--cpus', str(config['cpu']), '--memory', str(config['memory_mb']) + 'm',
                '-p', '%s:%s:6379' % (self.request['management_ip'], config['port']),
                '--mount', 'type=bind,src=%s,dst=/data' % (self.path / 'data')]
        if start:
            argv.insert(1, '-d')
        self.docker(*argv, image, *COMMAND)

    def apply(self):
        self.gates()
        state, marker = self.inspect(), self.marker()
        self.owned(state, marker)
        self.available(state, self.request['config']['port'])
        image = json.loads(self.docker('image', 'inspect', IMAGE).stdout)[0]['Id']
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
            raise RuntimeError('Unsafe Redis image identity')
        if state:
            self.docker('stop', '--time', '30', self.name)
            self.docker('rm', self.name)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = self.path / 'data'
        data.mkdir(exist_ok=True, mode=0o700)
        os.chown(data, 999, 999)
        os.chmod(data, 0o700)
        marker = {'owner': self.request['owner'], 'image_id': image, 'config': self.request['config']}
        (self.path / 'owner.json').write_text(json.dumps(marker))
        os.chmod(self.path / 'owner.json', 0o600)
        self.create(self.request['config'], image)
        self.owned(self.inspect(), marker)
        return {'applied': True, 'image_id': image}

    def verify(self):
        self.gates()
        state, marker = self.inspect(), self.marker()
        self.owned(state, marker)
        if not state or not state['State']['Running']:
            raise RuntimeError('Redis container is not running')
        self.wait(self.request['config']['port'])
        proof = probe(self.request['management_ip'], self.request['config']['port'],
                      '__kuberweaver_probe:' + self.request['owner'], self.request['config']['version'])
        if self.request.get('inject_failure'):
            raise RuntimeError('Intentional Redis failure after real RESP readback')
        return {'healthy': True, 'protocol': 'resp2', 'image_id': state['Image'], **proof}

    def restore(self):
        self.gates()
        old = self.request['snapshot']
        if old.get('protocol') != 'redis-snapshot-v1' or old.get('owner') != self.request['owner'] or old.get('node_id') != self.request['node_id']:
            raise RuntimeError('Invalid Redis snapshot identity/protocol')
        state, marker = self.inspect(), self.marker()
        self.owned(state, marker)
        # Validate and stage every byte BEFORE stopping/removing current resources.
        staging = ROOT / ('.restore-' + self.request['node_id'] + '-' + self.request['job_id'])
        if staging.exists() or staging.is_symlink():
            raise RuntimeError('Existing Redis restore staging requires inspection')
        staging.mkdir(parents=True, mode=0o700)
        try:
            if old['files'] is not None:
                raw = base64.b64decode(old['archive'], validate=True)
                if len(raw) > LIMIT:
                    raise RuntimeError('Redis snapshot archive exceeds limit')
                with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as stream:
                    members = stream.getmembers()
                    if len(members) > 4096 or sum(m.size for m in members) > LIMIT:
                        raise RuntimeError('Redis snapshot expansion exceeds limit')
                    names = set()
                    for m in members:
                        parts = Path(m.name).parts
                        if not parts or parts[0] != 'service' or '..' in parts or not (m.isfile() or m.isdir()) or m.mode & ~0o777 or m.name in names:
                            raise RuntimeError('Unsafe Redis snapshot member')
                        names.add(m.name)
                    stream.extractall(staging, filter='data')
                    for m in members:
                        child = staging / m.name
                        os.chmod(child, m.mode)
                        os.chown(child, m.uid, m.gid)
                if tree(staging / 'service') != old['files']:
                    raise RuntimeError('Redis staged data differs from snapshot')
                saved = json.loads((staging / 'service' / 'owner.json').read_text())
                if saved != old['marker'] or saved['owner'] != self.request['owner']:
                    raise RuntimeError('Redis snapshot marker mismatch')
            if old['exists']:
                self.owned(old['inspect'], old['marker'])
                self.docker('image', 'inspect', old['marker']['image_id'])
            if state:
                self.docker('stop', '--time', '30', self.name)
                self.docker('rm', self.name)
            if self.path.exists():
                tree(self.path)
                shutil.rmtree(self.path)
            if old['files'] is not None:
                (staging / 'service').rename(self.path)
            if tree(self.path) != old['files']:
                raise RuntimeError('Restored Redis persistent data differs from snapshot')
            if old['exists']:
                config = old['marker']['config']
                self.create(config, old['marker']['image_id'], start=old['running'])
                actual = self.inspect()
                self.owned(actual, old['marker'])
                if not actual or actual['State']['Running'] != old['running']:
                    raise RuntimeError('Restored Redis running state differs')
                if old['running']:
                    self.wait(config['port'], expected=old['probe_value'])
                return {'restored': True, 'absent': False, 'data_verified': True, 'image_id': actual['Image']}
            if self.inspect() is not None or self.path.exists() != (old['files'] is not None):
                raise RuntimeError('Redis initial absence was not restored')
            return {'restored': True, 'absent': True, 'data_verified': True}
        finally:
            shutil.rmtree(staging)


def main():
    try:
        request = json.loads(Path(sys.argv[1]).read_text())
        service = Service(request)
        operations = {'snapshot': service.snapshot, 'apply': service.apply,
                      'verify': service.verify, 'restore': service.restore}
        result = operations[request['action']]()
        print(json.dumps(result))
        return 0
    except Exception as exc:
        # Fixed helper error messages never echo raw subprocess output or data.
        print(json.dumps({'error': str(exc) if isinstance(exc, RuntimeError) else 'Invalid Redis request or runtime failure'}))
        return 1


def resp(ip, port, *args):
    encoded = [str(arg).encode('utf-8') for arg in args]
    wire = b'*%d\r\n' % len(encoded) + b''.join(b'$%d\r\n' % len(arg) + arg + b'\r\n' for arg in encoded)
    with socket.create_connection((ip, port), timeout=3) as sock:
        sock.sendall(wire)
        with sock.makefile('rb') as stream:
            line = stream.readline(65537)
            if not line.endswith(b'\r\n') or len(line) > 65536:
                raise RuntimeError('Invalid Redis response')
            if line[:1] == b'+':
                return line[1:-2].decode('utf-8')
            if line[:1] == b'$':
                size = int(line[1:-2])
                if size == -1:
                    return None
                if not 0 <= size <= 65536:
                    raise RuntimeError('Redis response exceeds probe limit')
                payload = stream.read(size + 2)
                if len(payload) != size + 2 or not payload.endswith(b'\r\n'):
                    raise RuntimeError('Truncated Redis response')
                return payload[:-2].decode('utf-8')
            raise RuntimeError('Redis command rejected or unexpected response')


def probe(ip, port, key, value):
    if resp(ip, port, 'PING') != 'PONG':
        raise RuntimeError('Redis PING readback failed')
    if resp(ip, port, 'SET', key, value) != 'OK' or resp(ip, port, 'GET', key) != value:
        raise RuntimeError('Redis SET/GET readback failed')
    return {'ping': 'PONG', 'set_get': True}


if __name__ == '__main__':
    raise SystemExit(main())
