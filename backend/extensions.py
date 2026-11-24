"""KuberWeaver extension protocol v1. Trusted Python, not a sandbox."""
from __future__ import annotations

import json
import inspect
import re
import sys
from types import ModuleType
from uuid import uuid4
from pathlib import Path
from copy import deepcopy
from typing import Any

_TOKEN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def _token(value: str, label: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError(f"Invalid {label}: expected a lowercase identifier")
    return value


def _json_copy(value: Any) -> Any:
    """Validate metadata without leaking provider reprs into discovery."""
    return json.loads(json.dumps(value, allow_nan=False))


PROTOCOL_VERSION = 1
_CAPABILITY = re.compile(r"(?:providers|hooks):[a-z][a-z0-9_-]{0,63}\Z")
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\Z")


def _safe_path(path: Path) -> Path:
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError("Symlinks or noncanonical paths are forbidden")
    return path


def validate_package(path: Path) -> dict:
    """Static protocol-v1 validation: no imports, subprocesses or installers."""
    import yaml
    path = _safe_path(path)
    if not path.is_dir():
        raise ValueError("Extension package must be a directory")
    # Reject all package links, not just the entrypoint, before trust/installation.
    if any(p.is_symlink() for p in path.rglob("*")):
        raise ValueError("Symlinks in extension packages are forbidden")
    manifest_path = path / "plugin.yaml"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 65536:
        raise ValueError("Missing or oversized plugin.yaml")
    class StrictLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            mapping = super().construct_mapping(node, deep=deep)
            if len(mapping) != len(node.value):
                raise ValueError("Duplicate YAML fields are forbidden")
            return mapping

    try:
        manifest = yaml.load(manifest_path.read_text(encoding="utf-8"), Loader=StrictLoader)
    except yaml.YAMLError as exc:
        raise ValueError("Malformed plugin.yaml") from exc
    required = {"id", "version", "entrypoint", "capabilities"}
    optional = {"api_version", "description"}
    if not isinstance(manifest, dict) or not required <= manifest.keys() or manifest.keys() - required - optional:
        raise ValueError("Invalid manifest fields")
    _token(manifest["id"], "extension id")
    if not isinstance(manifest["version"], str) or not _VERSION.fullmatch(manifest["version"]):
        raise ValueError("version must be a semantic-version string")
    if type(manifest.get("api_version", PROTOCOL_VERSION)) is not int or manifest.get("api_version", PROTOCOL_VERSION) != PROTOCOL_VERSION:
        raise ValueError("Unsupported extension api_version")
    if "description" in manifest and not isinstance(manifest["description"], str):
        raise ValueError("description must be a string")
    # A single fixed entrypoint keeps v1 path handling reviewable.
    if manifest["entrypoint"] != "module.py" or not (path / "module.py").is_file():
        raise ValueError("entrypoint must be the regular file module.py")
    capabilities = manifest["capabilities"]
    if not isinstance(capabilities, list) or any(not isinstance(c, str) for c in capabilities):
        raise ValueError("capabilities must be a list of strings")
    if len(set(capabilities)) != len(capabilities) or any(c != "data:read-write" and not _CAPABILITY.fullmatch(c) for c in capabilities):
        raise ValueError("Unknown or duplicate capability declaration")
    return {**manifest, "api_version": PROTOCOL_VERSION, "path": str(path)}


def inspect_directory(path: Path) -> list[dict]:
    """Read manifests only. Invalid siblings and duplicate IDs fail closed."""
    path = Path(path)
    try:
        path = _safe_path(path)
        if not path.exists():
            return []
        if not path.is_dir():
            raise ValueError("Extension root must be a directory")
        packages = sorted(path.iterdir())
    except (OSError, ValueError) as exc:
        return [{"path": str(path), "status": "error", "error": str(exc)}]
    reports = []
    for package in packages:
        if not package.is_dir() and not package.is_symlink():
            continue
        try:
            reports.append({**validate_package(package), "status": "disabled"})
        except Exception as exc:
            reports.append({"path": str(package), "status": "error", "error": "Invalid extension package", "error_type": type(exc).__name__})
    counts: dict[str, int] = {}
    for report in reports:
        if "id" in report:
            counts[report["id"]] = counts.get(report["id"], 0) + 1
    for report in reports:
        if counts.get(report.get("id"), 0) > 1:
            report.update(status="error", error="Duplicate extension id")
    return reports


def read_enabled(path: Path) -> set[str]:
    """Read data/extensions.json; malformed state must never enable code."""
    path = _safe_path(path)
    if not path.exists():
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "enabled"} or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Invalid extension configuration")
    enabled = value["enabled"]
    if not isinstance(enabled, list) or any(not isinstance(identifier, str) for identifier in enabled):
        raise ValueError("enabled must be a list of IDs")
    if len(set(enabled)) != len(enabled):
        raise ValueError("Duplicate enabled extension id")
    return {_token(identifier, "enabled id") for identifier in enabled}


