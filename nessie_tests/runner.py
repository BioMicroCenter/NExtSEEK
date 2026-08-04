from __future__ import annotations
import os
import subprocess
import time
from pathlib import Path
from nessie_tests import corpus, evaluate, http_driver, report
from nessie_tests import route_observer as ro
from nessie_tests.manifest import (
    CriterionObservation, NessieManifest, NessieManifestEntry, cost_summary,
    write_manifest,
)


# The route sources that represent a real routing DECISION. Everything else
# means no router decided the turn, so that turn's route is not evidence about
# routing: `heuristic` is a keyword regex that can never emit `unrelated` (task
# 816 fell to it), and `forced` and `pipeline` bypass the router outright.
#
# `sticky` belongs HERE. It is a deliberate product decision taken DOWNSTREAM of
# a real BAML call — the router said NExtSEEK, the previous turn was
# container_cc, and the product chose to stay — so bucketing it under "the
# router was unavailable" would discard exactly the evidence a live run exists
# to collect about sticky routing.
#
# An ALLOWLIST, not a denylist, on purpose. Denying the three known fallbacks
# would silently TRUST any source added later; an allowlist flags it as
# not-evidence until someone decides it is. That is the fail-safe direction for
# an instrument whose whole job is telling the truth about the product.
ROUTE_DECISION_SOURCES = frozenset({"baml", "sticky"})


def default_route_criterion(variant) -> dict | None:
    """No route expectation is injected any more. Deliberately.

    This used to return ``route == nextseek_query`` for every variant tagged
    "base", which ``corpus.load_base`` applies to all 366 imported variants. No
    one ever curated that: it was an assumption, and it made deliberate
    ``container_cc`` routing (open-ended analysis, resource creation) read as a
    product failure. Routing is asserted where it has actually been decided —
    the ``route_gate`` variants in corpus.json, which carry explicit ``route``
    criteria and run in the route tier — cheaper, but not free: see the
    ``case_tier`` comment in ``run_suite`` for what route-only does and does not
    stop.
    """
    return None


def _iso(clock):  # avoid datetime.now() so tests are deterministic
    return f"t={clock():.3f}"


def corpus_fingerprint(corpus_path=None) -> str:
    """sha256 over the unified corpus bytes.

    This is what makes a two-run diff honest. `--seed` changes sampling, not the
    database, so the same seed picks the same cases — but only if the corpus is
    unchanged. If corpus.json was edited between runs the same seed selected a
    DIFFERENT set, and a diff tool must say so rather than silently mis-pair cases.

    Until 2026-08-04 it hashed the vendored catalog plus the superseded overlay
    file. Fingerprints do not compare across that boundary, and should not: the
    corpus file really did change.
    """
    try:
        return corpus.sha256_of(corpus_path or corpus._UNIFIED)
    except Exception:
        return "<unreadable>"


def git_sha() -> str | None:
    """Short HEAD sha, or None outside a checkout (the deployed image has no .git)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


_MAX_OBSERVED_CHARS = 600


def _trim(value):
    """Keep an observed value readable in the manifest.

    Some fields (api_result_full, a whole cypher result set) are megabytes; the
    point of recording them is triage, not archival.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= _MAX_OBSERVED_CHARS else text[:_MAX_OBSERVED_CHARS] + " …[trimmed]"


# Criteria that cannot be honestly evaluated once the route is forced. A route
# assertion under `force_route` tests the harness's own request body, not the
# product's routing, so keeping it would manufacture a pass on every arm that
# happens to agree and a failure on every arm that does not. Neither is evidence.
STRIPPED_UNDER_FORCING = frozenset({"route", "engine"})


def _criterion_field(c):
    """`criteria` mixes PassCriterion objects with the plain dicts
    `default_route_criterion` is declared to return, so both shapes are read."""
    return c.get("field") if isinstance(c, dict) else getattr(c, "field", None)


