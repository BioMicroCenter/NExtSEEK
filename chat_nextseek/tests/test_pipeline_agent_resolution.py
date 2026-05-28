"""Sample resolution: directive's samples_ref → source UIDs → lineage leaves
→ catalog filter. Deterministic, no LLM."""
from unittest.mock import MagicMock, patch

from chat_nextseek.pipeline.agent import _resolve_samples
from chat_nextseek.schemas.pipeline import SamplesRef


def _bundle_with_two_rnaseq_and_one_wgs():
    return {"data": [
        {"sample_type": "NHP", "samples": [
            {"uuid": "NHP-1", "metadata": {"UID": "NHP-1"}, "children": [
                {"uuid": "TIS-1", "metadata": {"UID": "TIS-1"}, "children": [
                    {"uuid": "D.SEQ-1",
                     "metadata": {"UID": "D.SEQ-1", "sample_type": "D.SEQ", "assay_name": "RNA-seq"}},
                ]},
            ]},
            {"uuid": "NHP-2", "metadata": {"UID": "NHP-2"}, "children": [
                {"uuid": "TIS-2", "metadata": {"UID": "TIS-2"}, "children": [
                    {"uuid": "D.SEQ-2",
                     "metadata": {"UID": "D.SEQ-2", "sample_type": "D.SEQ", "assay_name": "RNA-seq"}},
                    {"uuid": "D.SEQ-3",
                     "metadata": {"UID": "D.SEQ-3", "sample_type": "D.SEQ", "assay_name": "WGS"}},
                ]},
            ]},
        ]},
    ]}


def test_resolve_keeps_all_typed_leaves_without_assay_drop():
    """New contract: resolution no longer hard-drops leaves by an assay regex.
    Every accepted-type (D.SEQ) leaf is kept as a candidate — including the WGS
    one whose assay wouldn't match the rnaseq pattern — because the sanity LLM,
    not a deterministic field, decides which are the right data type."""
    session = {"results_history": [{
        "user_query": "find mice",
        "api_result_full": {"data": {"rows": [{"uid": "NHP-1"}, {"uid": "NHP-2"}]}},
    }]}
    samples_ref = SamplesRef(kind="last_search")
    with patch("chat_nextseek.pipeline.agent.fetch_reporter_metadata") as fetch_md:
        fetch_md.return_value = {"ok": True, **_bundle_with_two_rnaseq_and_one_wgs()}
        with patch("chat_nextseek.pipeline.agent.annotate_metadata_with_sampletypes",
                   side_effect=lambda config, raw: raw):
            res = _resolve_samples(MagicMock(), session, samples_ref, "rnaseq")
    assert sorted(res["source_uids"]) == ["NHP-1", "NHP-2"]
    # All three D.SEQ leaves are candidates — the WGS one is NOT dropped here.
    assert {leaf["uid"] for leaf in res["leaves_filtered"]} == {"D.SEQ-1", "D.SEQ-2", "D.SEQ-3"}
    assert res["dropped_by_assay_mismatch"] == []
    assert res["source_uids_with_no_leaves"] == []


def test_resolve_explicit_uids_passes_them_through():
    session = {"results_history": []}
    samples_ref = SamplesRef(kind="explicit_uids", uids=["NHP-1", "NHP-2"])
    with patch("chat_nextseek.pipeline.agent.fetch_reporter_metadata") as fetch_md:
        fetch_md.return_value = {"ok": True, **_bundle_with_two_rnaseq_and_one_wgs()}
        with patch("chat_nextseek.pipeline.agent.annotate_metadata_with_sampletypes",
                   side_effect=lambda config, raw: raw):
            res = _resolve_samples(MagicMock(), session, samples_ref, "rnaseq")
    assert sorted(res["source_uids"]) == ["NHP-1", "NHP-2"]


def test_resolve_accessions_skips_lineage_for_fetchngs():
    session = {}
    samples_ref = SamplesRef(kind="accessions", accessions=["SRR1", "SRR2"])
    res = _resolve_samples(MagicMock(), session, samples_ref, "fetchngs")
    assert res["accessions"] == ["SRR1", "SRR2"]
    assert res["source_uids"] == []
    assert res["leaves_filtered"] == []


