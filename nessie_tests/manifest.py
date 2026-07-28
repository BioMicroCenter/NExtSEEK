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
    # Which router produced the route. Anything other than "baml" means the BAML
    # router did not decide this turn — task 816 fell through to `heuristic`, a
    # keyword regex that can never emit `unrelated`. That is an infrastructure
    # condition, not a pass.
    route_source: str | None = None
    cost: float | None = None
    elapsed_s: float = 0.0
    failed_criteria: list[str] = Field(default_factory=list)
    observations: list[CriterionObservation] = Field(default_factory=list)
    poll_errors: int = 0
    reason: str = ""
    expected_fail: bool = False


class NessieManifest(BaseModel):
    """One run's record.

    The bookkeeping fields below exist so two runs can be diffed honestly. Without
    them three run directories are indistinguishable: same tier, same scope, no
    record of the seed or of which cases were selected. ``corpus_fingerprint`` is the
    load-bearing one — if the overlay changed between runs then the SAME seed selected
    a DIFFERENT set of cases, and a diff tool must say so rather than silently
    mis-pairing them.
    """
    started_at: str
    ended_at: str
    tier: str
    scope: str
    seed: int | None = None
    sample: float | None = None
    selected_ids: list[str] = Field(default_factory=list)
    overridden_ids: list[str] = Field(default_factory=list)
    corpus_fingerprint: str | None = None
    base_url: str | None = None
    git_sha: str | None = None
    entries: list[NessieManifestEntry] = Field(default_factory=list)


def write_manifest(m: NessieManifest, path: Path) -> None:
    Path(path).write_text(m.model_dump_json(indent=2), encoding="utf-8")


def load_manifest(path: Path) -> NessieManifest:
    return NessieManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))