def run_case(v, *, tier, post_query, get_progress, bundle_reader=None,
             pace_s=0.0, force_route=None, strip_route_criteria=False,
             full_timeout_s=600.0, sleep=time.sleep, clock=time.monotonic
             ) -> NessieManifestEntry:
    """Drive one variant to an entry. The body `run_suite` used to inline.

    Extracted so `bayesian.py` can call it twice per variant with opposite
    `force_route` values without forking the poll loop, the route observation
    rules, the outage handling or the cost accounting. Every one of those has been
    a bug at least once; there must go on being exactly one of each.

    `force_route` and `strip_route_criteria` are inert unless set, so `run_suite`
    behaves exactly as it did before the extraction.
    """
    expected_fail = "known_fail" in v.tags
    is_gate = "route_gate" in v.tags
    # requires_env skip (both tiers): a variant needing an unset env var is
    # not runnable here — record it skipped, don't fail the gate.
    missing_env = [name for name in v.requires_env if name not in os.environ]
    if missing_env:
        return NessieManifestEntry(
            id=v.id, family=v.family, tier=tier, status="skipped",
            reason=f"requires_env unset: {missing_env}", expected_fail=expected_fail)
    # tier selection: the route tier only exercises route_gate cases (route
    # assertions only). Anything else needs a real turn/launch — skip it,
    # don't fail, so a route-tier run stays SMALL. It does not stay
    # side-effect-free: the gates it does run execute to completion on the
    # server (see the `case_tier` comment below), so a pipeline-launch gate
    # really launches. Skipping is what stops a route run doing that 283
    # times, not something that makes any single case free.
    if tier == "route" and not is_gate:
        return NessieManifestEntry(
            id=v.id, family=v.family, tier=tier, status="skipped",
            reason="needs execution; skipped at route tier", expected_fail=expected_fail)
    # per-case DEPTH: route_gate cases are ALWAYS driven route-only, even in
    # a full run; everything else is driven at the global tier's depth.
    #
    # Route-only is a CLIENT-side stop and NOTHING ELSE. This comment used to
    # claim these cases "never execute a real turn/launch". They do.
    # `http_driver.drive` breaks its own poll loop at `route_decided`
    # (http_driver.py:96-98); there is no cancel, no abort and no DELETE
    # anywhere in the harness or in the endpoint. The server has already
    # started the turn on a daemon thread and returned 202, and its only
    # early return is ROUTE_UNRELATED (cc_assistant.py:352-366) — both the NS
    # and the CC branches fall straight through into full execution. So every
    # gate whose route is not `unrelated` runs to completion, and on a CC gate
    # that is a full Opus turn, launch included.
    #
    # NO ROUTE IS FREE, `unrelated` included. It is merely the cheapest: the
    # BAML router call (`_decide_route` at cc_assistant.py:203 →
    # `cc_router.decide` → `_baml_decision`) is made on EVERY turn, and
    # `route_decided` is emitted at cc_assistant.py:347-350, before the
    # ROUTE_UNRELATED check at :352. What `unrelated` skips is the answering
    # turn, not the router that decided to skip it.
    #
    # What route-only actually buys is WALL CLOCK and a shorter window for the
    # harness to trip over a slow turn — not money, and not blast radius. It
    # also costs the run its accounting: `v_cost` below is read off
    # `query_complete`, which route-tier polling never observes, so the spend
    # is real and unmeasurable from here. `manifest.cost_summary` reports that
    # as `unmeasured` rather than as $0.
    case_tier = "route" if is_gate else tier
    session_id = None
    v_status, v_route, v_engine, v_cost, failed, reason = "passed", None, None, None, [], ""
    v_route_source = None
    v_route_sources: list[str] = []
    v_outage = False
    observations: list[CriterionObservation] = []
    poll_errors = 0
    # Case-level, not per-turn: it is reported once on the entry, so a multi-turn
    # case must report every criterion it dropped, not just its last turn's.
    stripped = 0
    # Did ANY turn of this case really test something? Accumulated across the
    # whole case on purpose — `status` is a case-level field, so claiming
    # "no assertions" about a case that asserted four real criteria on its
    # first turn would be the instrument lying in the other direction. The
    # vacuous TURN stays visible: every one of its observation rows is
    # recorded `skipped` with the reason that skipped it.
    evaluated_any = False
    t0 = clock()
    extra = default_route_criterion(v)
    try:
        for i, turn in enumerate(v.turns):
            if pace_s and i > 0:
                sleep(pace_s)
            # force_new ONLY on a case's first turn: isolate the case, but
            # keep its own follow-ups in the session its seed opened.
            res = http_driver.drive(turn.query, tier=case_tier, post_query=post_query,
                                    get_progress=get_progress, session_id=session_id,
                                    force_new=(session_id is None),
                                    force_route=force_route, full_timeout_s=full_timeout_s,
                                    sleep=sleep, clock=clock)
            session_id = res.session_id
            poll_errors += res.poll_errors
            v_route, v_engine = res.route_obs.route, res.route_obs.engine
            # `route` and `engine` stay LAST-write-wins: the report displays
            # the route the case ended on, and changing that would change
            # what every existing report says.
            #
            # `route_source` deliberately does NOT. corpus.apply_route_policy
            # attaches its route criterion to turns[0], which is always a COLD
            # turn, so pinning the recorded source to turn 0 makes the field
            # describe the same turn the assertion tests. Assigning it every
            # turn left an entry carrying its LAST turn's source — a follow-up
            # that went `sticky` was never what the route criterion was about.
            if i == 0:
                v_route_source = res.route_obs.source
            # ...and the whole sequence, because one value cannot say whether
            # some middle turn fell to the keyword regex. A turn with no
            # `route_decided` event contributes nothing: it observed no routing
            # decision at all, which is not a router that fell back.
            if res.route_obs.source is not None:
                v_route_sources.append(res.route_obs.source)
            qc = next((e["data"] for e in reversed(res.payload.get("progress") or [])
                       if e.get("event") == "query_complete"), {})
            v_cost = qc.get("total_cost_usd", v_cost)
            bundle_summary = None
            if case_tier == "full" and bundle_reader is not None and session_id is not None:
                bundle_summary = bundle_reader(session_id)
            last_reply = qc.get("reply")
            criteria = list(turn.pass_criteria) + ([extra] if extra else [])
            if strip_route_criteria:
                kept = [c for c in criteria
                        if _criterion_field(c) not in STRIPPED_UNDER_FORCING]
                stripped += len(criteria) - len(kept)
                criteria = kept
            passed, results, observed = evaluate.evaluate_turn(
                res.payload, criteria, res.route_obs,
                last_reply=last_reply, bundle_summary=bundle_summary)
            observations += [
                CriterionObservation(
                    turn=turn.label, field=r["field"], op=r["op"], expected=r.get("value"),
                    observed=_trim(observed.get(r["field"])),
                    passed=r["passed"], skipped=r.get("skipped", False),
                    reason=r.get("reason", ""))
                for r in results
            ]
            evaluated_any = evaluated_any or evaluate.any_criterion_evaluated(results)
            # One authority for this turn's status: passed / failed / error.
            turn_status = evaluate.classify_turn_status(passed, last_reply)
            if turn_status == "error":
                # Provider outage: the fallback chain gave up before the
                # product ran, so this turn is infrastructure, not evidence.
                # The case stops here either way — its remaining turns share a
                # session with a turn that never reached the product, so they
                # cost money and prove nothing.
                if failed:
                    # ...but an EARLIER turn already produced a genuine red,
                    # and an outage on a later turn does not un-fail it. Stay
                    # `failed`, stay gate-visible. Overwriting here sent a real
                    # regression out as an exempt grey `outage` row.
                    reason = (f"{evaluate.OUTAGE_REASON}. Recorded AFTER "
                              f"{len(failed)} criterion failure(s) on an earlier "
                              "turn, which still count")
                else:
                    v_status, v_outage, reason = "error", True, evaluate.OUTAGE_REASON
                break
            if turn_status == "failed":
                v_status = "failed"
                failed += [f"{turn.label}:{r['field']}" for r in results if not r["passed"]]
    except Exception as exc:  # infra/endpoint failure ≠ assertion failure
        v_status, reason = "error", f"{type(exc).__name__}: {exc}"
    # A case that evaluated nothing is not a pass. Guarded on "passed" so
    # every other outcome wins: `failed` (a red is evidence, and it stands),
    # `error`/outage (which say WHY nothing was proved, and the outage
    # exemption depends on the status staying `error`).
    #
    # Placed BEFORE _apply_xpass deliberately — a known_fail case that
    # asserted nothing must not be promoted to `xpass`, which would claim the
    # expected failure had stopped happening. It demonstrated neither.
    if v_status == "passed" and not evaluated_any:
        v_status, reason = "no_assertions", evaluate.NO_ASSERTIONS_REASON
    if strip_route_criteria:
        # A forced arm is not evidence about a known_fail expectation. The tag
        # records something about ROUTER-DECIDED behaviour, and a forced turn
        # never let the router decide, so promoting a pass to `xpass` would
        # claim the expected failure had stopped happening on evidence that
        # cannot support it.
        xpass_reason = None
    else:
        v_status, xpass_reason = _apply_xpass(v_status, expected_fail)
    if xpass_reason:
        reason = xpass_reason
    # Appended LAST, after the no_assertions guard and the xpass promotion, both
    # of which overwrite `reason` outright. Stripping every criterion off a case
    # is exactly what produces `no_assertions`, so the count is at its most
    # load-bearing precisely where an earlier placement would have lost it.
    if stripped:
        note = f"stripped {stripped} route criteri{'on' if stripped == 1 else 'a'} (forced route)"
        reason = f"{reason}; {note}" if reason else note
    return NessieManifestEntry(
        id=v.id, family=v.family, tier=tier, status=v_status, route=v_route, engine=v_engine,
        route_source=v_route_source, route_sources=v_route_sources,
        cost=v_cost, elapsed_s=round(clock() - t0, 3), failed_criteria=failed,
        observations=observations, poll_errors=poll_errors,
        reason=reason, expected_fail=expected_fail, outage=v_outage)


