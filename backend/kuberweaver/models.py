"""Closed API envelope; plugin config is validated by its manifest schema."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)


class Position(StrictModel):
    x: float = 0
    y: float = 0


class GraphNode(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    plugin_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    label: str = Field(default='', max_length=160)
    position: Position = Field(default_factory=Position)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    price_monthly: float = Field(default=0, ge=0, le=1000000)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')


class Edge(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    source: str = Field(max_length=80)
    target: str = Field(max_length=80)


class Strategy(StrictModel):
    canary: int = Field(default=1, ge=0, le=100)
    batch_size: int = Field(default=1, ge=1, le=100)


class Graph(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    environment: Literal['dev', 'staging', 'production'] = 'dev'
    nodes: list[GraphNode] = Field(min_length=1, max_length=100)
    edges: list[Edge] = Field(default_factory=list, max_length=1000)
    strategy: Strategy = Field(default_factory=Strategy)
    failure_mode: Literal['none', 'canary', 'rolling'] = 'none'


class Policies(StrictModel):
    max_nodes: int = Field(default=20, ge=1, le=100)
    max_cpu: float = Field(default=16, ge=0, le=1024)
    max_memory_mb: int = Field(default=16384, ge=0, le=1048576)
    max_disk_gb: float = Field(default=200, ge=0, le=100000)
    max_monthly_cost: float = Field(default=500, ge=0, le=1000000)
    require_canary_production: bool = True
    allowed_environments: list[Literal['dev', 'staging', 'production']] = Field(default_factory=lambda: ['dev', 'staging', 'production'])


class SettingsPatch(StrictModel):
    policies: dict[str, JsonValue] | None = None


class JobRequest(StrictModel):
    graph: Graph


class BlueprintRequest(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    graph: Graph
