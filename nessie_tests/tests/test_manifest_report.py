from pathlib import Path
import pytest
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


# --------------------------------------------------------------------------- #
# B2 — an outage must be visible AS an outage in the HTML, not as a plain
# `error` row indistinguishable from a dead endpoint.
# --------------------------------------------------------------------------- #

def test_an_outage_row_says_outage_not_error(tmp_path):
    from nessie_tests.manifest import NessieManifest, NessieManifestEntry

    m = NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[NessieManifestEntry(id="sys.q", family="system_question", tier="full",
                                     status="error", outage=True, reason="provider outage"),
                 NessieManifestEntry(id="infra.q", family="system_question", tier="full",
                                     status="error", reason="TimeoutError: read timed out")])

    doc = report.generate_html(m, tmp_path).read_text(encoding="utf-8")

    assert "<td>outage</td>" in doc
    assert "<td>error</td>" in doc, "a non-outage error must still render as error"


def test_an_outaged_known_fail_is_not_rendered_as_xfail(tmp_path):
    """`xfail` claims the expected failure was observed. An outage observed nothing."""
    from nessie_tests.manifest import NessieManifest, NessieManifestEntry

    m = NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[NessieManifestEntry(id="cons.nhp", family="nessie_consistency", tier="full",
                                     status="error", outage=True, expected_fail=True)])

    doc = report.generate_html(m, tmp_path).read_text(encoding="utf-8")

    assert "<td>outage</td>" in doc
    assert "xfail" not in doc


# --------------------------------------------------------------------------- #
# `route_sources` — the per-turn sequence. A single `route_source` is
# last-write-wins, which cannot distinguish "every turn was routed by BAML" from
# "turn 3 fell to the keyword regex". Sequence beats last-write-wins for triage
# and costs almost nothing. It defaults to [] so manifests written before this
# change still load.
# --------------------------------------------------------------------------- #

SEED6B = Path("/home/cdemu/nessie-run-seed6b/manifest.json")


def test_route_sources_defaults_to_empty_so_old_manifests_still_load(tmp_path):
    p = tmp_path / "manifest.json"
    # an entry serialised before the field existed
    old = ('{"started_at":"a","ended_at":"b","tier":"full","scope":"all","entries":'
           '[{"id":"c0","family":"f","tier":"full","status":"passed","route_source":"baml"}]}')
    p.write_text(old, encoding="utf-8")

    loaded = M.load_manifest(p)

    assert loaded.entries[0].route_sources == []
    assert loaded.entries[0].route_source == "baml"


def test_route_sources_round_trips(tmp_path):
    m = M.NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[M.NessieManifestEntry(id="c0", family="f", tier="full", status="passed",
                                       route_source="baml",
                                       route_sources=["baml", "sticky"])])
    p = tmp_path / "manifest.json"
    M.write_manifest(m, p)

    assert M.load_manifest(p).entries[0].route_sources == ["baml", "sticky"]


@pytest.mark.skipif(not SEED6B.exists(), reason=f"stored run evidence absent: {SEED6B}")
def test_the_stored_seed6b_run_still_loads():
    """Real run evidence, written before `route_sources` existed."""
    loaded = M.load_manifest(SEED6B)

    assert loaded.entries, "the stored manifest loaded but carries no entries"
    assert all(e.route_sources == [] for e in loaded.entries)


# --------------------------------------------------------------------------- #
# C1 part 2 — `no_assertions`: a case that evaluated ZERO criteria.
#
# It is a sixth status rather than a flag because it is genuinely a different
# outcome from `passed`, `failed` and `error`: nothing was tested. Old manifests
# never carry the value, so they still load.
# --------------------------------------------------------------------------- #

def test_no_assertions_round_trips(tmp_path):
    m = M.NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[M.NessieManifestEntry(id="green.refine_recall", family="nessie_green",
                                       tier="full", status="no_assertions",
                                       route="container_cc",
                                       reason="asserted nothing about the product")])
    p = tmp_path / "manifest.json"
    M.write_manifest(m, p)

    assert M.load_manifest(p).entries[0].status == "no_assertions"


def test_the_five_older_statuses_still_load(tmp_path):
    """Adding a value to the Literal must not remove one."""
    for status in ("passed", "failed", "skipped", "error", "xpass"):
        m = M.NessieManifest(
            started_at="a", ended_at="b", tier="full", scope="all",
            entries=[M.NessieManifestEntry(id="c", family="f", tier="full", status=status)])
        p = tmp_path / f"{status}.json"
        M.write_manifest(m, p)
        assert M.load_manifest(p).entries[0].status == status


def test_a_no_assertions_row_renders_with_its_own_class(tmp_path):
    m = M.NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[M.NessieManifestEntry(id="green.refine_recall", family="nessie_green",
                                       tier="full", status="no_assertions")])

    doc = report.generate_html(m, tmp_path).read_text(encoding="utf-8")

    assert "<td>no_assertions</td>" in doc
    assert "class='no_assertions'" in doc
    assert ".no_assertions{" in doc, "the status has no CSS class, so it renders unstyled"


def test_a_known_fail_that_asserted_nothing_is_not_rendered_as_xfail(tmp_path):
    """`xfail` claims the expected failure was observed. Nothing was observed."""
    m = M.NessieManifest(
        started_at="a", ended_at="b", tier="full", scope="all",
        entries=[M.NessieManifestEntry(id="repro.x", family="nessie_repro", tier="full",
                                       status="no_assertions", expected_fail=True)])

    doc = report.generate_html(m, tmp_path).read_text(encoding="utf-8")

    assert "<td>no_assertions</td>" in doc
    assert "xfail" not in doc


@pytest.mark.skipif(not SEED6B.exists(), reason=f"stored run evidence absent: {SEED6B}")
def test_the_stored_seed6b_run_still_loads_after_the_new_status():
    """Real 142 KB run evidence written before `no_assertions` existed."""
    loaded = M.load_manifest(SEED6B)

    assert loaded.entries
    assert "no_assertions" not in {e.status for e in loaded.entries}