def run_suite(*, base_url, auth_header, tier, scope="specific", family=None, variant_id=None,
              corpus_path, out_dir, post_query=None, get_progress=None, bundle_reader=None,
              pace_s=0.0, run_consistency: bool = False, sample: float = 1.0, seed: int = 0,
              cases_path=None, sleep=time.sleep, clock=time.monotonic) -> NessieManifest:
    if post_query is None or get_progress is None:
        post_query, get_progress = http_driver.make_default_clients(base_url, auth_header)
    if cases_path:
        # An explicit running order replaces sampling entirely: scope, family,
        # variant_id, sample and seed are all selection knobs and the file IS the
        # selection. Mixing them would make "what ran" depend on two sources.
        variants = corpus.select_cases(corpus.merged(corpus_path),
                                       *corpus.load_case_file(cases_path))
    else:
        variants = corpus.select(corpus.merged(corpus_path), scope=scope, family=family,
                                 variant_id=variant_id)
        if sample < 1.0:
            variants = corpus.sample(variants, sample, seed)
    # Recorded so two run directories can be told apart and diffed honestly.
    #
    # `overridden_ids` is deliberately absent since 2026-08-04. It named the ids
    # where an overlay variant replaced a base one, and that merge no longer
    # happens: there is one definition per id. `manifest.py` keeps the field with
    # a default_factory so old manifests keep their value and new ones record [].
    run_meta = {
        "seed": None if cases_path else seed,
        "sample": None if cases_path else sample,
        "cases_file": str(cases_path) if cases_path else None,
        "selected_ids": [v.id for v in variants],
        "corpus_fingerprint": corpus_fingerprint(corpus_path),
        "base_url": base_url,
        "git_sha": git_sha(),
    }
    started = _iso(clock)
    entries: list[NessieManifestEntry] = []
    for v in variants:
        entries.append(run_case(
            v, tier=tier, post_query=post_query, get_progress=get_progress,
            bundle_reader=bundle_reader, pace_s=pace_s, sleep=sleep, clock=clock))
    if run_consistency:
        from nessie_tests import consistency
        for g in corpus.load_consistency_groups(corpus_path):
            def _drive(q):
                # force_new: without it the API falls back to the caller's most
                # recently updated session, so the group inherited whatever ran
                # before it. Confirmed in the 2026-07-27 run: tasks 837 (a CC write),
                # 838 and 839 all shared sid=1310fa6cbdc74d50903e709e619db733, which
                # means the "same question twice" comparison was contaminated by a
                # third, unrelated turn's results_history.
                r = http_driver.drive(q, tier="full" if tier == "full" else "route",
                                      post_query=post_query, get_progress=get_progress,
                                      force_new=True,
                                      sleep=sleep, clock=clock)
                # `reply` is what lets run_group see a provider outage. Without it
                # the group only ever saw {route, count}, so an outage surfaced as
                # "count could not be resolved" and read as product drift.
                return {"route": r.route_obs.route,
                        "count": consistency.get_result_count(r.payload),
                        "reply": consistency.get_last_reply(r.payload)}
            g_t0 = clock()
            g_expected_fail = "known_fail" in g.get("tags", [])
            try:
                gr = consistency.run_group(g, _drive)
                # An outaged group is infrastructure, exactly as an outaged turn
                # is. It is NOT eligible for xpass either: a known_fail group that
                # never reached the product has not demonstrated anything, in
                # either direction. (getattr: a test double for run_group may
                # predate the flag.)
                g_outage = getattr(gr, "outage", False)
                if g_outage:
                    g_status, g_reason = "error", "; ".join(gr.reasons)
                else:
                    g_status, g_reason = _apply_xpass(
                        "passed" if gr.passed else "failed", g_expected_fail)
                entries.append(NessieManifestEntry(
                    id=g["id"], family="nessie_consistency", tier=tier,
                    status=g_status, reason=g_reason, outage=g_outage,
                    elapsed_s=round(clock() - g_t0, 3),
                    # The group's per-query evidence used to be discarded, so a
                    # consistency result was a bare pass/fail with nothing to review.
                    # CriterionObservation.op is a plain str, so "observed" is legal
                    # and no schema change is needed.
                    observations=[
                        CriterionObservation(
                            turn=o.get("query", ""), field="count", op="observed",
                            expected=None, observed=_trim(o.get("count")),
                            passed=gr.passed, reason="")
                        for o in gr.observations
                    ] + [
                        CriterionObservation(
                            turn=o.get("query", ""), field="route", op="observed",
                            expected=None, observed=_trim(o.get("route")),
                            passed=gr.passed, reason="")
                        for o in gr.observations
                    ],
                    # An outaged group failed no criterion — it evaluated none.
                    # Its reason already carries the whole story.
                    failed_criteria=[] if g_outage else gr.reasons,
                    expected_fail=g_expected_fail))
            except Exception as exc:  # infra/endpoint failure ≠ assertion failure
                entries.append(NessieManifestEntry(
                    id=g["id"], family="nessie_consistency", tier=tier,
                    status="error", reason=f"{type(exc).__name__}: {exc}",
                    elapsed_s=round(clock() - g_t0, 3),
                    expected_fail=g_expected_fail))
    manifest = NessieManifest(started_at=started, ended_at=_iso(clock), tier=tier, scope=scope,
                              entries=entries, **run_meta)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, Path(out_dir) / "manifest.json")
    report.generate_html(manifest, Path(out_dir))
    return manifest


