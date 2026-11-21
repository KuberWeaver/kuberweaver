"""Interactive blueprint builder: kw blueprints create without --graph-file.

Same in-process boundaries as test_cli (real HTTP server, real vault, real
admission). The wizard stays a thin client: prompts assemble a graph, the API
validates and creates; backend gaps are surfaced, never stubbed client-side.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from test_cli import env, run, _graph_file  # noqa: E402
from kw.main import cli  # noqa: E402

UUID = "564d1a0f-835e-fa97-808d-5065da910771"
KEY = "fd4c8e2e58442577948f3545f90856bf"


def add_manifest(app, plugin_id, category, **extra):
    directory = app.state.plugins_dir / plugin_id
    directory.mkdir(parents=True, exist_ok=True)
    body = {"id": plugin_id, "name": plugin_id, "version": "1.0.0",
            "capability": "live", "engine": "test", "price_monthly": 1,
            "category": category, "executor": {"provider": "test"},
            "schema": {"type": "object", "additionalProperties": False, "properties": {}},
            "defaults": {}}
    body.update(extra)
    (directory / "manifest.yaml").write_text(json.dumps(body))
    return body


def vm_manifest(app):
    """Compute resource with the virtual-machine admission profile (solo job)."""
    return add_manifest(
        app, "test-vm", "compute", admission_profile="virtual-machine",
        schema={"type": "object", "additionalProperties": False,
                "required": ["cpu", "memory_mb", "disk_mb"],
                "properties": {
                    "cpu": {"type": "integer", "minimum": 1, "maximum": 2, "default": 1},
                    "memory_mb": {"type": "integer", "minimum": 128, "maximum": 1024, "default": 256},
                    "disk_mb": {"type": "integer", "minimum": 64, "maximum": 512, "default": 128}}},
        defaults={"cpu": 1, "memory_mb": 256, "disk_mb": 128},
        resources={"cpu": 1, "memory_mb": 256, "disk_gb": 1})


def brick_manifest(app, plugin_id="test-brick"):
    """Workload service targeting an existing guest (target_uuid + ssh_key_id)."""
    return add_manifest(
        app, plugin_id, "monitoring", kind="workload",
        schema={"type": "object", "additionalProperties": False,
                "required": ["target_uuid", "ssh_key_id"],
                "properties": {
                    "target_uuid": {"type": "string",
                                    "pattern": r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"},
                    "ssh_key_id": {"type": "string", "pattern": r"^[0-9a-f]{32}$"},
                    "port": {"type": "integer", "minimum": 1, "maximum": 65535, "default": 10051}}},
        defaults={"port": 10051})


def web_manifest(app, plugin_id="test-web"):
    """Non-workload service: combinable with edges, targets/port admission."""
    return add_manifest(
        app, plugin_id, "web",
        schema={"type": "object", "additionalProperties": False,
                "properties": {
                    "targets": {"type": "array", "items": {"enum": ["app", "branch-client"]},
                                "default": ["app"]},
                    "port": {"type": "integer", "minimum": 18090, "maximum": 18109,
                             "default": 18090}}},
        defaults={"targets": ["app"], "port": 18090})


def wizard(runner, input_text, *args):
    return runner.invoke(cli, ["--json", "blueprints", "create", *args], input=input_text)


def test_wizard_vm_resource_creates_blueprint(env):
    app, runner = env
    vm_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    # name, env(default), resource 1, cpu/mem/disk defaults, create(yes), deploy(no)
    r = wizard(runner, "manual-test\n\n1\n\n\n\n\nn\n")
    assert r.exit_code == 0, r.output + r.stderr
    bp = json.loads(r.stdout)
    assert bp["name"] == "manual-test"
    graph = bp["graph"]
    assert graph["environment"] == "dev"
    assert len(graph["nodes"]) == 1
    node = graph["nodes"][0]
    assert node["plugin_id"] == "test-vm"
    assert node["config"]["cpu"] == 1 and node["config"]["memory_mb"] == 256
    assert graph["edges"] == []
    assert "deploys alone" in r.stderr
    assert json.loads(run(runner, "--json", "deploy", "list").output) == []


def test_wizard_service_deploy_approve(env):
    app, runner = env
    from test_core_jobs import RecordingExecutor, install_executor
    install_executor(app, RecordingExecutor())
    brick_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    key = json.loads(run(runner, "--json", "keys", "generate", "--name", "wizard").output)
    # name, env, no resource, service 1, target_uuid, key menu 1, port default,
    # create(default yes), deploy(yes), approve(yes)
    r = wizard(runner, f"svc-test\n\nnone\n1\n{UUID}\n1\n\n\ny\ny\n")
    assert r.exit_code == 0, r.output + r.stderr
    bp = json.loads(r.stdout)
    node = bp["graph"]["nodes"][0]
    assert node["plugin_id"] == "test-brick"
    assert node["config"]["target_uuid"] == UUID
    assert node["config"]["ssh_key_id"] == key["id"]
    jobs = json.loads(run(runner, "--json", "deploy", "list").output)
    assert len(jobs) == 1
    job = json.loads(run(runner, "--json", "deploy", "get", jobs[0]["id"]).output)
    assert job["approved_by"] == "admin"
    assert job["status"] != "pending_approval"


def test_wizard_two_services_chained(env):
    app, runner = env
    web_manifest(app, "test-web")
    web_manifest(app, "test-web2")
    run(runner, "auth", "login", "--username", "admin")
    # name, env, no resource, services 1,2; web1 targets/port defaults;
    # web2 targets default, port 18091 (avoid same target+port claim); create, no deploy
    r = wizard(runner, "web-pair\n\nnone\n1,2\n\n\n\n18091\n\nn\n")
    assert r.exit_code == 0, r.output + r.stderr
    graph = json.loads(r.stdout)["graph"]
    assert [n["plugin_id"] for n in graph["nodes"]] == ["test-web", "test-web2"]
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["source"] == "test-web-1" and edge["target"] == "test-web2-1"
    assert graph["nodes"][1]["config"]["port"] == 18091


def test_wizard_import_resource_guidance(env):
    app, runner = env
    brick_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    # name, env, import resource (guidance, no node), service 1, uuid, key id,
    # port default, create, no deploy
    r = wizard(runner, f"imported\n\nimport\n1\n{UUID}\n{KEY}\n\n\nn\n")
    assert r.exit_code == 0, r.output + r.stderr
    assert "kw resources create --import" in r.stderr
    bp = json.loads(r.stdout)
    assert bp["graph"]["nodes"][0]["plugin_id"] == "test-brick"


def test_wizard_workload_guard_and_field_reprompt(env):
    app, runner = env
    brick_manifest(app, "test-brick")
    brick_manifest(app, "test-brick2")
    run(runner, "auth", "login", "--username", "admin")
    # two workloads refused -> pick 1; bad uuid re-prompted; then valid answers
    r = wizard(runner, f"guarded\n\nnone\n1,2\n1\nnot-a-uuid\n{UUID}\n{KEY}\n\n\nn\n")
    assert r.exit_code == 0, r.output + r.stderr
    assert "one per job" in r.stderr
    assert "invalid value" in r.stderr
    assert len(json.loads(r.stdout)["graph"]["nodes"]) == 1


def test_wizard_empty_graph_refused(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    r = wizard(runner, "empty\n\nnone\nnone\n")
    assert r.exit_code == 1
    assert "at least one resource or service" in r.stderr
    assert json.loads(run(runner, "--json", "blueprints", "list").output) == []


def test_wizard_name_option_skips_question(env):
    app, runner = env
    vm_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    r = wizard(runner, "\n1\n\n\n\n\nn\n", "--name", "named")
    assert r.exit_code == 0, r.output + r.stderr
    assert "Blueprint name:" not in r.stderr
    assert json.loads(r.stdout)["name"] == "named"


def test_wizard_admission_refusal_creates_nothing(env):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    # sample-service schema has no port bound; admission enforces 18090..18109.
    # name, env, resource 1, targets default, port 99999, cpu/mem/disk defaults,
    # public_cidr/version skipped, no second resource, no services
    r = wizard(runner, "bad-port\n\n1\n\n99999\n\n\n\n\n\nn\nnone\n")
    assert r.exit_code == 1
    assert "admission refused" in r.stderr
    assert json.loads(run(runner, "--json", "blueprints", "list").output) == []
    assert json.loads(run(runner, "--json", "deploy", "list").output) == []


def test_wizard_cancel_creates_nothing(env):
    app, runner = env
    vm_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    r = wizard(runner, "cancel-me\n\n1\n\n\n\nn\n")
    assert r.exit_code == 0
    assert "Cancelled" in r.stderr
    assert r.stdout.strip() == ""
    assert json.loads(run(runner, "--json", "blueprints", "list").output) == []


def test_graph_file_still_requires_name(env, tmp_path):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    r = run(runner, "blueprints", "create", "--graph-file", _graph_file(tmp_path))
    assert r.exit_code == 2


def esxi_manifest(app):
    """VMware-like resource: x-options-source annotations on every infra id."""
    return add_manifest(
        app, "test-esxi", "compute", admission_profile="virtual-machine",
        schema={"type": "object", "additionalProperties": False,
                "required": ["connection_id", "vm_name", "datastore_id",
                             "network_ids", "resource_pool_id", "ssh_key_id"],
                "properties": {
                    "connection_id": {"type": "string", "pattern": "^[0-9a-f]{32}$",
                                      "x-options-source": "connections"},
                    "vm_name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,39}$"},
                    "datastore_id": {"type": "string", "minLength": 1,
                                     "x-options-source": "datastores"},
                    "network_ids": {"type": "array", "minItems": 1, "uniqueItems": True,
                                    "items": {"type": "string", "minLength": 1,
                                              "x-options-source": "networks"}},
                    "resource_pool_id": {"type": "string", "minLength": 1,
                                         "x-options-source": "resource_pools"},
                    "memory_mb": {"type": "integer", "minimum": 512, "maximum": 16384,
                                  "default": 2048},
                    "disk_gb": {"type": "integer", "minimum": 10, "maximum": 100,
                                "default": 20},
                    "ssh_key_id": {"type": "string", "pattern": "^[0-9a-f]{32}$",
                                   "x-options-source": "ssh-keys"}}},
        defaults={"memory_mb": 2048, "disk_gb": 20},
        resources={"cpu": 2, "memory_mb": 2048, "disk_gb": 20})


def add_connection(runner, label="Lab ESXi"):
    return json.loads(run(runner, "--json", "connectors", "add",
                          "--label", label, "--driver", "recording",
                          "--endpoint", "https://192.0.2.10",
                          "--secret-ref", "hypervisors/lab",
                          "--secrets-provider", "connection-secrets").output)


def test_wizard_esxi_options_from_observation(env):
    app, runner = env
    esxi_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    conn = add_connection(runner)
    r = run(runner, "connectors", "discover", conn["id"])
    assert r.exit_code == 0, r.output + r.stderr
    key = json.loads(run(runner, "--json", "keys", "generate", "--name", "esxi").output)
    # name, env, resource 1, connection menu 1, vm_name, datastore 1, networks 1,
    # pool 1, memory/disk defaults, ssh key menu 1, create, no deploy
    r = wizard(runner, "esxi-test\n\n1\n1\ntest-vm-01\n1\n1\n1\n\n\n1\n\nn\n")
    assert r.exit_code == 0, r.output + r.stderr
    config = json.loads(r.stdout)["graph"]["nodes"][0]["config"]
    assert config["connection_id"] == conn["id"]
    assert config["vm_name"] == "test-vm-01"
    assert config["datastore_id"] == "ds-1"
    assert config["network_ids"] == ["network-1"]
    assert config["resource_pool_id"] == "pool-1"
    assert config["ssh_key_id"] == key["id"]
    assert "Read inventory" not in r.stderr  # cached observation, no probe needed


def test_wizard_esxi_discovery_on_demand(env):
    app, runner = env
    esxi_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    conn = add_connection(runner)  # never observed
    key = json.loads(run(runner, "--json", "keys", "generate", "--name", "esxi").output)
    # same path, but the datastore step offers the read-only probe: answer y
    r = wizard(runner, "esxi-ondemand\n\n1\n1\ntest-vm-02\ny\n1\n1\n1\n\n\n1\n\nn\n")
    assert r.exit_code == 0, r.output + r.stderr
    assert "no usable inventory" in r.stderr
    config = json.loads(r.stdout)["graph"]["nodes"][0]["config"]
    assert config["connection_id"] == conn["id"]
    assert config["datastore_id"] == "ds-1"
    assert config["network_ids"] == ["network-1"]
    assert config["resource_pool_id"] == "pool-1"


def test_wizard_esxi_manual_fallback(env):
    app, runner = env
    esxi_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    conn = add_connection(runner)  # never observed
    key = json.loads(run(runner, "--json", "keys", "generate", "--name", "esxi").output)
    # decline the probe once, then type raw ids for datastore/network/pool
    r = wizard(runner, "esxi-manual\n\n1\n1\ntest-vm-03\nn\nds-manual\nnet-manual\npool-manual\n\n\n1\n\nn\n")
    assert r.exit_code == 0, r.output + r.stderr
    assert "no usable inventory" in r.stderr
    config = json.loads(r.stdout)["graph"]["nodes"][0]["config"]
    assert config["connection_id"] == conn["id"]
    assert config["datastore_id"] == "ds-manual"
    assert config["network_ids"] == ["net-manual"]
    assert config["resource_pool_id"] == "pool-manual"


def test_wizard_duplicate_name_warns_and_renames(env, tmp_path):
    app, runner = env
    run(runner, "auth", "login", "--username", "admin")
    run(runner, "--json", "blueprints", "create", "--name", "dup",
        "--graph-file", _graph_file(tmp_path))
    # name dup -> warning, decline -> rename dup2 -> env -> no resource/service
    r = wizard(runner, "dup\nn\ndup2\n\nnone\nnone\n")
    assert r.exit_code == 1  # empty graph refusal, after the rename
    assert "already used" in r.stderr
    listing = json.loads(run(runner, "--json", "blueprints", "list").output)
    assert [b["name"] for b in listing] == ["dup"]


def test_wizard_duplicate_name_anyway(env, tmp_path):
    app, runner = env
    vm_manifest(app)
    run(runner, "auth", "login", "--username", "admin")
    first = json.loads(run(runner, "--json", "blueprints", "create", "--name", "dup",
                           "--graph-file", _graph_file(tmp_path)).output)
    # name dup -> warning, accept -> env -> VM 1 defaults -> create -> no deploy
    r = wizard(runner, "dup\ny\n\n1\n\n\n\n\nn\n")
    assert r.exit_code == 0, r.output + r.stderr
    assert "already used" in r.stderr
    second = json.loads(r.stdout)
    assert second["name"] == "dup" and second["id"] != first["id"]
    listing = json.loads(run(runner, "--json", "blueprints", "list").output)
    assert sorted(b["name"] for b in listing) == ["dup", "dup"]
