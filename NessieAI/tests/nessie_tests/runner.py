from __future__ import annotations
import contextlib
import dataclasses
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from NessieAI.tests.nessie_tests import corpus, evaluate, http_driver, report
from NessieAI.tests.nessie_tests import route_observer as ro
# Django-free at import time; re-exported so callers catch one class from here.
from NessieAI.tests.nessie_tests.bundle import BundleReaderUnavailable
from NessieAI.tests.nessie_tests.manifest import (
    CriterionObservation, NessieManifest, NessieManifestEntry, cost_summary,
    load_manifest, write_manifest,
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


# The evaluation venue runs a `git archive` snapshot of the branch, which has no
# .git; its prepare step writes the snapshot's sha into this file at the snapshot
# root (scripts/graph_search/nessie_venue.sh). It is the repository root here.
SNAPSHOT_FILE = Path(__file__).resolve().parents[3] / "SNAPSHOT"


def git_sha() -> str | None:
    """Short HEAD sha; else the sha in SNAPSHOT_FILE; else None.

    The deployed image and the venue's snapshot have no .git, so without the
    fallback every venue run would record `None` and a resumed arms run could
    never tell that its code had not changed.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=5,
        )
        sha = out.stdout.strip() if out.returncode == 0 else ""
        if sha:
            return sha
    except Exception:
        pass
    try:
        text = SNAPSHOT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text.split()[0] if text else None


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
#
# `route_source` joined them on review. Under forcing it is `"forced"` on BOTH
# arms by construction — that is what `ROUTE_DECISION_SOURCES` excludes it for,
# three lines of reasoning above — so an assertion on it is a harness artifact in
# exactly the way the other two are. It is asserted by 2 corpus criteria, both on
# route_gate variants, which `test_no_route_gate_variant_is_selected_for_the_paid_
# paired_run` already keeps out of the paired run; this closes the gap anyway
# rather than relying on that to hold.
STRIPPED_UNDER_FORCING = frozenset({"route", "engine", "route_source"})


def _criterion_field(c):
    """`criteria` mixes PassCriterion objects with the plain dicts
    `default_route_criterion` is declared to return, so both shapes are read."""
    return c.get("field") if isinstance(c, dict) else getattr(c, "field", None)


def run_case(v, *, tier, post_query, get_progress, bundle_reader=None,
             pace_s=0.0, force_route=None, force_parser_mode=None,
             strip_route_criteria=False, payload_dir=None, prompt_variant=None,
             full_timeout_s=600.0, sleep=time.sleep, clock=time.monotonic
             ) -> NessieManifestEntry:
    """Drive one variant to an entry. The body `run_suite` used to inline.

    Extracted so `bayesian.py` can call it twice per variant with opposite
    `force_route` values without forking the poll loop, the route observation
    rules, the outage handling or the cost accounting. Every one of those has been
    a bug at least once; there must go on being exactly one of each.

    `force_route` and `strip_route_criteria` are inert unless set, so `run_suite`
    behaves exactly as it did before the extraction.

    `strip_route_criteria` now means "this turn's engine was chosen by the harness"
    and does TWO things, both resting on that one fact. It removes `route`/`engine`
    (`STRIPPED_UNDER_FORCING`), and it hands `evaluate_turn` `forced=True`, which
    additionally skips NS-pipeline-internal criteria on an arm that really ran
    container_cc — see the ENGINE_NEUTRAL_FIELDS comment in `evaluate.py` for why
    that is sound only under forcing, and why the router-decided path is untouched.
    The name is kept. `run_suite` passes it only on a forced run (`force_route`
    set), so no router-decided run can reach either behaviour.

    `force_parser_mode` rides on every turn beside `force_route`: the evaluation
    switch of the graph_search Nessie POC (spec E2), which `run_arms` sets per arm.

    `payload_dir`, when given, receives each driven turn's final payload as
    `<payload_dir>/<variant id>/<turn label>.json`: the query, the task and session
    ids, the status, the route observation, the whole `query_complete` data (reply,
    debug, files, artifacts) and the turn's wall time. It is written as soon as the
    turn returns, so a failure later in the case cannot lose a paid turn's evidence.
    The arms scorer reads these files. Nothing is written without it.
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
    # EVERY turn's task id, not just the first: this is the collector's join key
    # to assistant_query_task, and a follow-up turn's row is where a
    # refine_and_recall case's actual answer lives. Accumulated inside the turn
    # loop so a case that raises partway still reports the turns it did drive.
    task_ids: list[str] = []
    v_status, v_route, v_engine, v_cost, failed, reason = "passed", None, None, None, [], ""
    v_route_source = None
    v_route_sources: list[str] = []
    v_outage = False
    observations: list[CriterionObservation] = []
    poll_errors = 0
    # Case-level, not per-turn: it is reported once on the entry, so a multi-turn
    # case must report every criterion it dropped, not just its last turn's.
    stripped = 0
    # The other half of the same accounting, and deliberately a SEPARATE number.
    # `stripped` counts criteria REMOVED before evaluation and never seen again;
    # these were kept, scored as skipped, and are visible row by row in
    # `observations` with the reason that skipped them. Summing the two would tell
    # a reader that N criteria vanished when only `stripped` of them did.
    forced_skips = 0
    # Did ANY turn of this case really test something? Accumulated across the
    # whole case on purpose — `status` is a case-level field, so claiming
    # "no assertions" about a case that asserted four real criteria on its
    # first turn would be the instrument lying in the other direction. The
    # vacuous TURN stays visible: every one of its observation rows is
    # recorded `skipped` with the reason that skipped it.
    evaluated_any = False
    t0 = clock()
    extra = default_route_criterion(v)
    # Payload file names already used in this case (a repeated turn label).
    payload_names: set[str] = set()
    try:
        for i, turn in enumerate(v.turns):
            if pace_s and i > 0:
                sleep(pace_s)
            # Read only when payloads are written: some callers hand in a clock
            # that yields a fixed number of ticks.
            t_turn = clock() if payload_dir is not None else None
            # force_new ONLY on a case's first turn: isolate the case, but
            # keep its own follow-ups in the session its seed opened.
            res = http_driver.drive(turn.query, tier=case_tier, post_query=post_query,
                                    get_progress=get_progress, session_id=session_id,
                                    force_new=(session_id is None),
                                    force_route=force_route,
                                    force_parser_mode=force_parser_mode,
                                    prompt_variant=prompt_variant,
                                    full_timeout_s=full_timeout_s,
                                    sleep=sleep, clock=clock)
            session_id = res.session_id
            if res.task_id:
                task_ids.append(res.task_id)
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
            if payload_dir is not None:
                # Before anything below can raise: a paid turn keeps its evidence
                # even when scoring it fails.
                _write_turn_payload(
                    payload_dir, v.id, turn, res, qc, payload_names,
                    force_route=force_route, force_parser_mode=force_parser_mode,
                    elapsed_s=round(clock() - t_turn, 3), prompt_variant=prompt_variant)
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
                last_reply=last_reply, bundle_summary=bundle_summary,
                forced=strip_route_criteria)
            # Counted from the results rather than decided here, because the
            # decision is `evaluate_turn`'s: it reads the route this turn REALLY
            # ran off the same observation it scores against, so on a paired run
            # this is non-zero on the cc arm and zero on the ns arm without the
            # runner needing to know which arm it is driving.
            #
            # Read off the `forced_skip` flag, NOT off the reason text. The reason
            # is composed in `evaluate.py` and there are now three of them; a
            # substring contract across the module boundary would break silently
            # the next time one is added or reworded.
            forced_skips += sum(1 for r in results if r.get("forced_skip"))
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
    # Same placement and same reason for it: `no_assertions` is exactly what a
    # case made entirely of these skips produces, so the count matters most where
    # an earlier append would have been overwritten.
    if forced_skips:
        # "unsatisfiable", not "NS-pipeline": the count now covers the two
        # api_artifact sub-assertions a CC turn cannot satisfy either, and those
        # are a publishing limit rather than an NS-pipeline field.
        note = (f"skipped {forced_skips} criteri{'on' if forced_skips == 1 else 'a'} "
                f"unsatisfiable on a forced container_cc arm")
        reason = f"{reason}; {note}" if reason else note
    return NessieManifestEntry(
        id=v.id, family=v.family, tier=tier, status=v_status, route=v_route, engine=v_engine,
        route_source=v_route_source, route_sources=v_route_sources,
        cost=v_cost, elapsed_s=round(clock() - t0, 3), failed_criteria=failed,
        observations=observations, task_ids=task_ids, poll_errors=poll_errors,
        reason=reason, expected_fail=expected_fail, outage=v_outage)


