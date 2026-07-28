from __future__ import annotations
import os
import time
from pathlib import Path
from nessie_tests import corpus, evaluate, http_driver, report
from nessie_tests import route_observer as ro
from nessie_tests.manifest import (
    CriterionObservation, NessieManifest, NessieManifestEntry, write_manifest,
)


def default_route_criterion(variant) -> dict | None:
    """No route expectation is injected any more. Deliberately.

    This used to return ``route == nextseek_query`` for every variant tagged
    "base", which ``corpus.load_base`` applies to all 366 imported variants. No
    one ever curated that: it was an assumption, and it made deliberate
    ``container_cc`` routing (open-ended analysis, resource creation) read as a
    product failure. Routing is asserted where it has actually been decided —
    the ``route_gate`` variants in overlay.json, which carry explicit ``route``
    criteria and run in the cheap route tier.
    """
    return None


def _iso(clock):  # avoid datetime.now() so tests are deterministic
    return f"t={clock():.3f}"


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


def run_suite(*, base_url, auth_header, tier, scope="specific", family=None, variant_id=None,
              overlay_path, out_dir, post_query=None, get_progress=None, bundle_reader=None,
              pace_s=0.0, run_consistency: bool = False, sample: float = 1.0, seed: int = 0,
              sleep=time.sleep, clock=time.monotonic) -> NessieManifest:
    if post_query is None or get_progress is None:
        post_query, get_progress = http_driver.make_default_clients(base_url, auth_header)
    variants = corpus.select(corpus.merged(overlay_path), scope=scope, family=family, variant_id=variant_id)
    if sample < 1.0:
        variants = corpus.sample(variants, sample, seed)
    started = _iso(clock)
    entries: list[NessieManifestEntry] = []
    for v in variants:
        expected_fail = "known_fail" in v.tags
        is_gate = "route_gate" in v.tags
        # requires_env skip (both tiers): a variant needing an unset env var is
        # not runnable here — record it skipped, don't fail the gate.
        missing_env = [name for name in v.requires_env if name not in os.environ]
        if missing_env:
            entries.append(NessieManifestEntry(
                id=v.id, family=v.family, tier=tier, status="skipped",
                reason=f"requires_env unset: {missing_env}", expected_fail=expected_fail))
            continue
        # tier selection: the route tier only exercises route_gate cases (route
        # assertions only). Anything else needs a real turn/launch — skip it,
        # don't fail, so a route-tier run never touches the live pipeline.
        if tier == "route" and not is_gate:
            entries.append(NessieManifestEntry(
                id=v.id, family=v.family, tier=tier, status="skipped",
                reason="needs execution; skipped at route tier", expected_fail=expected_fail))
            continue
        # per-case DEPTH: route_gate cases are ALWAYS driven route-only (so the
        # CC pipeline/reingest cases never execute a real turn/launch, even in a
        # full run); everything else is driven at the global tier's depth.
        case_tier = "route" if is_gate else tier
        session_id = None
        v_status, v_route, v_engine, v_cost, failed, reason = "passed", None, None, None, [], ""
        observations: list[CriterionObservation] = []
        poll_errors = 0
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
                                        sleep=sleep, clock=clock)
                session_id = res.session_id
                poll_errors += res.poll_errors
                v_route, v_engine = res.route_obs.route, res.route_obs.engine
                qc = next((e["data"] for e in reversed(res.payload.get("progress") or [])
                           if e.get("event") == "query_complete"), {})
                v_cost = qc.get("total_cost_usd", v_cost)
                bundle_summary = None
                if case_tier == "full" and bundle_reader is not None and session_id is not None:
                    bundle_summary = bundle_reader(session_id)
                criteria = list(turn.pass_criteria) + ([extra] if extra else [])
                passed, results, observed = evaluate.evaluate_turn(
                    res.payload, criteria, res.route_obs,
                    last_reply=qc.get("reply"), bundle_summary=bundle_summary)
                observations += [
                    CriterionObservation(
                        turn=turn.label, field=r["field"], op=r["op"], expected=r.get("value"),
                        observed=_trim(observed.get(r["field"])),
                        passed=r["passed"], reason=r.get("reason", ""))
                    for r in results
                ]
                if not passed:
                    v_status = "failed"
                    failed += [f"{turn.label}:{r['field']}" for r in results if not r["passed"]]
        except Exception as exc:  # infra/endpoint failure ≠ assertion failure
            v_status, reason = "error", f"{type(exc).__name__}: {exc}"
        v_status, xpass_reason = _apply_xpass(v_status, expected_fail)
        if xpass_reason:
            reason = xpass_reason
        entries.append(NessieManifestEntry(
            id=v.id, family=v.family, tier=tier, status=v_status, route=v_route, engine=v_engine,
            cost=v_cost, elapsed_s=round(clock() - t0, 3), failed_criteria=failed,
            observations=observations, poll_errors=poll_errors,
            reason=reason, expected_fail=expected_fail))
    if run_consistency:
        from nessie_tests import consistency
        for g in corpus.load_consistency_groups(overlay_path):
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
                return {"route": r.route_obs.route, "count": consistency.get_result_count(r.payload)}
            g_t0 = clock()
            g_expected_fail = "known_fail" in g.get("tags", [])
            try:
                gr = consistency.run_group(g, _drive)
                g_status, g_reason = _apply_xpass(
                    "passed" if gr.passed else "failed", g_expected_fail)
                entries.append(NessieManifestEntry(
                    id=g["id"], family="nessie_consistency", tier=tier,
                    status=g_status, reason=g_reason,
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
                    failed_criteria=gr.reasons, expected_fail=g_expected_fail))
            except Exception as exc:  # infra/endpoint failure ≠ assertion failure
                entries.append(NessieManifestEntry(
                    id=g["id"], family="nessie_consistency", tier=tier,
                    status="error", reason=f"{type(exc).__name__}: {exc}",
                    elapsed_s=round(clock() - g_t0, 3),
                    expected_fail=g_expected_fail))
    manifest = NessieManifest(started_at=started, ended_at=_iso(clock), tier=tier, scope=scope, entries=entries)
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


def classify_entries(manifest: NessieManifest) -> dict:
    """Split a manifest into the buckets a summary needs.

    Single source of truth, shared by ``gate_failed`` and the management command's
    printed summary. They used to classify independently and disagreed: the summary's
    "real failures" excluded xpass while the gate counted it, so a run could print
    "GATE: PASS" and then exit 1. And its known-fail bucket included *passing*
    known-fails, which is the line that read as reassurance.
    """
    entries = manifest.entries
    real_fails = [
        e for e in entries
        if e.status == "xpass" or (e.status in ("failed", "error") and not e.expected_fail)
    ]
    return {
        "total": len(entries),
        "counts": {s: sum(1 for e in entries if e.status == s)
                   for s in ("passed", "failed", "skipped", "error", "xpass")},
        "real_fails": real_fails,
        # Known-fails that actually failed. A known_fail that PASSED is an xpass and
        # belongs in real_fails, not here.
        "known_failed": [e for e in entries if e.expected_fail and e.status != "xpass"],
    }


def gate_failed(manifest: NessieManifest) -> int:
    """Count real failures.

    A known_fail case that fails is expected and excluded. An ``xpass`` is
    always counted: it means the corpus is asserting something that is no
    longer true, which is exactly the kind of drift that makes a green run
    misleading.
    """
    return sum(
        1 for e in manifest.entries
        if e.status == "xpass" or (e.status in ("failed", "error") and not e.expected_fail)
    )
