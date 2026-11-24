"""Offline ESXi contract tests: real vim objects, transport adapters only.

No hypervisor is contacted. InventoryStub implements the pyVmomi accessor/method
boundary; it does not replace discovery or normalization. TLS tests additionally
exercise the real SOAP adapter/serializer with an in-memory HTTPS boundary.
"""
import hashlib
import importlib.util
import json
import socket
from pathlib import Path

import pytest
from pyVmomi import vim

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "extensions/esxi-runtime"

CONNECTION = {"endpoint": "https://192.0.2.10", "tls_sha256": None}
CREDENTIALS = json.dumps({"username": "offline-reader", "password": "synthetic-password"}).encode()
UUID = "11111111-1111-4111-8111-111111111111"
MARKER = "kuberweaver-job:" + "a" * 32


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real network access is forbidden in ESXi tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


class InventoryStub:
    """Documented vim transport adapter, with a strict read-only method allowlist."""

    def __init__(self):
        self.properties = {}
        self.calls = []
        self.failures = {}
        self.events = []
        self.disconnected = []
        self.connect_kwargs = []
        self.si = vim.ServiceInstance("ServiceInstance", self)
        self.view = vim.view.ContainerView("view-1", self)
        self.manager = vim.view.ViewManager("viewManager", self)
        self.license_manager = vim.LicenseManager("licenseManager", self)
        self.content = vim.ServiceInstanceContent(
            sessionManager=vim.SessionManager("sessionManager", self),
            rootFolder=vim.Folder("root", self), viewManager=self.manager,
            licenseManager=self.license_manager,
            about=vim.AboutInfo(name="VMware ESXi", version="8.0.3", apiType="HostAgent",
                                build="24022510", apiVersion="8.0.3"),
        )
        self.host = self.object(vim.HostSystem, "host-1", name="esxi.offline")
        self.ds = self.object(vim.Datastore, "ds-2", summary=vim.Datastore.Summary(
            name="z-store", capacity=4096, freeSpace=1024, accessible=True))
        self.ds_first = self.object(vim.Datastore, "ds-1", summary=vim.Datastore.Summary(
            name="a-store", capacity=2048, freeSpace=512, accessible=False))
        self.network = self.object(vim.Network, "net-2", name="z-network")
        self.network_first = self.object(vim.Network, "net-1", name="a-network")
        self.pool = self.object(vim.ResourcePool, "pool-1", name="Resources")
        self.vm = self.object(vim.VirtualMachine, "vm-2", name="z-vm", summary=vim.vm.Summary(
            config=vim.vm.Summary.ConfigSummary(name="z-vm", uuid=UUID, numCpu=2,
                                               memorySizeMB=1024, annotation="synthetic-vm-secret"),
            runtime=vim.vm.RuntimeInfo(powerState="poweredOff")),
            config=vim.vm.ConfigInfo(annotation=MARKER))
        self.vm_first = self.object(vim.VirtualMachine, "vm-1", name="a-vm", summary=vim.vm.Summary(
            config=vim.vm.Summary.ConfigSummary(name="a-vm", uuid="22222222-2222-4222-8222-222222222222",
                                               numCpu=1, memorySizeMB=512),
            runtime=vim.vm.RuntimeInfo(powerState="poweredOn")),
            config=vim.vm.ConfigInfo(annotation="operator note, not a portal marker"))
        self.entities = [self.network, self.vm, self.ds, self.pool, self.host,
                         self.vm_first, self.network_first, self.ds_first]
        self.properties[("licenseManager", "licenses")] = [vim.LicenseManager.LicenseInfo(
            editionKey="esxBasic", licenseKey="SYNTHETIC-LICENSE-KEY")]
        self.properties[("view-1", "view")] = self.entities

    def object(self, kind, moid, **properties):
        obj = kind(moid, self)
        for name, value in properties.items():
            self.properties[(moid, name)] = value
        return obj

    def InvokeAccessor(self, obj, info):
        key = (obj._GetMoId(), info.name)
        value = self.properties[key]
        if isinstance(value, BaseException):
            raise value
        return value

    def InvokeMethod(self, obj, info, args):
        self.calls.append((obj._GetMoId(), info.name, args))
        if info.name in self.failures:
            raise self.failures[info.name]
        if info.name == "RetrieveContent":
            return self.content
        if info.name == "CreateContainerView":
            assert args[0] == self.content.rootFolder
            assert set(args[1]) == {vim.HostSystem, vim.Datastore, vim.Network,
                                    vim.ResourcePool, vim.VirtualMachine}
            assert args[2] is True
            return self.view
        if info.name == "Destroy":
            self.events.append("destroy")
            return None
        if info.name in {"Login", "Logout"}:
            self.events.append(info.name.lower())
            return None
        raise AssertionError(f"Non-allowlisted vim method: {info.name}")

    def DropConnections(self):
        self.events.append("drop")

    def connect(self, **kwargs):
        self.connect_kwargs.append(kwargs)
        return self.si

    def disconnect(self, si):
        self.events.append("disconnect")
        self.disconnected.append(si)


