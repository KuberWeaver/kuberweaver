"""Interactive blueprint builder: compose a deployment graph without a JSON file.

Walks a human through name -> resources -> linked services -> dry-run validation
-> blueprint creation -> optional deployment request and approval. Every step
goes through the same API routes as the rest of the CLI; the server stays
authoritative (admission is re-checked with /api/plans/validate before anything
is created). Backend gaps are surfaced as the real governed commands, never
simulated client-side.

Admission rules reflected as guidance (the server still decides):
- virtual-machine plugins deploy alone: one node, no edges, dev;
- workload bricks deploy one per job; services chain onto an existing guest
  through target_uuid + ssh_key_id, not through a same-graph VM node.
"""
from __future__ import annotations

import copy

import click
from jsonschema import Draft202012Validator

from kw.client import ApiError
from kw.commands.common import client

RESOURCE_CATEGORIES = {"compute", "network", "security", "infrastructure"}
ENVIRONMENTS = ["dev", "staging", "production"]
OMIT = object()


def say(text: str = "") -> None:
    click.echo(text, err=True)


def _fmt_default(value) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _type_hint(spec: dict) -> str:
    kind = spec.get("type", "string")
    if "enum" in spec:
        hint = "|".join(str(choice) for choice in spec["enum"])
    elif kind == "array":
        items = spec.get("items", {})
        if "enum" in items:
            hint = "list of " + "|".join(str(choice) for choice in items["enum"])
        else:
            hint = f"list of {items.get('type', 'string')} (comma-separated)"
    elif kind == "boolean":
        hint = "yes/no"
    else:
        hint = kind
    if "minimum" in spec or "maximum" in spec:
        hint += f", {spec.get('minimum', '')}..{spec.get('maximum', '')}"
    return hint


def _parse(raw: str, spec: dict):
    kind = spec.get("type", "string")
    if "enum" in spec and raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(spec["enum"]):
            return spec["enum"][index - 1]
    if kind == "integer":
        return int(raw)
    if kind == "number":
        return float(raw)
    if kind == "boolean":
        lowered = raw.lower()
        if lowered in ("y", "yes", "true", "1"):
            return True
        if lowered in ("n", "no", "false", "0"):
            return False
        raise ValueError("answer yes or no")
    if kind == "array":
        items = spec.get("items", {})
        return [_parse(part.strip(), items) for part in raw.split(",") if part.strip()]
    return raw


def prompt_field(key: str, spec: dict, default, required: bool):
    label = f"  {spec.get('title') or key} ({_type_hint(spec)})"
    if default is not None:
        label += f" [default: {_fmt_default(default)}]"
    elif required:
        label += " [required]"
    while True:
        raw = click.prompt(label, default="", show_default=False, err=True).strip()
        if raw == "":
            if default is not None:
                return copy.deepcopy(default)
            if required:
                say("  this field is required")
                continue
            return OMIT
        try:
            value = _parse(raw, spec)
        except ValueError as err:
            say(f"  invalid value: {err}")
            continue
        errors = list(Draft202012Validator(spec).iter_errors(value))
        if errors:
            say(f"  invalid value: {errors[0].message}")
            continue
        return value


def prompt_config(plugin: dict, c, ctx: dict) -> dict:
    schema = plugin.get("schema", {})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    defaults = {key: spec["default"] for key, spec in props.items()
                if isinstance(spec, dict) and "default" in spec}
    defaults.update(plugin.get("defaults", {}))
    say(f"{plugin.get('name', plugin['id'])} configuration (Enter accepts the default):")
    for attempt in range(3):
        config = {}
        for key, spec in props.items():
            source = spec.get("x-options-source")
            if not source and spec.get("type") == "array":
                source = spec.get("items", {}).get("x-options-source")
            if not source and key == "connection_id":
                source = "connections"
            if not source and key == "ssh_key_id":
                source = "ssh-keys"
            if not source and key == "target_uuid" and ctx.get("connection_id"):
                source = "virtual_machines"
            if source:
                value = prompt_options(key, spec, source, c, ctx,
                                       defaults.get(key), key in required)
                if source == "connections" and value is not OMIT:
                    ctx["connection_id"] = value
            else:
                if key == "target_uuid":
                    say("  target_uuid: UUID of an existing guest (already deployed or")
                    say("  imported); after a deployment find it with 'kw inventory list'.")
                value = prompt_field(key, spec, defaults.get(key), key in required)
            if value is not OMIT:
                config[key] = value
        errors = list(Draft202012Validator(schema).iter_errors(config))
        if not errors:
            return config
        for error in errors:
            say(f"  config error at {'.'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}")
    raise click.ClickException("configuration still invalid after 3 attempts; nothing created")


