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
    # USD for this case, read off the last `query_complete` event's
    # `total_cost_usd`. `None` means NO cost was ever observed — which is not
    # the same as "this case was free", and the two must never be summed
    # together. Only the container_cc engine emits `total_cost_usd` at all, and
    # a route-tier turn stops polling long before `query_complete`, so `None` is
    # the common case rather than the exceptional one. See ``cost_summary``.
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


# Entry statuses that mean the harness never issued a request for this case.
# `run_suite` reaches `status="skipped"` by exactly two `continue`s (an unset
# `requires_env`, and a non-gate case at route tier) and BOTH precede the call to
# `http_driver.drive`, so a skipped case genuinely cost nothing. That is a real
# $0, not an unobserved one, and folding it in with the unobserved ones would
# overstate the problem in the other direction: a 280-case route run in which 240
# cases never left the harness is not 240 cases of unmeasured spend.
_NEVER_EXECUTED = frozenset({"skipped"})


def cost_summary(entries) -> dict:
    """What a run is entitled to SAY about money.

    This used to be one expression — ``round(sum(e.cost or 0.0 for e in entries), 4)``
    — and the ``or 0.0`` is the whole bug: it turns "we never saw a cost" into a
    confident ``$0.00``. Three different facts were collapsed into that zero:

    * the case never ran (a genuine 0),
    * the case ran and reported 0.0 (an observed 0),
    * the case ran, was billed, and the harness never polled far enough to see it.

    The third is the normal outcome for a route-tier gate. ``http_driver.drive``
    breaks the CLIENT poll loop at ``route_decided`` (http_driver.py:96-98) and
    that is all it does — there is no cancel, abort or DELETE anywhere in the
    harness or in the endpoint. The server started the turn on a daemon thread
    and returned 202, and its only early return is ``ROUTE_UNRELATED``
    (nextseek_api/services/cc_assistant.py:352-366), so every gate that is not
    routed ``unrelated`` runs to completion and bills for it after the harness
    has walked away. Cost is read off ``query_complete``, which route-tier
    polling never reaches. A route run therefore spends roughly one full turn
    per non-``unrelated`` gate and can account for none of it.

    Returned keys:

    ``total_cost``
        The observed total, or ``None`` when nothing was observed AND something
        executed. ``None`` rather than a sentinel string because it is the same
        "no measurement" value ``NessieManifestEntry.cost`` already uses, and
        because it keeps an unpatched consumer from printing a plausible number.
        Deliberately still 0.0 when nothing executed at all: that zero is true.
    ``cost_observed`` / ``cost_unmeasured``
        How many cases reported a cost, and how many RAN and did not.
    ``cost_partial``
        True when both are non-zero. A partial total presented as a total is the
        same lie in miniature — every full-tier run so far has been partial,
        because NS-routed cases emit no ``total_cost_usd`` at all.
    ``cost_display``
        The one preformatted string every summary prints, so the CLI, the HTML
        report and ``manage.py nessie`` cannot describe the same run differently.
    """
    entries = list(entries)
    observed = [e for e in entries if e.cost is not None]
    unmeasured = [e for e in entries
                  if e.cost is None and e.status not in _NEVER_EXECUTED]
    # float(): `sum([])` is an int, and a run that spent nothing rendering as
    # "$0" while every other run renders "$0.0" is a needless difference.
    total = round(float(sum(e.cost for e in observed)), 4)
    n_obs, n_un = len(observed), len(unmeasured)
    executed = n_obs + n_un
    if not n_obs and n_un:
        display = (f"unmeasured ({n_un} executed case(s) reported no cost; a turn the "
                   "harness stopped polling still runs and still bills)")
    elif n_un:
        display = (f"${total} observed on {n_obs} of {executed} executed case(s) — "
                   f"PARTIAL, {n_un} reported no cost, so the real spend is higher")
    elif n_obs:
        display = f"${total} (all {n_obs} executed case(s) reported a cost)"
    else:
        display = f"${total} (no case executed)"
    return {
        "total_cost": total if (n_obs or not n_un) else None,
        "cost_observed": n_obs,
        "cost_unmeasured": n_un,
        "cost_partial": bool(n_obs and n_un),
        "cost_display": display,
    }


def write_manifest(m: NessieManifest, path: Path) -> None:
    Path(path).write_text(m.model_dump_json(indent=2), encoding="utf-8")


def load_manifest(path: Path) -> NessieManifest:
    return NessieManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))
