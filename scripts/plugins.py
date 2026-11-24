#!/usr/bin/env python3
"""Local extension lifecycle. No downloads, dependency installs, or implicit imports."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.extensions import inspect_directory, read_enabled, validate_package


def safe(path: Path) -> Path:
    path = path.absolute()
    if path.resolve() != path:
        raise ValueError("Symlinks/noncanonical paths are forbidden")
    return path


def save_config(path: Path, enabled: set[str]):
    safe(path)
    fd, name = tempfile.mkstemp(prefix=".extensions-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "enabled": sorted(enabled)}, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if read_enabled(path) != enabled:
            raise RuntimeError("Extension configuration verification failed")
    finally:
        Path(name).unlink(missing_ok=True)


def selected(root: Path, identifier: str) -> dict:
    matches = [r for r in inspect_directory(root) if r.get("id") == identifier]
    if len(matches) != 1 or matches[0]["status"] == "error":
        raise ValueError("Extension is missing, invalid, or has a duplicate id")
    return matches[0]


def doctor(path: Path, execute: bool, trust: bool) -> dict:
    manifest = validate_package(path)
    if not execute:
        # Compile syntax without evaluating any source or register function.
        compile((path / "module.py").read_bytes(), str(path / "module.py"), "exec")
        return {**manifest, "status": "validated", "executed": False}
    if not trust:
        raise ValueError("Executing Python requires --execute --trust; this is NOT a sandbox")
    # A fresh interpreter/temporary data tree protects app registry/state, NOT the host.
    probe = '''import contextlib, io, json, pathlib, shutil, sys, tempfile
sys.path.insert(0, sys.argv[1])
from backend.extensions import ExtensionRegistry
with tempfile.TemporaryDirectory(dir=sys.argv[3]) as temporary:
    root = pathlib.Path(temporary) / "extensions"
    root.mkdir()
    shutil.copytree(sys.argv[2], root / "probe")
    registry = ExtensionRegistry()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        reports = registry.load_directory(root, {sys.argv[4]})
    print(json.dumps(reports[0]))
'''
    scratch = os.environ.get("TMPDIR", str(Path.home() / ".hermes" / "cache" / "scratch"))
    Path(scratch).mkdir(parents=True, exist_ok=True)
    result = subprocess.run([sys.executable, "-I", "-c", probe, str(ROOT), str(path), scratch, manifest["id"]], text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError("Isolated doctor failed; no plugin output is disclosed")
    report = json.loads(result.stdout)
    # Do not claim the temporary module path is the installed package path.
    report.update(path=str(path), isolated_process=True, executed=True)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("KUBERWEAVER_DATA_DIR", ROOT / "data")))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Static discovery; never imports Python")
    sub.add_parser("path", help="Print source, state and config paths")
    install = sub.add_parser("install", help="Install a local directory, disabled; no remote sources")
    install.add_argument("source", type=Path)
    enable = sub.add_parser("enable", help="Explicit trust; activation requires app restart")
    enable.add_argument("id")
    enable.add_argument("--trust", action="store_true")
    disable = sub.add_parser("disable", help="Disable on next app restart; preserve durable state")
    disable.add_argument("id")
    check = sub.add_parser("doctor", help="Static validation by default; execution is opt-in")
    check.add_argument("target", help="Installed id or local package path")
    check.add_argument("--execute", action="store_true")
    check.add_argument("--trust", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = safe(args.data_dir)
        root = safe(data / "extensions")
        config = safe(data / "extensions.json")
        if args.command == "path":
            result = {"extensions": str(root), "plugin_data": str(data / "plugin-data"), "config": str(config)}
        elif args.command == "doctor":
            path = Path(args.target).absolute()
            if not path.exists():
                path = Path(selected(root, args.target)["path"])
            result = doctor(path, args.execute, args.trust)
        elif args.command == "list":
            enabled = read_enabled(config)
            result = inspect_directory(root)
            for row in result:
                if row["status"] != "error" and row["id"] in enabled:
                    row["status"] = "enabled"  # Configured; not a claim of active runtime state.
        else:
            data.mkdir(parents=True, exist_ok=True, mode=0o700)
            root.mkdir(exist_ok=True, mode=0o700)
            lockpath = safe(data / ".extensions.lock")
            descriptor = os.open(lockpath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                enabled = read_enabled(config)
                if args.command == "install":
                    manifest = validate_package(args.source)
                    destination = safe(root / manifest["id"])
                    if destination.exists() or any(r.get("id") == manifest["id"] for r in inspect_directory(root)):
                        raise ValueError("Extension id already installed; no implicit overwrite")
                    if args.source.absolute() == root or root.is_relative_to(args.source.absolute()):
                        raise ValueError("Source must not contain the extension install root")
                    with tempfile.TemporaryDirectory(prefix=".extension-stage-", dir=data) as temporary:
                        staging = Path(temporary) / "package"
                        shutil.copytree(args.source, staging, symlinks=True)
                        validate_package(staging)
                        staging.rename(destination)
                    enabled.discard(manifest["id"])
                    save_config(config, enabled)
                    # Read the exact installed target before claiming success.
                    installed = validate_package(destination)
                    result = {**installed, "status": "installed_disabled", "restart_required": True}
                else:
                    if args.command == "enable":
                        if not args.trust:
                            raise ValueError("Enabling trusted host Python requires --trust")
                        manifest = selected(root, args.id)
                        enabled.add(manifest["id"])
                    else:
                        # Removing a stale ID must work after its source was deleted.
                        if not args.id or args.id not in enabled:
                            selected(root, args.id)
                        enabled.discard(args.id)
                    save_config(config, enabled)
                    result = {"id": args.id, "status": "enabled" if args.id in enabled else "disabled", "restart_required": True}
        print(json.dumps(result, indent=2))
        return 1 if isinstance(result, dict) and result.get("status") == "error" else 0
    except (OSError, ValueError, TypeError, SyntaxError, subprocess.TimeoutExpired) as exc:
        # Avoid reprs/tracebacks from plugin code or host environment.
        print(json.dumps({"status": "error", "error": str(exc) if type(exc) is ValueError else type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
