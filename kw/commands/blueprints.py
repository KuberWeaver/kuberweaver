"""Blueprints (plans de déploiement): resources + services delivered together."""
from __future__ import annotations
import json
from pathlib import Path

import click

from kw.commands.common import api, client, out


def _load_graph(path: str) -> dict:
    return json.loads(Path(path).read_text())


@click.group()
def blueprints():
    """List, create, inspect and duplicate blueprints."""


@blueprints.command("list")
@click.pass_context
@api
def list_cmd(ctx):
    c = client(ctx)
    data = c.get("/api/blueprints")
    c.close()
    out(ctx, data, columns=["id", "name", "created_by", "created_at"])


@blueprints.command("create")
@click.option("--name", required=True)
@click.option("--graph-file", required=True, type=click.Path(exists=True),
              help="JSON file holding the deployment graph.")
@click.pass_context
@api
def create_cmd(ctx, name, graph_file):
    c = client(ctx)
    data = c.post("/api/blueprints", json={"name": name, "graph": _load_graph(graph_file)})
    c.close()
    out(ctx, data)


@blueprints.command("get")
@click.argument("identity")
@click.pass_context
@api
def get_cmd(ctx, identity):
    c = client(ctx)
    data = c.get(f"/api/blueprints/{identity}")
    c.close()
    out(ctx, data)


@blueprints.command("copy")
@click.argument("identity")
@click.option("--name", required=True, help="Name for the duplicated blueprint.")
@click.pass_context
@api
def copy_cmd(ctx, identity, name):
    """Duplicate an existing blueprint (same graph) under a new name."""
    c = client(ctx)
    source = c.get(f"/api/blueprints/{identity}")
    data = c.post("/api/blueprints", json={"name": name, "graph": source["graph"]})
    c.close()
    out(ctx, data)
