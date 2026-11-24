"""The CLI contract, locked: help, exit codes, stream separation, JSON shape.

These are the properties an operator or a wrapper script depends on, so they are
asserted once here instead of being re-derived in every command test.
"""
import json

from click.testing import CliRunner  # noqa: E402

from kw.main import cli  # noqa: E402
from test_cli import env  # noqa: E402,F401  (fixture)

GROUPS = ['auth', 'connectors', 'keys', 'blueprints', 'deploy', 'inventory', 'finops',
          'logs', 'modules', 'services', 'resources', 'rollback', 'decommission',
          'tokens', 'providers']


def test_every_group_is_registered_and_explains_itself():
    runner = CliRunner()
    listing = runner.invoke(cli, ['--help'])
    assert listing.exit_code == 0, listing.output
    for name in GROUPS:
        assert name in listing.output, name
        help_text = runner.invoke(cli, [name, '--help'])
        assert help_text.exit_code == 0, (name, help_text.output)
        assert 'Usage' in help_text.output
        assert help_text.stderr.strip() == ''


def test_an_unknown_command_is_a_usage_error_on_stderr_only():
    runner = CliRunner()
    result = runner.invoke(cli, ['frobnicate'])
    assert result.exit_code == 2
    assert result.stdout.strip() == ''
    assert 'frobnicate' in result.stderr


def test_an_unreachable_api_fails_cleanly_without_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv('KW_CONFIG_DIR', str(tmp_path / 'kwcfg'))
    monkeypatch.setenv('KW_URL', 'http://127.0.0.1:9')
    monkeypatch.delenv('KW_AGENT_TOKEN', raising=False)
    monkeypatch.setenv('KW_PASSWORD', 'admin')
    runner = CliRunner()
    result = runner.invoke(cli, ['auth', 'whoami'])
    assert result.exit_code == 4
    assert result.stdout.strip() == ''
    assert 'error:' in result.stderr
    assert 'Traceback' not in result.stderr


def _login(runner):
    result = runner.invoke(cli, ['auth', 'login', '--username', 'admin'])
    assert result.exit_code == 0, result.output + result.stderr


def test_a_refusal_keeps_stdout_empty_and_names_the_cause(env):
    app, runner = env
    _login(runner)
    result = runner.invoke(cli, ['deploy', 'watch', 'does-not-exist'])
    assert result.exit_code == 1, result.output + result.stderr
    assert result.stdout.strip() == ''
    assert 'Unknown job' in result.stderr


def test_json_output_parses_for_every_read_command(env):
    app, runner = env
    _login(runner)
    commands = [
        ('auth', 'whoami'),
        ('keys', 'list'),
        ('connectors', 'drivers'),
        ('connectors', 'list'),
        ('providers', 'list'),
        ('blueprints', 'list'),
        ('inventory', 'list'),
        ('finops',),
        ('services', 'list'),
        ('deploy', 'list'),
        ('tokens', 'list'),
    ]
    for command in commands:
        result = runner.invoke(cli, ['--json', *command])
        assert result.exit_code == 0, (command, result.output, result.stderr)
        payload = json.loads(result.stdout)
        assert isinstance(payload, (dict, list)), command


def test_no_seeded_secret_appears_in_cli_output(env):
    app, runner = env
    _login(runner)
    path = app.state.state_dir / 'bootstrap-credentials.txt'
    secrets = []
    for line in path.read_text().splitlines():
        role, password = line.split(':', 1)
        if password != role:          # the development default is not a secret
            secrets.append(password)
    assert secrets, 'the fixture must seed at least one random password'
    for command in [('auth', 'whoami'), ('keys', 'list'), ('inventory', 'list')]:
        result = runner.invoke(cli, ['--json', *command])
        assert result.exit_code == 0, command
        for secret in secrets:
            assert secret not in result.stdout, command
