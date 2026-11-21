#!/usr/bin/env python3
"""Refuse committed secrets in tracked files, and optionally in history.

The scan is deliberately narrow. False positives block every push, so it looks
for material that is unambiguously a credential and nothing that merely mentions
a password.

A private key counts only as a complete block: header, body, footer. A test that
asserts on the header string, or that rejects a pasted key, holds no credential.
Token prefixes are never tolerated, including in fixtures.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

KEY_BLOCK = re.compile(
    rb'-----BEGIN (?:OPENSSH|RSA|EC|DSA|PGP) PRIVATE KEY-----'
    rb'(?:(?!-----END ).){100,}'
    rb'-----END (?:OPENSSH|RSA|EC|DSA|PGP) PRIVATE KEY-----',
    re.DOTALL,
)

PATTERNS = [
    (KEY_BLOCK, 'private key block', True),
    (re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{20,}'), 'github token', False),
    (re.compile(rb'\bgithub_pat_[A-Za-z0-9_]{20,}'), 'github fine-grained token', False),
    (re.compile(rb'\bAKIA[0-9A-Z]{16}\b'), 'aws access key id', False),
    (re.compile(rb'\bxox[baprs]-[A-Za-z0-9-]{10,}'), 'slack token', False),
    (re.compile(rb'\bAIza[0-9A-Za-z_-]{35}\b'), 'google api key', False),
    (re.compile(rb'\bsk-[A-Za-z0-9]{32,}\b'), 'generic secret key', False),
]

FORBIDDEN_PATHS = ('data/', 'docs/prompt/')
FORBIDDEN_SUFFIXES = ('.pem', '.key', '.p12', '.pfx')
FIXTURE_PATHS = ('tests/', 'frontend/src/')
FIXTURE_SUFFIXES = ('.test.ts', '.test.tsx', '.spec.ts', '.spec.tsx')


def is_fixture(path: str) -> bool:
    return path.startswith(FIXTURE_PATHS) or path.endswith(FIXTURE_SUFFIXES)


def scan(name: str, content: bytes, prefix: str = '') -> list[str]:
    if name.startswith(FORBIDDEN_PATHS) or name.endswith(FORBIDDEN_SUFFIXES):
        return [f'{prefix}{name}: runtime state or key material is tracked']
    problems = []
    fixture = is_fixture(name)
    for pattern, label, tolerated_in_fixtures in PATTERNS:
        if fixture and tolerated_in_fixtures:
            continue
        if pattern.search(content):
            problems.append(f'{prefix}{name}: looks like a {label}')
    return problems


def tracked_files() -> list[bytes]:
    output = subprocess.run(['git', 'ls-files', '-z'], capture_output=True, check=True).stdout
    return [name for name in output.split(b'\0') if name]


def scan_tree() -> list[str]:
    problems = []
    for raw in tracked_files():
        name = raw.decode(errors='replace')
        content = subprocess.run(['git', 'show', f':{name}'], capture_output=True).stdout
        problems += scan(name, content)
    return problems


def reachable_blobs() -> dict[str, str]:
    """Map every reachable blob to one path that contains it.

    Reading blobs rather than the textual diff keeps the match on the original
    content, without diff prefixes or truncation.
    """
    output = subprocess.run(['git', 'rev-list', '--objects', '--all'],
                            capture_output=True, check=True).stdout.decode(errors='replace')
    blobs: dict[str, str] = {}
    types: dict[str, bytes] = {}
    for line in output.splitlines():
        parts = line.split(' ', 1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if sha not in types:
            types[sha] = subprocess.run(['git', 'cat-file', '-t', sha],
                                        capture_output=True).stdout.strip()
        if types[sha] == b'blob':
            blobs.setdefault(sha, path)
    return blobs


def scan_history() -> list[str]:
    blobs = reachable_blobs()
    if not blobs:
        return []
    reader = subprocess.run(['git', 'cat-file', '--batch'],
                            input='\n'.join(blobs).encode(), capture_output=True)
    data = reader.stdout
    problems = []
    offset = 0
    for sha, path in blobs.items():
        header_end = data.find(b'\n', offset)
        if header_end < 0:
            break
        header = data[offset:header_end].split()
        if len(header) < 3:
            break
        size = int(header[2])
        body = data[header_end + 1:header_end + 1 + size]
        offset = header_end + 1 + size + 1
        problems += scan(path, body, prefix=f'history {sha[:8]} ')
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    arguments = parser.parse_args()
    problems = scan_tree()
    if arguments.history:
        problems += scan_history()
    if problems:
        for problem in dict.fromkeys(problems):
            print(problem, file=sys.stderr)
        return 1
    if not arguments.quiet:
        print('secret scan clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
