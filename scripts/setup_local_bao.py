#!/usr/bin/env python3
"""Opt-in local OpenBao for NEW V2 connections; never changes global bindings."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.secrets import OpenBaoProvider, SecretError, SecretValue, _protected_read

SCOPE = "kuberweaver/connections"
POLICY_NAME = "kuberweaver-v2-connections"
POLICY = 'path "secret/data/kuberweaver/connections/*" { capabilities = ["read", "create", "update"] }\n'
IMAGE = "openbao/openbao@sha256:5046911075e137e154ce49ab051502f4ec7cf367d484ae4a4dce665d9b90561f"
NAME = "kw-openbao-v2"
LABEL = "io.kuberweaver.owner"
OWNER = "kuberweaver-v2-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]


def docker(*args, missing=False):
    try:
        result = subprocess.run(["docker", "--host", "unix:///var/run/docker.sock", *args],
                                capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
        if result.returncode:
            if missing and ("No such container:" in result.stderr or "No such image:" in result.stderr):
                return None
            raise ValueError
        return result.stdout
    except Exception:
        raise SecretError("local OpenBao Docker operation failed") from None


def inspect(kind):
    raw = docker(kind, "inspect", IMAGE if kind == "image" else NAME, missing=True)
    try:
        return json.loads(raw)[0] if raw is not None else None
    except Exception:
        raise SecretError("local OpenBao Docker inspection failed") from None


def private_write(path: Path, value: bytes):
    """Atomic, durable 0600 writes through NOFOLLOW directory descriptors."""
    directory_fd = fd = None
    temporary = None
    try:
        path = safe_path(path)
        if path.exists():
            _protected_read(path)
        directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError
        candidate = ".local-bao-" + secrets.token_hex(16)
        fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        temporary = candidate
        with os.fdopen(fd, "wb") as stream:
            fd = None
            os.fchmod(stream.fileno(), 0o600)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path)
        os.replace(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
        if _protected_read(path) != value:
            raise ValueError
    except Exception:
        raise SecretError("local OpenBao private write or verification failed") from None
    finally:
        if fd is not None:
            os.close(fd)
        if directory_fd is not None:
            try:
                if temporary is not None:
                    os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(directory_fd)


def safe_path(path: Path) -> Path:
    path = Path(path).absolute()
    if (".." in path.parts or "," in str(path) or any(ord(c) < 32 for c in str(path))
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise SecretError("local OpenBao refuses symlink, mount syntax or noncanonical paths")
    return path


def private_dir(path: Path, *, create=True) -> None:
    path = safe_path(path)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    elif not path.is_dir():
        raise SecretError("local OpenBao owned state missing; run setup explicitly")
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise SecretError("local OpenBao directory is not private or owned")


class LocalBao:
    def __init__(self, state_dir: Path, port: int = 19200):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise SecretError("invalid local OpenBao loopback port")
        self.state = safe_path(state_dir)
        self.port = port
        self.endpoint = f"http://127.0.0.1:{port}"
        self.base = self.state / "local-bao"
        self.raft = self.base / "raft"
        self.config = self.base / "config.hcl"
        self.bootstrap_file = self.base / "bootstrap.json"
        self.token_file = self.base / "app-token"
        self.rotation_file = self.base / "pending-revocation.json"
        self.plugin_dir = self.state / "plugin-data" / "openbao-local"
        self.hcl = f'''ui = false
api_addr = "{self.endpoint}"
cluster_addr = "https://127.0.0.1:8201"
storage "raft" {{
  path = "/bao/data"
  node_id = "kw-openbao-v2"
}}
listener "tcp" {{
  address = "0.0.0.0:8200"
  tls_disable = true
}}
'''.encode()

    def api(self, path, *, token=None, payload=None, method=None, allowed=(200, 204)):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Vault-Token"] = token
        try:
            request = Request(self.endpoint + "/v1/" + path, headers=headers,
                              data=json.dumps(payload).encode() if payload is not None else None,
                              method=method or ("POST" if payload is not None else "GET"))
            opener = build_opener(ProxyHandler({}), NoRedirect())
            try:
                response = opener.open(request, timeout=5)
            except HTTPError as error:
                response = error
            with response:
                if response.code not in allowed:
                    raise ValueError
                # A denial body is never evidence or diagnostic output.
                raw = response.read(2 * 1024 * 1024 + 1) if response.code != 403 else b""
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError
            value = json.loads(raw) if raw else {}
            if not isinstance(value, dict):
                raise ValueError
            return value
        except Exception:
            raise SecretError("local OpenBao API refused operation or transport failed") from None

    def bootstrap(self, *, allow_init=False, fresh=False, unseal=True):
        initialized = self.api("sys/init").get("initialized")
        exists = safe_path(self.bootstrap_file).exists()
        if initialized is False:
            if not allow_init or not fresh or exists:
                raise SecretError("refusing initialization without fresh owned storage")
            keys = self.api("sys/init", method="PUT", payload={"secret_shares": 3, "secret_threshold": 2})
            private_write(self.bootstrap_file, json.dumps(keys).encode())
        elif initialized is not True or not exists:
            raise SecretError("local OpenBao bootstrap missing; recover manually, never reinitialize")
        try:
            keys = json.loads(_protected_read(self.bootstrap_file))
            if (not isinstance(keys["root_token"], str) or not keys["root_token"]
                    or not isinstance(keys["keys"], list) or len(keys["keys"]) != 3
                    or any(not isinstance(k, str) or not k for k in keys["keys"])
                    or len(set(keys["keys"])) != 3):
                raise ValueError
        except Exception:
            raise SecretError("local OpenBao bootstrap unavailable or invalid") from None
        status = self.api("sys/seal-status")
        if status.get("sealed") is True and unseal:
            for key in keys["keys"]:
                self.api("sys/unseal", payload={"key": key})
                if self.api("sys/seal-status").get("sealed") is False:
                    break
        if self.api("sys/seal-status").get("sealed") is not False:
            raise SecretError("local OpenBao is sealed")
        return keys["root_token"]

    def token_safe(self, root, token):
        data = self.api("auth/token/lookup", token=root, payload={"token": token}, allowed=(200, 400, 403)).get("data", {})
        return (data.get("policies") == [POLICY_NAME] and not data.get("identity_policies")
                and data.get("orphan") is True and data.get("renewable") is False
                and type(data.get("ttl")) is int and 0 < data["ttl"] <= 604800
                and data.get("explicit_max_ttl") == 604800)

    def resume_rotation(self, root, *, allow_write=True):
        """Settle durable intent; {} records completed, denial-verified revocation."""
        if not safe_path(self.rotation_file).exists():
            return
        private_dir(self.base, create=False)
        try:
            pending = json.loads(_protected_read(self.rotation_file, 65536))
            if pending == {}:
                return
            if not isinstance(pending, dict) or set(pending) != {"old", "new"}:
                raise ValueError
            old, new = pending["old"], pending["new"]
            if (any(not isinstance(value, str) or not value or len(value) > 16384
                    or any(not 33 <= ord(c) <= 126 for c in value) for value in (old, new))
                    or secrets.compare_digest(old, new)):
                raise ValueError
            current = _protected_read(safe_path(self.token_file), 16384).decode("ascii").strip()
            if not (secrets.compare_digest(current, old) or secrets.compare_digest(current, new)):
                raise ValueError
        except Exception:
            raise SecretError("local OpenBao pending rotation unavailable or invalid") from None
        if not allow_write:
            raise SecretError("local OpenBao rotation incomplete; run setup to recover")
        if not self.token_safe(root, new):
            raise SecretError("local OpenBao pending rotation token expired or unsafe")
        if secrets.compare_digest(current, old):
            private_write(self.token_file, new.encode("ascii"))
        # An already revoked token can return 400; only denial readback settles it.
        self.api("auth/token/revoke", token=root, payload={"token": old}, allowed=(200, 204, 400))
        self.api("auth/token/lookup", token=root, payload={"token": old}, allowed=(400, 403))
        private_write(self.rotation_file, b"{}")

    def configure(self, root, *, allow_write=True, rotate_token=False):
        if rotate_token and not allow_write:
            raise SecretError("token rotation is setup-only")
        self.resume_rotation(root, allow_write=allow_write)
        mounts = self.api("sys/mounts", token=root)["data"]
        if "secret/" not in mounts and allow_write:
            self.api("sys/mounts/secret", token=root, payload={"type": "kv", "options": {"version": "2"}})
            mounts = self.api("sys/mounts", token=root)["data"]
        mount = mounts.get("secret/", {})
        if mount.get("type") != "kv" or mount.get("options", {}).get("version") != "2":
            raise SecretError("refusing absent or non-v2 local OpenBao mount")
        policy_path = "sys/policies/acl/" + POLICY_NAME
        if allow_write:
            self.api(policy_path, token=root, payload={"policy": POLICY}, method="PUT")
        if self.api(policy_path, token=root).get("data", {}).get("policy") != POLICY:
            raise SecretError("local OpenBao policy readback differs")
        exists = safe_path(self.token_file).exists()
        old = _protected_read(self.token_file, 16384).decode("ascii").strip() if exists else None
        if not exists or rotate_token:
            if not allow_write:
                raise SecretError("local OpenBao application token missing")
            created = self.api("auth/token/create-orphan", token=root, payload={
                "policies": [POLICY_NAME], "no_default_policy": True, "ttl": "168h",
                "explicit_max_ttl": "168h", "renewable": False, "display_name": POLICY_NAME})
            token = created["auth"]["client_token"]
            if not self.token_safe(root, token):
                raise SecretError("local OpenBao created token is unsafe")
            if rotate_token and old:
                if secrets.compare_digest(old, token):
                    raise SecretError("local OpenBao rotation returned previous token")
                private_write(self.rotation_file, json.dumps({"old": old, "new": token}).encode())
            private_write(self.token_file, token.encode("ascii"))
        token = _protected_read(self.token_file, 16384).decode("ascii").strip()
        if not self.token_safe(root, token):
            raise SecretError("local OpenBao token expired or unsafe; explicit rotation required")
        if rotate_token and old:
            self.resume_rotation(root)

    def verify(self, root, *, write=False):
        self.resume_rotation(root, allow_write=False)
        provider = OpenBaoProvider(self.endpoint, {}, token_file=self.token_file,
                                   namespace=SCOPE, allow_write=True, allow_loopback_http=True)
        if write:
            marker = SecretValue(secrets.token_urlsafe(48).encode())
            try:
                provider.store("setup-check", marker)
            finally:
                marker.close()
        with provider.resolve("setup-check") as value:
            if not value.bytes():
                raise SecretError("local OpenBao setup marker missing")
        token = _protected_read(self.token_file, 16384).decode("ascii").strip()
        if not self.token_safe(root, token):
            raise SecretError("local OpenBao token expired or unsafe")
        checks = {
            "out_of_scope_denied": ("secret/data/kuberweaver/outside", "GET", None),
            "out_of_scope_write_denied": ("secret/data/kuberweaver/outside", "POST", {"data": {"value": "denial-probe"}}),
            "admin_denied": ("sys/mounts", "GET", None),
            "list_denied": ("secret/metadata/" + SCOPE, "LIST", None),
            "delete_denied": ("secret/data/" + SCOPE + "/setup-check", "DELETE", None),
        }
        for path, method, payload in checks.values():
            self.api(path, token=token, method=method, payload=payload, allowed=(403,))
        return {"setup_roundtrip": write, "setup_marker_read": True, "scope": "secret/data/" + SCOPE + "/*",
                "capabilities": ["read", "create", "update"], "credentials_written": False,
                **{name: True for name in checks}}

    def run(self, action="setup", *, rotate_token=False):
        if action not in ("setup", "verify", "unseal") or (rotate_token and action != "setup"):
            raise SecretError("invalid local OpenBao action or token rotation")
        self.prepare(create=action == "setup")
        lock_path = safe_path(self.base / ".setup.lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "rb") as lock:
            info = os.fstat(lock.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid() or info.st_nlink != 1):
                raise SecretError("local OpenBao setup lock is not protected")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise SecretError("local OpenBao setup already running") from None
            fresh = not any(self.raft.iterdir())
            if not fresh and not safe_path(self.bootstrap_file).exists():
                raise SecretError("local OpenBao recovery bootstrap missing; never reinitialize old storage")
            item = self.container(allow_start=action == "setup")
            for attempt in range(30):
                try:
                    self.api("sys/init")
                    break
                except SecretError:
                    if attempt == 29:
                        raise
                    time.sleep(0.1)
            root = self.bootstrap(allow_init=action == "setup", fresh=fresh, unseal=action != "verify")
            self.configure(root, allow_write=action == "setup", rotate_token=rotate_token)
            evidence = self.verify(root, write=action == "setup")
            if action == "setup":
                config = {"endpoint": self.endpoint, "approved": {}, "token_file": str(self.token_file),
                          "namespace": SCOPE, "allow_write": True, "allow_loopback_http": True}
                private_write(self.plugin_dir / "provider.json", json.dumps(config).encode())
            return {**evidence, "container": NAME, "id": item["Id"], "image": IMAGE, "port": self.port,
                    "initialized": self.api("sys/init")["initialized"],
                    "sealed": self.api("sys/seal-status")["sealed"], "provider": "secrets/openbao-local",
                    "global_binding_changed": False, "extension_activation_changed": False,
                    "transport": "loopback-http-lab", "storage": "encrypted-single-node-raft"}

    def prepare(self, *, create=True):
        for path in (self.base, self.raft, self.plugin_dir):
            private_dir(path, create=create)
        for path in (self.config, self.bootstrap_file, self.token_file, self.rotation_file, self.plugin_dir / "provider.json"):
            if safe_path(path).exists():
                _protected_read(path)
        if any(path.is_symlink() for path in self.raft.rglob("*")):
            raise SecretError("local OpenBao refuses symlinks inside owned raft storage")

    def validate(self, item, image):
        config, host = item["Config"], item["HostConfig"]
        mounts = {(m["Type"], m["Source"], m["Destination"], m["RW"]) for m in item["Mounts"]}
        expected = {("bind", str(self.raft), "/bao/data", True),
                    ("bind", str(self.config), "/bao/config/bao.hcl", False)}
        if (config.get("Labels", {}).get(LABEL) != OWNER or item["Name"] != "/" + NAME
                or config["Image"] != IMAGE or item["Image"] != image["Id"]
                or config["User"] != f"{os.getuid()}:{os.getgid()}"
                or config.get("Env") != image.get("Config", {}).get("Env") or host.get("Devices")
                or config["Entrypoint"] != ["/usr/bin/bao"]
                or config["Cmd"] != ["server", "-config=/bao/config/bao.hcl"]
                or host["PortBindings"] != {"8200/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.port)}]}
                or host.get("PublishAllPorts") or host.get("Privileged") or host.get("CapAdd")
                or host.get("CapDrop") != ["ALL"] or host.get("SecurityOpt") != ["no-new-privileges:true"]
                # Docker versions report the built-in default bridge under either alias.
                or host.get("NetworkMode") not in ("default", "bridge")
                or mounts != expected or len(item["Mounts"]) != 2):
            raise SecretError("refusing foreign or incompatible local OpenBao container")

    def container(self, *, allow_start=False):
        image = inspect("image")
        if image is None and allow_start:
            docker("pull", IMAGE)
            image = inspect("image")
        if image is None or IMAGE not in image.get("RepoDigests", []):
            raise SecretError("local OpenBao pinned image unavailable")
        item = inspect("container")
        if item is not None:
            self.validate(item, image)
        if item is None or not item["State"]["Running"]:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", self.port))
                except OSError:
                    raise SecretError("local OpenBao loopback port occupied") from None
        if self.config.exists():
            if _protected_read(self.config) != self.hcl:
                raise SecretError("refusing changed local OpenBao configuration")
        elif item is None and allow_start:
            private_write(self.config, self.hcl)
        else:
            raise SecretError("local OpenBao configuration missing")
        if item is None:
            if not allow_start:
                raise SecretError("local OpenBao container missing; run setup explicitly")
            docker("run", "-d", "--name", NAME, "--label", f"{LABEL}={OWNER}", "--restart", "no",
                   "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges:true", "--publish", f"127.0.0.1:{self.port}:8200",
                   "--mount", f"type=bind,src={self.raft},dst=/bao/data",
                   "--mount", f"type=bind,src={self.config},dst=/bao/config/bao.hcl,readonly",
                   "--entrypoint", "/usr/bin/bao", "--pull", "never", IMAGE, "server", "-config=/bao/config/bao.hcl")
        elif not item["State"]["Running"]:
            if not allow_start:
                raise SecretError("local OpenBao is stopped; run setup explicitly")
            docker("start", NAME)
        item = inspect("container")
        if item is None:
            raise SecretError("local OpenBao container readback missing")
        self.validate(item, image)
        if not item["State"]["Running"]:
            raise SecretError("local OpenBao container is not running")
        return item


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "verify", "unseal"), nargs="?", default="setup")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--port", type=int, default=19200)
    parser.add_argument("--rotate-token", action="store_true", help="Explicitly replace the local application token (setup only)")
    args = parser.parse_args(argv)
    if args.rotate_token and args.action != "setup":
        parser.error("--rotate-token requires setup")
    try:
        result = LocalBao(args.state_dir, args.port).run(args.action, rotate_token=args.rotate_token)
        print(json.dumps(result))
        return 0
    except SecretError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print("Local OpenBao operation failed; no credentials printed. Inspect only owned local resources.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