def test_discovery_accepts_real_sdk_integer_types():
    from pyVmomi.VmomiSupport import long as sdk_long
    module = load_driver()
    inventory = InventoryStub()
    summary = inventory.properties[("ds-2", "summary")]
    summary.capacity = sdk_long(4096)
    summary.freeSpace = sdk_long(1024)
    result = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect).discover(
        CONNECTION, CREDENTIALS)
    datastore = next(row for row in result["datastores"] if row["id"] == "ds-2")
    assert datastore["capacity_bytes"] == 4096
    assert type(datastore["capacity_bytes"]) is int
    assert type(datastore["free_bytes"]) is int


def test_discovery_normalizes_sorted_whitelist_from_real_vim_objects():
    module = load_driver()
    inventory = InventoryStub()
    result = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect).discover(
        CONNECTION, CREDENTIALS)
    assert result == {
        "server": {"name": "VMware ESXi", "version": "8.0.3", "api_type": "HostAgent",
                   "host_id": "host-1", "build": "24022510", "api_version": "8.0.3",
                   "license_edition": "esxBasic", "write_api_supported": False},
        "datastores": [
            {"id": "ds-1", "name": "a-store", "capacity_bytes": 2048, "free_bytes": 512, "accessible": False},
            {"id": "ds-2", "name": "z-store", "capacity_bytes": 4096, "free_bytes": 1024, "accessible": True}],
        "networks": [{"id": "net-1", "name": "a-network"}, {"id": "net-2", "name": "z-network"}],
        "resource_pools": [{"id": "pool-1", "name": "Resources"}],
        "virtual_machines": [
            {"id": "vm-1", "name": "a-vm", "uuid": "22222222-2222-4222-8222-222222222222",
             "power_state": "poweredOn", "cpu": 1, "memory_mb": 512, "ownership_sha256": None},
            {"id": "vm-2", "name": "z-vm", "uuid": UUID, "power_state": "poweredOff", "cpu": 2,
             "memory_mb": 1024, "ownership_sha256": hashlib.sha256(MARKER.encode()).hexdigest()}],
    }
    assert inventory.events == ["destroy", "disconnect"]
    assert inventory.disconnected == [inventory.si]
    output = json.dumps(result)
    assert "SYNTHETIC-LICENSE-KEY" not in output
    assert "synthetic-vm-secret" not in output
    assert "synthetic-password" not in output
    assert MARKER not in output


def test_default_connection_uses_pki_timeout_and_no_global_session(monkeypatch):
    import ssl
    from pyVim.connect import GetSi

    module = load_driver()
    inventory = InventoryStub()
    options = []
    prior_global = GetSi()

    def stub_factory(**kwargs):
        options.append(kwargs)
        return inventory

    monkeypatch.setattr(module, "SoapStubAdapter", stub_factory)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    result = module.ESXiDriver().discover(CONNECTION, CREDENTIALS)
    assert result["server"]["host_id"] == "host-1"
    (kwargs,) = options
    assert kwargs["host"] == "192.0.2.10"
    assert kwargs["port"] == 443
    assert kwargs["path"] == "/sdk"
    assert kwargs["httpConnectionTimeout"] == 10
    assert kwargs["httpProxyHost"] is None
    assert kwargs["sslContext"].verify_mode == ssl.CERT_REQUIRED
    assert kwargs["sslContext"].check_hostname is True
    assert not kwargs.get("sessionId")
    assert not kwargs.get("customHeaders")
    assert GetSi() is prior_global
    assert inventory.events == ["login", "destroy", "logout", "drop"]
    assert next(args for _, name, args in inventory.calls if name == "Login") == [
        "offline-reader", "synthetic-password", None]


