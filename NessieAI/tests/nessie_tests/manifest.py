from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

from NessieAI.tests.nessie_tests import turn_cost


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


class TurnMeta(BaseModel):
    """One driven turn's money and model record: the harness side of the turn-record
    contract (`turn_cost` names the events and fields it is read from).

    Every field defaults, so a turn from a server older than the contract records
    Nones and empty lists, and `cost`/`partial` then say it was unmeasured rather
    than free.
    """
    turn: str = ""
    task_id: str | None = None
    route: str | None = None
    source: str | None = None
    # The engine part: the terminal event's `total_cost_usd`, CC or NS.
    engine_cost: float | None = None
    # The router part: `route_decided`'s `router_cost_usd`.
    router_cost: float | None = None
    cost_partial: bool = False
    router_cost_partial: bool = False
    models_used: list[str] = Field(default_factory=list)
    model_fallback: list[dict] = Field(default_factory=list)
    router_model: str | None = None
    router_fallback: dict | None = None
    # False when neither event carried its fallback key: silence, not "none".
    fallback_reported: bool = False
    # Derived once by `turn_cost.turn_total` and stored, so a reader of the
    # manifest never re-derives them differently.
    cost: float | None = None
    partial: bool = False

    @property
    def fell_back(self) -> bool:
        return turn_cost.fell_back(self)

    @classmethod
    def from_payload(cls, payload: dict, *, turn: str = "",
                     task_id: str | None = None) -> "TurnMeta":
        fields = turn_cost.read_turn(payload)
        cost, partial = turn_cost.turn_total(
            engine_cost=fields["engine_cost"], router_cost=fields["router_cost"],
            route=fields["route"], source=fields["source"],
            cost_partial=fields["cost_partial"],
            router_cost_partial=fields["router_cost_partial"])
        return cls(turn=turn, task_id=task_id, cost=cost, partial=partial, **fields)


