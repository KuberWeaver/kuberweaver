"""Release script: refusal paths, dry run and tag creation in a scratch repository."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts' / 'release.py'

CHANGELOG = """# Changelog

## [Unreleased]

### Added

- A tracked change.
"""

IDENTITY = {
    'GIT_AUTHOR_NAME': 'Release Test',
    'GIT_AUTHOR_EMAIL': 'release-test@example.invalid',
    'GIT_COMMITTER_NAME': 'Release Test',
    'GIT_COMMITTER_EMAIL': 'release-test@example.invalid',
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_SYSTEM': os.devnull,
    'GIT_TERMINAL_PROMPT': '0',
}


def environment() -> dict[str, str]:
    merged = os.environ.copy()
    merged.update(IDENTITY)
    return merged


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(['git', *arguments], cwd=repository, capture_output=True,
                            text=True, env=environment())
    assert result.returncode == 0, result.stderr
    return result.stdout


def release(repository: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=repository,
                          capture_output=True, text=True, env=environment())


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = tmp_path / 'kuberweaver'
    path.mkdir()
    git(path, 'init', '--quiet')
    git(path, 'symbolic-ref', 'HEAD', 'refs/heads/develop')
    (path / 'CHANGELOG.md').write_text(CHANGELOG, encoding='utf-8')
    git(path, 'add', 'CHANGELOG.md')
    git(path, 'commit', '--quiet', '-m', 'init')
    return path


def tags(repository: Path) -> list[str]:
    return git(repository, 'tag', '--list').split()


def test_dry_run_prints_the_plan_and_creates_nothing(repository):
    result = release(repository, '--dry-run')
    assert result.returncode == 0, result.stderr
    assert 'v0.1.0' in result.stdout
    assert 'dry run' in result.stdout
    assert '1 promoted' in result.stdout
    assert tags(repository) == []
    assert git(repository, 'status', '--porcelain') == ''
    assert (repository / 'CHANGELOG.md').read_text(encoding='utf-8') == CHANGELOG


def test_release_promotes_the_section_and_tags_the_commit(repository):
    result = release(repository)
    assert result.returncode == 0, result.stderr
    assert tags(repository) == ['v0.1.0']
    assert git(repository, 'cat-file', '-t', 'v0.1.0').strip() == 'tag'
    assert git(repository, 'rev-parse', 'HEAD').strip() == git(repository, 'rev-parse', 'v0.1.0^{commit}').strip()
    assert git(repository, 'status', '--porcelain') == ''
    assert git(repository, 'log', '-1', '--format=%s').strip() == 'chore(release): v0.1.0'
    text = (repository / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert text.startswith('# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - ')
    assert '### Added\n\n- A tracked change.' in text
    assert '## [0.1.0] - ' in text.split('## [Unreleased]')[1]


@pytest.mark.parametrize(
    ('level', 'expected'),
    [('patch', 'v1.2.4'), ('minor', 'v1.3.0'), ('major', 'v2.0.0')],
)
def test_bump_is_computed_from_the_last_tag(repository, level, expected):
    git(repository, 'tag', 'v1.2.3')
    result = release(repository, '--dry-run', '--bump', level)
    assert result.returncode == 0, result.stderr
    assert f'version: {expected}' in result.stdout
    assert tags(repository) == ['v1.2.3']


def test_explicit_version_overrides_the_bump(repository):
    git(repository, 'tag', 'v1.2.3')
    result = release(repository, '--dry-run', '--version', '2.0.0')
    assert result.returncode == 0, result.stderr
    assert 'version: v2.0.0 (requested)' in result.stdout


def test_release_from_another_branch_is_refused(repository):
    git(repository, 'checkout', '--quiet', '-b', 'feat/something')
    result = release(repository)
    assert result.returncode == 1
    assert 'releases are cut from develop' in result.stderr
    assert tags(repository) == []


def test_dirty_tracked_tree_is_refused(repository):
    (repository / 'CHANGELOG.md').write_text(CHANGELOG + '\n- pending edit\n', encoding='utf-8')
    result = release(repository)
    assert result.returncode == 1
    assert 'tracked files are modified' in result.stderr
    assert tags(repository) == []


def test_untracked_files_do_not_block_a_release(repository):
    (repository / 'scratch.txt').write_text('local note\n', encoding='utf-8')
    result = release(repository, '--dry-run')
    assert result.returncode == 0, result.stderr


def test_empty_unreleased_section_is_refused(repository):
    (repository / 'CHANGELOG.md').write_text('# Changelog\n\n## [Unreleased]\n', encoding='utf-8')
    git(repository, 'commit', '--quiet', '-am', 'empty')
    result = release(repository)
    assert result.returncode == 1
    assert 'nothing to release' in result.stderr
    assert tags(repository) == []


def test_missing_changelog_is_refused(repository):
    git(repository, 'rm', '--quiet', 'CHANGELOG.md')
    git(repository, 'commit', '--quiet', '-m', 'drop changelog')
    result = release(repository)
    assert result.returncode == 1
    assert 'CHANGELOG.md is missing' in result.stderr


def test_existing_tag_is_refused(repository):
    git(repository, 'tag', 'v0.1.0')
    result = release(repository, '--version', '0.1.0')
    assert result.returncode == 1
    assert 'tag v0.1.0 already exists' in result.stderr
    assert tags(repository) == ['v0.1.0']


def test_version_below_the_last_release_is_refused(repository):
    git(repository, 'tag', 'v2.0.0')
    result = release(repository, '--version', '1.9.9')
    assert result.returncode == 1
    assert 'is not greater than the last release v2.0.0' in result.stderr


def test_malformed_version_is_refused(repository):
    result = release(repository, '--version', '1.0')
    assert result.returncode == 1
    assert 'expected MAJOR.MINOR.PATCH' in result.stderr


def test_already_documented_version_is_refused(repository):
    git(repository, 'tag', 'v0.0.9')
    text = CHANGELOG.replace(
        '## [Unreleased]\n',
        '## [Unreleased]\n\n### Fixed\n\n- New entry.\n\n## [0.1.0] - 2026-01-01\n\n### Added\n\n- Older entry.\n',
    )
    (repository / 'CHANGELOG.md').write_text(text, encoding='utf-8')
    git(repository, 'commit', '--quiet', '-am', 'document 0.1.0')
    result = release(repository, '--version', '0.1.0')
    assert result.returncode == 1
    assert 'already documents version 0.1.0' in result.stderr


def test_integration_branch_out_of_sync_with_the_remote_is_refused(repository):
    first = git(repository, 'rev-parse', 'HEAD').strip()
    (repository / 'CHANGELOG.md').write_text(CHANGELOG + '\n### Changed\n\n- Later work.\n', encoding='utf-8')
    git(repository, 'commit', '--quiet', '-am', 'later work')
    git(repository, 'update-ref', 'refs/remotes/origin/develop', first)
    result = release(repository)
    assert result.returncode == 1
    assert 'not in sync with origin/develop' in result.stderr
    assert tags(repository) == []


def test_sync_check_is_skipped_when_the_remote_branch_is_absent(repository):
    assert 'refs/remotes/origin/develop' not in git(repository, 'show-ref')
    result = release(repository, '--dry-run')
    assert result.returncode == 0, result.stderr
