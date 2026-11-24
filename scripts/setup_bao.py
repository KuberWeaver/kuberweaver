#!/usr/bin/env python3
"""Provision only the owned, persistent OpenBao lab. Never print credentials.

`setup` creates/unseals and verifies; `unseal` restores availability after restart;
`tunnel` stays in the foreground for a supervisor; `verify` never provisions.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, HTTPRedirectHandler, build_opener, Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.secrets import OpenBaoProvider, SecretError, _protected_read

POLICY = 'path "secret/data/kuberweaver/*" { capabilities = ["read"] }\n'
ENDPOINT = "http://127.0.0.1:18200"
SSH_CONFIG = "/home/ubu/Desktop/infra-platform-lab/ssh_config"
SSH = ["ssh", "-F", SSH_CONFIG, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
LABEL = "io.kuberweaver.owner"

# This root-owned guest script is supplied over SSH stdin, never copied into a
# global service directory. Its output contains configuration/evidence only.
REMOTE_SETUP = r"""
import json, os, pathlib, subprocess, time
LABEL = "io.kuberweaver.owner"
def run(args):
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("owned OpenBao Docker operation failed")
    return p.stdout.strip()
def inspect(kind, name):
    p = subprocess.run(["docker", kind, "inspect", name], capture_output=True, text=True)
    return json.loads(p.stdout)[0] if p.returncode == 0 else None
before = json.loads(run(["docker", "ps", "--all", "--format", "json"]).replace("\n", ",").join(["[", "]"]))
base = pathlib.Path("/opt/kuberweaver-secrets")
if base.is_symlink():
    raise RuntimeError("owned path must not be symlink")
base.mkdir(mode=0o700, exist_ok=True)
os.chmod(base, 0o700)
config = base / "bao.hcl"
if config.is_symlink():
    raise RuntimeError("owned configuration must not be symlink")
config.write_text('''
ui = false
# OpenBao 2.x removed mlock; do not grant IPC_LOCK or use ignored options.
api_addr = "http://127.0.0.1:18200"
cluster_addr = "https://127.0.0.1:8201"
storage "raft" {
  path = "/bao/data"
  node_id = "kw-openbao"
}
listener "tcp" {
  address = "0.0.0.0:8200"
  tls_disable = true
}
''')
os.chmod(config, 0o644)
run(["docker", "pull", "openbao/openbao:latest"])
pulled = inspect("image", "openbao/openbao:latest")
digest = pulled["RepoDigests"][0]
volume = inspect("volume", "kw-openbao-data")
if volume is None:
    run(["docker", "volume", "create", "--label", LABEL + "=secrets", "kw-openbao-data"])
    volume = inspect("volume", "kw-openbao-data")
if (volume.get("Labels") or {}).get(LABEL) != "secrets":
    raise RuntimeError("refusing unowned volume")
os.chown(volume["Mountpoint"], 100, 1000)
os.chmod(volume["Mountpoint"], 0o700)
container = inspect("container", "kw-openbao")
if container is None:
    run(["docker", "run", "-d", "--name", "kw-openbao", "--label", LABEL + "=secrets",
         "--restart", "unless-stopped", "--user", "100:1000", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges:true", "--publish", "127.0.0.1:18200:8200",
         "--mount", "type=volume,src=kw-openbao-data,dst=/bao/data",
         "--mount", "type=bind,src=/opt/kuberweaver-secrets/bao.hcl,dst=/bao/config/bao.hcl,readonly",
         digest, "server", "-config=/bao/config/bao.hcl"])
else:
    if (container["Config"].get("Labels") or {}).get(LABEL) != "secrets":
        raise RuntimeError("refusing unowned container")
    if container["HostConfig"]["PortBindings"] != {"8200/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18200"}]}:
        raise RuntimeError("owned container has unexpected port exposure")
    run(["docker", "start", "kw-openbao"])
container = inspect("container", "kw-openbao")
image = inspect("image", container["Image"])
for _ in range(100):
    check = subprocess.run(["docker", "exec", "kw-openbao", "bao", "version"], capture_output=True, text=True)
    if check.returncode == 0:
        version = check.stdout.strip()
        break
    time.sleep(0.1)
else:
    raise RuntimeError("owned OpenBao container did not start")
after = json.loads(run(["docker", "ps", "--all", "--format", "json"]).replace("\n", ",").join(["[", "]"]))
def unrelated(rows):
    return {row["Names"]: (row["ID"], row["State"]) for row in rows if row["Names"] != "kw-openbao"}
