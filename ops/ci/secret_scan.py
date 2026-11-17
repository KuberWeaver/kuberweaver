#!/usr/bin/env python3
"""Refuse committed secrets in tracked files, and optionally in history.

Patterns are deliberately narrow: false positives here block every push, so the
scanner looks for material that is unambiguously a credential, not for the word
"password" in documentation.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

PATTERNS = [
    (re.compile(rb'-----BEGIN (?:OPENSSH|RSA|EC|DSA|PGP) PRIVATE KEY-----'), 'private key block'),
    (re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{20,}'), 'github token'),
    (re.compile(rb'\bgithub_pat_[A-Za-z0-9_]{20,}'), 'github fine-grained token'),
    (re.compile(rb'\bAKIA[0-9A-Z]{16}\b'), 'aws access key id'),
    (re.compile(rb'\bxox[baprs]-[A-Za-z0-9-]{10,}'), 'slack token'),
    (re.compile(rb'\bAIza[0-9A-Za-z_-]{35}\b'), 'google api key'),
    (re.compile(rb'\bsk-[A-Za-z0-9]{32,}\b'), 'generic secret key'),
]

FORBIDDEN_PATHS = ('data/', 'docs/prompt/')
FORBIDDEN_SUFFIXES = ('.pem', '.key', '.p12', '.pfx')


def tracked_files():
    output = subprocess.run(['git', 'ls-files', '-z'], capture_output=True, check=True).stdout
    return [name for name in output.split(b'\0') if name]


def scan_tree() -> list[str]:
    problems = []
    for raw in tracked_files():
        name = raw.decode(errors='replace')
        if name.startswith(FORBIDDEN_PATHS) or name.endswith(FORBIDDEN_SUFFIXES):
            problems.append(f'{name}: runtime state or key material is tracked')
            continue
        content = subprocess.run(['git', 'show', f':{name}'], capture_output=True).stdout
        for pattern, label in PATTERNS:
            if pattern.search(content):
                problems.append(f'{name}: looks like a {label}')
    return problems


def scan_history() -> list[str]:
    problems = []
    log = subprocess.run(['git', 'log', '-p', '--all', '--no-color', '--no-ext-diff'],
                         capture_output=True).stdout
    for pattern, label in PATTERNS:
        for match in pattern.finditer(log):
            start = log.rfind(b'\n+', 0, match.start())
            snippet = log[start + 1:start + 120].split(b'\n')[0].decode(errors='replace')
            problems.append(f'history: looks like a {label}: {snippet[:60]}')
            break
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
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    if not arguments.quiet:
        print('secret scan clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
