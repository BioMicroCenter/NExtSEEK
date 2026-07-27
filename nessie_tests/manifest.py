from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field


class CriterionObservation(BaseModel):
    """One criterion's expected-vs-OBSERVED record.

    The manifest used to store criterion *names* only, so a failure list could
    not be triaged without querying assistant_query_task after the fact. The
    observed value is what makes a run self-explaining.
    """
    turn: str
    field: str
    op: str
    expected: object | None = None
    observed: object | None = None
    passed: bool
    reason: str = ""


class NessieManifestEntry(BaseModel):
    id: str
    family: str
    tier: Literal["route", "full"]
    # `xpass` = tagged known_fail but every criterion passed. Reporting it as
    # `passed` hides a stale expectation behind a green run.
    status: Literal["passed", "failed", "skipped", "error", "xpass"]
    route: str | None = None
    engine: str | None = None
    cost: float | None = None
    elapsed_s: float = 0.0
    failed_criteria: list[str] = Field(default_factory=list)
    observations: list[CriterionObservation] = Field(default_factory=list)
    poll_errors: int = 0
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
