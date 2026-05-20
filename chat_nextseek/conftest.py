"""Make the chat_nextseek/ directory importable to pytest.

Pytest's rootdir is resolved at the worktree parent (which owns the
[tool.pytest.ini_options] block), so the chat_nextseek/ directory itself
is not auto-added to sys.path. This conftest fixes that for top-level
packages that live alongside src/ (e.g. e2e/).
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