@pytest.mark.parametrize("connection", [
    None, {}, {"endpoint": 42}, {"endpoint": "http://192.0.2.10"},
    {"endpoint": "https://esxi.invalid"}, {"endpoint": "https://192.0.2.10/sdk"},
    {"endpoint": "https://reader:synthetic-password@192.0.2.10"},
    {"endpoint": "https://192.0.2.10?token=synthetic-password"},
    {"endpoint": "https://192.0.2.10#fragment"}, {"endpoint": "https://192.0.2.10/"},
    {"endpoint": "https://192.0.2.10:"}, {"endpoint": "https://192.0.2.10:0"},
    {"endpoint": "https://192.0.2.10:65536"}, {"endpoint": "https://192.0.2.10\n"},
    {"endpoint": "https://[fe80::1%eth0]"}, {**CONNECTION, "tls_sha256": "aa:" * 21 + "aa"},
    {**CONNECTION, "tls_sha256": "a" * 63}, {**CONNECTION, "tls_sha256": "g" * 64},
    {**CONNECTION, "tls_sha256": True}, {**CONNECTION, "tls_sha256": " " + "a" * 64},
], ids=lambda value: type(value).__name__)
def test_invalid_connection_is_rejected_before_connector(connection):
    module = load_driver()
    inventory = InventoryStub()
    with pytest.raises(module.ESXiError, match="^ESXi invalid connection$"):
        module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect).discover(
            connection, CREDENTIALS)
    assert inventory.connect_kwargs == []


@pytest.mark.parametrize("credentials", [
    None, "synthetic-password", b"\xff", b"not-json", b"[]", b"null", b"{}",
    b'{"username":"reader"}', b'{"password":"synthetic-password"}',
    b'{"username":null,"password":"synthetic-password"}',
    b'{"username":"","password":"synthetic-password"}',
    b'{"username":"reader","password":42}', b'{"username":"reader","password":""}',
    b'{"username":"reader","password":"synthetic-password","token":"unexpected"}',
    b'{"username":"first","username":"second","password":"synthetic-password"}',
], ids=lambda value: type(value).__name__)
def test_invalid_credentials_are_rejected_without_connection_or_secret_output(credentials, caplog, capsys):
    import traceback

    module = load_driver()
    inventory = InventoryStub()
    with pytest.raises(module.ESXiError, match="^ESXi invalid credentials$") as caught:
        module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect).discover(
            CONNECTION, credentials)
    assert inventory.connect_kwargs == []
    assert caught.value.__context__ is None
    assert "synthetic-password" not in "".join(traceback.format_exception(caught.value))
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("failure,message", [
    (vim.fault.InvalidLogin(msg="synthetic-password"), "ESXi authentication failed"),
    (TimeoutError("synthetic-password"), "ESXi connection timed out"),
    (ConnectionError("synthetic-password"), "ESXi connection failed"),
    (RuntimeError("synthetic-password"), "ESXi connection failed"),
])
def test_login_errors_are_sanitized_and_transport_is_closed(monkeypatch, failure, message, caplog, capsys):
    import traceback
    from pyVim.connect import GetSi

    module = load_driver()
    inventory = InventoryStub()
    inventory.failures["Login"] = failure
    monkeypatch.setattr(module, "SoapStubAdapter", lambda **kwargs: inventory)
    prior_global = GetSi()
    with pytest.raises(module.ESXiError) as caught:
        module.ESXiDriver().discover(CONNECTION, CREDENTIALS)
    assert str(caught.value) == message
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert "synthetic-password" not in "".join(traceback.format_exception(caught.value))
    assert inventory.events == ["drop"]
    assert GetSi() is prior_global
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("evidence,expected", [
    ([], (None, None)),
    ([vim.LicenseManager.LicenseInfo()], (None, None)),
    ([vim.LicenseManager.LicenseInfo(editionKey="esxEnterprisePlus")], ("esxEnterprisePlus", None)),
    ([vim.LicenseManager.LicenseInfo(editionKey="unknownEdition")], ("unknownEdition", None)),
    ([vim.LicenseManager.LicenseInfo(editionKey="esxBasic"),
      vim.LicenseManager.LicenseInfo(editionKey="esxEnterprisePlus")], (None, None)),
    (vim.fault.NoPermission(msg="SYNTHETIC-LICENSE-KEY"), (None, None)),
    (None, (None, None)),
])
def test_license_uncertainty_never_claims_write_entitlement(evidence, expected):
    module = load_driver()
    inventory = InventoryStub()
    if evidence is None:
        inventory.content.licenseManager = None
    else:
        inventory.properties[("licenseManager", "licenses")] = evidence
    result = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect).discover(
        CONNECTION, CREDENTIALS)
    server = result["server"]
    assert (server["license_edition"], server["write_api_supported"]) == expected
    assert inventory.events == ["destroy", "disconnect"]
    assert "SYNTHETIC-LICENSE-KEY" not in json.dumps(result)


