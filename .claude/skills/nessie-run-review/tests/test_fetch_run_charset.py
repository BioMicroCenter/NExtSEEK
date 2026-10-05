"""Every mysql call in fetch_run.py reads the pull as utf8mb4 (no mojibake from the client charset)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_run.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_run_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_mysql_call_reads_utf8mb4():
    mod = _load()
    lines = [l for t in (mod.REMOTE, mod.RAW) for l in t.splitlines() if "mysql -u" in l]
    assert len(lines) == 2, lines
    for l in lines:
        assert "--default-character-set=utf8mb4" in l, l