def connection_entries(c) -> list[dict]:
    try:
        connections = c.get("/api/connections") or []
    except ApiError:
        connections = []
    return [{"value": conn["id"],
             "label": f"{conn.get('label', conn['id'])} ({conn.get('driver', '?')}, {conn.get('endpoint', '')})"}
            for conn in connections if conn.get("enabled")]


def ssh_key_entries(c) -> list[dict]:
    try:
        keys = c.get("/api/ssh-keys") or []
    except ApiError:
        keys = []
    return [{"value": key["id"], "label": f"{key['id']}  {key.get('name', '')}"}
            for key in keys if key.get("enabled")]


def observation_entries(c, connection_id: str, source: str, ctx: dict) -> list[dict] | None:
    """Options from the connector's cached observation, never invented.

    Usable means enabled connection, reachable observation at the current
    revision. Otherwise the evidence state is shown and a read-only discovery
    is offered; declining falls back to manual entry for the rest of the run.
    """
    try:
        conn = c.get(f"/api/connections/{connection_id}")
    except ApiError as err:
        say(f"  cannot read connection: {err.detail}")
        return None
    obs = conn.get("observation")
    usable = (conn.get("enabled") and obs and obs.get("status") == "reachable"
              and obs.get("revision") == conn.get("revision") and obs.get("inventory"))
    if not usable:
        if ctx.get("discovery_declined"):
            return None
        if not obs:
            state = "never observed"
        else:
            state = f"{obs.get('status')} at {obs.get('observed_at')}"
            if obs.get("revision") != conn.get("revision"):
                state += " (stale revision)"
        say(f"  no usable inventory for {conn.get('label', connection_id)}: {state}")
        if not conn.get("enabled"):
            say("  connection is disabled; enter values manually")
            ctx["discovery_declined"] = True
            return None
        if not click.confirm("  Read inventory from the connector now? (read-only probe)",
                             default=True, err=True):
            ctx["discovery_declined"] = True
            return None
        try:
            obs = c.post(f"/api/connections/{connection_id}/discover")
        except ApiError as err:
            say(f"  discovery refused: {err.detail}")
            ctx["discovery_declined"] = True
            return None
        if obs.get("status") != "reachable" or not obs.get("inventory"):
            say(f"  discovery failed: {obs.get('error') or 'unreachable'}")
            ctx["discovery_declined"] = True
            return None
    inventory = obs.get("inventory") or {}
    rows = inventory.get(source) or []
    if source == "datastores":
        entries = []
        for row in rows:
            free_gib = row.get("free_bytes", 0) / (1024 ** 3)
            label = f"{row.get('name', row['id'])}  [{row['id']}] {free_gib:.1f} GiB free"
            blocked = row.get("accessible") is not True
            if blocked:
                label += " (inaccessible)"
            entries.append({"value": row["id"], "label": label, "blocked": blocked})
        return entries
    if source == "virtual_machines":
        return [{"value": row.get("uuid") or row["id"],
                 "label": f"{row.get('name', row['id'])}  [{row.get('uuid') or row['id']}] {row.get('power_state', '')}"}
                for row in rows]
    return [{"value": row["id"],
             "label": f"{row['name']}  [{row['id']}]" if row.get("name") and row["name"] != row["id"] else row["id"]}
            for row in rows]


