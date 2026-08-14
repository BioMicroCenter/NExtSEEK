"""Pure derivations for Plan 005 operation and server audit surfaces."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from nextseek_api.cc_assistant.op_registry.models import OpSpec, Transport

REPO_ROOT = Path(__file__).resolve().parents[3]
WS_CONTRACT = (
    REPO_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek" / "bin" / "_ws_contract.py"
)
GRANULAR = REPO_ROOT / "nextseek_api" / "assistant" / "granular.py"
WRITE_GATE = REPO_ROOT / "nextseek_api" / "assistant" / "write_gate.py"
READ_SAFE_JSON = REPO_ROOT / "nextseek_api" / "assistant" / "read_safe_endpoints.json"

_API_READ = "api-read"
_API_WRITE = "api-write"
_ALLOWED_METHODS = frozenset({"GET", "POST"})


def _parse_string_set(value: ast.AST) -> frozenset[str]:
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        if value.func.id == "frozenset" and value.args:
            value = value.args[0]
    if isinstance(value, (ast.Set, ast.List)):
        items: list[str] = []
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                items.append(elt.value)
        return frozenset(items)
    raise AssertionError(f"unsupported set literal: {ast.dump(value)}")


def parse_frozenset_assignment(path: Path, name: str) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return _parse_string_set(node.value)
    raise AssertionError(f"{name!r} assignment not found in {path}")


def parse_dict_keys(path: Path, name: str) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    candidates: list[ast.Dict] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name and isinstance(
                    node.value, ast.Dict
                ):
                    candidates.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and isinstance(node.value, ast.Dict)
        ):
            candidates.append(node.value)
    if not candidates:
        raise AssertionError(f"{name!r} dict not found in {path}")
    keys: set[str] = set()
    for candidate in candidates:
        for key in candidate.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
    if not keys:
        raise AssertionError(f"{name!r} dict had no string keys in {path}")
    return frozenset(keys)


def derive_sidecar_ops_from_ops(ops: list[OpSpec] | tuple[OpSpec, ...]) -> frozenset[str]:
    return frozenset(op.runner_key for op in ops if op.transport is Transport.sidecar)


def derive_ws_contract_sidecar_ops(
    path: Path = WS_CONTRACT,
) -> frozenset[str]:
    return parse_frozenset_assignment(path, "SIDECAR_OPS")


def derive_handler_sidecar_ops(path: Path = GRANULAR) -> frozenset[str]:
    return parse_dict_keys(path, "_HANDLERS")


def derive_write_gate_sidecar_ops(path: Path = WRITE_GATE) -> frozenset[str]:
    return parse_frozenset_assignment(path, "SIDECAR_OPS")


def derive_write_gate_read_class_ops(path: Path = WRITE_GATE) -> frozenset[str]:
    declared = derive_write_gate_sidecar_ops(path)
    return declared - {_API_READ, _API_WRITE}


def derive_write_gate_policy_union(path: Path = WRITE_GATE) -> frozenset[str]:
    read_class = derive_write_gate_read_class_ops(path)
    return read_class | {_API_READ, _API_WRITE}


def derive_transport_minus_write_gate(
    ops: list[OpSpec] | tuple[OpSpec, ...],
    *,
    write_gate_path: Path = WRITE_GATE,
) -> frozenset[str]:
    transport = derive_sidecar_ops_from_ops(ops)
    write_gate_ops = derive_write_gate_sidecar_ops(write_gate_path)
    return transport - write_gate_ops


def derive_read_safe_entries(path: Path = READ_SAFE_JSON) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise AssertionError("read_safe_endpoints.json must be a list")
    return entries


def derive_read_safe_pairs_from_json(path: Path = READ_SAFE_JSON) -> frozenset[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for entry in derive_read_safe_entries(path):
        endpoint = entry["endpoint"]
        for method in entry["methods"]:
            pairs.add((endpoint, method))
    return frozenset(pairs)


def derive_read_safe_pairs_from_ops(
    ops: list[OpSpec] | tuple[OpSpec, ...],
    *,
    op_id: str = _API_READ,
) -> frozenset[tuple[str, str]]:
    api_read = next(op for op in ops if op.op_id == op_id)
    pairs: set[tuple[str, str]] = set()
    for endpoint in api_read.read_safe_endpoints:
        for method in endpoint.methods:
            pairs.add((endpoint.endpoint, method))
    return frozenset(pairs)


def audit_read_safe_json_schema(path: Path = READ_SAFE_JSON) -> None:
    pairs: set[tuple[str, str]] = set()
    for entry in derive_read_safe_entries(path):
        endpoint = entry["endpoint"]
        assert endpoint.startswith("/nextseek_api/"), endpoint
        assert "*" not in endpoint, endpoint
        assert entry["source"].strip(), endpoint
        assert entry["rationale"].strip(), endpoint
        for method in entry["methods"]:
            assert method in _ALLOWED_METHODS, (endpoint, method)
            pair = (endpoint, method)
            assert pair not in pairs, pair
            pairs.add(pair)


def find_op(ops: list[OpSpec] | tuple[OpSpec, ...], op_id: str) -> OpSpec:
    return next(op for op in ops if op.op_id == op_id)