@pytest.mark.parametrize("api_type", ["VirtualCenter", "", "UnexpectedApi"])
def test_only_standalone_host_agents_are_accepted(api_type):
    module = load_driver()
    inventory = InventoryStub()
    inventory.content.about.apiType = api_type
    with pytest.raises(module.ESXiError, match="^ESXi standalone host required$"):
        module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect).discover(
            CONNECTION, CREDENTIALS)
    assert inventory.events == ["disconnect"]
    assert [name for _, name, _ in inventory.calls] == ["RetrieveContent"]


class MemoryHTTPS:
    """HTTP/TLS boundary only; real SDK serializes and deserializes SOAP.

    Each response closes the underlying TLS stream, forcing an authenticated
    reconnect before the next request. Base SSL handshake is replaced with a
    synthetic peer socket; the extension's actual pin wrapper is not mocked.
    """

    def __init__(self, monkeypatch, certificates):
        import io
        import ssl
        import xml.etree.ElementTree as ET
        from pyVmomi import SoapAdapter

        self.certificates = list(certificates)
        self.requests = []
        self.sockets = []
        self.options = []
        boundary = self

        class Peer:
            def __init__(self, certificate):
                self.certificate = certificate
                self.closed = False

            def getpeercert(self, binary_form=False):
                assert binary_form is True
                return self.certificate

            def close(self):
                self.closed = True

        def handshake(context, raw, **kwargs):
            certificate = boundary.certificates[0]
            if len(boundary.certificates) > 1:
                boundary.certificates.pop(0)
            peer = Peer(certificate)
            boundary.sockets.append(peer)
            return peer

        class Response(io.BytesIO):
            status = 200

            def getheader(self, key, default=None):
                return default

        class HTTPSConnection:
            def __init__(self, *, host, port, context, timeout):
                self.host, self.context = host, context
                self.sock = None
                self.response = None
                boundary.options.append((host, port, timeout))

            def connect(self):
                self.sock = self.context.wrap_socket(object(), server_hostname=self.host)

            def request(self, method, url, body, headers):
                if self.sock is None:
                    self.connect()
                assert method == "POST" and url == "/sdk"
                operation = list(list(ET.fromstring(body))[0])[0].tag.rsplit("}", 1)[-1]
                boundary.requests.append((operation, body))
                if operation == "RetrieveServiceContent":
                    payload = '<RetrieveServiceContentResponse><returnval><rootFolder type="Folder">root</rootFolder><sessionManager type="SessionManager">SessionManager</sessionManager><about><name>VMware ESXi</name><version>8.0.3</version><apiType>HostAgent</apiType></about></returnval></RetrieveServiceContentResponse>'
                elif operation == "Login":
                    payload = '<LoginResponse><returnval><key>synthetic-session</key><userName>offline-reader</userName></returnval></LoginResponse>'
                elif operation == "Logout":
                    payload = '<LogoutResponse />'
                else:
                    raise AssertionError("Unexpected SOAP operation")
                self.response = Response(('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns="urn:vim25"><soapenv:Body>' + payload + '</soapenv:Body></soapenv:Envelope>').encode())

            def getresponse(self):
                self.close()
                return self.response

            def close(self):
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None

        monkeypatch.setattr(ssl.SSLContext, "wrap_socket", handshake)
        monkeypatch.setattr(SoapAdapter, "HTTPSConnection", HTTPSConnection)


@pytest.mark.parametrize("change_at_login", [False, True])
def test_pin_mismatch_blocks_credentials_on_the_actual_soap_connection(monkeypatch, change_at_login):
    import hashlib

    module = load_driver()
    trusted_der = b"synthetic trusted certificate DER"
    wrong_der = b"synthetic other certificate DER"
    certificates = [trusted_der, wrong_der] if change_at_login else [wrong_der]
    transport = MemoryHTTPS(monkeypatch, certificates)
    pin = hashlib.sha256(trusted_der).hexdigest()
    with pytest.raises(module.ESXiError, match="^ESXi TLS verification failed$"):
        module.ESXiDriver().discover({**CONNECTION, "tls_sha256": pin}, CREDENTIALS)
    assert [operation for operation, _ in transport.requests] == (
        ["RetrieveServiceContent"] if change_at_login else [])
    assert all(b"synthetic-password" not in body for _, body in transport.requests)
    assert all(peer.closed for peer in transport.sockets)