def run_suite(*, base_url, auth_header, tier, scope="specific", family=None, variant_id=None,
              corpus_path, out_dir, post_query=None, get_progress=None, bundle_reader=None,
              pace_s=0.0, run_consistency: bool = False, sample: float = 1.0, seed: int = 0,
              cases_path=None, force_route=None, force_parser_mode=None,
              sleep=time.sleep, clock=time.monotonic, prompt_variant=None) -> NessieManifest:
    """One whole run.

    `force_route` forces every turn, the consistency groups' included (a normal run
    made forced, as `--force-route` asks), and therefore strips the route criteria
    exactly as `run_paired` does. `force_parser_mode` adds the evaluation switch and
    needs the ns route. Neither is set by default, so a router-decided run is
    unchanged.

    At `tier="full"` the bundle reader is proven before the first turn
    (`check_bundle_reader`), and a reader that cannot read raises
    BundleReaderUnavailable with nothing sent.
    """
    _check_force(force_route, force_parser_mode, prompt_variant)
    if tier == "full":
        check_bundle_reader(bundle_reader)
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
            bundle_reader=bundle_reader, pace_s=pace_s, force_route=force_route,
            force_parser_mode=force_parser_mode, prompt_variant=prompt_variant,
            strip_route_criteria=force_route is not None, sleep=sleep, clock=clock))
    if run_consistency:
        from NessieAI.tests.nessie_tests import consistency
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
                                      force_new=True, force_route=force_route,
                                      force_parser_mode=force_parser_mode,
                                      prompt_variant=prompt_variant,
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


