#!/usr/bin/env python3
"""Validate a preset and derive its one-brick jobs in dependency order.

A preset is data: a stack graph of workload bricks plus the configuration of
each brick. The workload execution contract is one brick per job, so a preset is
submitted as one job per step, in the order this script prints. Every step is
checked against the same catalogue and admission policy the portal uses; the
script never touches a guest, never writes state and never resolves a secret.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from kuberweaver.catalog import Catalog  # noqa: E402
from kuberweaver.models import Graph  # noqa: E402
from kuberweaver.module_paths import module_directory  # noqa: E402
from kuberweaver.policy import Admission  # noqa: E402
from kuberweaver.workload_execution import WorkloadError, WorkloadExecutor  # noqa: E402

IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
KEY = re.compile(r'[0-9a-f]{32}')
ENVIRONMENTS = ('dev', 'staging', 'production')
PRESET_KEYS = {'id', 'name', 'version', 'environment', 'description', 'target', 'steps'}
STEP_KEYS = {'id', 'brick', 'requires', 'config'}
TARGET_KEYS = {'count', 'note'}
PORTAL_INPUTS = {'target_uuid', 'ssh_key_id'}


class PresetError(RuntimeError):
    """A preset that cannot become a plan. Raised before anything is written."""


class _DefaultSettings:
    """The unset settings the portal starts with, without opening a store."""

    def get(self, collection, key):
        return None


def _require(condition, message):
    if not condition:
        raise PresetError(message)


def load_preset(path):
    path = Path(path)
    try:
        body = yaml.safe_load(path.read_text())
    except OSError as error:
        raise PresetError(f'preset cannot be read: {error}') from None
    except yaml.YAMLError as error:
        raise PresetError(f'preset is not valid YAML: {str(error)[:200]}') from None
    _require(isinstance(body, dict), 'preset must be a mapping')
    extra = set(body) - PRESET_KEYS
    _require(not extra, 'preset carries undeclared fields: ' + ', '.join(sorted(extra)))
    for field in ('id', 'name', 'description', 'target', 'steps'):
        _require(field in body, f'preset is missing {field}')
    _require(isinstance(body['id'], str) and IDENTIFIER.fullmatch(body['id']),
             'preset id must be 1..64 lowercase alphanumeric/hyphen characters')
    _require(isinstance(body['name'], str) and 1 <= len(body['name']) <= 160,
             'preset name must be 1..160 characters')
    _require(isinstance(body['description'], str) and body['description'].strip(),
             'preset description must not be empty')
    _require(body.get('version', 1) == 1, 'only preset version 1 is supported')
    _require(body.get('environment') in ENVIRONMENTS,
             'preset environment must be dev, staging or production')
    target = body['target']
    _require(isinstance(target, dict) and not (set(target) - TARGET_KEYS),
             'preset target carries undeclared fields')
    _require(target.get('count') == 1, 'a preset must target exactly one guest')
    _require(isinstance(target.get('note'), str) and target['note'].strip(),
             'preset target must explain how the guest is supplied')
    steps = body['steps']
    _require(isinstance(steps, list) and 1 <= len(steps) <= 20,
             'preset steps must be a list of 1..20 entries')
    seen = set()
    for position, step in enumerate(steps):
        _require(isinstance(step, dict), f'step {position} must be a mapping')
        extra = set(step) - STEP_KEYS
        _require(not extra, f'step {position} carries undeclared fields: ' + ', '.join(sorted(extra)))
        for field in ('id', 'brick', 'requires', 'config'):
            _require(field in step, f'step {position} is missing {field}')
        step_id = step['id']
        _require(isinstance(step_id, str) and IDENTIFIER.fullmatch(step_id),
                 f'step {position} id must be 1..64 lowercase alphanumeric/hyphen characters')
        _require(step_id not in seen, f'step {step_id} is declared twice')
        seen.add(step_id)
        _require(isinstance(step['brick'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', step['brick']),
                 f'step {step_id} names an invalid brick')
        _require(isinstance(step['requires'], list)
                 and all(isinstance(item, str) for item in step['requires']),
                 f'step {step_id} requires must be a list of step ids')
        _require(isinstance(step['config'], dict), f'step {step_id} config must be a mapping')
        leaked = PORTAL_INPUTS & set(step['config'])
        _require(not leaked, f'step {step_id} must not carry the portal inputs: ' + ', '.join(sorted(leaked)))
    return body


def dependency_order(steps):
    """Kahn's algorithm, deterministic in the declared step order."""
    known = {step['id'] for step in steps}
    declared = [step['id'] for step in steps]
    dependencies = {}
    for step in steps:
        for requirement in step['requires']:
            _require(requirement in known,
                     f'step {step["id"]} requires unknown step {requirement}')
            _require(requirement != step['id'], f'step {step["id"]} requires itself')
        dependencies[step['id']] = set(step['requires'])
    order, done = [], set()
    while len(order) < len(declared):
        ready = [step_id for step_id in declared
                 if step_id not in done and dependencies[step_id] <= done]
        _require(ready, 'preset steps contain a dependency cycle')
        for step_id in ready:
            order.append(step_id)
            done.add(step_id)
    return order