class ExtensionRegistry:
    """One registry for builtins and optional, explicitly trusted providers."""

    def __init__(self):
        self._entries: dict[tuple[str, str], tuple[Any, dict]] = {}
        self._hooks: dict[str, list] = {}
        self._loaded_ids: set[str] = set()

    def register(self, kind: str, name: str, provider: Any, description: dict | None = None):
        key = (_token(kind, "kind"), _token(name, "name"))
        if key in self._entries:
            raise ValueError(f"Provider already registered: {kind}/{name}")
        if description is not None and not isinstance(description, dict):
            raise TypeError("description must be a JSON object")
        metadata = _json_copy(description or {})
        if {"kind", "name", "provider"} & metadata.keys():
            raise ValueError("description cannot override kind/name/provider")
        self._entries[key] = (provider, metadata)

    def add_hook(self, event: str, handler):
        _token(event, "event")
        if not callable(handler) or inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(getattr(handler, "__call__", None)):
            raise TypeError("hook handler must be a synchronous callable")
        self._hooks.setdefault(event, []).append(handler)

    def emit(self, event: str, payload: dict) -> list[dict]:
        if not isinstance(payload, dict):
            raise TypeError("event payload must be a dict")
        reports = []
        for index, handler in enumerate(tuple(self._hooks.get(event, ()))):
            report = {"event": event, "hook": index, "status": "ok"}
            try:
                # Every observer receives its own copy, never a mutable gate input.
                handler(deepcopy(payload))
            except (Exception, SystemExit) as exc:
                # Do not include exception text: it can contain credentials.
                report.update(status="error", error_type=type(exc).__name__)
            reports.append(report)
        return reports

    def load_directory(self, path: Path, enabled: set[str] | None = None) -> list[dict]:
        """Activate explicit IDs only; each register(ctx) is an atomic transaction.

        Call at startup before serving requests. Rebuild the registry to disable;
        this method deliberately does not unload running code or providers.
        """
        enabled = set(enabled or ())
        for identifier in enabled:
            _token(identifier, "enabled id")
        reports = inspect_directory(path)
        for report in reports:
            if report["status"] == "error" or report["id"] not in enabled:
                continue
            module_name = f"_kuberweaver_extension_{report['id'].replace('-', '_')}_{uuid4().hex}"
            context = None
            try:
                if report["id"] in self._loaded_ids:
                    raise ValueError("Extension id is already loaded")
                package = Path(report["path"])
                manifest = validate_package(package)
                if manifest != {k: v for k, v in report.items() if k != "status"}:
                    raise ValueError("Manifest changed during discovery")
                staged = ExtensionRegistry()
                context = _RegistrationContext(report, staged, Path(path).absolute().parent / "plugin-data")
                module = ModuleType(module_name)
                module.__file__ = str(package / "module.py")
                sys.modules[module_name] = module
                # No sys.path modification, auto-pip, downloads, or dependency hooks.
                exec(compile((package / "module.py").read_bytes(), module.__file__, "exec"), module.__dict__)
                register = getattr(module, "register", None)
                if not callable(register) or inspect.iscoroutinefunction(register):
                    raise ValueError("Extension must define synchronous register(ctx)")
                result = register(context)
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise TypeError("register(ctx) cannot return an awaitable")
                context._close()
                if self._entries.keys() & staged._entries.keys():
                    raise ValueError("Extension conflicts with an existing provider")
                self._entries.update(staged._entries)
                for event, handlers in staged._hooks.items():
                    self._hooks.setdefault(event, []).extend(handlers)
                self._loaded_ids.add(report["id"])
                report.update(status="loaded", providers=staged.list(), hooks=sorted(staged._hooks))
            except (Exception, SystemExit) as exc:
                sys.modules.pop(module_name, None)
                report.update(status="error", error="Extension registration failed", error_type=type(exc).__name__)
            finally:
                if context is not None:
                    context._close()
        return reports

    def get(self, kind: str, name: str) -> Any:
        return self._entries[(kind, name)][0]

    def list(self, kind: str | None = None) -> list[dict]:
        return [
            {"kind": k, "name": n, **deepcopy(description)}
            for (k, n), (_, description) in sorted(self._entries.items())
            if kind is None or kind == k
        ]


class _RegistrationContext:
    """Scoped SDK, not a raw registry and not a Python security sandbox."""

    protocol_version = PROTOCOL_VERSION

    def __init__(self, manifest: dict, staged: ExtensionRegistry, data_root: Path):
        self.__plugin_id = manifest["id"]
        self.__capabilities = frozenset(manifest["capabilities"])
        self.__register = staged.register
        self.__add_hook = staged.add_hook
        self.__data_root = data_root
        self.__active = True

    @property
    def plugin_id(self) -> str:
        return self.__plugin_id

    @property
    def capabilities(self) -> frozenset[str]:
        return self.__capabilities

    def _close(self):
        self.__active = False

    def __require(self, capability: str, *, registration: bool = True):
        if capability not in self.capabilities:
            raise PermissionError("Undeclared extension capability")
        if registration and not self.__active:
            raise RuntimeError("Registration context is closed")

    def register(self, kind: str, name: str, provider: Any, description: dict | None = None):
        self.__require(f"providers:{kind}")
        self.__register(kind, name, provider, description)

    def add_hook(self, event: str, handler):
        self.__require(f"hooks:{event}")
        self.__add_hook(event, handler)

    def plugin_data_dir(self) -> Path:
        """Return a durable, namespaced path separate from installed source."""
        self.__require("data:read-write", registration=False)
        # Check on every access: an existing symlink must never redirect state.
        root = _safe_path(self.__data_root)
        target = _safe_path(root / _token(self.plugin_id, "extension id"))
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        return target

