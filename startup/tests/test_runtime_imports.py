"""Guard: ./startup.sh installs only startup's main dependencies, so no runtime module may import anything else."""
from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Deliberate, guarded imports: MySQLdb is host-provided and optional (schema_fixups), app is the bedrock-proxy
# image's own package (model_reach_probe, inside try/except ImportError).
GUARDED = {"MySQLdb", "app"}
# Not run by ./startup.sh: lane-only settings and the maintainer-only seed refresh kit.
SKIP_DIRS = ("dev", "seed/regenerate")
DIST_TO_IMPORT = {"pymysql": "pymysql", "pyyaml": "yaml", "python-dotenv": "dotenv"}


def _declared() -> set[str]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    names = {d.split(">")[0].split("=")[0].split("<")[0].strip().lower() for d in deps}
    return {DIST_TO_IMPORT.get(n, n.replace("-", "_")) for n in names}


def test_runtime_modules_import_only_declared_dependencies() -> None:
    allowed = _declared() | set(sys.stdlib_module_names) | {"startup"} | GUARDED
    bad = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in {"tests", ".venv"} or "tests" in rel.parts or rel.name == "conftest.py" \
                or rel.as_posix().startswith(tuple(d + "/" for d in SKIP_DIRS)):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
                    else [])
            bad += [f"{rel}: {m}" for m in mods if m.split(".")[0] not in allowed]
    assert not bad, "imports not in startup's main dependencies: " + "; ".join(bad)


def test_drain_problem_works_without_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from startup.lib import web_swap

    monkeypatch.setitem(sys.modules, "yaml", None)
    (tmp_path / ".env").write_text("NEXTSEEK_DRAIN_SECONDS=400\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    stop_grace_period: 5m\n")
    assert "above 270" in web_swap.drain_problem(tmp_path)
    (tmp_path / "docker-compose.yml").write_text("services: [unclosed\n")
    assert web_swap.drain_problem(tmp_path) is None   # a compose file it cannot read is no reason to crash a rebuild
