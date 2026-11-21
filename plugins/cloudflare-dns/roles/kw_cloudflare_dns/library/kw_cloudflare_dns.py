#!/usr/bin/python
"""Controller-local Ansible module for one create-only Cloudflare DNS record."""
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import ssl
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ansible.module_utils.basic import AnsibleModule


ORIGIN = 'https://api.cloudflare.com/client/v4'
ID = re.compile(r'[0-9a-f]{32}')
TOKEN = re.compile(r'[A-Za-z0-9_-]{1,512}')
LABEL = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
NAME = re.compile(rf'(?:{LABEL}\.)+{LABEL}')
ACTIONS = ('snapshot', 'apply', 'verify', 'rollback', 'remove')


class BrickError(Exception):
    """Contains a curated message, never raw provider evidence."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json_response(stream):
    limit = 256 * 1024
    body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError()
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError()
    return result


class Cloudflare:
    def __init__(self, zone, token):
        self.url = f'{ORIGIN}/zones/{zone}/dns_records'
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect(),
                                   HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method, *, suffix='', payload=None, missing_ok=False):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.url + suffix, data=data, method=method,
                          headers={'Accept': 'application/json', 'Content-Type': 'application/json'})
        request.add_unredirected_header('Authorization', 'Bearer ' + self.token)
        try:
            try:
                with self.opener.open(request, timeout=15) as stream:
                    body = read_json_response(stream)
            except HTTPError as error:
                with error:
                    if missing_ok and error.code == 404:
                        body = read_json_response(error)
                        errors = body.get('errors')
                        if (body.get('success') is False and isinstance(errors, list) and errors
                                and all(isinstance(item, dict) and item.get('code') == 81044
                                        for item in errors)):
                            return None
                    raise
            if body.get('success') is not True or 'result' not in body or body['result'] is None:
                raise ValueError()
            return body['result']
        except (OSError, ValueError, HTTPException):
            raise BrickError('Cloudflare DNS request failed') from None

    def require_absent(self, name):
        query = urlencode({'name.exact': name, 'per_page': 1})
        rows = self.request('GET', suffix='?' + query)
        if not isinstance(rows, list):
            raise BrickError('Cloudflare DNS list response is invalid')
        if rows:
            raise BrickError('DNS name already has a record; adoption is not allowed')


def private_write(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.kw-dns-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def desired_record(config):
    zone, name, kind, address, ttl = (config.get(key) for key in
                                      ('zone_id', 'record_name', 'record_type', 'address', 'ttl'))
    try:
        ip = ipaddress.ip_address(address) if isinstance(address, str) and '%' not in address else None
    except ValueError:
        ip = None
    if (not isinstance(zone, str) or not ID.fullmatch(zone)
            or not isinstance(name, str) or len(name) > 253 or not NAME.fullmatch(name)
            or ip is None or kind != ('A' if ip.version == 4 else 'AAAA')
            or type(ttl) is not int or (ttl != 1 and not 60 <= ttl <= 86400)):
        raise BrickError('Invalid Cloudflare DNS record configuration')
    return {'name': name.lower(), 'type': kind, 'content': str(ip), 'ttl': ttl, 'proxied': False}


def record_fingerprint(row, record_id, desired, marker):
    if (not isinstance(row, dict) or row.get('id') != record_id or row.get('comment') != marker
            or row.get('proxied') is not False or type(row.get('ttl')) is not int
            or any(row.get(key) != desired[key] for key in ('name', 'type', 'ttl'))):
        raise BrickError('Cloudflare record does not match the job receipt')
    content = row.get('content')
    try:
        canonical = str(ipaddress.ip_address(content)) if isinstance(content, str) else None
    except ValueError:
        canonical = None
    if canonical != desired['content']:
        raise BrickError('Cloudflare record does not match the job receipt')
    # Do not persist provider-supplied content, comments, or error bodies.
    return digest({key: row.get(key) for key in ('settings', 'tags', 'modified_on')})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def lifecycle(action, config, desired, receipt_path, api):
    binding = digest(config)
    phase = None
    try:
        raw_receipt = receipt_path.read_text()
    except FileNotFoundError:
        receipt = None
    else:
        receipt = json.loads(raw_receipt)
        if not isinstance(receipt, dict) or receipt.get('binding') != binding:
            raise BrickError('Cloudflare DNS receipt belongs to a different configuration')
        phase = receipt.get('phase')
        if phase in ('creating', 'deleting'):
            raise BrickError('Cloudflare DNS mutation outcome is uncertain; manual review required')
        if phase not in ('absent', 'created', 'removed'):
            raise BrickError('Cloudflare DNS receipt is invalid')

    if action == 'snapshot':
        if receipt is not None and phase != 'absent':
            raise BrickError('Cloudflare DNS job already has a completed mutation receipt')
        api.require_absent(desired['name'])
        private_write(receipt_path, {'binding': binding, 'phase': 'absent'})
        return False, {'absent': True}
    if receipt is None:
        raise BrickError('Cloudflare DNS snapshot is required for this action')

    if action == 'apply' and phase == 'absent':
        api.require_absent(desired['name'])
        marker = 'kuberweaver:' + secrets.token_hex(16)
        private_write(receipt_path, {**receipt, 'phase': 'creating', 'marker': marker})
        row = api.request('POST', payload={**desired, 'comment': marker})
        record_id = row.get('id') if isinstance(row, dict) else None
        if not isinstance(record_id, str) or not ID.fullmatch(record_id):
            raise BrickError('Cloudflare DNS create receipt is invalid; manual review required')
        fingerprint = record_fingerprint(row, record_id, desired, marker)
        private_write(receipt_path, {**receipt, 'phase': 'created', 'record_id': record_id,
                                     'marker': marker, 'fingerprint': fingerprint})
        return True, {}

    cleanup = action in ('rollback', 'remove')
    if cleanup and phase in ('absent', 'removed'):
        return False, {'removed': phase == 'removed'}
    if phase != 'created':
        raise BrickError('Cloudflare DNS record has no confirmed creation receipt')
    record_id, marker = receipt.get('record_id'), receipt.get('marker')
    if (not isinstance(record_id, str) or not ID.fullmatch(record_id)
            or not isinstance(marker, str) or not re.fullmatch(r'kuberweaver:[0-9a-f]{32}', marker)):
        raise BrickError('Cloudflare DNS receipt is invalid')
    suffix = '/' + record_id
    row = api.request('GET', suffix=suffix, missing_ok=cleanup)
    if row is not None:
        if record_fingerprint(row, record_id, desired, marker) != receipt.get('fingerprint'):
            raise BrickError('Cloudflare record does not match the job receipt')
    if not cleanup:
        return False, {'assertions': {'record-matches': True}} if action == 'verify' else {}

    if row is not None:
        private_write(receipt_path, {**receipt, 'phase': 'deleting'})
        deleted = api.request('DELETE', suffix=suffix)
        if not isinstance(deleted, dict) or deleted.get('id') != record_id:
            raise BrickError('Cloudflare DNS deletion receipt is invalid; manual review required')
        if api.request('GET', suffix=suffix, missing_ok=True) is not None:
            raise BrickError('Cloudflare DNS deletion could not be confirmed; manual review required')
    private_write(receipt_path, {**receipt, 'phase': 'removed'})
    return row is not None, {'removed': True}


def run(action, config, result_path, token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise BrickError('A valid vault-resolved Cloudflare API token is required')
    if action not in ACTIONS:
        raise BrickError('Invalid Cloudflare DNS action')
    desired = desired_record(config)
    result_path = Path(result_path)
    if not result_path.is_absolute() or result_path.name != f'{action}.result.json':
        raise BrickError('Invalid workload result path')
    folder = result_path.parent
    lock = os.open(folder / 'cloudflare-dns.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, 'rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        changed, report = lifecycle(action, config, desired, folder / 'cloudflare-dns.receipt.json',
                                    Cloudflare(config['zone_id'], token))
        private_write(result_path, {'brick': 'cloudflare-dns', **report})
    return changed


def main():
    module = AnsibleModule(argument_spec={
        'action': {'type': 'str', 'required': True, 'choices': ACTIONS},
        'config': {'type': 'dict', 'required': True},
        'result_path': {'type': 'path', 'required': True},
        'api_token': {'type': 'str', 'required': True, 'no_log': True},
    })
    try:
        changed = run(module.params['action'], module.params['config'],
                      module.params['result_path'], module.params['api_token'])
    except BrickError as error:
        module.fail_json(msg=str(error))
    except (ValueError, OSError):
        module.fail_json(msg='Cloudflare DNS local state is unavailable or invalid')
    else:
        module.exit_json(changed=changed)


if __name__ == '__main__':
    main()
