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
    # True when this criterion could not be evaluated at all and was recorded
    # rather than scored. `passed` is True for such a row (an unevaluable
    # criterion is not evidence either way), which on its own is
    # indistinguishable from a genuine pass — the fact used to live ONLY as a
    # `"SKIPPED — "` prefix on `reason`, so every downstream reader had to
    # string-match it and a stored manifest could not be re-checked for vacuity
    # the way `outage` can. Defaults False so older manifests still load.
    skipped: bool = False
    reason: str = ""


class NessieManifestEntry(BaseModel):
    id: str
    family: str
    tier: Literal["route", "full"]
    # `xpass` = tagged known_fail but every criterion passed. Reporting it as
    # `passed` hides a stale expectation behind a green run.
    #
    # `no_assertions` = the case evaluated ZERO criteria: every one it carried was
    # recorded `skipped` as unobservable (or it carried none). That is not a pass
    # — nothing was tested — and reporting it as one is how a CC-routed case in a
    # floored family could look green while proving nothing. It counts as a real
    # failure (`runner._is_real_failure`), same as `xpass`, because both mean the
    # corpus is out of step with what the harness can actually observe.
    #
    # A value added, never removed: manifests written before it existed carry one
    # of the original five and still load unchanged.
    status: Literal["passed", "failed", "skipped", "error", "xpass", "no_assertions"]
    route: str | None = None
    engine: str | None = None
    # Which router produced the route on TURN 0, deliberately — not the last
    # turn, unlike `route` and `engine` above. `corpus.apply_route_policy`
    # attaches its route criterion to `turns[0]`, so this field describes the
    # same turn the assertion tests. A source outside
    # `runner.ROUTE_DECISION_SOURCES` means no router decided the turn: task 816
    # fell through to `heuristic`, a keyword regex that can never emit
    # `unrelated`. That is an infrastructure condition, not a pass.
    route_source: str | None = None
    # Every turn's source, in order. `route_source` alone cannot tell "BAML
    # routed all four turns" from "turn 3 fell to the keyword regex", and the
    # second is not evidence about routing. Turns whose payload carried no
    # `route_decided` event contribute nothing — they observed no routing
    # decision at all, which is not the same as a router that fell back.
    # Defaults to [] so manifests written before this field existed still load.
    route_sources: list[str] = Field(default_factory=list)
    cost: float | None = None
    elapsed_s: float = 0.0
    failed_criteria: list[str] = Field(default_factory=list)
    observations: list[CriterionObservation] = Field(default_factory=list)
    poll_errors: int = 0
    reason: str = ""
    expected_fail: bool = False
    # True when this entry's `error` is a PROVIDER OUTAGE (the reply carried
    # nessie_tests.outage.PROVIDER_OUTAGE_MARKER) rather than an infrastructure
    # fault of the harness's own. Both are `error`, but only an outage is exempt
    # from the gate: a TimeoutError against a dead endpoint still has to fail.
    # A separate flag rather than a sixth status, so `status == "error"` keeps
    # meaning exactly what it meant and old manifests still load.
    outage: bool = False


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
    # Set instead of seed/sample when the run came from an explicit --cases file.
    # Null seed AND null sample is how a reader tells a hand-authored probe from a
    # sampled run, which otherwise look identical in the manifest.
    cases_file: str | None = None
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