# ── forced arms (graph_search Nessie POC, spec E1 to E3) ─────────────────────
#
# Each arm forces the NS route and then the NS parser (the evaluation switch,
# `force_parser_mode`), so the comparison is between two agents, not two routers.
# The keys are the names `manage.py nessie --arms` takes.
ARM_PRESETS = {
    "graph": {"force_route": "ns", "force_parser_mode": "graph"},
    "api": {"force_route": "ns", "force_parser_mode": "api"},
    # The NS route forced, the parser NOT: it picks graph or API itself, and the
    # payload's debug.parser_plan.mode records which. The unforced arm of a
    # prompt-variant comparison (`prompt_variant` on run_arms).
    "auto": {"force_route": "ns", "force_parser_mode": None},
}

# The evaluation prompt variants, `chat_nextseek.prompt_variants.VARIANT_NAMES`.
# Pinned against that file's and the request model's source text by
# tests/test_prompt_variant_harness.py: the host lane cannot import the engine.
PROMPT_VARIANTS = ("v2", "v2_apoc", "v3")

ARMS_FILE = "arms.json"
PAYLOADS_DIR = "payloads"

# The evidence can name real people (spec E4): directories 700 and files 600,
# whatever the process umask (a `docker exec` runs with the image's default).
_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class UnknownArm(ValueError):
    """An arm name outside ARM_PRESETS."""


class ArmsRunRefused(RuntimeError):
    """Refused before any turn was sent, so nothing was billed."""


class NoArmsRunToResume(ArmsRunRefused):
    """`resume` was asked for, but the output directory holds no arms.json."""


class PriorArmsRunWouldBeOverwritten(ArmsRunRefused):
    """A fresh run was pointed at a directory that already holds an arms run."""


class CasesChanged(ArmsRunRefused):
    """The questions are not provably the ones the run being resumed asked."""


class ArmsChanged(ArmsRunRefused):
    """The arm list differs from the run being resumed; the rotation depends on it."""


def check_bundle_reader(bundle_reader) -> None:
    """Refuse a full-depth run whose bundle reader cannot read, before any turn is sent.

    `run_case` drives the paid turn first and reads the bundle second, and it catches
    every exception as infrastructure. So a reader that cannot read turns every
    full-depth case into a billed turn, zero evaluated criteria and `status="error"`:
    the 8.4 defect, hit twice in the week of 2026-08-17 on the module CLI, which never
    configured Django. Proving the reader first makes that a refusal that costs nothing.

    The check is the reader's own `preflight()` (see `bundle.summary_for_session`), so
    each reader says what reading needs. A reader without one is not checked: test
    doubles, and any reader with nothing to set up.
    """
    check = getattr(bundle_reader, "preflight", None)
    if check is None:
        return
    try:
        check()
    except Exception as exc:
        raise BundleReaderUnavailable(
            f"refused, nothing was billed: no full-tier turn could be scored, because {exc}"
        ) from exc


class PromptVariantChanged(ArmsRunRefused):
    """The prompt variant differs from the run being resumed; the run would mix two prompt sets."""


