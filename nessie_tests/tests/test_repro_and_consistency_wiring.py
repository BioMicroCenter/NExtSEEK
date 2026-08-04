from pathlib import Path
from nessie_tests import corpus, runner

CORPUS = Path(__file__).resolve().parents[1] / "corpus.json"


# `test_repro_cases_are_known_fail_or_explicitly_fixed` lived here until
# 2026-08-04. It read `load_overlay()` rather than `merged()`, so it was holding
# four `nessie_repro` variants to account that were fully retired on 2026-07-30
# and can never run again — the 2026-08-04 review's finding 3. Pointing it at
# `merged()` made it fail honestly (zero repro variants, `assert len(repro) >= 3`),
# which forced the reinstate-or-delete decision the finding asked for. If the
# family is ever reinstated, this test comes back with it.


def test_consistency_groups_assert_against_every_graph_limit():
    """Was `count_not: 250`, which went dead the moment the graph limit moved to 5000.

    A hardcoded sentinel is a guard that silently expires. `count_not_limit` checks
    the count against every limit the corpus has run under.
    """
    groups = corpus.load_consistency_groups(CORPUS)

    assert any(g["assert"].get("count_not_limit") for g in groups)
    assert not any("count_not" in g["assert"] for g in groups), (
        "a hardcoded count_not sentinel is back; use count_not_limit"
    )


def test_runner_reports_consistency_group(tmp_path):
    ROUTED = {"status": "running", "progress": [
        {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}}]}
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        corpus_path=CORPUS, out_dir=tmp_path, run_consistency=True,
        post_query=lambda b: {"task_id": "t", "session_id": "s"},
        get_progress=lambda tid: ROUTED, sleep=lambda s: None, clock=lambda: 0.0)
    assert any(e.family == "nessie_consistency" for e in m.entries)


def test_consistency_infra_error_does_not_discard_manifest(tmp_path):
    """A live infra error inside a consistency group must be recorded as an
    ``error`` entry, not propagated (which would abort before manifest write)."""
    def _boom(tid):
        raise ConnectionError("endpoint down")

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        corpus_path=CORPUS, out_dir=tmp_path, run_consistency=True,
        post_query=lambda b: {"task_id": "t", "session_id": "s"},
        get_progress=_boom, sleep=lambda s: None, clock=lambda: 0.0)
    # (a) run_suite did not propagate — it returned a manifest
    assert m is not None
    # (b) the manifest.json was still written
    assert (Path(tmp_path) / "manifest.json").exists()
    # (c) a consistency entry recorded the infra failure as an error
    errs = [e for e in m.entries if e.family == "nessie_consistency" and e.status == "error"]
    assert errs, f"expected a nessie_consistency error entry, got {[(e.family, e.status) for e in m.entries]}"
