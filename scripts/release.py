#!/usr/bin/env python3
"""Tag a release from the integration branch.

The script is deliberately narrow: it promotes the ``Unreleased`` section of
``CHANGELOG.md`` to the released version, commits that single file on the
integration branch and creates an annotated tag on that commit. It never
pushes, never rewrites history and never touches another branch. Publishing
stays a human decision because the protected branches only move by pull
request.
"""
from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

SEMVER = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')
UNRELEASED_HEADING = '## [Unreleased]'
CHANGELOG = 'CHANGELOG.md'
BUMP_LEVELS = ('major', 'minor', 'patch')


class ReleaseError(RuntimeError):
    """A refusal. Raised before anything is written."""


def git(*arguments: str) -> str:
    result = subprocess.run(['git', *arguments], capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseError(f"git {' '.join(arguments)}: {detail}")
    return result.stdout


def git_optional(*arguments: str) -> str | None:
    result = subprocess.run(['git', *arguments], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def repository_root() -> Path:
    return Path(git('rev-parse', '--show-toplevel').strip())


def current_branch() -> str:
    branch = git_optional('symbolic-ref', '--short', '-q', 'HEAD')
    if not branch:
        raise ReleaseError('detached HEAD: check out the integration branch before releasing')
    return branch


def tracked_changes() -> bool:
    return bool(git('status', '--porcelain', '--untracked-files=no').strip())


def parse_version(text: str) -> tuple[int, int, int]:
    match = SEMVER.match(text.strip())
    if not match:
        raise ReleaseError(f'invalid version {text!r}: expected MAJOR.MINOR.PATCH')
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def bump(base: tuple[int, int, int], level: str) -> tuple[int, int, int]:
    major, minor, patch = base
    if level == 'major':
        return (major + 1, 0, 0)
    if level == 'minor':
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def latest_tag() -> tuple[str, tuple[int, int, int]] | None:
    best: tuple[str, tuple[int, int, int]] | None = None
    for name in git('tag', '--list').split():
        try:
            version = parse_version(name)
        except ReleaseError:
            continue
        if best is None or version > best[1]:
            best = (name, version)
    return best


def unreleased_section(text: str) -> tuple[int, int, int]:
    """Return the heading offset, body start and entry count of ``Unreleased``."""
    lines = text.splitlines(keepends=True)
    offset = 0
    heading_start = -1
    body_start = -1
    for line in lines:
        if line.strip() == UNRELEASED_HEADING:
            if heading_start >= 0:
                raise ReleaseError('CHANGELOG.md contains more than one Unreleased section')
            heading_start = offset
            body_start = offset + len(line)
        elif heading_start >= 0 and line.startswith('## '):
            break
        offset += len(line)
    if heading_start < 0:
        raise ReleaseError(f'CHANGELOG.md has no {UNRELEASED_HEADING} section')
    body = text[body_start:offset]
    entries = sum(1 for line in body.splitlines() if re.match(r'^\s*[-*] \S', line))
    if entries == 0:
        raise ReleaseError('the Unreleased section has no entries: nothing to release')
    return heading_start, body_start, entries


def promote(text: str, version: str, date: str) -> str:
    heading_start, body_start, _ = unreleased_section(text)
    heading = text[heading_start:body_start]
    body = text[body_start:]
    released = f'\n## [{version}] - {date}\n'
    return text[:heading_start] + heading.rstrip('\n') + '\n' + released + body


def resolve_version(arguments: argparse.Namespace) -> tuple[str, tuple[int, int, int], str]:
    previous = latest_tag()
    base = previous[1] if previous else (0, 0, 0)
    if arguments.version:
        target = parse_version(arguments.version)
        origin = 'requested'
    else:
        target = bump(base, arguments.bump)
        origin = f'{arguments.bump} bump of {previous[0] if previous else "the initial 0.0.0"}'
    if previous and target < previous[1]:
        raise ReleaseError(
            f'v{".".join(map(str, target))} is not greater than the last release {previous[0]}'
        )
    tag = f'v{".".join(map(str, target))}'
    if git_optional('rev-parse', '--verify', '--quiet', f'refs/tags/{tag}') is not None:
        raise ReleaseError(f'tag {tag} already exists')
    return tag, target, origin


def verify(arguments: argparse.Namespace) -> tuple[str, Path, str]:
    root = repository_root()
    branch = current_branch()
    if branch != arguments.branch:
        raise ReleaseError(
            f'releases are cut from {arguments.branch}, the current branch is {branch}'
        )
    if tracked_changes():
        raise ReleaseError('tracked files are modified: commit or discard them before releasing')
    remote = git_optional('rev-parse', '--verify', '--quiet', f'refs/remotes/{arguments.remote}/{branch}')
    if remote is not None:
        head = git('rev-parse', 'HEAD').strip()
        if head != remote:
            raise ReleaseError(
                f'{branch} is not in sync with {arguments.remote}/{branch}: '
                'rebase or fast-forward before releasing'
            )
    return branch, root, git('rev-parse', '--short', 'HEAD').strip()


def changelog_text(root: Path) -> str:
    path = root / CHANGELOG
    if not path.is_file():
        raise ReleaseError(f'{CHANGELOG} is missing')
    return path.read_text(encoding='utf-8')


def describe(text: str, version: str, date: str) -> tuple[str, int]:
    _, _, entries = unreleased_section(text)
    for line in text.splitlines():
        if line.startswith(f'## [{version}]'):
            raise ReleaseError(f'CHANGELOG.md already documents version {version}')
    return promote(text, version, date), entries


def restore(root: Path) -> None:
    """Undo a partially applied release, keeping unrelated work untouched."""
    subprocess.run(['git', 'reset', '--quiet', 'HEAD', '--', CHANGELOG], cwd=root, capture_output=True)
    subprocess.run(['git', 'checkout', '--', CHANGELOG], cwd=root, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description='Tag a release from the default branch.')
    parser.add_argument('--branch', default='main', help='branch releases are cut from (default: main)')
    parser.add_argument('--remote', default='origin', help='remote used for the sync check (default: origin)')
    parser.add_argument('--bump', choices=BUMP_LEVELS, default='minor',
                        help='version bump applied to the last tag when --version is omitted (default: minor)')
    parser.add_argument('--version', help='explicit MAJOR.MINOR.PATCH instead of a computed bump')
    parser.add_argument('--dry-run', action='store_true', help='print the plan and create nothing')
    arguments = parser.parse_args()

    try:
        branch, root, revision = verify(arguments)
        text = changelog_text(root)
        tag, _, origin = resolve_version(arguments)
        date = datetime.date.today().isoformat()
        updated, entries = describe(text, tag[1:], date)
    except ReleaseError as error:
        print(f'release refused: {error}', file=sys.stderr)
        return 1

    print(f'release plan for {root.name}')
    print(f'  branch:  {branch} at {revision}')
    print(f'  version: {tag} ({origin})')
    print(f'  entries: {entries} promoted to "{tag[1:]} - {date}"')
    if arguments.dry_run:
        print('dry run: CHANGELOG.md unchanged, no commit, no tag')
        return 0

    path = root / CHANGELOG
    try:
        path.write_text(updated, encoding='utf-8')
        git('-C', str(root), 'add', CHANGELOG)
        git('-C', str(root), 'commit', '--quiet', '-m', f'chore(release): {tag}')
        git('-C', str(root), 'tag', '--annotate', tag, '--message', f'KuberWeaver {tag}')
    except ReleaseError as error:
        restore(root)
        print(f'release aborted: {error}', file=sys.stderr)
        return 1

    print(f'released {tag} on {branch}')
    print(f'next: merge {branch} through a pull request, then push the tag with "git push {arguments.remote} {tag}"')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
