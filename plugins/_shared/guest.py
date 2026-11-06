#!/usr/bin/env python3
"""Reviewed remote provider helper. Only /opt/kuberweaver and owned containers.

Operation dispatch is generic lifecycle dispatch, never per-plugin branching.
"""
import base64
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path('/opt/kuberweaver/services')


def docker(*args, check=True):
    result = subprocess.run(['docker', *args], text=True, capture_output=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError('Docker operation failed: ' + result.stderr[:250])
    return result


def inspect(name):
    result = docker('container', 'inspect', name, check=False)
    if result.returncode:
        if 'No such' in result.stderr:
            return None
        raise RuntimeError('Cannot inspect Docker container')
    return json.loads(result.stdout)[0]


def owned(state, request):
    if state:
        labels = state['Config'].get('Labels') or {}
        if labels.get('kw.managed') != 'true' or labels.get('kw.owner') != request['owner']:
            raise RuntimeError('Container is not owned by this graph/node')
        for mount in state.get('Mounts', []):
            if mount.get('Type') != 'bind' or mount.get('Source') != str(ROOT / request['node_id'] / 'www'):
                raise RuntimeError('Container has unsupported foreign mount; refusing mutation')


def tree(path):
    if not path.exists():
        return None
    result = {}
    for child in sorted(path.rglob('*')):
        if child.is_symlink() or (child.is_file() and child.stat().st_nlink > 1):
            raise RuntimeError('Symlinks/hardlinks are not permitted in prototype configuration')
        if child.is_file():
            result[str(child.relative_to(path))] = {'sha256': hashlib.sha256(child.read_bytes()).hexdigest(), 'mode': child.stat().st_mode & 0o777}
        elif child.is_dir():
            result[str(child.relative_to(path)) + '/'] = {'mode': child.stat().st_mode & 0o777}
        else:
            raise RuntimeError('Unsupported file type in prototype configuration')
    return result


def location(request):
    node = request['node_id']
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', node):
        raise RuntimeError('Invalid node identity')
    path = ROOT / node
    for parent in (Path('/opt/kuberweaver'), ROOT, path):
        if parent.is_symlink():
            raise RuntimeError('Unsafe service path')
    return path, 'kw-' + node


def fetch(request, path=None, port=None):
    url = 'http://%s:%s%s' % (request['management_ip'], port or request['config']['port'], path or request['service']['health_path'])
    # Never use a controller/guest proxy for private management healthchecks.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=3) as response:
        return response.read(65536).decode()


def wait_payload(request, expected=None, path=None, port=None):
    last = None
    for _ in range(25):
        try:
            payload = fetch(request, path, port)
            if expected is None or payload == expected:
                return payload
            last = 'Payload differs from snapshot/desired version'
        except Exception as exc:
            last = str(exc)
        time.sleep(0.4)
    raise RuntimeError('Healthcheck failed: ' + str(last))


def snapshot(request):
    path, name = location(request)
    state = inspect(name)
    owned(state, request)
    files = tree(path)
    if files is not None:
        marker = path / 'owner.json'
        if not marker.is_file() or json.loads(marker.read_text()).get('owner') != request['owner']:
            raise RuntimeError('Configuration path is not owned by this graph/node')
    # Refuse a foreign listener before removing/creating any workload.
    port = request['config']['port']
    ports = [int(binding['HostPort']) for bindings in (state or {}).get('HostConfig', {}).get('PortBindings', {}).values() for binding in (bindings or [])]
    if port not in ports:
        with socket.socket() as sock:
            try:
                sock.bind((request['management_ip'], port))
            except OSError as exc:
                raise RuntimeError('Management port is already in use') from exc
    archive = None
    if files is not None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as stream:
            stream.add(path, arcname='service', recursive=True)
        if len(buffer.getvalue()) > 2 * 1024 * 1024:
            raise RuntimeError('Prototype snapshot exceeds 2 MiB limit')
        archive = base64.b64encode(buffer.getvalue()).decode()
    previous_payload = None
    old_port = None
    if state and state['State']['Running']:
        # Read back the actual running old payload on its actual old binding.
        bindings = [binding for values in state['HostConfig']['PortBindings'].values() for binding in (values or [])]
        if len(bindings) != 1 or bindings[0]['HostIp'] != request['management_ip']:
            raise RuntimeError('Unsupported previous container port bindings')
        old_port = int(bindings[0]['HostPort'])
        previous_payload = wait_payload(request, path='/version.json', port=old_port)
    return {'exists': state is not None, 'config_exists': files is not None, 'inspect': state,
            'archive': archive, 'files': files, 'payload': previous_payload, 'port': old_port,
            'running': bool(state and state['State']['Running'])}


def apply(request):
    path, name = location(request)
    state = inspect(name)
    owned(state, request)
    if state:
        docker('rm', '-f', name)
    path.mkdir(parents=True, exist_ok=True, mode=0o755)
    www = path / 'www'
    www.mkdir(exist_ok=True, mode=0o755)
    os.chmod(path, 0o755)
    os.chmod(www, 0o755)
    payload = json.dumps({'service': request['plugin_id'], 'node': request['node_id'], 'version': request['config']['version']}, sort_keys=True) + '\n'
    (www / 'version.json').write_text(payload)
    (www / 'index.html').write_text(payload)
    os.chmod(www / 'version.json', 0o644)
    os.chmod(www / 'index.html', 0o644)
    (path / 'owner.json').write_text(json.dumps({'owner': request['owner'], 'job_id': request['job_id']}))
    os.chmod(path / 'owner.json', 0o600)
    service = request['service']
    config = request['config']
    docker('run', '-d', '--name', name, '--label', 'kw.managed=true', '--label', 'kw.owner=' + request['owner'],
           '--label', 'kw.plugin=' + request['plugin_id'], '--restart', 'unless-stopped',
           '--cpus', str(config['cpu']), '--memory', str(config['memory_mb']) + 'm',
           '--pids-limit', '128', '--security-opt', 'no-new-privileges:true',
           '-p', '%s:%s:%s' % (request['management_ip'], config['port'], service['container_port']),
           '--mount', 'type=bind,src=%s,dst=%s,readonly' % (www, service['document_root']), service['image'])
    return {'applied': True, 'container': name}


def verify(request):
    _, name = location(request)
    state = inspect(name)
    owned(state, request)
    if not state or not state['State']['Running']:
        raise RuntimeError('Prototype container is not running')
    expected = json.dumps({'service': request['plugin_id'], 'node': request['node_id'], 'version': request['config']['version']}, sort_keys=True) + '\n'
    payload = wait_payload(request, expected=expected)
    if request.get('inject_failure'):
        # The request deliberately checks the wrong payload AFTER real HTTP succeeds.
        raise RuntimeError('Intentional prototype healthcheck failure after real HTTP readback')
    return {'healthy': True, 'payload': json.loads(payload), 'body': payload, 'image_id': state['Image'],
            'bindings': state['HostConfig']['PortBindings']}


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(30)
        self.sock.connect('/var/run/docker.sock')


def recreate(name, state):
    connection = DockerConnection('localhost')
    config = dict(state['Config'])
    config['Image'] = state['Image']  # immutable actual old image, not a mutable tag
    config['HostConfig'] = state['HostConfig']
    connection.request('POST', '/v1.45/containers/create?name=' + name, json.dumps(config), {'Content-Type': 'application/json'})
    response = connection.getresponse()
    body = response.read().decode()
    connection.close()
    if response.status != 201:
        raise RuntimeError('Docker restore create failed: ' + body[:250])


def restore(request):
    path, name = location(request)
    old = request['snapshot']
    state = inspect(name)
    owned(state, request)
    if state:
        docker('rm', '-f', name)
    if path.exists():
        tree(path)  # reject symlinks before recursive removal
        shutil.rmtree(path)
    if old['config_exists']:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(old['archive'])), mode='r:gz') as stream:
            for member in stream.getmembers():
                parts = Path(member.name).parts
                if not parts or parts[0] != 'service' or '..' in parts or member.issym() or member.islnk():
                    raise RuntimeError('Unsafe snapshot archive member')
            # Extract to a private directory, then rename fixed archive root.
            staging = path.parent / ('.restore-' + request['node_id'])
            if staging.exists():
                raise RuntimeError('Existing restore staging path requires operator inspection')
            staging.mkdir(mode=0o700)
            try:
                stream.extractall(staging, filter='data')
                (staging / 'service').rename(path)
            finally:
                staging.rmdir()
    if tree(path) != old['files']:
        raise RuntimeError('Restored configuration bytes/modes do not match snapshot')
    if old['exists']:
        recreate(name, old['inspect'])
        if old['running']:
            docker('start', name)
        actual = inspect(name)
        if not actual or actual['Image'] != old['inspect']['Image'] or actual['State']['Running'] != old['running']:
            raise RuntimeError('Restored image/running state differs from snapshot')
        # Docker normalizes some fields at create time, so compare safety-critical actual state.
        for field in ('PortBindings', 'Binds', 'Mounts', 'NanoCpus', 'Memory', 'RestartPolicy', 'SecurityOpt', 'PidsLimit', 'NetworkMode'):
            if actual['HostConfig'].get(field) != old['inspect']['HostConfig'].get(field):
                raise RuntimeError('Restored container configuration differs: ' + field)
        for field in ('Labels', 'Env', 'Cmd', 'Entrypoint', 'User', 'WorkingDir', 'ExposedPorts', 'Volumes', 'Healthcheck', 'StopSignal', 'Tty', 'OpenStdin'):
            if actual['Config'].get(field) != old['inspect']['Config'].get(field):
                raise RuntimeError('Restored container configuration differs: ' + field)
        payload = None
        if old['running']:
            payload = wait_payload(request, expected=old['payload'], path='/version.json', port=old['port'])
        return {'restored': True, 'absent': False, 'payload': json.loads(payload) if payload else None,
                'body': payload, 'image_id': actual['Image'], 'config_verified': True}
    if inspect(name) is not None or path.exists() != old['config_exists']:
        raise RuntimeError('Initial absence was not restored')
    return {'restored': True, 'absent': True, 'config_verified': True}


OPERATIONS = {'snapshot': snapshot, 'apply': apply, 'verify': verify, 'restore': restore}


def main():
    try:
        request = json.loads(Path(sys.argv[1]).read_text())
        result = OPERATIONS[request['action']](request)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
