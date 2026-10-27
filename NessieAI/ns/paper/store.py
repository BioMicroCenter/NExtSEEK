"""Content-addressed storage for a run's artifacts (spec §4).

One folder per run under NEXTSEEK_PAPER_REINGEST_DIR (default
<NEXTSEEK_OUTPUTS_DIR or outputs>/paper_reingest). A key is "<name>.<digest>": the same
content always gets the same key, and changed content a new one.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil

from pydantic import BaseModel

_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")
_KEY = re.compile(r"^[A-Za-z0-9_-]+\.[0-9a-f]{16}$")


def _root() -> str:
    return os.environ.get("NEXTSEEK_PAPER_REINGEST_DIR") or os.path.join(
        os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs", "paper_reingest")


def _run_dir(run_id: str) -> str:
    if not _SAFE.match(run_id or ""):
        raise ValueError(f"bad run_id: {run_id!r}")
    return os.path.join(_root(), run_id)


def discard_run(run_id: str) -> None:
    """Remove a run's folder (a run whose row was never saved); a missing folder is fine."""
    shutil.rmtree(_run_dir(run_id), ignore_errors=True)


def save_artifact(run_id: str, name: str, model: BaseModel) -> str:
    if not _SAFE.match(name or ""):
        raise ValueError(f"bad artifact name: {name!r}")
    payload = model.model_dump_json()
    key = f"{name}.{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
    folder = _run_dir(run_id)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{key}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.replace(tmp, os.path.join(folder, f"{key}.json"))
    return key


def load_artifact(run_id: str, key: str, model_cls: type[BaseModel]) -> BaseModel:
    if not _KEY.match(key or ""):
        raise ValueError(f"bad artifact key: {key!r}")
    with open(os.path.join(_run_dir(run_id), f"{key}.json"), encoding="utf-8") as handle:
        return model_cls.model_validate_json(handle.read())