@pytest.fixture(scope="module")
def self_signed_certificate():
    import os
    import ssl
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="esxi-tls-", dir=os.environ["TMPDIR"]) as folder:
        cert, key = Path(folder) / "cert.pem", Path(folder) / "key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=offline-esxi-test",
        ], check=True, capture_output=True, timeout=15)
        der = ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="ascii"))
        yield cert, key, der


@pytest.mark.parametrize("correct_pin", [False, True])
def test_real_local_self_signed_tls_checks_pin_before_login_bytes(monkeypatch, self_signed_certificate, correct_pin):
    """Real OpenSSL TLS over AF_UNIX socketpair: no IP connection or network."""
    import hashlib
    import ssl
    import threading

    module = load_driver()
    cert, key, der = self_signed_certificate
    options = []
    inventory = InventoryStub()

    def factory(**kwargs):
        options.append(kwargs)
        return inventory

    monkeypatch.setattr(module, "SoapStubAdapter", factory)
    pin = hashlib.sha256(der).hexdigest().upper() if correct_pin else "00" * 32
    module.ESXiDriver().discover({**CONNECTION, "tls_sha256": pin}, CREDENTIALS)
    client_context = options[0]["sslContext"]
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_raw, server_raw = socket.socketpair()
    client_raw.settimeout(3)
    server_raw.settimeout(3)
    received = []
    server_errors = []

    def server():
        try:
            with server_context.wrap_socket(server_raw, server_side=True) as stream:
                received.append(stream.recv(1024))
        except (ssl.SSLError, OSError) as error:
            server_errors.append(type(error).__name__)
        finally:
            server_raw.close()

    thread = threading.Thread(target=server)
    thread.start()
    try:
        if correct_pin:
            with client_context.wrap_socket(client_raw, server_hostname="192.0.2.10") as stream:
                stream.sendall(b"synthetic-login-bytes")
        else:
            with pytest.raises(ssl.SSLCertVerificationError):
                client_context.wrap_socket(client_raw, server_hostname="192.0.2.10")
    finally:
        client_raw.close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    if correct_pin:
        assert received == [b"synthetic-login-bytes"]
        assert not server_errors
    else:
        assert not any(received)


def corrupt_inventory(inventory, case):
    """Corrupt the transport evidence; never patch the discovery implementation."""
    if case == "no-host":
        inventory.entities.remove(inventory.host)
    elif case == "two-hosts":
        inventory.entities.append(inventory.object(vim.HostSystem, "host-2", name="other"))
    elif case == "duplicate-id":
        inventory.entities.append(inventory.ds)
    elif case == "duplicate-uuid":
        inventory.properties[("vm-1", "summary")].config.uuid = UUID
    elif case == "unknown-entity":
        inventory.entities.append(vim.Folder("unexpected", inventory))
    elif case == "empty-id":
        inventory.entities.append(inventory.object(vim.Network, "", name="invalid"))
    elif case == "missing-about":
        inventory.content.__dict__["about"] = None
    elif case == "empty-server-name":
        inventory.content.about.name = ""
    elif case == "empty-version":
        inventory.content.about.version = ""
    elif case == "missing-ds-summary":
        inventory.properties[("ds-2", "summary")] = None
    elif case.startswith("ds-"):
        field, value = {
            "ds-free-negative": ("freeSpace", -1), "ds-free-overflow": ("freeSpace", 4097),
            "ds-capacity-negative": ("capacity", -1), "ds-empty-name": ("name", ""),
            "ds-accessible-invalid": ("accessible", "false"),
        }[case]
        # Simulate malformed SDK data without substituting non-vim summaries.
        inventory.properties[("ds-2", "summary")].__dict__[field] = value
    elif case == "empty-network-name":
        inventory.properties[("net-1", "name")] = ""
    elif case == "missing-vm-summary":
        inventory.properties[("vm-2", "summary")] = None
    else:
        summary = inventory.properties[("vm-2", "summary")]
        if case == "missing-vm-config":
            summary.__dict__["config"] = None
        elif case == "missing-vm-runtime":
            summary.__dict__["runtime"] = None
        elif case == "vm-name-mismatch":
            summary.config.name = "other-name"
        elif case == "vm-unknown-power":
            summary.runtime.powerState = "unknown"
        else:
            field, value = {
                "vm-missing-uuid": ("uuid", None), "vm-invalid-uuid": ("uuid", "not-a-uuid"),
                "vm-zero-cpu": ("numCpu", 0), "vm-bool-cpu": ("numCpu", True),
                "vm-missing-memory": ("memorySizeMB", None),
            }[case]
            summary.config.__dict__[field] = value


