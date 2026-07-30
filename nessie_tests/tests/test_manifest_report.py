from pathlib import Path
from nessie_tests import manifest as M
from nessie_tests import report


def _sample():
    return M.NessieManifest(
        started_at="t0", ended_at="t1", tier="route", scope="specific",
        entries=[M.NessieManifestEntry(id="route.cc_pipeline_launch", family="nessie_route",
                                       tier="route", status="passed", route="container_cc",
                                       engine="container_cc:opus")])


def test_manifest_roundtrip(tmp_path):
    p = tmp_path / "manifest.json"
    M.write_manifest(_sample(), p)
    loaded = M.load_manifest(p)
    assert loaded.entries[0].route == "container_cc"
    assert loaded.tier == "route"


def test_generate_html_contains_id(tmp_path):
    out = report.generate_html(_sample(), tmp_path)
    assert out.name == "report.html"
    assert "route.cc_pipeline_launch" in out.read_text()


def test_generate_html_escapes_reason(tmp_path):
    m = M.NessieManifest(
        started_at="t0", ended_at="t1", tier="route", scope="specific",
        entries=[M.NessieManifestEntry(id="x", family="nessie_route", tier="route",
                                       status="error", reason="<script>a & b</script>")])
    text = report.generate_html(m, tmp_path).read_text()
    assert "<script>a & b</script>" not in text
    assert "&lt;script&gt;a &amp; b&lt;/script&gt;" in text


# --------------------------------------------------------------------------- #
# T3.9 — report.py:30 filtered to `not o.passed`, so a PASSING case rendered
# nothing at all. That blind spot is exactly how the assay count went 324 -> 5 and
# the MetNet study count went 10 -> 51 while both cases stayed green: the report
# could show that a criterion was satisfied, never what the answer actually was.
# --------------------------------------------------------------------------- #

def _entry(*obs, status="passed"):
    from nessie_tests.manifest import CriterionObservation, NessieManifestEntry
    return NessieManifestEntry(
        id="sys.show_me_all_assays_i_have_acce", family="system_question", tier="full",
        status=status,
        observations=[CriterionObservation(turn="main", field=f, op=o, expected=e,
                                           observed=v, passed=p)
                      for f, o, e, v, p in obs])


def _html(entry, tmp_path):
    from nessie_tests.manifest import NessieManifest
    from nessie_tests import report
    m = NessieManifest(started_at="a", ended_at="b", tier="full", scope="all", entries=[entry])
    return report.generate_html(m, tmp_path).read_text(encoding="utf-8")


def test_a_passing_case_shows_what_the_answer_actually_was(tmp_path):
    """The regression lock: 'passed' must not mean 'render nothing'."""
    doc = _html(_entry(("api_result_meta.row_count", "gte", 300, 324, True)), tmp_path)

    assert "324" in doc, "a passing case rendered no observed value at all"
    assert "api_result_meta.row_count" in doc


def test_the_masked_regression_would_now_be_visible(tmp_path):
    """324 -> 5 stayed green because `row_count gte 1` was still satisfied."""
    doc = _html(_entry(("api_result_meta.row_count", "gte", 1, 5, True)), tmp_path)

    assert ">5<" in doc or ">5 " in doc or "5" in doc
    assert "all passed" in doc, "the summary should say the criteria passed, and still show 5"


def test_failing_rows_are_still_shown_and_still_marked_failed(tmp_path):
    doc = _html(
        _entry(("graph_result.count", "lte", 25, 51, False)), tmp_path)

    assert "51" in doc
    assert "class='failed'" in doc


def test_mixed_rows_report_how_many_failed(tmp_path):
    doc = _html(_entry(
        ("neo4j_ok", "true", None, True, True),
        ("graph_result.count", "lte", 25, 51, False),
    ), tmp_path)

    assert "2 criteria, 1 failed" in doc


def test_a_case_with_no_observations_renders_no_table(tmp_path):
    assert "<details>" not in _html(_entry(), tmp_path)


# --------------------------------------------------------------------------- #
# T3.8 — three run directories used to be indistinguishable.
# --------------------------------------------------------------------------- #

def test_the_manifest_records_what_makes_a_diff_honest(tmp_path):
    from nessie_tests import runner

    ROUTED = {"status": "running", "progress": [
        {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus",
                                            "source": "baml", "reasoning": ""}}]}
    OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"

    m = runner.run_suite(
        base_url="http://dev:8000", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path, variant_id="route.ns_advanced",
        post_query=lambda b: {"task_id": "t", "session_id": "s"},
        get_progress=lambda tid: ROUTED, sleep=lambda s: None, clock=lambda: 0.0,
        sample=0.1, seed=7)

    assert m.seed == 7
    assert m.sample == 0.1
    assert m.selected_ids == ["route.ns_advanced"]
    assert m.base_url == "http://dev:8000"
    assert len(m.corpus_fingerprint) == 64
    # src != "baml" is an infrastructure condition, so the source must be recorded.
    assert next(e for e in m.entries).route_source == "baml"


def test_the_fingerprint_changes_when_the_overlay_changes(tmp_path):
    from nessie_tests import runner

    OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"
    edited = tmp_path / "overlay.json"
    edited.write_text(Path(OVERLAY).read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert runner.corpus_fingerprint(OVERLAY) != runner.corpus_fingerprint(edited), (
        "an edited overlay must change the fingerprint, or a diff will mis-pair "
        "cases the same seed no longer selects"
    )
