"""Standalone ESXi extension: read-only discovery plus owned-VM destruction."""
import hashlib
import hmac
import ipaddress
import json
import re
import ssl
import time
from urllib.parse import urlsplit

try:
    from pyVmomi import SoapStubAdapter, vim
except ImportError:
    SoapStubAdapter = vim = None


class ESXiError(RuntimeError):
    """Public error containing only a static, non-sensitive message."""


class _UnsupportedServer(Exception):
    pass


class _InvalidInventory(Exception):
    pass


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise _InvalidInventory
    return value


def _integer(value, minimum=0):
    # The SOAP deserializer returns VmomiSupport.long (an int subclass).
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _InvalidInventory
    return int(value)


def _vm(entity):
    summary = entity.summary
    if (not isinstance(summary, vim.vm.Summary) or
            not isinstance(summary.config, vim.vm.Summary.ConfigSummary) or
            not isinstance(summary.runtime, vim.vm.RuntimeInfo)):
        raise _InvalidInventory
    name = _text(entity.name)
    identity = _text(summary.config.uuid)
    if (re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identity) is None or
            name != summary.config.name or
            summary.runtime.powerState not in {"poweredOn", "poweredOff", "suspended"}):
        raise _InvalidInventory
    # The annotation is free-form operator text, so only a portal marker leaves
    # the driver, and only as a digest an adoption can compare against.
    config = entity.config
    annotation = None if config is None else config.annotation
    if annotation is not None and not isinstance(annotation, str):
        raise _InvalidInventory
    marker = annotation if annotation and annotation.startswith("kuberweaver-job:") else ""
    return {"id": _text(entity._GetMoId()), "name": name, "uuid": identity,
            "power_state": summary.runtime.powerState,
            "cpu": _integer(summary.config.numCpu, 1),
            "memory_mb": _integer(summary.config.memorySizeMB, 1),
            "ownership_sha256": hashlib.sha256(marker.encode("utf-8")).hexdigest() if marker else None}


class _PinnedTLSContext(ssl.SSLContext):
    """Pin-only TLS: verify the actual peer before HTTP, on EVERY TLS socket.

    pyVmomi 9.1.1's thumbprint fallback skips checks after successful PKI and
    mutates its context after failure. Do not use that fallback or a separate
    probe followed by insecure login. CERT_NONE here is confined to this
    private context whose wrap_socket never returns an unpinned connection.
    """

    def __new__(cls, pin):
        return super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)

    def __init__(self, pin):
        self.check_hostname = False
        self.verify_mode = ssl.CERT_NONE
        self._pin = pin.lower()

    def wrap_socket(self, *args, **kwargs):
        stream = super().wrap_socket(*args, **kwargs)
        try:
            actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(actual, self._pin):
                raise ssl.SSLCertVerificationError("ESXi certificate pin mismatch")
            return stream
        except BaseException:
            stream.close()
            raise


def _validate_connection(connection):
    try:
        if not isinstance(connection, dict):
            raise ValueError
        raw = connection["endpoint"]
        if not isinstance(raw, str) or any(ord(c) <= 32 or ord(c) == 127 for c in raw):
            raise ValueError
        endpoint = urlsplit(raw)
        if (not raw.startswith("https://") or endpoint.scheme != "https" or
                endpoint.username is not None or endpoint.password is not None or
                endpoint.path or "?" in raw or "#" in raw or "%" in raw or
                endpoint.netloc.endswith(":")):
            raise ValueError
        ipaddress.ip_address(endpoint.hostname)
        if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
            raise ValueError
        pin = connection.get("tls_sha256")
        if pin is not None and (not isinstance(pin, str) or re.fullmatch(r"[0-9a-fA-F]{64}", pin) is None):
            raise ValueError
        return
    except (KeyError, TypeError, ValueError):
        pass
    raise ESXiError("ESXi invalid connection")


def _credentials(credentials):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        if not isinstance(credentials, bytes):
            raise ValueError
        auth = json.loads(credentials.decode("utf-8"), object_pairs_hook=unique_object)
        if (not isinstance(auth, dict) or set(auth) != {"username", "password"} or
                any(not isinstance(value, str) or not value for value in auth.values())):
            raise ValueError
        return auth
    except (TypeError, ValueError):
        pass
    raise ESXiError("ESXi invalid credentials")


