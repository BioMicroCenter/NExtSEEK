from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field


class NessieManifestEntry(BaseModel):
    id: str
    family: str
    tier: Literal["route", "full"]
    status: Literal["passed", "failed", "skipped", "error"]
    route: str | None = None
    engine: str | None = None
    cost: float | None = None
    elapsed_s: float = 0.0
    failed_criteria: list[str] = Field(default_factory=list)
    reason: str = ""
    expected_fail: bool = False


class NessieManifest(BaseModel):
    started_at: str
    ended_at: str
    tier: str
    scope: str
    entries: list[NessieManifestEntry] = Field(default_factory=list)


def write_manifest(m: NessieManifest, path: Path) -> None:
    Path(path).write_text(m.model_dump_json(indent=2), encoding="utf-8")


def load_manifest(path: Path) -> NessieManifest:
    return NessieManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))