@pytest.mark.parametrize("case", [
    "no-host", "two-hosts", "duplicate-id", "duplicate-uuid", "unknown-entity", "empty-id",
    "missing-about", "empty-server-name", "empty-version", "missing-ds-summary",
    "ds-free-negative", "ds-free-overflow", "ds-capacity-negative", "ds-empty-name",
    "ds-accessible-invalid", "empty-network-name", "missing-vm-summary", "missing-vm-config",
    "missing-vm-runtime", "vm-name-mismatch", "vm-unknown-power", "vm-missing-uuid",
    "vm-invalid-uuid", "vm-zero-cpu", "vm-bool-cpu", "vm-missing-memory",
])
def test_inconsistent_inventory_is_rejected_without_fabricated_objects(case):
    module = load_driver()
    inventory = InventoryStub()
    corrupt_inventory(inventory, case)
    with pytest.raises(module.ESXiError, match="^ESXi inconsistent inventory$"):
        module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect).discover(
            CONNECTION, CREDENTIALS)
    assert inventory.events == (["disconnect"] if case == "missing-about" else ["destroy", "disconnect"])


def test_infrastructure_dependency_is_pinned_without_changing_core_requirements():
    path = ROOT / "requirements-infrastructure.txt"
    assert path.is_file(), "Optional infrastructure requirements are missing"
    requirements = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    assert requirements == ["pyvmomi==9.1.1.0"]


def test_vm_readback_is_a_fresh_read_by_exact_uuid_not_name():
    module = load_driver()
    inventory = InventoryStub()
    driver = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect)
    initial = driver.discover(CONNECTION, CREDENTIALS)["virtual_machines"][1]
    inventory.properties[("vm-2", "name")] = "renamed-vm"
    inventory.properties[("vm-2", "summary")].config.name = "renamed-vm"
    inventory.properties[("vm-2", "summary")].runtime.powerState = "suspended"
    assert callable(getattr(driver, "read_resource", None)), "Independent VM readback is missing"
    current = driver.read_resource(CONNECTION, CREDENTIALS, UUID)
    assert current == {**initial, "name": "renamed-vm", "power_state": "suspended"}
    assert len(inventory.connect_kwargs) == 2
    assert inventory.events == ["destroy", "disconnect", "destroy", "disconnect"]
    with pytest.raises(module.ESXiError, match="^ESXi resource not found$"):
        driver.read_resource(CONNECTION, CREDENTIALS, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    with pytest.raises(module.ESXiError, match="^ESXi invalid resource identity$"):
        driver.read_resource(CONNECTION, CREDENTIALS, "renamed-vm")
    assert len(inventory.connect_kwargs) == 3


@pytest.mark.parametrize("stage", ["content", "view-create", "view-read", "vm-read", "destroy", "disconnect"])
def test_failure_after_connection_always_closes_scoped_objects(stage, caplog, capsys):
    module = load_driver()
    inventory = InventoryStub()
    failure = RuntimeError("synthetic-password must not escape")
    disconnector = inventory.disconnect
    if stage in {"content", "view-create", "destroy"}:
        inventory.failures[{"content": "RetrieveContent", "view-create": "CreateContainerView",
                            "destroy": "Destroy"}[stage]] = failure
    elif stage == "view-read":
        inventory.properties[("view-1", "view")] = failure
    elif stage == "vm-read":
        inventory.properties[("vm-2", "summary")] = failure
    else:
        def disconnector(si):
            inventory.disconnect(si)
            raise failure
    with pytest.raises(module.ESXiError, match="^ESXi connection failed$") as caught:
        module.ESXiDriver(connector=inventory.connect, disconnector=disconnector).discover(
            CONNECTION, CREDENTIALS)
    assert caught.value.__context__ is None
    assert inventory.disconnected == [inventory.si]
    methods = [name for _, name, _ in inventory.calls]
    assert ("Destroy" in methods) is (stage not in {"content", "view-create"})
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


def test_inventory_timeout_is_sanitized_and_view_is_destroyed():
    module = load_driver()
    inventory = InventoryStub()
    inventory.properties[("vm-2", "summary")] = TimeoutError("synthetic-password")
    with pytest.raises(module.ESXiError, match="^ESXi connection timed out$"):
        module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect).discover(
            CONNECTION, CREDENTIALS)
    assert inventory.events == ["destroy", "disconnect"]