def choose(key: str, entries: list[dict], spec: dict):
    """Numbered menu; accepts numbers (comma-separated for arrays) or raw values."""
    for index, entry in enumerate(entries, 1):
        say(f"    {index}) {entry['label']}")
    hint = "numbers comma-separated" if spec.get("type") == "array" else "number"
    while True:
        raw = click.prompt(f"  {spec.get('title') or key} ({hint}, or raw value)",
                           default="", show_default=False, err=True).strip()
        if raw == "":
            return OMIT
        parts = raw.split(",") if spec.get("type") == "array" else [raw]
        values = []
        blocked = False
        for part in parts:
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(entries):
                entry = entries[int(part) - 1]
                if entry.get("blocked"):
                    say(f"  cannot select: {entry['label']}")
                    blocked = True
                    break
                values.append(entry["value"])
            else:
                values.append(part)
        if blocked:
            continue
        value = values if spec.get("type") == "array" else values[0]
        errors = list(Draft202012Validator(spec).iter_errors(value))
        if errors:
            say(f"  invalid value: {errors[0].message}")
            continue
        return value


def resolve_entries(key: str, source: str, c, ctx: dict) -> list[dict] | None:
    if source == "connections":
        entries = connection_entries(c)
        if not entries:
            say("  no enabled connector; add one with 'kw connectors add'")
        return entries
    if source == "ssh-keys":
        entries = ssh_key_entries(c)
        if not entries:
            say("  no SSH key yet; generate one with 'kw keys generate --name <name>'")
        return entries
    connection_id = ctx.get("connection_id")
    if not isinstance(connection_id, str) or not connection_id:
        entries = connection_entries(c)
        if not entries:
            say("  no enabled connector; enter values manually")
            return None
        say(f"  {key}: pick the connector to read options from")
        picked = choose("connector", entries, {"type": "string", "pattern": "^[0-9a-f]{32}$"})
        if picked is OMIT or not isinstance(picked, str):
            return None
        ctx["connection_id"] = connection_id = picked
    return observation_entries(c, connection_id, source, ctx)


def prompt_options(key: str, spec: dict, source: str, c, ctx: dict, default, required: bool):
    while True:
        entries = resolve_entries(key, source, c, ctx)
        if entries:
            value = choose(key, entries, spec)
            if value is not OMIT:
                return value
        else:
            return prompt_field(key, spec, default, required)
        if default is not None:
            return copy.deepcopy(default)
        if required:
            say("  this field is required")
            continue
        return OMIT


def show_menu(plugins: list[dict]) -> None:
    for index, plugin in enumerate(plugins, 1):
        price = plugin.get("price_monthly", 0)
        say(f"  {index}) {plugin['id']:<18} {plugin.get('name', '')} "
            f"[{plugin.get('engine', '?')}, {plugin.get('capability', 'planned')}] {price:g} EUR/mo")


def pick_plugins(plugins: list[dict], answer: str) -> list[dict] | None:
    """Resolve a menu answer to plugins; None means the answer was invalid."""
    chosen = []
    for part in answer.split(","):
        part = part.strip()
        if not part.isdigit() or not 1 <= int(part) <= len(plugins):
            return None
        plugin = plugins[int(part) - 1]
        if plugin.get("capability") != "live":
            say(f"  {plugin['id']} is planned and cannot execute yet; pick a live plugin")
            return None
        if plugin not in chosen:
            chosen.append(plugin)
    return chosen


IMPORT_RESOURCE = """Importing existing OpenTofu code is a separate governed flow (pinned saved
plan, human admin), not a blueprint node:
  kw resources create --import --environment <env> --stack <name> \\
      --revision <git-commit> --bundle-path <path> --bundle-sha256 <sha256>
  (add --dry-run to verify the pins without applying)
Once applied, services target the imported guest in their own blueprint with
target_uuid=<guest uuid> and ssh_key_id=<key id>. Continuing without a new
resource node."""

