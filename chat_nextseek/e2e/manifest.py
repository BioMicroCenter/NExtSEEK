"""Run manifest: variants sampled + their pass/fail status."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ManifestEntry(BaseModel):
    id: str
    family: str
    status: Literal["passed", "failed", "skipped"]
    elapsed_s: float = 0.0
    failed_criteria: list[str] = Field(default_factory=list)
    reason: str = ""


class Manifest(BaseModel):
    started_at: str
    ended_at: str
    ratio: float
    seed: int
    profile: str
    variants: list[ManifestEntry]


def write_manifest(m: Manifest, path: Path) -> None:
    Path(path).write_text(m.model_dump_json(indent=2), encoding="utf-8")


def load_manifest(path: Path) -> Manifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return Manifest.model_validate(payload)


def filter_for_rerun(m: Manifest, *, failed_only: bool) -> list[str]:
    """Return the list of variant IDs to re-execute from a manifest.

    With failed_only=True: only entries with status=='failed'.
    With failed_only=False: passed + failed (skipped variants are NOT replayed
    — a skip means requires_env was unsatisfied; replaying without fixing the
    env would just produce another skip).
    """
    if failed_only:
        return [e.id for e in m.variants if e.status == "failed"]
    return [e.id for e in m.variants if e.status != "skipped"]