def _apply_xpass(status: str, expected_fail: bool) -> "tuple[str, str]":
    """Promote a passing known_fail to ``xpass``.

    Module-level and shared by BOTH the variant loop and the consistency branch.
    It used to be inline in the variant loop only, so a consistency group tagged
    known_fail that passed was recorded as plain "passed" and then printed under
    "expected to fail" — which is how cons.nhp_sequencing_engine read as a
    reassuring known failure while actually passing. Keeping one implementation
    means the bug cannot relocate to a third call site.
    """
    if expected_fail and status == "passed":
        return "xpass", "known_fail case passed every criterion; the expectation is stale"
    return status, ""


def _is_real_failure(entry) -> bool:
    """Is this entry a failure the run should be held to?

    ONE definition, called by both ``classify_entries`` and ``gate_failed``. They
    have disagreed before and it produced a run that printed "GATE: PASS" and then
    exited 1, so the two must never re-derive this independently.

    A provider outage is exempt. It is an ``error`` like any other infrastructure
    fault, but unlike a TimeoutError against a dead endpoint it says nothing at all
    about the product: the fallback chain 503'd before the turn ran. Ten of the
    eighteen reds in the 2026-08-03 seed-6 run were one Bedrock outage, and letting
    that fail a gate makes every outage look like a regression. Every OTHER error
    keeps its gate-failing behaviour.

    The exemption does NOT extend to an entry that also recorded a criterion
    failure. The runner no longer emits that combination (a mid-case outage
    leaves an already-failed case `failed`), but this is the second of two
    independent guards on purpose: an outage flag silently swallowing a real red
    is precisely the failure mode this whole change exists to prevent, and a
    manifest from an older run can still carry the pair.

    ``no_assertions`` counts, and `expected_fail` does NOT excuse it — same
    treatment as ``xpass``, for the same reason. A `known_fail` tag is a claim
    that the case FAILS; a case that evaluated zero criteria demonstrated
    neither that nor its absence, so the tag cannot excuse it. Both statuses mean
    the corpus is asserting something out of step with reality, which is exactly
    the drift that makes a green run misleading.
    """
    # `status == "error"` is part of the exemption, not decoration. A
    # `no_assertions` entry ALWAYS has empty `failed_criteria`, so without it a
    # manifest carrying `outage=True` alongside `no_assertions` was exempted
    # wholesale. The runner cannot emit that pair (the outage branch sets
    # `error` and breaks), but this guard exists for manifests the runner did
    # not write, which is the only place the pair can occur.
    if (getattr(entry, "outage", False) and entry.status == "error"
            and not entry.failed_criteria):
        return False
    return (entry.status in ("xpass", "no_assertions")
            or (entry.status in ("failed", "error") and not entry.expected_fail))


