"""The sidecar's WS contract and the plugin's copy are one contract in two files.

``ns-sidecar/app/contract.py`` is the definition; the plugin bins import the byte-identical
``cc-runtime/build_context/plugins/nextseek/bin/_ws_contract.py`` standalone, without the ``sidecar`` package. Both
files name this test as the guard against drift, and until now it did not exist. A drift is silent at runtime: an
op in one copy's ``SIDECAR_OPS`` and not the other is refused by whichever side lacks it, with a VALIDATION error
that reads like a bad argument.
"""
from __future__ import annotations

import ast

from NessieAI import paths

SIDECAR_CONTRACT = paths.NS_SIDECAR_DIR / "app" / "contract.py"
PLUGIN_CONTRACT = paths.CC_PLUGIN_BIN / "_ws_contract.py"


def _sidecar_ops(path) -> frozenset[str]:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SIDECAR_OPS" for t in node.targets):
            return frozenset(ast.literal_eval(node.value.args[0]))
    raise AssertionError(f"SIDECAR_OPS not found in {path}")


def test_the_two_copies_are_byte_identical():
    assert SIDECAR_CONTRACT.read_bytes() == PLUGIN_CONTRACT.read_bytes(), (
        "ns-sidecar/app/contract.py and the plugin's bin/_ws_contract.py have drifted; change both, then "
        "update the sidecar digest in test_step7_sidecar_port.py and ns-sidecar/PORT-EVIDENCE.json"
    )


def test_every_op_has_an_argument_model():
    ops = _sidecar_ops(SIDECAR_CONTRACT)
    text = SIDECAR_CONTRACT.read_text(encoding="utf-8")
    tree = ast.parse(text)
    models = next(node for node in tree.body if isinstance(node, ast.Assign)
                  and any(getattr(t, "id", None) == "_OP_ARG_MODELS" for t in node.targets))
    keys = frozenset(key.value for key in models.value.keys)
    assert keys == ops


def test_aggregate_is_in_both_copies():
    assert "aggregate" in _sidecar_ops(SIDECAR_CONTRACT)
    assert "aggregate" in _sidecar_ops(PLUGIN_CONTRACT)
