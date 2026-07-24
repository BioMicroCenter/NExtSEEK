from pathlib import Path
from nessie_tests import runner

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"

CC_ROUTED = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus", "source": "baml", "reasoning": ""}}]}

NS_ROUTED = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}}]}


def _post():
    def post_query(body):
        return {"task_id": "t", "session_id": "s"}
    return post_query


def test_run_suite_route_tier_specific(tmp_path):
    # route tier + specific scope → only route_gate cases; injected clients, no live stack
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.cc_reingest",
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "route.cc_reingest")
    assert entry.status == "passed" and entry.route == "container_cc"
    assert (tmp_path / "manifest.json").exists() and (tmp_path / "report.html").exists()


def test_default_route_criterion_only_for_base():
    from e2e.catalog import Variant, Turn
    base = Variant(family="f", id="b", name="n", tags=["base"], turns=[Turn(label="m", query="q")])
    ov = Variant(family="nessie_route", id="o", name="n", tags=["overlay"], turns=[Turn(label="m", query="q")])
    assert runner.default_route_criterion(base) == {"field": "route", "op": "eq", "value": "nextseek_query"}
    assert runner.default_route_criterion(ov) is None


# ── FIX 2 — tier-gating ───────────────────────────────────────────────────

def test_route_tier_skips_full_tagged_variant(tmp_path):
    # A `full`-tagged (non-route_gate) variant needs execution → skipped in route tier.
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="green.global_count",
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "green.global_count")
    assert entry.status == "skipped"


def test_full_tier_drives_route_gate_cc_route_only(tmp_path):
    # A route_gate CC variant in a FULL run is driven route-only: it passes on
    # route==container_cc without a completed turn AND never calls bundle_reader.
    def boom_bundle(session_id):  # would run only at full depth
        raise AssertionError("bundle_reader must not run for a route_gate case")

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.cc_reingest",
        post_query=_post(), get_progress=lambda tid: CC_ROUTED, bundle_reader=boom_bundle,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "route.cc_reingest")
    assert entry.status == "passed" and entry.route == "container_cc"


def test_unsatisfied_requires_env_is_skipped(tmp_path, monkeypatch):
    from e2e.catalog import Variant, Turn
    v = Variant(family="nessie_route", id="needs.env", name="n",
                tags=["nessie", "route_gate", "overlay"],
                requires_env=["NESSIE_DEFINITELY_UNSET_ENV"],
                turns=[Turn(label="m", query="q",
                            pass_criteria=[{"field": "route", "op": "eq", "value": "container_cc"}])])
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [v])
    monkeypatch.delenv("NESSIE_DEFINITELY_UNSET_ENV", raising=False)
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "needs.env")
    assert entry.status == "skipped"


# ── FIX 5 — a criteria MISS is a failure, not an error ────────────────────

def test_criteria_miss_marks_failed_with_reasons(tmp_path):
    # route_gate case asserts route==container_cc but the turn routes nextseek_query.
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.cc_reingest",
        post_query=_post(), get_progress=lambda tid: NS_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    entry = next(e for e in m.entries if e.id == "route.cc_reingest")
    assert entry.status == "failed"
    assert entry.failed_criteria and any("route" in fc for fc in entry.failed_criteria)