def case_money(turns_meta, *, turns_sent: int) -> dict:
    """`cost`, `cost_partial` and `fallback_turns` for a case, from its turn records.

    `turns_sent` is how many turns the harness started. A started turn with no
    record is one whose driver raised: it may have billed, and was not observed.
    """
    turns_meta = list(turns_meta)
    cost, partial = turn_cost.case_total(
        [(t.cost, t.partial) for t in turns_meta],
        missing_turns=max(0, turns_sent - len(turns_meta)))
    return {"cost": cost, "cost_partial": partial, "turns_meta": turns_meta,
            "fallback_turns": sum(1 for t in turns_meta if t.fell_back)}


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
    # USD for this case: the SUM of its turns' observed costs, each turn being
    # its router call plus its engine turn (`turn_cost`). It used to be the LAST
    # turn's `total_cost_usd`, which dropped every earlier turn of a multi-turn
    # case. `None` means NO cost was ever observed, which is not the same as
    # "this case was free", and the two must never be summed together. See
    # ``cost_summary``.
    cost: float | None = None
    # True when `cost` is a floor: some turn that ran was measured only in part
    # or not at all, or said its own figure was partial. Always False when `cost`
    # is None, which already says nothing was observed. Defaults False so
    # manifests written before it existed load and read as they always did.
    cost_partial: bool = False
    # One record per driven turn, in order: its router and engine costs and the
    # models that answered it. Defaults to [] so older manifests still load.
    turns_meta: list[TurnMeta] = Field(default_factory=list)
    # How many of those turns had any model fall back (`TurnMeta.fell_back`).
    fallback_turns: int = 0
    elapsed_s: float = 0.0
    failed_criteria: list[str] = Field(default_factory=list)
    observations: list[CriterionObservation] = Field(default_factory=list)
    # Every turn's endpoint task id, in turn order, so a collector can join this
    # entry to its assistant_query_task rows without parsing prose out of
    # `reason` -- which is written for a human and carries no id at all in most
    # of its branches.
    #
    # A LIST, not a single id, because an entry is a CASE and a case is up to
    # three turns: 30 of the 127 variants in the paired selection are multi-turn
    # (158 turns in all), 25 of them `refine_and_recall`, whose whole subject is
    # the FOLLOW-UP. Recording only turn 0 would join every one of those to the
    # answer to a different question and silently discard 31 turns' artifacts.
    #
    # Defaults to [] so manifests written before this field existed still load;
    # `collect` records an entry with no ids as a join failure rather than as an
    # absence of artifacts, because those are different facts.
    task_ids: list[str] = Field(default_factory=list)
    poll_errors: int = 0
    reason: str = ""
    expected_fail: bool = False
    # True when this entry's `error` is a PROVIDER OUTAGE (the reply carried
    # NessieAI.tests.nessie_tests.outage.PROVIDER_OUTAGE_MARKER) rather than an infrastructure
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
    load-bearing one — if corpus.json changed between runs then the SAME seed selected
    a DIFFERENT set of cases, and a diff tool must say so rather than silently
    mis-pairing them. Until 2026-08-04 it hashed the vendored catalog plus the
    superseded overlay file, so fingerprints do not compare across that commit;
    that is correct, because the corpus file really did change.
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
    # DEAD since 2026-08-04 and kept only so manifests written before then still
    # parse: it named the ids where an overlay variant replaced a base one, and
    # the unified corpus has one definition per id, so no run writes it any more.
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
# overstate the problem in the other direction: a 283-case route run in which 240
# cases never left the harness is not 240 cases of unmeasured spend.
#
# NOT an exhaustive list of the free cases, deliberately. An `error` entry whose
# exception fired on the very first `post_query` (connection refused) also cost
# nothing, and it still counts as unmeasured. That is the fail-safe direction:
# `skipped` is decided by this harness and provable from the control flow above,
# whereas "the error happened before the request was billed" is a guess about
# where an arbitrary exception was raised. Over-reporting unmeasured spend is
# recoverable; under-reporting it is the bug this whole function exists to fix.
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
    has walked away. The engine's cost is on the turn's terminal event, which
    route-tier polling never reaches; the router's cost is on ``route_decided``,
    which it does reach. A route run therefore spends roughly one full turn per
    non-``unrelated`` gate and can account only for the router's part of it:
    such a case is PARTIAL (``turn_cost.turn_total``).

    An ``unrelated`` gate is the CHEAPEST route, not a free one. The router's
    model call is made on every turn and ``route_decided`` is emitted before the
    ``unrelated`` check (``NessieAI/cc/turn.py``, ``_run``). What ``unrelated``
    skips is the answering turn, not the router, so the router's price is its
    whole cost: fully measured when ``route_decided`` carries it, and UNMEASURED
    when it does not, because the router call was real and the harness never saw
    its price.

    Returned keys:

    ``total_cost``
        The observed total, or ``None`` when nothing was observed AND something
        executed. ``None`` rather than a sentinel string because it is the same
        "no measurement" value ``NessieManifestEntry.cost`` already uses, and
        because it keeps an unpatched consumer from printing a plausible number.
        Deliberately still 0.0 when nothing executed at all: that zero is true.
    ``cost_observed`` / ``cost_unmeasured``
        How many cases reported a cost, and how many RAN and did not.
    ``cost_partial_cases``
        How many of the ``cost_observed`` cases reported only part of their
        spend (``NessieManifestEntry.cost_partial``): a turn whose router or
        engine part went unseen, or whose own figure said it was partial.
    ``cost_partial``
        True when something was observed and the total is still a floor: some
        case was unmeasured or partial. A partial total presented as a total is
        the same lie in miniature. Every full-tier run before the router and NS
        prices existed was partial, because NS-routed cases emitted no
        ``total_cost_usd`` at all, and a CC turn that ends in ``query_error``
        carries no cost field (``NessieAI/cc/translate.py``).
    ``cost_display``
        The one preformatted string every summary prints, so the CLI, the HTML
        report and ``manage.py nessie`` cannot describe the same run differently.
    """
    entries = list(entries)
    observed = [e for e in entries if e.cost is not None]
    # getattr: a manifest entry from before the flag, or a test double, has none.
    partial = [e for e in observed if getattr(e, "cost_partial", False)]
    unmeasured = [e for e in entries
                  if e.cost is None and e.status not in _NEVER_EXECUTED]
    # float(): `sum([])` is an int, and a run that spent nothing rendering as
    # "$0" while every other run renders "$0.0" is a needless difference.
    total = round(float(sum(e.cost for e in observed)), 4)
    n_obs, n_part, n_un = len(observed), len(partial), len(unmeasured)
    executed = n_obs + n_un
    # `:.4f` rather than the raw float repr, so a $1.5 total does not render as
    # "$1.5" next to a "$1.4791" from the same `round(..., 4)`.
    if not n_obs and n_un:
        display = (f"unmeasured ({n_un} executed case(s) reported no cost; a turn the "
                   "harness stopped polling still runs and still bills)")
    elif n_un or n_part:
        display = (f"${total:.4f} observed on {n_obs} of {executed} executed case(s), "
                   f"PARTIAL: {n_part} case(s) measured only in part and {n_un} reported "
                   f"no cost, so the real spend is higher")
    elif n_obs:
        display = f"${total:.4f} (all {n_obs} executed case(s) reported a cost)"
    else:
        display = f"${total:.4f} (no case executed)"
    return {
        "total_cost": total if (n_obs or not n_un) else None,
        "cost_observed": n_obs,
        "cost_unmeasured": n_un,
        "cost_partial_cases": n_part,
        "cost_partial": bool(n_obs and (n_un or n_part)),
        "cost_display": display,
    }


def fallback_summary(entries) -> dict:
    """Which turns of a run had a model fall back, as the summaries print it.

    ``fallback_turns`` counts turns whose ``TurnMeta.fell_back`` is True and
    ``fallback_cases`` lists the entries holding them, so a reader can go from the
    headline to the turns. ``fallback_unreported_turns`` counts turns that carried
    neither fallback field: a server older than the turn-record contract says
    nothing, and nothing is not "no fallback". ``fallback_display`` is the one
    string the CLI, the HTML report and ``manage.py nessie`` print.
    """
    entries = list(entries)
    turns = [t for e in entries for t in (getattr(e, "turns_meta", None) or [])]
    cases = [e for e in entries if getattr(e, "fallback_turns", 0)]
    n_fb = sum(getattr(e, "fallback_turns", 0) for e in entries)
    n_unrep = sum(1 for t in turns if not t.fallback_reported)
    if not turns:
        display = "no turn recorded a model record"
    else:
        display = (f"{n_fb} of {len(turns)} turn(s) fell back to another model, "
                   f"in {len(cases)} case(s)")
        if n_unrep:
            display += f"; {n_unrep} turn(s) did not report whether they fell back"
    return {
        "fallback_turns": n_fb,
        "fallback_cases": cases,
        "fallback_unreported_turns": n_unrep,
        "fallback_display": display,
    }


def write_manifest(m: NessieManifest, path: Path) -> None:
    Path(path).write_text(m.model_dump_json(indent=2), encoding="utf-8")


def load_manifest(path: Path) -> NessieManifest:
    return NessieManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))
