from __future__ import annotations
import os
import time
from pathlib import Path
from nessie_tests import corpus, evaluate, http_driver, report
from nessie_tests import route_observer as ro
from nessie_tests.manifest import NessieManifest, NessieManifestEntry, write_manifest


def default_route_criterion(variant) -> dict | None:
    return {"field": "route", "op": "eq", "value": ro.ROUTE_NS} if "base" in variant.tags else None


def _iso(clock):  # avoid datetime.now() so tests are deterministic
    return f"t={clock():.3f}"


def run_suite(*, base_url, auth_header, tier, scope="specific", family=None, variant_id=None,
              overlay_path, out_dir, post_query=None, get_progress=None, bundle_reader=None,
              pace_s=0.0, run_consistency: bool = False,
              sleep=time.sleep, clock=time.monotonic) -> NessieManifest:
    if post_query is None or get_progress is None:
        post_query, get_progress = http_driver.make_default_clients(base_url, auth_header)
    variants = corpus.select(corpus.merged(overlay_path), scope=scope, family=family, variant_id=variant_id)
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
        t0 = clock()
        extra = default_route_criterion(v)
        try:
            for i, turn in enumerate(v.turns):
                if pace_s and i > 0:
                    sleep(pace_s)
                res = http_driver.drive(turn.query, tier=case_tier, post_query=post_query,
                                        get_progress=get_progress, session_id=session_id,
                                        sleep=sleep, clock=clock)
                session_id = res.session_id
                v_route, v_engine = res.route_obs.route, res.route_obs.engine
                qc = next((e["data"] for e in reversed(res.payload.get("progress") or [])
                           if e.get("event") == "query_complete"), {})
                v_cost = qc.get("total_cost_usd", v_cost)
                bundle_summary = None
                if case_tier == "full" and bundle_reader is not None and session_id is not None:
                    bundle_summary = bundle_reader(session_id)
                criteria = list(turn.pass_criteria) + ([extra] if extra else [])
                passed, results = evaluate.evaluate_turn(res.payload, criteria, res.route_obs,
                                                         last_reply=qc.get("reply"),
                                                         bundle_summary=bundle_summary)
                if not passed:
                    v_status = "failed"
                    failed += [f"{turn.label}:{r['field']}" for r in results if not r["passed"]]
        except Exception as exc:  # infra/endpoint failure ≠ assertion failure
            v_status, reason = "error", f"{type(exc).__name__}: {exc}"
        entries.append(NessieManifestEntry(
            id=v.id, family=v.family, tier=tier, status=v_status, route=v_route, engine=v_engine,
            cost=v_cost, elapsed_s=round(clock() - t0, 3), failed_criteria=failed,
            reason=reason, expected_fail=expected_fail))
    if run_consistency:
        from nessie_tests import consistency
        for g in corpus.load_consistency_groups(overlay_path):
            def _drive(q):
                r = http_driver.drive(q, tier="full" if tier == "full" else "route",
                                      post_query=post_query, get_progress=get_progress,
                                      sleep=sleep, clock=clock)
                return {"route": r.route_obs.route, "count": consistency.get_result_count(r.payload)}
            try:
                gr = consistency.run_group(g, _drive)
                entries.append(NessieManifestEntry(
                    id=g["id"], family="nessie_consistency", tier=tier,
                    status="passed" if gr.passed else "failed",
                    failed_criteria=gr.reasons, expected_fail="known_fail" in g.get("tags", [])))
            except Exception as exc:  # infra/endpoint failure ≠ assertion failure
                entries.append(NessieManifestEntry(
                    id=g["id"], family="nessie_consistency", tier=tier,
                    status="error", reason=f"{type(exc).__name__}: {exc}",
                    expected_fail="known_fail" in g.get("tags", [])))
    manifest = NessieManifest(started_at=started, ended_at=_iso(clock), tier=tier, scope=scope, entries=entries)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, Path(out_dir) / "manifest.json")
    report.generate_html(manifest, Path(out_dir))
    return manifest


def gate_failed(manifest: NessieManifest) -> int:
    """Count real failures (exclude expected_fail/known_fail)."""
    return sum(1 for e in manifest.entries if e.status in ("failed", "error") and not e.expected_fail)