def _brick_contract(plugins_dir, brick_id):
    try:
        directory = module_directory(plugins_dir, brick_id)
        brick = WorkloadExecutor(Path(plugins_dir) / 'unused', directory.parent).brick(brick_id)
    except FileNotFoundError:
        raise PresetError(f'brick {brick_id}: Unknown brick: {brick_id}') from None
    except (WorkloadError, ValueError) as error:
        raise PresetError(f'brick {brick_id}: {error}') from None
    lock_path = brick['_directory'] / 'roles.lock.json'
    _require(lock_path.is_file(), f'brick {brick_id} has no roles.lock.json')
    try:
        lock = json.loads(lock_path.read_text())
    except ValueError:
        raise PresetError(f'brick {brick_id} has an unreadable roles.lock.json') from None
    role = brick['implementation']
    entry = lock.get(role['role']) if isinstance(lock, dict) else None
    _require(isinstance(entry, dict) and entry.get('version') == str(role['version']),
             f'brick {brick_id} is not locked at {role["version"]}')
    return brick


def build_plan(preset, *, plugins_dir, target_uuid, ssh_key_id):
    _require(isinstance(target_uuid, str) and UUID.fullmatch(target_uuid),
             'target_uuid must be a VM UUID')
    _require(isinstance(ssh_key_id, str) and KEY.fullmatch(ssh_key_id),
             'ssh_key_id must be 32 lowercase hexadecimal characters')
    plugins_dir = Path(plugins_dir)
    order = dependency_order(preset['steps'])
    declared = [step['id'] for step in preset['steps']]
    _require(order == declared,
             'preset steps are not in dependency order; expected ' + ', '.join(order))
    catalog = Catalog(plugins_dir)
    admission = Admission(catalog, _DefaultSettings())
    steps, edges = [], []
    for step in preset['steps']:
        brick = _brick_contract(plugins_dir, step['brick'])
        schema_defaults = {key: spec['default'] for key, spec in
                           brick['schema'].get('properties', {}).items()
                           if isinstance(spec, dict) and 'default' in spec}
        config = {**brick.get('defaults', {}), **schema_defaults, **step['config'],
                  'target_uuid': target_uuid, 'ssh_key_id': ssh_key_id}
        try:
            jsonschema.Draft202012Validator(brick['schema']).validate(config)
        except jsonschema.ValidationError as error:
            path = '.'.join(str(item) for item in error.absolute_path) or '<root>'
            raise PresetError(f'step {step["id"]} violates {brick["id"]} at {path}: '
                              f'{error.message[:160]}') from None
        graph = {
            'name': f'{preset["id"]}-{step["id"]}'[:160],
            'environment': preset['environment'],
            'nodes': [{'id': step['id'], 'plugin_id': brick['id'], 'label': brick['name'],
                       'config': config}],
            'edges': [],
            'strategy': {'canary': 1, 'batch_size': 1},
            'failure_mode': 'none',
        }
        Graph.model_validate(graph)
        report = admission.validate(graph)
        if not report['allowed']:
            codes = ', '.join(sorted({violation['code'] for violation in report['violations']}))
            raise PresetError(f'step {step["id"]} is not admissible: {codes}')
        steps.append({'id': step['id'], 'brick': step['brick'],
                      'requires': list(step['requires']), 'graph': graph})
        for requirement in step['requires']:
            edges.append({'id': f'{requirement}--{step["id"]}', 'source': requirement,
                          'target': step['id']})
    return {
        'preset': {'id': preset['id'], 'name': preset['name'],
                   'version': preset.get('version', 1), 'environment': preset['environment']},
        'target': {'uuid': target_uuid, 'ssh_key_id': ssh_key_id, 'count': 1},
        'order': order,
        'stack': {'name': preset['id'], 'environment': preset['environment'],
                  'nodes': [{'id': step['id'], 'brick': step['brick']} for step in preset['steps']],
                  'edges': edges},
        'steps': steps,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('preset', help='path to a preset YAML file')
    parser.add_argument('--uuid', required=True, help='target guest UUID for every step')
    parser.add_argument('--ssh-key', required=True, help='managed SSH key id for every step')
    parser.add_argument('--plugins', default=str(ROOT / 'modules'), help='module catalogue directory')
    arguments = parser.parse_args(argv)
    try:
        preset = load_preset(arguments.preset)
        plan = build_plan(preset, plugins_dir=arguments.plugins,
                          target_uuid=arguments.uuid, ssh_key_id=arguments.ssh_key)
    except PresetError as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
