"""Trusted opt-in provider; configuration lives outside replaceable source."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from backend.secrets import OpenBaoProvider, SecretError, _protected_read


class _LocalOpenBaoProvider(OpenBaoProvider):
    """Keep the setup script's diagnostic marker out of connection references."""

    def _path(self, name: str) -> str:
        if name == "setup-check":
            raise SecretError("secret reference is reserved for local OpenBao diagnostics")
        return super()._path(name)


def register(ctx):
    provider, status = None, "unconfigured"
    try:
        path = ctx.plugin_data_dir() / "provider.json"
        if path.exists() or path.is_symlink():
            status = "unavailable"
            config = json.loads(_protected_read(path, 16384))
            expected_keys = {"endpoint", "approved", "token_file", "namespace", "allow_write", "allow_loopback_http"}
            if (not isinstance(config, dict) or set(config) != expected_keys
                    or config["approved"] != {} or config["namespace"] != "kuberweaver/connections"
                    or config["allow_write"] is not True or config["allow_loopback_http"] is not True):
                raise ValueError
            endpoint = urlsplit(config["endpoint"])
            if (endpoint.port is None or not 1024 <= endpoint.port <= 65535
                    or config["endpoint"] != f"http://127.0.0.1:{endpoint.port}"):
                raise ValueError
            token_path = path.parents[2] / "local-bao" / "app-token"
            if config["token_file"] != str(token_path):
                raise ValueError
            token = _protected_read(Path(config["token_file"]), 16384).decode("ascii").strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]+", token):
                raise ValueError
            provider = _LocalOpenBaoProvider(**config)
            status = "configured"
    except Exception:
        # No exception text, paths, endpoints, tokens or provider repr in discovery.
        provider, status = None, "unavailable"
    ctx.register("secrets", "openbao-local", provider, {
        "label": "OpenBao local / nouvelles connexions V2", "reference_only": True,
        "available": provider is not None, "status": status,
        "description": "Configuration locale uniquement ; connectivité non sondée à l'enregistrement.",
    })
