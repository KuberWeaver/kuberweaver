#!/usr/bin/env python3
"""Run the pre-push checks once, and report them as one result.

This exists because the three gates (tests, credential scan, whitespace) are
tedious to type separately, and because a failing test must not be mistaken for
a passing change. It writes nothing to the repository and never touches the live
`data/` directory: tests run against a disposable state directory under `$TMPDIR`.

Usage:
    python3 ops/ci/check.py            # everything
    python3 ops/ci/check.py --fast     # hygiene + secret scan only

Exit code 0 means every selected check passed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / '.venv' / 'bin' / 'python'
PYTHON = str(VENV_PYTHON if VENV_PYTHON.is_file() else sys.executable)

results: list[tuple[str, bool, str]] = []


def run(label: str, argv: list[str], *, cwd: Path = ROOT,
        env: dict | None = None) -> None:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    completed = subprocess.run(argv, cwd=str(cwd), env=merged,
                               capture_output=True, text=True)
    ok = completed.returncode == 0
    tail = (completed.stdout + completed.stderr).strip().splitlines()
    detail = tail[-1] if tail else ''
    results.append((label, ok, detail))


def hygiene() -> None:
    run('hygiene', [sys.executable, '-m', 'compileall', '-q',
                    'backend', 'kw', 'scripts', 'ops'])
    run('whitespace', ['git', 'diff', '--check'])


def secrets() -> None:
    run('secret scan', [sys.executable, 'ops/ci/secret_scan.py', '--history'])


def tests() -> None:
    state = tempfile.mkdtemp(prefix='kw-check.', dir=os.environ.get('TMPDIR'))
    env = {
        'KUBERWEAVER_STATE_DIR': state,
        # The test database URL file is deliberately unset so no live database
        # can be selected; PostgreSQL fixtures create their own schema.
        'KUBERWEAVER_TEST_DATABASE_URL_FILE': '',
    }
    run('pytest', [PYTHON, '-m', 'pytest', 'tests', '-q', '--tb=short'], env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fast', action='store_true',
                        help='skip the test suite')
    arguments = parser.parse_args()

    hygiene()
    secrets()
    if not arguments.fast:
        tests()

    width = max(len(label) for label, _, _ in results)
    for label, ok, detail in results:
        print(f"{label.ljust(width)}  {'PASS' if ok else 'FAIL'}  {detail[:160]}")
    failed = [label for label, ok, _ in results if not ok]
    print()
    if failed:
        print(f"failed: {', '.join(failed)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