print(json.dumps({"image_digest": image["RepoDigests"][0], "pulled_image_digest": digest,
    "version": version, "container_id": container["Id"], "container": "kw-openbao",
    "volume": "kw-openbao-data", "storage": "encrypted-single-node-raft", "uid": container["Config"]["User"],
    "volume_mode": oct(os.stat(volume["Mountpoint"]).st_mode & 0o777),
    "volume_owner": str(os.stat(volume["Mountpoint"]).st_uid) + ":" + str(os.stat(volume["Mountpoint"]).st_gid),
    "cap_drop": container["HostConfig"]["CapDrop"],
    "security_opt": container["HostConfig"]["SecurityOpt"],
    "port_bindings": container["HostConfig"]["PortBindings"],
    "restart_policy": container["HostConfig"]["RestartPolicy"]["Name"],
    "command": container["Config"]["Cmd"], "memory_locking": "removed-upstream-no-IPC_LOCK",
    "other_containers_unchanged": unrelated(before) == unrelated(after)}))
"""


def private_json(path: Path, payload: dict) -> None:
    """Atomic 0600 write; credential files must never be printed or Git-tracked."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SecretError("refusing symlink credential destination")
    fd, temporary = tempfile.mkstemp(prefix=".bao-private-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_token(path: Path, token: str) -> None:
    # The application consumes a bare token, never the bootstrap response.
    fd, temporary = tempfile.mkstemp(prefix=".bao-private-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise SecretError("refusing symlink credential destination")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def api(path: str, *, token: str | None = None, payload=None, method: str | None = None,
        allowed=(200, 204)):
    """Root-only bootstrap transport: fixed loopback endpoint and no proxies."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    request = Request(ENDPOINT + "/v1/" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers, method=method or ("POST" if payload is not None else "GET"))
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            body = response.read(2 * 1024 * 1024)
            status = response.status
    except HTTPError as error:
        status = error.code
        if status not in allowed:
            raise SecretError(f"OpenBao bootstrap API refused operation (HTTP {status})") from None
        body = error.read(2 * 1024 * 1024)
        error.close()
    except Exception:
        raise SecretError("OpenBao bootstrap transport failed") from None
    if status not in allowed:
        raise SecretError("unexpected OpenBao API status")
    if not body:
        return {}
    try:
        return json.loads(body)
    except Exception:
        raise SecretError("invalid OpenBao bootstrap response") from None


def tunnel_command():
    return SSH + ["-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3", "-N", "-L",
        "127.0.0.1:18200:127.0.0.1:18200", "infra-operations"]


def check_loopback_port(port: int = 18200) -> None:
    with socket.socket() as probe:
        # ssh also sets REUSEADDR; a closed prior tunnel's TIME_WAIT is not a listener.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise SecretError("local 18200 occupied; use supervised tunnel with --external-tunnel") from None


@contextmanager
def tunnel(external: bool = False):
    process = None
    if not external:
        # Never silently trust a pre-existing listener or use an untracked nohup.
        check_loopback_port()
        process = subprocess.Popen(tunnel_command(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process is not None and process.poll() is not None:
                raise SecretError("SSH loopback tunnel failed")
            try:
                api("sys/seal-status")
                break
            except SecretError:
                time.sleep(0.1)
        else:
            raise SecretError("OpenBao did not become reachable")
        yield
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def bootstrap(data: Path, rotate_token: bool = False) -> str:
    bootstrap_path = data / "bao-bootstrap.json"
    initialized = api("sys/init")["initialized"]
    if not initialized:
        if bootstrap_path.exists():
            raise SecretError("bootstrap exists but server is uninitialized; refusing replacement")
        # One local custodian holding all shares is a lab-only recovery model.
        keys = api("sys/init", payload={"secret_shares": 3, "secret_threshold": 2}, method="PUT")
        private_json(bootstrap_path, keys)
        del keys
    keys = json.loads(_protected_read(bootstrap_path))
    status = api("sys/seal-status")
    if status["sealed"]:
        for key in keys["keys"]:
            status = api("sys/unseal", payload={"key": key})
            if not status["sealed"]:
                break
    if status["sealed"]:
        raise SecretError("OpenBao remained sealed")
    root = keys["root_token"]
    mounts = api("sys/mounts", token=root)["data"]
    if "secret/" not in mounts:
        api("sys/mounts/secret", token=root, payload={"type": "kv", "options": {"version": "2"}})
    mounts = api("sys/mounts", token=root)["data"]
    if mounts["secret/"]["type"] != "kv" or mounts["secret/"]["options"].get("version") != "2":
        raise SecretError("refusing unexpected secret mount")
    api("sys/policies/acl/kuberweaver-read", token=root, payload={"policy": POLICY}, method="PUT")
    policy_readback = api("sys/policies/acl/kuberweaver-read", token=root)
    if policy_readback["data"]["policy"] != POLICY:
        raise SecretError("OpenBao policy readback differs")
    if not (data / "bao-token").exists() or rotate_token:
        if rotate_token and (data / "bao-token").exists():
            old = _protected_read(data / "bao-token", 16384).decode().strip()
            api("auth/token/revoke", token=root, payload={"token": old})
        created = api("auth/token/create-orphan", token=root, payload={
            "policies": ["kuberweaver-read"], "no_default_policy": True,
            "ttl": "168h", "renewable": False, "display_name": "kuberweaver-lab-read"})
        private_token(data / "bao-token", created["auth"]["client_token"])
    return root


def backend_plaintext_check(value: bytes) -> dict:
    """Observe demo plaintext absent from owned storage/logs; not a crypto audit."""
    import shlex
    remote = '''
import json, pathlib, subprocess, sys
needle = sys.stdin.buffer.read(1024 * 1024 + 1)
assert needle and len(needle) <= 1024 * 1024
volume = json.loads(subprocess.check_output(["docker", "volume", "inspect", "kw-openbao-data"]))[0]
assert volume["Labels"].get("io.kuberweaver.owner") == "secrets"
root = pathlib.Path(volume["Mountpoint"]).resolve()
files = 0
found = False
for path in root.rglob("*"):
    if not path.is_file() or not path.resolve().is_relative_to(root):
        continue
    files += 1
    with path.open("rb") as stream:
        overlap = b""
        while block := stream.read(1024 * 1024):
            block = overlap + block
            found |= needle in block
            overlap = block[-len(needle):]
logs = subprocess.run(["docker", "logs", "kw-openbao"], capture_output=True)
assert logs.returncode == 0
print(json.dumps({"demo_plaintext_absent_from_backend_files": not found,
    "demo_plaintext_absent_from_container_logs": needle not in logs.stdout + logs.stderr,
    "backend_files_examined": files}))
'''
    result = subprocess.run(SSH + ["infra-operations", "sudo -n python3 -c " + shlex.quote(remote)],
        input=value, capture_output=True, timeout=60)
    if result.returncode:
        raise SecretError("owned backend plaintext observation failed")
    evidence = json.loads(result.stdout)
    if (not evidence["demo_plaintext_absent_from_backend_files"]
            or not evidence["demo_plaintext_absent_from_container_logs"]
            or not evidence["backend_files_examined"]):
        raise SecretError("owned backend plaintext observation failed")
    return evidence


def verify(data: Path, root: str | None = None) -> dict:
    token = _protected_read(data / "bao-token", 16384).decode().strip()
    provider = OpenBaoProvider(ENDPOINT, {"demo": "kuberweaver/demo"},
        token_file=data / "bao-token", allow_loopback_http=True)
    # Setup only: write a random demonstration value; verification never persists it.
    expected = None
    if root:
        expected = secrets.token_urlsafe(48)
        api("secret/data/kuberweaver/demo", token=root, payload={"data": {"value": expected}})
    with provider.resolve("demo") as value:
        if expected is not None and not secrets.compare_digest(value.bytes(), expected.encode()):
            raise SecretError("demo readback differs")
        if not value.bytes():
            raise SecretError("demo value empty")
        storage_observation = backend_plaintext_check(value.bytes())
    checks = {
        "out_of_scope_read_denied": ("secret/data/other/demo", None, "GET"),
        "app_write_denied": ("secret/data/kuberweaver/demo", {"data": {"value": "denial-probe"}}, "POST"),
        "mount_admin_denied": ("sys/mounts", None, "GET"),
        "metadata_list_denied": ("secret/metadata/kuberweaver", None, "LIST"),
    }
    results = {}
    for name, (path, payload, method) in checks.items():
        try:
            # Allow only 403: a 200 is a hard failure, not an accepted denial.
            api(path, token=token, payload=payload, method=method, allowed=(403,))
        except SecretError:
            raise SecretError("least-privilege verification failed") from None
        results[name] = True
    return {"demo_roundtrip": True, "app_scope": "secret/data/kuberweaver/*",
            "app_capabilities": ["read"], "token_ttl_requested": "168h",
            "descriptor": provider.describe(), **results, **storage_observation,
            "initialized": api("sys/init")["initialized"],
            "sealed": api("sys/seal-status")["sealed"]}


def restart_check(data: Path) -> dict:
    """Verify an existing secret survives an owned container restart, without reseeding."""
    provider = OpenBaoProvider(ENDPOINT, {"demo": "kuberweaver/demo"},
        token_file=data / "bao-token", allow_loopback_http=True)
    with provider.resolve("demo") as value:
        before = bytearray(value.bytes())
    try:
        remote = '''
import json, subprocess
item = json.loads(subprocess.check_output(["docker", "inspect", "kw-openbao"]))[0]
assert item["Config"]["Labels"].get("io.kuberweaver.owner") == "secrets"
subprocess.run(["docker", "restart", "kw-openbao"], check=True, stdout=subprocess.DEVNULL)
'''
        result = subprocess.run(SSH + ["infra-operations", "sudo -n python3 -"],
            input=remote, text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise SecretError("owned OpenBao restart failed")
        for _ in range(100):
            try:
                sealed = api("sys/seal-status")["sealed"]
                break
            except SecretError:
                time.sleep(0.1)
        else:
            raise SecretError("OpenBao unavailable after restart")
        if not sealed:
            raise SecretError("expected manual-unseal state after restart")
        bootstrap(data)
        with provider.resolve("demo") as value:
            equal = secrets.compare_digest(value.bytes(), before)
        if not equal:
            raise SecretError("persisted demo changed after restart")
        return {"restart_persistence_verified": True, "sealed_after_restart": True,
                "manual_unseal_verified": True}
    finally:
        before[:] = b"\0" * len(before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "unseal", "verify", "restart-check", "tunnel"), nargs="?", default="setup")
    parser.add_argument("--external-tunnel", action="store_true")
    parser.add_argument("--rotate-token", action="store_true")
    args = parser.parse_args()
    if args.action == "tunnel":
        os.execvp("ssh", tunnel_command())
    data = ROOT / "data"
    if data.is_symlink():
        raise SecretError("refusing symlink data directory")
    data.mkdir(mode=0o700, exist_ok=True)
    os.chmod(data, 0o700)
    lock_path = data / ".bao-setup.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        evidence_path = data / "evidence" / "openbao.json"
        infrastructure = {}
        if args.action == "setup":
            result = subprocess.run(SSH + ["infra-operations", "sudo -n python3 -"],
                input=REMOTE_SETUP, text=True, capture_output=True, timeout=300)
            if result.returncode:
                raise SecretError("owned guest OpenBao setup failed; inspect only its container/config")
            infrastructure = json.loads(result.stdout)
            if not infrastructure["other_containers_unchanged"]:
                raise SecretError("unrelated container state changed during setup")
        elif evidence_path.exists():
            infrastructure = json.loads(evidence_path.read_text())
        with tunnel(args.external_tunnel):
            root = bootstrap(data, args.rotate_token) if args.action in ("setup", "unseal") else None
            persistence = restart_check(data) if args.action == "restart-check" else {}
            evidence = {**infrastructure, **verify(data, root if args.action == "setup" else None), **persistence,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "guest": "infra-operations", "guest_address": "192.168.122.10",
                "endpoint": ENDPOINT, "transport": "loopback HTTP inside managed SSH tunnel",
                "production_ready": False, "auto_unseal": False, "ha": False}
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
            # Read back precisely the evidence just written, never credential files.
            if json.loads(evidence_path.read_text()) != evidence:
                raise SecretError("evidence readback differs")
        print(json.dumps({"ok": True, "evidence": str(evidence_path),
                          "container": "kw-openbao", "tunnel": "external" if args.external_tunnel else "closed"}))


if __name__ == "__main__":
    try:
        main()
    except SecretError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Do not stringify unknown exceptions or dump locals containing keys.
        print("OpenBao operation failed; no credentials printed. Check owned resources and recovery documentation.", file=sys.stderr)
        sys.exit(1)
