"""The container's copies of the op tables are Django's (approach 1, piece 2).

The plugin cannot import NExtSEEK, so it carries its own exit table, reasons and op limits; the sidecar's WS contract
carries the codes it passes through. Parsed, never imported: the plugin's modules import their siblings by path.
"""
from __future__ import annotations

import ast
from pathlib import Path

from NessieAI import paths
from NessieAI.ns.op_limits import OP_LIMITS_S
from nextseek_api.assistant import op_errors


def _literal(path: Path, name: str):
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if any(getattr(target, "id", None) == name for target in targets):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset":
                value = value.args[0]
            return ast.literal_eval(value)
    raise AssertionError(f"{name} not found in {path}")


def test_the_plugins_exit_table_is_djangos():
    assert _literal(paths.CC_PLUGIN_BIN / "_op_errors.py", "EXIT") == op_errors.EXIT


def test_the_plugins_reasons_are_djangos():
    assert tuple(_literal(paths.CC_PLUGIN_BIN / "_op_errors.py", "REASONS")) == op_errors.REASONS


def test_the_plugins_op_limits_are_djangos():
    assert _literal(paths.CC_PLUGIN_BIN / "_op_road.py", "OP_LIMITS_S") == OP_LIMITS_S


def test_the_sidecar_contract_can_carry_every_code_djangos_ops_send():
    contract = paths.NS_SIDECAR_DIR / "app" / "contract.py"
    exits = _literal(contract, "ERROR_EXIT")
    for code in op_errors.CODES:
        assert exits[code] == op_errors.EXIT[code]
    assert tuple(_literal(contract, "REASONS")) == op_errors.REASONS


def test_the_sidecar_passes_through_exactly_djangos_codes_and_reasons():
    ns_client = paths.NS_SIDECAR_DIR / "app" / "ns_client.py"
    assert set(_literal(ns_client, "_NEXTSEEK_CODES")) == set(op_errors.CODES)
    assert set(_literal(ns_client, "_REASONS")) == set(op_errors.REASONS)