def _not_routing_evidence(entry) -> bool:
    """Did ANY turn of this case get its route from something other than a decision?

    If one turn fell to the keyword regex, that turn's route says nothing about
    routing, so the case cannot be read as evidence either — hence "any", not
    "turn 0". See ``ROUTE_DECISION_SOURCES`` for why `sticky` is a decision and
    `heuristic` / `forced` / `pipeline` are not.

    Falls back to the single ``route_source`` when ``route_sources`` is empty, so
    a manifest written before the sequence existed classifies exactly as it did
    then — including the case where NO route was observed at all (``None``),
    which was never bucketed and must not start being: it is silence about the
    router, not a report of one falling back.
    """
    sources = list(getattr(entry, "route_sources", None) or [])
    if not sources:
        one = getattr(entry, "route_source", None)
        sources = [one] if one is not None else []
    return any(s not in ROUTE_DECISION_SOURCES for s in sources)


def classify_entries(manifest: NessieManifest) -> dict:
    """Split a manifest into the buckets a summary needs.

    Single source of truth, shared by ``gate_failed`` and the management command's
    printed summary. They used to classify independently and disagreed: the summary's
    "real failures" excluded xpass while the gate counted it, so a run could print
    "GATE: PASS" and then exit 1. And its known-fail bucket included *passing*
    known-fails, which is the line that read as reassurance.
    """
    entries = manifest.entries
    real_fails = [e for e in entries if _is_real_failure(e)]
    # Disjoint from real_fails by construction: a case that recorded a genuine red
    # before the provider died WAS scored, so it is not a case lost to an outage
    # and must not be listed on the exempt line as well as the failure line.
    outage = [e for e in entries
              if getattr(e, "outage", False) and not _is_real_failure(e)]
    return {
        "total": len(entries),
        "counts": {s: sum(1 for e in entries if e.status == s)
                   for s in ("passed", "failed", "skipped", "error", "xpass",
                             "no_assertions")},
        "real_fails": real_fails,
        # Cases the LLM provider took out from under the run. Their manifest status
        # is `error`, so they are already inside counts["error"] — this bucket is
        # what lets the printed summary give them their own line instead of folding
        # them into the pass/fail headline where they read as regressions.
        "outage": outage,
        # Known-fails that actually failed. A known_fail that PASSED is an xpass and
        # belongs in real_fails, not here. An OUTAGED one belongs in neither: it
        # demonstrated neither the known failure nor its absence — and neither
        # does a `no_assertions` one, which evaluated no criteria at all, so it is
        # excluded for exactly the same reason.
        "known_failed": [e for e in entries
                         if e.expected_fail and e.status not in ("xpass", "no_assertions")
                         and not getattr(e, "outage", False)],
        # Cases where some turn's route came from no router at all. Task 816 fell
        # to `heuristic` (1 in 65), a keyword regex that can never emit
        # `unrelated` — so its route was not evidence about routing. An
        # infrastructure flag, not a pass. The key name is load-bearing: the
        # management command reads it.
        "heuristic_routed": [e for e in entries if _not_routing_evidence(e)],
        # Money. `total_cost`, `cost_observed`, `cost_unmeasured`, `cost_partial`
        # and `cost_display` — see `manifest.cost_summary` for why a summed
        # `e.cost or 0.0` was a lie and what each key is allowed to claim.
        # `total_cost` keeps its name and its float-or-None type because
        # `manage.py nessie` reads it; `cost_display` is what a summary should
        # actually print.
        **cost_summary(entries),
    }


def gate_failed(manifest: NessieManifest) -> int:
    """Count real failures.

    A known_fail case that fails is expected and excluded, and so is a provider
    outage. An ``xpass`` is always counted: it means the corpus is asserting
    something that is no longer true, which is exactly the kind of drift that
    makes a green run misleading.

    Delegates to ``_is_real_failure`` so this and ``classify_entries`` cannot
    drift apart again.
    """
    return sum(1 for e in manifest.entries if _is_real_failure(e))
