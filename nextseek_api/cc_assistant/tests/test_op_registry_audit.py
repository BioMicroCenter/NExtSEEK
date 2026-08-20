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
WRITE_GATE = REPO_ROOT / "nextseek_api" / "assistant" / "write_gate.py"
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
    return frozenset(_parse_dict_key_to_handler(path, name))


def _parse_dict_key_to_handler(path: Path, name: str) -> dict[str, str]:
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
    mapping: dict[str, str] = {}
    for candidate in candidates:
        for key, value in zip(candidate.keys, candidate.values):
            if not (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Name)
            ):
                continue
            mapping[key.value] = value.id
    if not mapping:
        raise AssertionError(f"{name!r} dict had no string-key/name-value pairs in {path}")
    return mapping


def _expected_handler_name(runner_key: str, *, prefix: str) -> str:
    return f"{prefix}{runner_key.replace('-', '_')}"


def _required_flags_in_order(
    forwarded: tuple[str, ...], required: tuple[str, ...]
) -> tuple[str, ...]:
    required_set = set(required)
    return tuple(flag for flag in forwarded if flag in required_set)


def _batch_forwarded_flags(text: str, runner_key: str) -> tuple[str, ...]:
    forwarded: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("set --"):
            continue
        if stripped.startswith('set -- "$@"'):
            forwarded.extend(re.findall(r"--[\w-]+", stripped))
            continue
        parts = stripped.split()
        if len(parts) >= 3 and parts[2] == runner_key:
            forwarded.extend(re.findall(r"--[\w-]+", stripped))
    if forwarded:
        return tuple(forwarded)
    exec_match = re.search(
        rf'exec python "\$SCRIPT_DIR/_batch_upload_runner\.py"\s+{re.escape(runner_key)}\s+"\$@"\s*$',
        text,
        flags=re.MULTILINE,
    )
    if exec_match:
        return ()
    exec_match = re.search(
        rf'exec python "\$SCRIPT_DIR/_batch_upload_runner\.py"\s+{re.escape(runner_key)}\s+(.+)$',
        text,
        flags=re.MULTILINE,
    )
    if exec_match:
        return tuple(re.findall(r"--[\w-]+", exec_match.group(1)))
    return ()


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
        forwarded = list(_batch_forwarded_flags(text, runner_key))
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


def test_dispatch_handlers_match_runner_keys():
    dispatch = _parse_dict_key_to_handler(NEXTSEEK_RUNNER, "_DISPATCH")
    for op in OPS:
        if op.backend is not Backend.dispatch or not op.available:
            continue
        expected = _expected_handler_name(op.runner_key, prefix="_dispatch_")
        actual = dispatch[op.runner_key]
        assert actual == expected, (
            f"{op.op_id}: _DISPATCH[{op.runner_key!r}] maps to {actual!r}, expected {expected!r}"
        )


def test_subcmd_handlers_match_runner_keys():
    subcmds = _parse_dict_key_to_handler(BATCH_RUNNER, "_CMDS")
    for op in OPS:
        if op.backend is not Backend.subcmd or not op.available:
            continue
        expected = _expected_handler_name(op.runner_key, prefix="_cmd_")
        actual = subcmds[op.runner_key]
        assert actual == expected, (
            f"{op.op_id}: _CMDS[{op.runner_key!r}] maps to {actual!r}, expected {expected!r}"
        )


def test_each_shim_runner_key_and_argv_forwarding():
    ops = _ops_by_bin()
    for shim_path in _installed_shims():
        parsed = _parse_shim(shim_path)
        op = ops[parsed.bin_name]
        assert op.runner_key == parsed.runner_key
        expected_flags = tuple(arg.flag for arg in op.argv if arg.required)
        passthrough = False
        if op.backend is Backend.dispatch:
            assert parsed.runner_module == QUERY_RUNNER
            runner_flags = tuple(
                flag for flag in parsed.forwarded_argv if flag != "--agent"
            )
        else:
            assert parsed.runner_module == BATCH_RUNNER_NAME
            runner_flags = parsed.forwarded_argv
            passthrough = (
                f'exec python "$SCRIPT_DIR/_batch_upload_runner.py" {parsed.runner_key} "$@"'
                in shim_path.read_text(encoding="utf-8")
            )
        if op.backend is Backend.subcmd and passthrough:
            continue
        actual_required = _required_flags_in_order(runner_flags, expected_flags)
        assert actual_required == expected_flags, (
            f"{parsed.bin_name}: required argv forwarding {actual_required!r} "
            f"!= expected order {expected_flags!r} (full forwarded {runner_flags!r})"
        )


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


def _expected_gate_class(op: OpSpec) -> GateClass:
    if op.transport is Transport.viewset:
        return GateClass.unrouted
    if op.transport is Transport.local_subcommand:
        return GateClass.read
    if op.transport is not Transport.sidecar:
        raise AssertionError(f"unexpected transport for {op.op_id}: {op.transport}")

    write_gate_sid = _parse_frozenset_assignment(WRITE_GATE, "SIDECAR_OPS")
    read_class_ops = write_gate_sid - {"api-read", "api-write"}
    handler_ops = _parse_dict_keys(GRANULAR, "_HANDLERS")

    if op.runner_key == "api-write":
        return GateClass.write_confirm
    if op.runner_key == "api-read":
        return GateClass.read
    if op.runner_key in read_class_ops:
        return GateClass.read
    if op.runner_key in handler_ops:
        return GateClass.read
    raise AssertionError(
        f"no enforcement policy for sidecar op {op.op_id!r} ({op.runner_key!r})"
    )


def test_gate_class_matches_enforcement():
    runner_text = NEXTSEEK_RUNNER.read_text(encoding="utf-8")
    assert "if not args.confirmed_write:" in runner_text
    assert '_err("WRITE_BLOCKED"' in runner_text
    batch_bins = {
        op.bin_name for op in OPS if op.transport is Transport.local_subcommand
    }
    for shim_path in _installed_shims():
        if shim_path.name not in batch_bins:
            continue
        text = shim_path.read_text(encoding="utf-8")
        assert "--confirmed-write" in text
        assert "forbidden" in text.casefold()

    for op in OPS:
        expected = _expected_gate_class(op)
        assert op.gate_class is expected, (
            f"{op.op_id}: gate_class {op.gate_class!r} != enforcement-derived {expected!r}"
        )


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