IMPORT_SERVICE = """Custom service code is not imported through the wizard: it becomes a catalog
plugin (manifest + pinned role, see plugins/) or a studio artifact
('kw modules artifacts create'). Continuing without services."""

VM_SOLO = """{name} is a virtual-machine job: it deploys alone (one node, no edges).
Services are added afterwards in their own blueprint, targeting this guest
with target_uuid and ssh_key_id. Skipping the service step."""


def run_wizard(ctx, name: str | None) -> dict | None:
    c = client(ctx)
    try:
        return _wizard(ctx, c, name)
    finally:
        c.close()


def _wizard(ctx, c, name: str | None) -> dict | None:
    say("Blueprint builder (Ctrl-C aborts; nothing is created before the final confirm)")
    if not name:
        name = click.prompt("Blueprint name", default="", show_default=False, err=True).strip()
    while True:
        if not name:
            raise click.ClickException("a name is required; nothing created")
        try:
            existing = c.get("/api/blueprints") or []
        except ApiError:
            existing = []
        match = next((b for b in existing if b.get("name") == name), None)
        if not match:
            break
        say(f"  name '{name}' already used by blueprint {match['id']} "
            f"(created {match.get('created_at', '?')}); ids stay distinct")
        if click.confirm("  Create another blueprint with this name?", default=False, err=True):
            break
        name = click.prompt("Blueprint name", default="", show_default=False, err=True).strip()
    environment = click.prompt("Environment", type=click.Choice(ENVIRONMENTS),
                               default="dev", show_default=True, err=True)
    catalog = c.get("/api/plugins") or []
    resources = sorted((p for p in catalog if p.get("category") in RESOURCE_CATEGORIES),
                       key=lambda p: (p.get("category", ""), p["id"]))
    services = sorted((p for p in catalog if p.get("category") not in RESOURCE_CATEGORIES),
                      key=lambda p: (p.get("category", ""), p["id"]))

    nodes: list[dict] = []
    edges: list[dict] = []
    counters: dict[str, int] = {}
    resource_nodes: list[dict] = []
    vm_solo = False
    options_ctx: dict = {}

    def add_node(plugin: dict, config: dict, column: int) -> dict:
        counters[plugin["id"]] = counters.get(plugin["id"], 0) + 1
        node = {"id": f"{plugin['id']}-{counters[plugin['id']]}",
                "plugin_id": plugin["id"],
                "label": plugin.get("name", plugin["id"]),
                "position": {"x": 70.0 + column * 270.0,
                             "y": 70.0 + 140.0 * (len(nodes))},
                "config": config}
        nodes.append(node)
        return node

    # Step 1: resources (catalog choice, none, or import guidance).
    if resources:
        say("Resources:")
        show_menu(resources)
    say("  none) no resource")
    say("  import) import existing OpenTofu code (separate governed flow)")
    while True:
        answer = click.prompt("Add a resource (number, none, import)",
                              default="none", show_default=True, err=True).strip().lower()
        if answer == "none":
            break
        if answer == "import":
            say(IMPORT_RESOURCE)
            break
        picked = pick_plugins(resources, answer) if resources else None
        if not picked:
            say("  answer with a number from the list, none, or import")
            continue
        plugin = picked[0]
        config = prompt_config(plugin, c, options_ctx)
        node = add_node(plugin, config, column=0)
        resource_nodes.append(node)
        if plugin.get("admission_profile") == "virtual-machine":
            say(VM_SOLO.format(name=plugin.get("name", plugin["id"])))
            vm_solo = True
            break
        if not click.confirm("Add another resource?", default=False, err=True):
            break

    # Step 2: services linked to the resources, or standalone on existing guests.
    service_nodes: list[dict] = []
    if not vm_solo:
        if services:
            say("Services:")
            show_menu(services)
        say("  none) no service")
        say("  import) import custom service code (catalog flow)")
        chosen: list[dict] = []
        while True:
            answer = click.prompt("Link services (numbers comma-separated, none, import)",
                                  default="none", show_default=True, err=True).strip().lower()
            if answer == "none":
                break
            if answer == "import":
                say(IMPORT_SERVICE)
                break
            picked = pick_plugins(services, answer) if services else None
            if not picked:
                say("  answer with numbers from the list, none, or import")
                continue
            if any(p.get("kind") == "workload" for p in picked) and len(picked) != 1:
                say("  workload bricks deploy one per job; pick exactly one")
                continue
            chosen = picked
            break
        for plugin in chosen:
            config = prompt_config(plugin, c, options_ctx)
            node = add_node(plugin, config, column=1)
            service_nodes.append(node)
            if resource_nodes:
                default = "1" if len(resource_nodes) == 1 else "none"
                while True:
                    link = click.prompt(f"Link {node['id']} to resource (number, none)",
                                        default=default, show_default=True, err=True).strip().lower()
                    if link == "none":
                        break
                    if link.isdigit() and 1 <= int(link) <= len(resource_nodes):
                        edges.append({"id": f"link-{len(edges) + 1}",
                                      "source": resource_nodes[int(link) - 1]["id"],
                                      "target": node["id"]})
                        break
                    say("  answer with a resource number or none")
        if len(service_nodes) > 1:
            for previous, following in zip(service_nodes, service_nodes[1:]):
                edges.append({"id": f"link-{len(edges) + 1}",
                              "source": previous["id"], "target": following["id"]})
            say("Services are chained in selection order (dependency edges).")

    if not nodes:
        raise click.ClickException("a blueprint needs at least one resource or service; nothing created")

    graph = {"name": name, "environment": environment, "nodes": nodes, "edges": edges}

    # Step 3: summary + authoritative dry-run before anything is created.
    say(f"Blueprint {name} ({environment}):")
    for node in nodes:
        say(f"  {node['id']:<24} {node['plugin_id']}")
    for edge in edges:
        say(f"  {edge['source']} -> {edge['target']}")
    report = c.post("/api/plans/validate", json=graph)
    if not report.get("allowed"):
        for violation in report.get("violations", []):
            say(f"  refused: {violation.get('code')}: {violation.get('message')}")
        raise click.ClickException("admission refused the graph; nothing created")
    cost = report.get("cost", {})
    say(f"Dry-run: allowed, estimated {cost.get('monthly', 0)} {cost.get('currency', 'EUR')}/month")
    for warning in report.get("warnings", []):
        say(f"  warning: {warning}")

    if not click.confirm("Create this blueprint?", default=True, err=True):
        say("Cancelled; nothing created.")
        return None
    record = c.post("/api/blueprints", json={"name": name, "graph": graph})
    say(f"Blueprint created: {record['id']}")

    # Step 4: optional deployment request, then explicit human approval.
    if click.confirm("Request deployment now?", default=False, err=True):
        try:
            job = c.post("/api/jobs", json={"graph": record["graph"]})
        except ApiError as err:
            say(f"Deployment request refused: {err.detail}")
            say(f"Retry with: kw deploy request --blueprint-id {record['id']}")
            return record
        say(f"Deployment requested: {job['id']} (status: {job.get('status')})")
        if click.confirm("Approve now? (executes the deployment; admin only)",
                         default=False, err=True):
            try:
                approved = c.post(f"/api/jobs/{job['id']}/approve")
                say(f"Approved (status: {approved.get('status')})")
            except ApiError as err:
                say(f"Approval refused: {err.detail}")
                say(f"An administrator can approve with: kw deploy approve {job['id']}")
        say(f"Follow: kw deploy watch {job['id']}  |  kw logs {job['id']} --follow")
        if vm_solo:
            say("Once succeeded: kw inventory list (guest UUID), then 'kw blueprints create'")
            say("to add a service targeting that guest (target_uuid, ssh_key_id).")
    else:
        say(f"Deploy later with: kw deploy request --blueprint-id {record['id']}")
    return record
