#!/usr/bin/env python3
"""Drive a workload brick end to end, outside the portal.

The portal resolves the target from recorded deployment evidence; this driver
takes the same values on the command line so the brick can be exercised alone.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from kuberweaver.workload_execution import WorkloadError, WorkloadExecutor  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--brick', required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--uuid', required=True)
    parser.add_argument('--address', required=True)
    parser.add_argument('--config', required=True, help='JSON object or @path')
    parser.add_argument('--key', required=True, help='path to the private key file')
    parser.add_argument('--ansible', default='ansible-playbook')
    parser.add_argument('--galaxy', default='ansible-galaxy')
    parser.add_argument('--lock-role', action='store_true')
    parser.add_argument('--remove', action='store_true')
    parser.add_argument('--state-dir', default=str(ROOT / 'data/plugin-data'))
    arguments = parser.parse_args()

    raw = arguments.config
    config = json.loads(Path(raw[1:]).read_text() if raw.startswith('@') else raw)
    executor = WorkloadExecutor(arguments.state_dir, ROOT / 'modules',
                                playbook=arguments.ansible, key_path=arguments.key,
                                user='ubuntu')
    if arguments.lock_role:
        print('locked:', json.dumps(executor.lock_role(arguments.brick, galaxy=arguments.galaxy)))
    target = {'uuid': arguments.uuid, 'address': arguments.address}
    emit = lambda stage, level, message: print(f'[{stage}] {message}', file=sys.stderr)
    if arguments.remove:
        print(json.dumps(executor.remove(arguments.brick, arguments.job, config, target, emit=emit), indent=2))
        return 0
    plan = executor.prepare(arguments.brick, config, target, arguments.job)
    print('plan:', json.dumps({k: plan[k] for k in ('brick', 'role_version', 'address',
                                                    'plan_digest', 'assertions')}, indent=2))
    try:
        evidence = executor.execute(arguments.brick, arguments.job, config, target, emit=emit)
    except WorkloadError as error:
        print('failed:', error)
        return 1
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