def _license(content):
    try:
        manager = content.licenseManager
        licenses = manager.licenses if manager is not None else []
        if len(licenses) != 1:
            return None, None
        edition = licenses[0].editionKey
        if not isinstance(edition, str) or not edition.strip():
            return None, None
        return edition, False if edition == "esxBasic" else None
    except vim.fault.NoPermission:
        return None, None


def Disconnect(si):
    """Scoped SDK disconnect, without touching pyVim.connect's global session.

    Its Disconnect compares managed IDs rather than object identity and can
    otherwise clear an unrelated caller's saved ServiceInstance.
    """
    try:
        si.RetrieveContent().sessionManager.Logout()
    finally:
        si._stub.DropConnections()


def _connect(*, connection, username, password):
    endpoint = urlsplit(connection["endpoint"])
    pin = connection.get("tls_sha256")
    context = _PinnedTLSContext(pin) if pin is not None else ssl.create_default_context()
    # API 5.5 is the SDK's default backwards-compatible SOAP baseline. A direct
    # stub avoids SmartConnect's unbounded/unpinned version probe and SetSi global.
    stub = SoapStubAdapter(host=endpoint.hostname, port=endpoint.port or 443,
        path="/sdk", version="vim.version.version9", httpProxyHost=None,
        httpConnectionTimeout=10, sslContext=context)
    si = vim.ServiceInstance("ServiceInstance", stub)
    try:
        si.RetrieveContent().sessionManager.Login(username, password, None)
        return si
    except BaseException:
        stub.DropConnections()
        raise


