#!/usr/bin/env python3
"""Print the next autopilot task, or the reason there is none.

Deterministic on purpose: the iteration agent spends its budget on the task, not
on deciding which task comes next.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'ops' / 'autopilot' / 'STATE.json'


def main() -> int:
    if not STATE.is_file():
        print(json.dumps({'action': 'stop', 'reason': 'STATE.json is missing'}))
        return 1
    state = json.loads(STATE.read_text())
    tasks = state.get('tasks') or []
    for task in tasks:
        if task.get('status') in ('pending', 'partial'):
            print(json.dumps({
                'action': 'work',
                'iteration': state.get('iteration', 0) + 1,
                'task': task,
                'remaining': sum(1 for t in tasks if t.get('status') in ('pending', 'partial')),
                'blocked': [t['id'] for t in tasks if t.get('status') == 'blocked'],
                'rules': str(ROOT / 'ops' / 'autopilot' / 'RULES.md'),
            }, indent=2))
            return 0
    print(json.dumps({'action': 'stop', 'reason': 'every task is done or blocked',
                      'blocked': [t['id'] for t in tasks if t.get('status') == 'blocked']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
