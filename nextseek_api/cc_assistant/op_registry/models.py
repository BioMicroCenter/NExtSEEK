"""Strict typed models for the CC operation registry (Plan 005)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, TypeAdapter


class Backend(str, Enum):
    dispatch = "dispatch"
    subcmd = "subcmd"


class Transport(str, Enum):
    viewset = "viewset"
    sidecar = "sidecar"
    local_subcommand = "local_subcommand"


class GateClass(str, Enum):
    read = "read"
    write_confirm = "write_confirm"
    unrouted = "unrouted"


class ArgSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag: str
    required: bool = False
    enum: list[str] = []


class ReadSafeEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    methods: list[str]
    rationale: str
    source: str


class AllowlistSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_runnable: bool = True
    patterns: list[str] = []


class SkillRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str
    input: str
    output: str


class OpSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str
    bin_name: str
    runner_key: str
    backend: Backend = Backend.dispatch
    runner: str
    transport: Transport
    assistant_endpoint: str
    gate_class: GateClass
    read_safe_endpoints: list[ReadSafeEndpoint] = []
    argv: list[ArgSpec] = []
    allowlist: AllowlistSpec = AllowlistSpec()
    response_envelope_fields: list[str] = []
    published_path: bool = False
    per_op_gate_enabled: bool = False
    available: bool = True
    skill_name: str | None = None
    skill_row: SkillRow | None = None


class RouteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_name: str
    description: str
    best_for: str
    not_for: str


OpList = TypeAdapter(list[OpSpec])