def _check_force(force_route, force_parser_mode, prompt_variant=None) -> None:
    if force_parser_mode is not None and force_route != "ns":
        raise ValueError(
            f"force_parser_mode={force_parser_mode!r} needs force_route='ns' (got "
            f"{force_route!r}): the switch lives in the NS parser, and an unforced turn "
            f"may be routed to Container-CC, where the field is ignored without a word.")
    if prompt_variant is not None and prompt_variant not in PROMPT_VARIANTS:
        raise ValueError(f"unknown prompt variant {prompt_variant!r}; the variants are "
                         f"{list(PROMPT_VARIANTS)}")
    if prompt_variant is not None and force_route != "ns":
        raise ValueError(
            f"prompt_variant={prompt_variant!r} needs force_route='ns' (got {force_route!r}): "
            f"the variant changes the NS agents' prompts, and an unforced turn may be routed "
            f"to Container-CC, where the field is ignored without a word.")


def _safe_name(text) -> str:
    name = _UNSAFE_NAME.sub("_", str(text)).strip()
    return name if name not in ("", ".", "..") else "_"


def _private_dir(path) -> Path:
    path = Path(path)
    if not path.is_dir():
        path.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        os.chmod(path, _PRIVATE_DIR_MODE)  # mkdir's mode is masked by the umask
    return path