def test_resolve_accessions_with_non_fetchngs_returns_error():
    samples_ref = SamplesRef(kind="accessions", accessions=["SRR1"])
    res = _resolve_samples(MagicMock(), {}, samples_ref, "rnaseq")
    assert "error" in res
    assert "fetchngs" in res["error"].lower()


def test_resolve_last_search_with_no_history_returns_error():
    samples_ref = SamplesRef(kind="last_search")
    res = _resolve_samples(MagicMock(), {"results_history": []}, samples_ref, "rnaseq")
    assert "error" in res
    assert "no pinned" in res["error"].lower() or "no search" in res["error"].lower()


def test_source_uids_with_no_leaves_tracked():
    """A source UID whose lineage has no D.SEQ leaves shows up in the orphans list."""
    bundle = {"data": [{"sample_type": "NHP", "samples": [
        {"uuid": "NHP-1", "metadata": {"UID": "NHP-1"}, "children": [
            {"uuid": "D.SEQ-1",
             "metadata": {"UID": "D.SEQ-1", "sample_type": "D.SEQ", "assay_name": "RNA-seq"}},
        ]},
        {"uuid": "NHP-2", "metadata": {"UID": "NHP-2"}, "children": []},
    ]}]}
    samples_ref = SamplesRef(kind="explicit_uids", uids=["NHP-1", "NHP-2"])
    with patch("chat_nextseek.pipeline.agent.fetch_reporter_metadata") as fetch_md:
        fetch_md.return_value = {"ok": True, **bundle}
        with patch("chat_nextseek.pipeline.agent.annotate_metadata_with_sampletypes",
                   side_effect=lambda config, raw: raw):
            res = _resolve_samples(MagicMock(), {}, samples_ref, "rnaseq")
    assert "NHP-2" in res["source_uids_with_no_leaves"]
    assert "NHP-1" not in res["source_uids_with_no_leaves"]


def test_uids_from_last_search_falls_back_to_reporter_plan_uids():
    """Issue 1: after a GEO/reporter turn (no api_result_full rows), a follow-up
    build must still find the report's UIDs — from the reporter bundle's
    reporter_plan.uids — instead of failing with 'No pinned search'."""
    from chat_nextseek.pipeline.agent import _uids_from_last_search
    session = {"results_history": [{
        "mode": "reporter",
        "user_query": "Create a GEO submission for A.GEX-1 and A.GEX-2",
        "api_result_full": None,
        "reporter_plan": {"uids": ["A.GEX-1", "A.GEX-2"]},
        "parser_plan": {"filters": {"uids": ["A.GEX-1", "A.GEX-2"]}},
    }]}
    assert _uids_from_last_search(session) == ["A.GEX-1", "A.GEX-2"]


def test_uids_from_last_search_falls_back_to_parser_filter_uids():
    """When a reporter bundle has no reporter_plan.uids, fall back to the parser
    plan's filters.uids."""
    from chat_nextseek.pipeline.agent import _uids_from_last_search
    session = {"results_history": [{
        "mode": "reporter",
        "api_result_full": None,
        "parser_plan": {"filters": {"uids": ["A.GEX-9"]}},
    }]}
    assert _uids_from_last_search(session) == ["A.GEX-9"]


def test_uids_from_last_search_prefers_api_rows_over_plan_uids():
    """A real search bundle's api rows still take precedence over plan uids."""
    from chat_nextseek.pipeline.agent import _uids_from_last_search
    session = {"results_history": [{
        "mode": "new_search",
        "api_result_full": {"data": {"rows": [{"uid": "MUS-1"}, {"uid": "MUS-2"}]}},
        "parser_plan": {"filters": {"uids": ["SHOULD-NOT-USE"]}},
    }]}
    assert _uids_from_last_search(session) == ["MUS-1", "MUS-2"]