class ESXiDriver:
    """Per-call sessions; injectable connection boundaries for offline adapters."""

    def __init__(self, *, connector=None, disconnector=None):
        self._connector = connector or _connect
        self._disconnector = disconnector or Disconnect

    def discover(self, connection: dict, credentials: bytes) -> dict:
        if vim is None:
            raise ESXiError("ESXi dependency unavailable")
        _validate_connection(connection)
        auth = _credentials(credentials)
        try:
            return self._discover(connection, auth)
        except _UnsupportedServer:
            message = "ESXi standalone host required"
        except _InvalidInventory:
            message = "ESXi inconsistent inventory"
        except vim.fault.InvalidLogin:
            message = "ESXi authentication failed"
        except TimeoutError:
            message = "ESXi connection timed out"
        except ssl.SSLError:
            message = "ESXi TLS verification failed"
        except Exception:
            message = "ESXi connection failed"
        # Outside the handler: neither raw exceptions nor their messages escape.
        raise ESXiError(message)

    def read_resource(self, connection: dict, credentials: bytes, identity: str) -> dict:
        """Read a fresh snapshot by the exact, case-sensitive exported BIOS UUID.

        No name fallback, identity normalization, session export or mutations.
        """
        if (not isinstance(identity, str) or
                re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identity) is None):
            raise ESXiError("ESXi invalid resource identity")
        for row in self.discover(connection, credentials)["virtual_machines"]:
            if row["uuid"] == identity:
                return row
        raise ESXiError("ESXi resource not found")

    def destroy_resource(self, connection: dict, credentials: bytes, identity: str, marker: str) -> bool:
        """Destroy one owned VM and confirm it is gone; True only then.

        Ownership is re-proven against the live annotation before any mutation,
        the VM is powered off first, and absence is re-read after the destroy
        task. Any other outcome raises so the portal records manual review.
        """
        if vim is None:
            raise ESXiError("ESXi dependency unavailable")
        if (not isinstance(identity, str) or
                re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identity) is None):
            raise ESXiError("ESXi invalid resource identity")
        if not isinstance(marker, str) or re.fullmatch(r"kuberweaver-job:[0-9a-f]{32}", marker) is None:
            raise ESXiError("ESXi invalid ownership marker")
        _validate_connection(connection)
        auth = _credentials(credentials)
        try:
            return self._destroy(connection, auth, identity, marker)
        except ESXiError:
            raise
        except vim.fault.InvalidLogin:
            raise ESXiError("ESXi authentication failed") from None
        except TimeoutError:
            raise ESXiError("ESXi connection timed out") from None
        except ssl.SSLError:
            raise ESXiError("ESXi TLS verification failed") from None
        except Exception:
            raise ESXiError("ESXi destruction failed") from None

    def _destroy(self, connection, auth, identity, marker):
        si = self._connector(connection=connection, username=auth["username"], password=auth["password"])
        try:
            content = si.RetrieveContent()
            view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
            try:
                target = next((vm for vm in view.view if vm.summary.config.uuid == identity), None)
                if target is None:
                    raise ESXiError("ESXi resource not found")
                annotation = target.config.annotation if target.config is not None else None
                live_marker = (annotation if isinstance(annotation, str)
                               and annotation.startswith("kuberweaver-job:") else "")
                if hashlib.sha256(live_marker.encode("utf-8")).hexdigest() != \
                        hashlib.sha256(marker.encode("utf-8")).hexdigest():
                    raise ESXiError("ESXi ownership marker mismatch")
                if target.summary.runtime.powerState == "poweredOn":
                    self._wait_task(target.PowerOffVM_Task())
                self._wait_task(target.Destroy_Task())
                if any(vm.summary.config.uuid == identity for vm in view.view):
                    raise ESXiError("ESXi destruction not confirmed")
                return True
            finally:
                view.Destroy()
        finally:
            self._disconnector(si)

    @staticmethod
    def _wait_task(task):
        deadline = time.monotonic() + 180
        while task.info.state not in ("success", "error"):
            if time.monotonic() > deadline:
                raise ESXiError("ESXi task timed out")
            time.sleep(0.5)
        if task.info.state != "success":
            raise ESXiError("ESXi task failed")

    def _discover(self, connection, auth):
        si = self._connector(connection=connection, username=auth["username"], password=auth["password"])
        try:
            content = si.RetrieveContent()
            if not isinstance(content.about, vim.AboutInfo):
                raise _InvalidInventory
            if content.about.apiType != "HostAgent":
                raise _UnsupportedServer
            view = content.viewManager.CreateContainerView(content.rootFolder, [
                vim.HostSystem, vim.Datastore, vim.Network, vim.ResourcePool, vim.VirtualMachine], True)
            try:
                return self._inventory(content, view.view)
            finally:
                view.Destroy()
        finally:
            self._disconnector(si)

    @staticmethod
    def _inventory(content, entities):
        about = content.about
        edition, write_supported = _license(content)
        server = {"name": _text(about.name), "version": _text(about.version), "api_type": about.apiType,
                  "build": about.build, "api_version": about.apiVersion,
                  "license_edition": edition, "write_api_supported": write_supported}
        result = {"server": server, "datastores": [], "networks": [],
                  "resource_pools": [], "virtual_machines": []}
        seen, uuids = set(), set()
        host_count = 0
        for entity in entities:
            if not isinstance(entity, (vim.HostSystem, vim.Datastore, vim.Network,
                                       vim.ResourcePool, vim.VirtualMachine)):
                raise _InvalidInventory
            identity = _text(entity._GetMoId())
            key = (type(entity), identity)
            if key in seen:
                raise _InvalidInventory
            seen.add(key)
            if isinstance(entity, vim.HostSystem):
                host_count += 1
                server["host_id"] = identity
            elif isinstance(entity, vim.Datastore):
                summary = entity.summary
                if not isinstance(summary, vim.Datastore.Summary):
                    raise _InvalidInventory
                capacity, free = _integer(summary.capacity), _integer(summary.freeSpace)
                if free > capacity or type(summary.accessible) is not bool:
                    raise _InvalidInventory
                result["datastores"].append({"id": identity, "name": _text(summary.name),
                    "capacity_bytes": capacity, "free_bytes": free,
                    "accessible": summary.accessible})
            elif isinstance(entity, vim.Network):
                result["networks"].append({"id": identity, "name": _text(entity.name)})
            elif isinstance(entity, vim.ResourcePool):
                result["resource_pools"].append({"id": identity, "name": _text(entity.name)})
            elif isinstance(entity, vim.VirtualMachine):
                row = _vm(entity)
                if row["uuid"].lower() in uuids:
                    raise _InvalidInventory
                uuids.add(row["uuid"].lower())
                result["virtual_machines"].append(row)
        if host_count != 1:
            raise _InvalidInventory
        for key in ("datastores", "networks", "resource_pools", "virtual_machines"):
            result[key].sort(key=lambda row: (row["name"], row["id"]))
        return result


def register(ctx):
    ctx.register("hypervisor", "esxi", ESXiDriver(),
                 {"label": "VMware ESXi", "read_only": False, "available": vim is not None})