@pytest.mark.parametrize("endpoint,host,port", [
    ("https://[2001:db8::1]:8443", "2001:db8::1", 8443),
    ("https://127.0.0.1", "127.0.0.1", 443), ("https://[::1]", "::1", 443),
])
def test_ip_origins_keep_pki_verification_even_for_loopback(monkeypatch, endpoint, host, port):
    import ssl

    module = load_driver()
    inventory = InventoryStub()
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return inventory

    monkeypatch.setattr(module, "SoapStubAdapter", factory)
    module.ESXiDriver().discover({"endpoint": endpoint, "tls_sha256": None}, CREDENTIALS)
    assert (options[0]["host"], options[0]["port"]) == (host, port)
    assert options[0]["sslContext"].verify_mode == ssl.CERT_REQUIRED
    assert options[0]["sslContext"].check_hostname is True


def test_matching_pin_allows_real_soap_login_without_exporting_session(monkeypatch):
    import hashlib
    from pyVim.connect import GetSi

    module = load_driver()
    certificate = b"synthetic trusted certificate DER"
    transport = MemoryHTTPS(monkeypatch, [certificate])
    connection = {**CONNECTION, "tls_sha256": hashlib.sha256(certificate).hexdigest().upper()}
    prior_global = GetSi()
    si = module._connect(connection=connection, username="offline-reader", password="synthetic-password")
    try:
        assert isinstance(si, vim.ServiceInstance)
        assert [operation for operation, _ in transport.requests] == ["RetrieveServiceContent", "Login"]
        assert b"synthetic-password" in transport.requests[-1][1]
        assert all(options == ("192.0.2.10", 443, 10) for options in transport.options)
        assert GetSi() is prior_global
    finally:
        module.Disconnect(si)
    assert all(peer.closed for peer in transport.sockets)
    assert [operation for operation, _ in transport.requests][-1] == "Logout"


def test_readback_preserves_uuid_case_exactly():
    module = load_driver()
    inventory = InventoryStub()
    identity = "ABCDEFAB-1234-4ABC-8ABC-ABCDEFABCDEF"
    inventory.properties[("vm-2", "summary")].config.uuid = identity
    driver = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect)
    assert driver.read_resource(CONNECTION, CREDENTIALS, identity)["uuid"] == identity
    with pytest.raises(module.ESXiError, match="^ESXi resource not found$"):
        driver.read_resource(CONNECTION, CREDENTIALS, identity.lower())


def test_readback_reports_ownership_digest_and_never_the_annotation():
    module = load_driver()
    inventory = InventoryStub()
    driver = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect)
    current = driver.read_resource(CONNECTION, CREDENTIALS, UUID)
    assert current["ownership_sha256"] == hashlib.sha256(MARKER.encode()).hexdigest()
    assert MARKER not in json.dumps(current)
    foreign = driver.read_resource(CONNECTION, CREDENTIALS, "22222222-2222-4222-8222-222222222222")
    assert foreign["ownership_sha256"] is None
    # A changed annotation changes the proof; a cleared one removes it.
    inventory.properties[("vm-2", "config")].annotation = "kuberweaver-job:" + "b" * 32
    changed = driver.read_resource(CONNECTION, CREDENTIALS, UUID)
    assert changed["ownership_sha256"] == hashlib.sha256(("kuberweaver-job:" + "b" * 32).encode()).hexdigest()
    inventory.properties[("vm-2", "config")].annotation = ""
    assert driver.read_resource(CONNECTION, CREDENTIALS, UUID)["ownership_sha256"] is None


def test_inventory_order_is_stable_when_remote_order_changes():
    module = load_driver()
    inventory = InventoryStub()
    driver = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect)
    initial = driver.discover(CONNECTION, CREDENTIALS)
    inventory.entities.reverse()
    assert driver.discover(CONNECTION, CREDENTIALS) == initial


def test_disconnect_does_not_clear_an_unrelated_sdk_global_session(monkeypatch):
    import pyVim.connect as sdk_connect

    module = load_driver()
    inventory = InventoryStub()
    unrelated = InventoryStub().si
    # pyVmomi compares ServiceInstances by moId, ignoring their backing stub.
    assert unrelated == inventory.si and unrelated is not inventory.si
    monkeypatch.setattr(sdk_connect, "_si", unrelated)
    monkeypatch.setattr(module, "SoapStubAdapter", lambda **kwargs: inventory)
    module.ESXiDriver().discover(CONNECTION, CREDENTIALS)
    assert sdk_connect.GetSi() is unrelated
    assert inventory.events == ["login", "destroy", "logout", "drop"]