def _private_write(path, text: str) -> None:
    """Atomic and mode 600: an interrupted write never leaves half a JSON file."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, _PRIVATE_FILE_MODE)  # O_CREAT keeps an existing file's mode
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_turn_payload(payload_dir, variant_id, turn, res, qc, used, *, force_route,
                        force_parser_mode, elapsed_s, prompt_variant=None) -> None:
    """One driven turn's final payload, for `run_case`'s `payload_dir`."""
    case_dir = _private_dir(_private_dir(payload_dir) / _safe_name(variant_id))
    name = _safe_name(turn.label)
    if name in used:
        name = f"{name}-{len(used)}"
    used.add(name)
    doc = {
        "variant_id": variant_id, "turn": turn.label, "query": turn.query,
        "task_id": res.task_id, "session_id": res.session_id, "status": res.status,
        "force_route": force_route, "force_parser_mode": force_parser_mode,
        "prompt_variant": prompt_variant,
        "route_obs": dataclasses.asdict(res.route_obs),
        "query_complete": qc, "elapsed_s": elapsed_s,
    }
    _private_write(case_dir / f"{name}.json", json.dumps(doc, indent=2, default=str))


def _validate_arms(arms) -> list[str]:
    arms = list(arms or [])
    unknown = [a for a in arms if a not in ARM_PRESETS]
    if unknown:
        raise UnknownArm(f"unknown arm(s) {unknown}; the arms are {sorted(ARM_PRESETS)}")
    if not arms:
        raise ValueError(f"no arms given; name one or more of {sorted(ARM_PRESETS)}")
    if len(set(arms)) != len(arms):
        raise ValueError(f"an arm is named twice in {arms}; each arm runs once per question")
    return arms


def _arm_done(entry) -> bool:
    """A recorded entry is done, except a provider outage: it tested nothing, so
    the next resume drives it again and its new entry replaces the old one."""
    return entry is not None and not getattr(entry, "outage", False)


def run_arms(*, base_url, auth_header, corpus_path, cases_path, out_dir, arms,
             resume=False, max_turns=None, full_timeout_s=600.0, skip_preflight=False,
             post_query=None, get_progress=None, bundle_reader=None,
             sleep=time.sleep, clock=time.monotonic, prompt_variant=None) -> dict:
    """Every question of a cases file through each forced NS arm (spec E1).

    Per question, every arm back to back, the first arm rotating with the
    question's index in the cases file, so time and order are not confounded with
    the arm. Each turn is `run_case` with that arm's forces, so the poll loop, the
    outage rule and the cost rule stay the ones every run uses.

    On disk under `out_dir`, rewritten after every (question, arm):
    `<arm>/manifest.json` (a NessieManifest), `<arm>/payloads/<id>/<turn>.json`,
    and `arms.json`: `run_meta` (git_sha, corpus_fingerprint, cases_sha256,
    cases_file, arms, base_url, preflight {passed_at, git_sha}, resumed),
    `progress` (state running | complete | max_turns | interrupted, turns_driven
    by this invocation, max_turns, questions, updated_at) and `questions`, one per
    cases-file question in file order: {id, family, first_arm, arms: {arm:
    {status, outage, task_ids, elapsed_s, git_sha}}}. `<arm>/report.html` is
    written at the end and whenever the run stops, an interrupt included.

    `resume` continues the run in `out_dir` and is refused without its arms.json,
    for a changed cases file, a changed arm list, or (when the cases file names
    corpus ids) a changed corpus; a fresh run onto an existing arms.json is
    refused too. A resume skips every (id, arm) its manifests hold except a
    provider outage, and skips the preflight when arms.json records a pass for
    this same git sha. `max_turns` caps the turns THIS invocation drives: it stops
    before a question whose pending arms would exceed it, and 0 runs the preflight
    and no question. Unless `skip_preflight`, the preflight runs first:
    `assert_force_route_works`, then `assert_parser_force_works(arms)`, and a
    refusal stops the run before any question and before arms.json is written.
    Before either, `check_bundle_reader` proves the bundle reader (always, since
    it costs no turn), and raises BundleReaderUnavailable with nothing sent.

    `prompt_variant` runs every turn, the preflight probes included, on that
    evaluation prompt set; it is recorded in `run_meta.prompt_variant` and in every
    payload, and a resume with a different one is refused. The arm `auto` forces
    the NS route only, so the parser routes each question itself.

    Returns the arms.json document plus `manifests` ({arm: NessieManifest}) and
    `arms_file`.
    """
    # Lazy, to keep the import graph one-way: preflight reads ARM_PRESETS from here.
    from NessieAI.tests.nessie_tests import preflight

    arms = _validate_arms(arms)
    _check_force("ns", None, prompt_variant)
    if max_turns is not None and max_turns < 0:
        raise ValueError(f"max_turns must be 0 or more; got {max_turns}")
    out_dir = Path(out_dir)
    arms_path = out_dir / ARMS_FILE

    prior = (json.loads(arms_path.read_text(encoding="utf-8"))
             if arms_path.exists() else None)
    if prior is None and resume:
        raise NoArmsRunToResume(
            f"resume was asked for but {out_dir} holds no {ARMS_FILE}, so there is "
            f"nothing to continue; this would start a fresh paid run instead. Point "
            f"--out at the run's directory, or drop --resume for a fresh run.")
    if prior is not None and not resume:
        raise PriorArmsRunWouldBeOverwritten(
            f"{out_dir} already holds an arms run ({ARMS_FILE}); a fresh run would "
            f"rewrite its paid results. Continue it with --resume, or give a new --out.")

    include_ids, inline = corpus.load_case_file(cases_path)
    variants = corpus.select_cases(corpus.merged(corpus_path), include_ids, inline)
    cases_sha = corpus.sha256_of(cases_path)
    fingerprint = corpus_fingerprint(corpus_path)
    sha = git_sha()

    prior_meta = (prior or {}).get("run_meta") or {}
    if prior is not None:
        if prior_meta.get("arms") != arms:
            raise ArmsChanged(
                f"the arms {arms} differ from the run being resumed "
                f"({prior_meta.get('arms')}); each question's first arm depends on the "
                f"list and its order. Resume with the same --arms, or give a new --out.")
        if prior_meta.get("prompt_variant") != prompt_variant:
            raise PromptVariantChanged(
                f"the prompt variant {prompt_variant!r} differs from the run being resumed "
                f"({prior_meta.get('prompt_variant')!r}); the run would mix two prompt sets. "
                f"Resume with the same --prompt-variant, or give a new --out.")
        if prior_meta.get("cases_sha256") != cases_sha:
            raise CasesChanged(
                f"the cases file {cases_path} is not the one this run was started with "
                f"(sha256 {prior_meta.get('cases_sha256')!r}, now {cases_sha!r}); "
                f"resuming would mix two question sets. Restore it, or give a new --out.")
        if include_ids and prior_meta.get("corpus_fingerprint") != fingerprint:
            raise CasesChanged(
                f"the cases file names corpus ids and the corpus changed since this run "
                f"started (fingerprint {prior_meta.get('corpus_fingerprint')!r}, now "
                f"{fingerprint!r}), so those questions may have changed. Restore the "
                f"corpus, or give a new --out.")

    # Before the preflight, which bills its probe turns: an arms run scores every
    # question from its bundle, so a reader that cannot read stops it here, free.
    check_bundle_reader(bundle_reader)

    if post_query is None or get_progress is None:
        post_query, get_progress = http_driver.make_default_clients(base_url, auth_header)

    recorded = prior_meta.get("preflight")
    if skip_preflight or (recorded and recorded.get("git_sha")
                          and recorded.get("git_sha") == sha):
        preflight_record = recorded
    else:
        preflight.assert_force_route_works(post_query, get_progress, sleep=sleep,
                                           clock=clock, ns_run_root_timeout_s=full_timeout_s)
        preflight.assert_parser_force_works(post_query, get_progress, arms, sleep=sleep,
                                            clock=clock, timeout_s=full_timeout_s,
                                            prompt_variant=prompt_variant)
        preflight_record = {"passed_at": _utc_now(), "git_sha": sha}

    _private_dir(out_dir)
    entries: dict[str, dict[str, NessieManifestEntry]] = {}
    started: dict[str, str] = {}
    for arm in arms:
        _private_dir(out_dir / arm)
        _private_dir(out_dir / arm / PAYLOADS_DIR)
        manifest_path = out_dir / arm / "manifest.json"
        if prior is not None and manifest_path.exists():
            m = load_manifest(manifest_path)
            entries[arm] = {e.id: e for e in m.entries}
            started[arm] = m.started_at
        else:
            entries[arm] = {}
            started[arm] = _utc_now()

    prior_questions = {q["id"]: q for q in (prior or {}).get("questions", [])}
    questions = [prior_questions.get(v.id) or {"id": v.id, "family": v.family,
                                               "first_arm": arms[i % len(arms)], "arms": {}}
                 for i, v in enumerate(variants)]
    selected_ids = [v.id for v in variants]
    position = {vid: k for k, vid in enumerate(selected_ids)}
    progress = {"state": "running", "turns_driven": 0, "max_turns": max_turns,
                "questions": len(variants), "updated_at": _utc_now()}
    doc = {
        "run_meta": {
            "git_sha": sha, "corpus_fingerprint": fingerprint, "cases_sha256": cases_sha,
            "cases_file": str(cases_path), "arms": arms, "base_url": base_url,
            "preflight": preflight_record, "full_timeout_s": full_timeout_s,
            "resumed": prior is not None, "prompt_variant": prompt_variant,
        },
        "progress": progress,
        "questions": questions,
    }
    manifests: dict[str, NessieManifest] = {}

    def persist(arm_names) -> None:
        for a in arm_names:
            manifests[a] = NessieManifest(
                started_at=started[a], ended_at=_utc_now(), tier="full", scope=f"arm:{a}",
                cases_file=str(cases_path), selected_ids=selected_ids,
                corpus_fingerprint=fingerprint, base_url=base_url, git_sha=sha,
                entries=sorted(entries[a].values(),
                               key=lambda e: position.get(e.id, len(position))))
            _private_write(out_dir / a / "manifest.json", manifests[a].model_dump_json(indent=2))
        progress["updated_at"] = _utc_now()
        _private_write(arms_path, json.dumps(doc, indent=2))

    # The preflight's pass is on disk before the first question is sent.
    persist(arms)
    driven = 0
    state = "running"
    try:
        for i, v in enumerate(variants):
            rec = questions[i]
            first = arms.index(rec["first_arm"]) if rec.get("first_arm") in arms else i % len(arms)
            pending = [a for a in arms[first:] + arms[:first]
                       if not _arm_done(entries[a].get(v.id))]
            if not pending:
                continue
            if max_turns is not None and driven + len(v.turns) * len(pending) > max_turns:
                state = "max_turns"
                break
            for arm in pending:
                preset = ARM_PRESETS[arm]
                entry = run_case(
                    v, tier="full", post_query=post_query, get_progress=get_progress,
                    bundle_reader=bundle_reader, force_route=preset["force_route"],
                    force_parser_mode=preset["force_parser_mode"],
                    strip_route_criteria=True, payload_dir=out_dir / arm / PAYLOADS_DIR,
                    full_timeout_s=full_timeout_s, sleep=sleep, clock=clock,
                    prompt_variant=prompt_variant)
                driven += len(v.turns)
                entries[arm][v.id] = entry
                rec["arms"][arm] = {"status": entry.status, "outage": entry.outage,
                                    "task_ids": list(entry.task_ids),
                                    "elapsed_s": entry.elapsed_s, "git_sha": sha}
                progress["turns_driven"] = driven
                persist([arm])
        else:
            state = "complete"
    except BaseException:
        state = "interrupted"
        raise
    finally:
        progress["state"] = state
        progress["turns_driven"] = driven
        persist(arms)
        for arm in arms:
            os.chmod(report.generate_html(manifests[arm], out_dir / arm), _PRIVATE_FILE_MODE)

    result = dict(doc)
    result["manifests"] = manifests
    result["arms_file"] = str(arms_path)
    return result
