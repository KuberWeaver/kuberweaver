"""Client-side configuration and session persistence. Secrets never in argv.

The session file holds a session cookie and CSRF token (both are credentials);
it is written 0600 inside a 0700 config directory, and cleared on logout.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8088"


def config_dir() -> Path:
    override = os.environ.get("KW_CONFIG_DIR")
    base = Path(override) if override else Path.home() / ".config" / "kuberweaver"
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    return base


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_config() -> dict:
    return _read_json(config_dir() / "config.json")


def save_config(data: dict) -> None:
    path = config_dir() / "config.json"
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)


def resolve_url(cli_option: str | None) -> str:
    return (cli_option or os.environ.get("KW_URL")
            or load_config().get("url") or DEFAULT_URL).rstrip("/")


def session_path() -> Path:
    return config_dir() / "session.json"


def load_session() -> dict:
    return _read_json(session_path())


def save_session(session: dict) -> None:
    path = session_path()
    path.write_text(json.dumps(session, indent=2))
    os.chmod(path, 0o600)


def clear_session() -> None:
    try:
        session_path().unlink()
    except FileNotFoundError:
        pass


def read_password(username: str) -> str:
    """Password from env or a 0600 file, never from a command-line argument."""
    env = os.environ.get("KW_PASSWORD")
    if env:
        return env
    file = os.environ.get("KW_PASSWORD_FILE")
    if file:
        return Path(file).read_text().strip()
    import click
    return click.prompt(f"Password for {username}", hide_input=True)


def agent_token() -> str | None:
    return os.environ.get("KW_AGENT_TOKEN")
