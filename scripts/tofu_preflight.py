#!/usr/bin/env python3
"""Read-only hypervisor checks and optional REAL init/validate/saved create plan.

No apply/destroy switch exists. All paths are administrator CLI inputs, not graph fields.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.kuberweaver.tofu_execution import TofuExecutor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'tools/tofu')
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--remote', action='store_true')
    parser.add_argument('--plan', action='store_true', help='REAL saved plan only, never apply')
    args = parser.parse_args()
    executor = TofuExecutor(args.state_dir, binary=args.binary)
    report = executor.preflight(remote=args.remote or args.plan)
    if args.plan and report['status'] != 'blocked':
        graph = {'name': 'OpenTofu read-only preflight', 'environment': 'dev',
                 'nodes': [{'id': 'vm1', 'plugin_id': 'libvirt-vm',
                            'config': {'cpu': 1, 'memory_mb': 128, 'disk_gb': 1}}],
                 'edges': [], 'failure_mode': 'none'}
        try:
            report['plan'] = executor.prepare_plan({'id': 'preflight-' + secrets.token_hex(8), 'graph': graph})
        except Exception as exc:
            report['status'] = 'blocked'
            report['error_type'] = type(exc).__name__
            report['error'] = 'Real init/validate/plan failed; no apply was attempted; raw provider output suppressed'
    print(json.dumps(report, indent=2))
    return 1 if report['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
