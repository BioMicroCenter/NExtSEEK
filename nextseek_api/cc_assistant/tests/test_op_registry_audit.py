"""Independent Audit A for Plan 005 Task 2 (OpSpec, OPS, RouteSpec)."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from nextseek_api.cc_assistant.op_registry import (
    CONTAINER_CC_ROUTE,
    GENERIC_CC_BUILTINS,
    OPS,
    OpList,
    OpSpec,
    discover_install,
)
from nextseek_api.cc_assistant.op_registry.models import (
    Backend,
    GateClass,
    RouteSpec,
    Transport,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGINS_ROOT = REPO_ROOT / "docker" / "cc-runtime" / "build_context" / "plugins"
DOCKERFILE = REPO_ROOT / "docker" / "cc-runtime" / "Dockerfile"
NEXTSEEK_BIN = PLUGINS_ROOT / "nextseek" / "bin"
NEXTSEEK_RUNNER = NEXTSEEK_BIN / "_nextseek_runner.py"
BATCH_RUNNER = NEXTSEEK_BIN / "_batch_upload_runner.py"
WS_CONTRACT = NEXTSEEK_BIN / "_ws_contract.py"
GRANULAR = REPO_ROOT / "nextseek_api" / "assistant" / "granular.py"
READ_SAFE_JSON = REPO_ROOT / "nextseek_api" / "assistant" / "read_safe_endpoints.json"
ROUTE_CAPABILITIES = REPO_ROOT / "dmac_assistant" / "build_context" / "route_capabilities.json"
CAPABILITIES_MD = (
    REPO_ROOT / "chat_nextseek" / "src" / "chat_nextseek" / "context" / "capabilities.md"
)

QUERY_RUNNER = "_nextseek_runner.py"
BATCH_RUNNER_NAME = "_batch_upload_runner.py"
SHIM_PREFIX = "nextseek-"


def _parse_frozenset_assignment(path: Path, name: str) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    if value.func.id == "frozenset" and value.args:
                        value = value.args[0]
                if isinstance(value, (ast.Set, ast.List)):
                    items: list[str] = []
                    for elt in value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            items.append(elt.value)
                    return frozenset(items)
    raise AssertionError(f"{name!r} assignment not found in {path}")


def _parse_dict_keys(path: Path, name: str) -> frozenset[str]:
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
    keys: list[str] = []
    for candidate in candidates:
        for key in candidate.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.append(key.value)
    return frozenset(keys)


@dataclass(frozen=True)
class ParsedShim:
    bin_name: str
    runner_module: str
    runner_key: str
    forwarded_argv: tuple[str, ...]


def _parse_shim(path: Path) -> ParsedShim:
    text = path.read_text(encoding="utf-8")
    if QUERY_RUNNER in text:
        runner_module = QUERY_RUNNER
        agent_match = re.search(r"--agent\s+(\S+)", text)
        if agent_match is None:
            raise AssertionError(f"{path.name}: missing --agent in shim")
        runner_key = agent_match.group(1)
        exec_lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("exec python ") and QUERY_RUNNER in stripped:
                exec_lines = [stripped]
            elif exec_lines and (
                stripped.startswith("--")
                or stripped.endswith("\\")
                or stripped.endswith('"$QUERY"')
                or stripped.endswith('"$PARSER_PLAN"')
                or "$PLANNER" in stripped
            ):
                exec_lines.append(stripped)
            elif exec_lines:
                break
        forwarded = re.findall(r"--[\w-]+", " ".join(exec_lines))
        return ParsedShim(path.name, runner_module, runner_key, tuple(forwarded))
    if BATCH_RUNNER_NAME in text:
        runner_module = BATCH_RUNNER_NAME
        set_match = re.search(r'set -- (\S+)', text)
        extract_match = re.search(
            rf'exec python "\$SCRIPT_DIR/_batch_upload_runner\.py"\s+(\S+)',
            text,
        )
        if set_match:
            runner_key = set_match.group(1)
        elif extract_match:
            runner_key = extract_match.group(1)
        else:
            raise AssertionError(f"{path.name}: cannot determine batch subcommand")
        exec_match = re.search(
            rf'exec python "\$SCRIPT_DIR/_batch_upload_runner\.py"\s+(.+)$',
            text,
            flags=re.MULTILINE,
        )
        forwarded: list[str] = []
        if exec_match:
            tail = exec_match.group(1)
            if tail.startswith(runner_key):
                tail = tail[len(runner_key) :]
            forwarded = re.findall(r"--[\w-]+", tail)
        return ParsedShim(path.name, runner_module, runner_key, tuple(forwarded))
    raise AssertionError(f"{path.name}: unknown runner reference")


def _ops_by_bin() -> dict[str, OpSpec]:
    return {op.bin_name: op for op in OPS}


def _installed_shims() -> tuple[Path, ...]:
    discovery = discover_install(plugins_root=PLUGINS_ROOT, dockerfile_path=DOCKERFILE)
    return tuple(shim.shim_path for shim in discovery.shims)


def _forbidden_route_tokens() -> frozenset[str]:
    route_data = json.loads(ROUTE_CAPABILITIES.read_text(encoding="utf-8"))
    forbidden: set[str] = set()
    for route in route_data["routes"]:
        for family in route.get("task_families", []):
            forbidden.add(family["name"])
            forbidden.update(family.get("example_queries", []))
    md = CAPABILITIES_MD.read_text(encoding="utf-8")
    for line in md.splitlines():
        if line.startswith("### "):
            label = re.sub(r"^\d+\.\s*", "", line.removeprefix("### ").strip())
            forbidden.add(label)
    return frozenset(token for token in forbidden if token.strip())


def test_ops_bulk_validate_via_typeadapter():
    dumped = OpList.dump_python(OPS)
    revalidated = OpList.validate_python(dumped)
    assert [o.op_id for o in revalidated] == [o.op_id for o in OPS]


def test_installed_shims_match_available_ops_bidirectionally():
    installed = {path.name for path in _installed_shims()}
    available = {op.bin_name for op in OPS if op.available}
    assert available == installed
    assert available - installed == installed - available == set()


def test_dispatch_runner_keys_match_ops():
    dispatch_keys = _parse_dict_keys(NEXTSEEK_RUNNER, "_DISPATCH")
    ops_keys = {
        op.runner_key for op in OPS if op.backend is Backend.dispatch and op.available
    }
    assert ops_keys == dispatch_keys


def test_subcmd_runner_keys_match_ops():
    subcmd_keys = _parse_dict_keys(BATCH_RUNNER, "_CMDS")
    ops_keys = {
        op.runner_key for op in OPS if op.backend is Backend.subcmd and op.available
    }
    assert ops_keys == subcmd_keys


def test_each_shim_runner_key_and_argv_forwarding():
    ops = _ops_by_bin()
    for shim_path in _installed_shims():
        parsed = _parse_shim(shim_path)
        op = ops[parsed.bin_name]
        assert op.runner_key == parsed.runner_key
        if op.backend is Backend.dispatch:
            assert parsed.runner_module == QUERY_RUNNER
            expected_flags = tuple(arg.flag for arg in op.argv if arg.required)
            for flag in expected_flags:
                assert flag in parsed.forwarded_argv, (
                    f"{parsed.bin_name}: required flag {flag} not forwarded"
                )
        else:
            assert parsed.runner_module == BATCH_RUNNER_NAME
            assert parsed.runner_key == op.runner_key


def test_sidecar_transport_matches_ws_contract_and_handlers():
    ws_ops = _parse_frozenset_assignment(WS_CONTRACT, "SIDECAR_OPS")
    handler_ops = _parse_dict_keys(GRANULAR, "_HANDLERS")
    sidecar_ops = {op.runner_key for op in OPS if op.transport is Transport.sidecar}
    assert sidecar_ops == ws_ops
    assert sidecar_ops == handler_ops


def test_viewset_transport_ops_are_query_plan_recall_pipeline():
    viewset_ops = {op.runner_key for op in OPS if op.transport is Transport.viewset}
    assert viewset_ops == {"query", "plan", "recall", "pipeline"}


def test_local_subcommand_transport_matches_batch_runner():
    local_ops = {
        op.runner_key for op in OPS if op.transport is Transport.local_subcommand
    }
    assert local_ops == _parse_dict_keys(BATCH_RUNNER, "_CMDS")


def test_gate_class_matches_enforcement():
    api_write = next(o for o in OPS if o.op_id == "api-write")
    api_read = next(o for o in OPS if o.op_id == "api-read")
    assert api_write.gate_class is GateClass.write_confirm
    assert api_read.gate_class is GateClass.read
    for op in OPS:
        if op.transport is Transport.viewset:
            assert op.gate_class is GateClass.unrouted
        elif op.transport is Transport.local_subcommand:
            assert op.gate_class is GateClass.read
        elif op.op_id in {"entity", "parse", "graph", "report", "generate-submission", "run-ls", "build-upload-xlsx"}:
            assert op.gate_class is GateClass.read


def test_read_safe_endpoints_schema_and_ops_api_read_agree():
    entries = json.loads(READ_SAFE_JSON.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    pairs: set[tuple[str, str]] = set()
    for entry in entries:
        assert entry["endpoint"].startswith("/nextseek_api/")
        assert "*" not in entry["endpoint"]
        assert entry["source"].strip()
        assert entry["rationale"].strip()
        for method in entry["methods"]:
            assert method in {"GET", "POST"}
            pair = (entry["endpoint"], method)
            assert pair not in pairs
            pairs.add(pair)
    api_read = next(o for o in OPS if o.op_id == "api-read")
    ops_pairs = {
        (endpoint.endpoint, method)
        for endpoint in api_read.read_safe_endpoints
        for method in endpoint.methods
    }
    assert ops_pairs == pairs
    for endpoint in api_read.read_safe_endpoints:
        assert endpoint.source.strip()
        assert endpoint.rationale.strip()


def test_unique_op_id_and_bin_name():
    assert len({op.op_id for op in OPS}) == len(OPS)
    assert len({op.bin_name for op in OPS}) == len(OPS)
    assert all(op.bin_name.startswith(SHIM_PREFIX) for op in OPS)


def test_model_forbids_unknown_fields():
    sample = OPS[0].model_dump()
    sample["bogus_field"] = 1
    with pytest.raises(ValidationError):
        OpSpec.model_validate(sample)


def test_query_and_recall_are_available():
    query = next(o for o in OPS if o.op_id == "query")
    recall = next(o for o in OPS if o.op_id == "recall")
    assert query.available is True
    assert recall.available is True
    assert query.transport is Transport.viewset
    assert recall.transport is Transport.viewset


def test_write_confirmation_policy():
    api_write = next(o for o in OPS if o.op_id == "api-write")
    assert any(a.flag == "--confirmed-write" and a.required for a in api_write.argv)
    api_read_shim = NEXTSEEK_BIN / "nextseek-api-read"
    text = api_read_shim.read_text(encoding="utf-8")
    assert "--confirmed-write" in text
    assert "nextseek-api-write" in text


def test_container_cc_route_has_no_forbidden_tokens():
    forbidden = _forbidden_route_tokens()
    prose_fields = (
        CONTAINER_CC_ROUTE.description,
        CONTAINER_CC_ROUTE.best_for,
        CONTAINER_CC_ROUTE.not_for,
    )
    lowered = "\n".join(prose_fields).casefold()
    hits = sorted(
        token for token in forbidden if token.casefold() in lowered
    )
    assert not hits, f"forbidden route prose tokens present: {hits[:5]}"


def test_route_spec_rejects_task_family_field():
    with pytest.raises(ValidationError):
        RouteSpec(
            route_name="container_cc",
            description="ok",
            best_for="ok",
            not_for="ok",
            task_families=[{"name": "sample_search"}],
        )


def test_generic_cc_builtins_are_explicit():
    assert GENERIC_CC_BUILTINS == ("bash", "filesystem", "skill-runner")
