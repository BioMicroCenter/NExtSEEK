from pathlib import Path
from nessie_tests import corpus, runner

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"


def test_repro_cases_are_known_fail():
    ov = corpus.load_overlay(OVERLAY)
    repro = [v for v in ov if v.family == "nessie_repro"]
    assert len(repro) >= 3
    assert all("known_fail" in v.tags for v in repro)


def test_consistency_groups_present_with_count_not_250():
    groups = corpus.load_consistency_groups(OVERLAY)
    assert any(g["assert"].get("count_not") == 250 for g in groups)


def test_runner_reports_consistency_group(tmp_path):
    ROUTED = {"status": "running", "progress": [
        {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}}]}
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path, run_consistency=True,
        post_query=lambda b: {"task_id": "t", "session_id": "s"},
        get_progress=lambda tid: ROUTED, sleep=lambda s: None, clock=lambda: 0.0)
    assert any(e.family == "nessie_consistency" for e in m.entries)
