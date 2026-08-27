"""BIN-2/PD-4: catalogs bind to the ACTUAL bin inventory — proven dynamically."""
import ast
import os
import stat
from pathlib import Path

from nextseek_api.cc_assistant import bin_inventory

_REPO = Path(__file__).resolve().parents[3]


def _mk_shim(d, name, runner, executable=True):
    p = d / name
    p.write_text(f'#!/bin/sh\nexec python "$SCRIPT_DIR/{runner}" --agent x\n')
    if executable:
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def test_discovery_is_dynamic_not_hardcoded(tmp_path):
    """The one shape a hardcoded return cannot satisfy: a synthetic bin dir."""
    _mk_shim(tmp_path, "nextseek-alpha", "_nextseek_runner.py")
    _mk_shim(tmp_path, "nextseek-beta", "_batch_upload_runner.py")
    _mk_shim(tmp_path, "nextseek-gamma", "_nextseek_runner.py", executable=False)
    _mk_shim(tmp_path, "not-a-nextseek-op", "_nextseek_runner.py")
    assert bin_inventory.discover_ops("query", bin_dir=tmp_path) == ("nextseek-alpha",)
    assert bin_inventory.discover_ops("batch-upload", bin_dir=tmp_path) == ("nextseek-beta",)
    assert bin_inventory.discover_ops(None, bin_dir=tmp_path) == (
        "nextseek-alpha",
        "nextseek-beta",
    )


def test_real_inventory_contains_new_ops_and_partitions():
    q = bin_inventory.discover_ops("query")
    b = bin_inventory.discover_ops("batch-upload")
    allops = bin_inventory.discover_ops(None)
    assert "nextseek-query" in q and "nextseek-recall" in q
    assert set(q) | set(b) == set(allops)
    assert set(q) & set(b) == set()
    bin_dir = _REPO / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek" / "bin"
    disk = tuple(
        sorted(
            p.name
            for p in bin_dir.iterdir()
            if p.name.startswith("nextseek-") and os.access(p, os.X_OK)
        )
    )
    assert allops == disk


def test_catalogs_bind_to_inventory():
    from nextseek_api.cc_assistant import step7_gate_catalog as cat
    from nextseek_api.cc_assistant import step7_per_op_evidence as ev
    from nextseek_api.cc_assistant.tests import validate_step7_compose_deploy as val

    q = bin_inventory.discover_ops("query")
    assert tuple(ev.BIN_OPS) == q
    assert tuple(cat.BIN_OPS) == q
    assert tuple(val.BIN_OPS) == q


def test_no_op_name_literals_in_catalog_modules():
    """AST scan (G-8): the catalog modules may not carry 'nextseek-*' string
    constants — binding must come from discovery, not a copied list."""
    for rel in (
        "nextseek_api/cc_assistant/step7_per_op_evidence.py",
        "nextseek_api/cc_assistant/step7_gate_catalog.py",
        "nextseek_api/cc_assistant/tests/validate_step7_compose_deploy.py",
    ):
        tree = ast.parse((_REPO / rel).read_text())
        literals = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value.startswith("nextseek-")
        ]
        assert literals == [], f"{rel} hardcodes op names: {literals}"
