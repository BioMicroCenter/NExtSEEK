"""Hermetic F821 (undefined-name) gate over the chat_nextseek package.

This gate exists because the May-2026 module-split refactor (cdemurjian)
moved helper functions between sibling modules without carrying the imports,
leaving runtime ``NameError`` landmines (e.g. ``_step_query is not defined``)
that only fire deep inside the compound planner ``plan`` op. pyflakes'
undefined-name (F821) check catches this whole class statically, without
importing the package (which would require mysql.connector / django that are
unavailable in the hermetic test box).

If this test fails with a genuine undefined name, the fix is almost always a
missing import where the symbol IS defined in a sibling module — add the
import (function-local if a top-level import would be circular). Do NOT add
names to the allow-list to silence a real runtime NameError.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src" / "chat_nextseek"

# Names that pyflakes reports as "undefined" but which are NOT runtime bugs:
# they appear only inside string / deferred annotations (``from __future__
# import annotations``) and are resolved via TYPE_CHECKING imports. We fix
# these cleanly rather than exclude them, so the allow-list is intentionally
# EMPTY. Add here only a symbol proven to be annotation-only AND unfixable.
_ALLOWED_UNDEFINED: set[str] = set()

_UNDEFINED_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):\d+:\s+undefined name '(?P<name>[^']+)'")


def _run_pyflakes() -> str:
    if shutil.which("uvx") is None:
        pytest.skip("uvx not available to run pyflakes hermetically")
    proc = subprocess.run(
        ["uvx", "pyflakes", str(_SRC)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.stdout + proc.stderr


def test_no_undefined_names_in_chat_nextseek():
    output = _run_pyflakes()
    offenders = []
    for line in output.splitlines():
        m = _UNDEFINED_RE.match(line.strip())
        if not m:
            continue
        if m.group("name") in _ALLOWED_UNDEFINED:
            continue
        offenders.append(line.strip())
    assert not offenders, (
        "pyflakes found undefined names (likely missing imports after a "
        "module split — these are runtime NameError landmines):\n"
        + "\n".join(offenders)
    )
