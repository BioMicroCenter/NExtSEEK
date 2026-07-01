"""Step 3 — CC upload filename validation (SPEC-3 §4, §10).

PURE module — no Django, no Celery import — so the hermetic validator test
(`test_cc_upload_validate.py`) imports it with zero celery dependency. Both the
celery task (`cc_upload_tasks.run_cc_upload_task`) and the upload action reuse it.
"""
from __future__ import annotations

import os


def validate_upload_filename(name: str) -> str:
    """Return a safe single-segment basename or raise ValueError. Rejects path
    separators, traversal, NUL, absolute, empty."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        raise ValueError(f"invalid filename: {name!r}")
    if "/" in name or "\\" in name or "\x00" in name or os.path.isabs(name):
        raise ValueError(f"invalid filename: {name!r}")
    base = os.path.basename(name)
    if base != name or not base:  # pragma: no cover - defensive; separators/absolute already rejected above
        raise ValueError(f"invalid filename: {name!r}")
    return base
