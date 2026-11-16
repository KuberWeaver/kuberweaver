"""Explicitly scoped credential providers, with opt-in verified vault writes."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
from typing import ContextManager, Iterator, Mapping, Protocol


class SecretError(RuntimeError):
    """Deliberately sanitized: never include a credential or upstream response."""


class SecretValue:
    """Best-effort lifetime reduction, not a guarantee of Python memory erasure."""

    __slots__ = ("_value", "_closed")

    def __init__(self, value: bytes):
        self._value = bytearray(value)
        self._closed = False

    def bytes(self) -> bytes:
        if self._closed:
            raise SecretError("secret context is closed")
        return bytes(self._value)

    def close(self) -> None:
        self._value[:] = b"\0" * len(self._value)
        self._closed = True

    def __repr__(self) -> str:
        return "<SecretValue redacted>"


class SecretProvider(Protocol):
    def resolve(self, name: str) -> ContextManager[SecretValue]: ...
    def describe(self) -> dict: ...


def _name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(
        r"[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)*", value
    ):
        raise SecretError("invalid secret name")
    return value


def _protected_read(path: Path, limit: int = 1024 * 1024) -> bytes:
    fd = None
    directory_fd = None
    try:
        # Walk from / through descriptor-relative NOFOLLOW opens. A preliminary
        # resolve()+open() check alone races when an ancestor is swapped.
        components = path.absolute().parts
        if ".." in components or len(components) < 2:
            raise SecretError("credential file is not protected")
        directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for component in components[1:-1]:
            child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory_fd)
        os.close(directory_fd)
        directory_fd = None
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid() or metadata.st_nlink != 1):
            raise SecretError("credential file is not protected")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            value = stream.read(limit + 1)
        if not value or len(value) > limit:
            raise SecretError("credential file is empty or too large")
        return value
    except (OSError, RuntimeError, ValueError):
        raise SecretError("credential file is unavailable or not protected") from None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if fd is not None:
            os.close(fd)


class RestrictedFileProvider:
    """Only administrator-approved logical names can address exact local files."""

    def __init__(self, approved: Mapping[str, str | Path]):
        self._approved = {_name(name): Path(path).absolute() for name, path in approved.items()}

    def describe(self) -> dict:
        return {"provider": "restricted-file", "capabilities": ["read"],
                "approved_names": sorted(self._approved), "persistent_secret_cache": False}

    @contextmanager
    def resolve(self, name: str) -> Iterator[SecretValue]:
        _name(name)
        if name not in self._approved:
            raise SecretError("secret name is not approved")
        secret = SecretValue(_protected_read(self._approved[name]))
        try:
            yield secret
        finally:
            secret.close()


class OpenBaoProvider:
    """KV v2 string values, with TLS mandatory except explicit numeric loopback HTTP.

    Approved references are configuration, never client-controlled URLs. Token is
    reloaded on every resolution to support rotation without an in-memory cache.
    """

    def __init__(self, endpoint: str, approved: Mapping[str, str], *, mount: str = "secret",
                 token_file: str | Path | None = None, token_env: str | None = None,
                 allow_loopback_http: bool = False, timeout: float = 5.0,
                 namespace: str | None = None, allow_write: bool = False):
        import ipaddress
        import ssl
        from urllib.parse import urlsplit
        from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, build_opener

        try:
            parsed = urlsplit(endpoint)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
                raise ValueError
            parsed.port  # validate malformed ports, before any network request
            if parsed.scheme == "http" and not (
                    allow_loopback_http and ipaddress.ip_address(parsed.hostname).is_loopback):
                raise ValueError
        except (ValueError, TypeError):
            raise SecretError("OpenBao endpoint requires verified TLS or explicit loopback lab HTTP") from None
        if (token_file is None) == (token_env is None):
            raise SecretError("configure exactly one OpenBao token source")
        if token_env is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise SecretError("invalid token environment variable name")
        if not 0 < timeout <= 60:
            raise SecretError("invalid OpenBao timeout")
        self._approved = {_name(name): _name(path) for name, path in approved.items()}
        if any(not path.startswith("kuberweaver/") for path in self._approved.values()):
            raise SecretError("OpenBao references must stay in the kuberweaver scope")
        self._namespace = _name(namespace) if namespace is not None else None
        if self._namespace is not None and not self._namespace.startswith('kuberweaver/'):
            raise SecretError('OpenBao namespace must stay in the kuberweaver scope')
        if type(allow_write) is not bool:
            raise SecretError('Explicit boolean required for OpenBao write permission')
        self._allow_write = allow_write
        self._mount = _name(mount)
        if "/" in self._mount:
            raise SecretError("OpenBao mount must be a single name")
        self._endpoint = endpoint.rstrip("/")
        self._token_file = Path(token_file) if token_file is not None else None
        self._token_env = token_env
        self._timeout = timeout
        self._transport = "tls-verified" if parsed.scheme == "https" else "loopback-http-lab"

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        # Never inherit proxy environment variables or forward auth on redirects.
        self._opener = build_opener(ProxyHandler({}), NoRedirect(),
                                    HTTPSHandler(context=ssl.create_default_context()))

    def describe(self) -> dict:
        return {"provider": "openbao-kvv2", "capabilities": ["read", "store"] if self._allow_write else ["read"],
                "approved_names": sorted(self._approved), "persistent_secret_cache": False,
                "transport": self._transport, "can_store": self._allow_write,
                "namespaced_references": self._namespace is not None}

    def _path(self, name: str) -> str:
        _name(name)
        if name in self._approved:
            return self._approved[name]
        if self._namespace is not None:
            return _name(self._namespace + '/' + name)
        raise SecretError('secret name is not approved')

    def store(self, name: str, value: SecretValue) -> None:
        """Opt-in KV write and exact readback; no secret value is returned."""
        import hmac
        import json
        from urllib.request import Request

        if not self._allow_write:
            raise SecretError('OpenBao source is read-only')
        path = self._path(name)
        try:
            raw = value.bytes()
            # Reserve room for JSON escaping (up to six bytes per input byte)
            # and the KV v2 metadata within resolve()'s bounded response.
            if not raw or len(raw) > 128 * 1024:
                raise ValueError
            raw_token = (_protected_read(self._token_file, 16384) if self._token_file is not None
                         else os.environ.get(self._token_env or '', '').encode())
            token = raw_token.decode('ascii').strip()
            if not token or not re.fullmatch(r'[A-Za-z0-9._-]+', token):
                raise ValueError
            request = Request(f'{self._endpoint}/v1/{self._mount}/data/{path}',
                data=json.dumps({'data': {'value': raw.decode('utf-8')}}).encode(),
                headers={'X-Vault-Token': token, 'Content-Type': 'application/json'}, method='POST')
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status not in (200, 204):
                    raise ValueError
            with self.resolve(name) as readback:
                if not hmac.compare_digest(raw, readback.bytes()):
                    raise ValueError
        except Exception:
            raise SecretError('OpenBao write or verification failed; inspect vault state before retry') from None

    @contextmanager
    def resolve(self, name: str) -> Iterator[SecretValue]:
        import json
        from urllib.request import Request

        path = self._path(name)
        secret = None
        try:
            raw_token = (_protected_read(self._token_file, 16384) if self._token_file is not None
                         else os.environ.get(self._token_env or "", "").encode())
            token = raw_token.decode("ascii").strip()
            if not token or not re.fullmatch(r"[A-Za-z0-9._-]+", token):
                raise ValueError
            request = Request(
                f"{self._endpoint}/v1/{self._mount}/data/{path}",
                headers={"X-Vault-Token": token, "Accept": "application/json"}, method="GET")
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError
            payload = json.loads(raw)
            value = payload["data"]["data"]["value"]
            if not isinstance(value, str) or not value:
                raise ValueError
            secret = SecretValue(value.encode("utf-8"))
            # Drop avoidable references before passing control to the caller.
            del raw_token, token, request, response, raw, payload, value
        except Exception:
            raise SecretError("OpenBao secret unavailable (authentication, transport or KV response)") from None
        try:
            yield secret
        finally:
            secret.close()