def load_driver():
    assert (PACKAGE / "module.py").is_file(), "ESXi extension is not implemented"
    spec = importlib.util.spec_from_file_location("esxi_contract_driver", PACKAGE / "module.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_is_optional_at_registration(monkeypatch):
    import builtins

    original = builtins.__import__

    def without_vmware(name, *args, **kwargs):
        if name.split(".")[0] in {"pyVim", "pyVmomi"}:
            raise ImportError("synthetic missing dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_vmware)
    module = load_driver()
    registrations = []

    class Context:
        def register(self, *args):
            registrations.append(args)

    module.register(Context())
    kind, name, driver, metadata = registrations.pop()
    assert (kind, name) == ("hypervisor", "esxi")
    assert metadata["available"] is False
    with pytest.raises(module.ESXiError, match="^ESXi dependency unavailable$"):
        driver.discover({}, b"{}")


def test_extension_registers_esxi_hypervisor_with_discovery_and_destruction():
    from backend.extensions import ExtensionRegistry

    registry = ExtensionRegistry()
    reports = registry.load_directory(PACKAGE.parent, {"esxi-runtime"})
    assert any(r.get("id") == "esxi-runtime" and r["status"] == "loaded" for r in reports)
    assert registry.list("hypervisor") == [{
        "kind": "hypervisor", "name": "esxi", "label": "VMware ESXi",
        "read_only": False, "available": True,
    }]
    driver = registry.get("hypervisor", "esxi")
    assert callable(driver.discover)
    assert callable(driver.destroy_resource)
    for mutation in ("execute", "rollback", "create", "delete", "update", "power_on"):
        assert not hasattr(driver, mutation)


class DestroyInventoryStub(InventoryStub):
    """Adds the destroy method boundary on top of the read-only transport stub."""

    def __init__(self, power="poweredOff"):
        super().__init__()
        self.destroyed = []
        self.powered_off = []
        self.task = self.object(vim.Task, "task-1", info=vim.TaskInfo(state="success"))
        self.properties[("vm-2", "summary")].runtime.powerState = power
        self.vm_view = vim.view.ContainerView("vm-view", self)
        self.properties[("vm-view", "view")] = [
            entity for entity in self.entities if isinstance(entity, vim.VirtualMachine)]

    def InvokeMethod(self, obj, info, args):
        if info.name == "CreateContainerView" and set(args[1]) == {vim.VirtualMachine}:
            return self.vm_view
        if isinstance(obj, vim.VirtualMachine) and info.name == "PowerOff":
            self.powered_off.append(obj._GetMoId())
            self.properties[(obj._GetMoId(), "summary")].runtime.powerState = "poweredOff"
            return self.task
        if isinstance(obj, vim.VirtualMachine) and info.name == "Destroy":
            self.destroyed.append(obj._GetMoId())
            self.properties[("vm-view", "view")] = [
                entity for entity in self.properties[("vm-view", "view")]
                if entity._GetMoId() != obj._GetMoId()]
            return self.task
        return super().InvokeMethod(obj, info, args)


def test_destroy_resource_powers_off_destroys_and_confirms_absence():
    module = load_driver()
    inventory = DestroyInventoryStub(power="poweredOn")
    driver = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect)
    assert driver.destroy_resource(CONNECTION, CREDENTIALS, UUID, MARKER) is True
    assert inventory.powered_off == ["vm-2"]
    assert inventory.destroyed == ["vm-2"]
    assert inventory.disconnected == [inventory.si]


def test_destroy_resource_refuses_a_foreign_marker_without_touching_the_vm():
    module = load_driver()
    inventory = DestroyInventoryStub()
    driver = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect)
    with pytest.raises(module.ESXiError):
        driver.destroy_resource(CONNECTION, CREDENTIALS, UUID, "kuberweaver-job:" + "b" * 32)
    assert inventory.destroyed == []
    assert inventory.powered_off == []


def test_destroy_resource_refuses_an_unknown_uuid():
    module = load_driver()
    inventory = DestroyInventoryStub()
    driver = module.ESXiDriver(connector=inventory.connect, disconnector=inventory.disconnect)
    with pytest.raises(module.ESXiError):
        driver.destroy_resource(CONNECTION, CREDENTIALS,
                                "99999999-9999-4999-8999-999999999999", MARKER)
    assert inventory.destroyed == []
