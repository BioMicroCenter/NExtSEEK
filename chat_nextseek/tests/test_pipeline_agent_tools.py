from chat_nextseek.pipeline.agent_tools import PIPELINE_TOOL_SCHEMAS


def test_four_tools_with_anthropic_shape():
    names = {t["name"] for t in PIPELINE_TOOL_SCHEMAS}
    assert names == {"resolve_samples", "write_samplesheet", "submit_to_tower", "conclude"}
    for t in PIPELINE_TOOL_SCHEMAS:
        assert set(t) == {"name", "description", "input_schema"}
        assert t["input_schema"]["type"] == "object"


def test_conclude_requires_outcome_and_message():
    conclude = next(t for t in PIPELINE_TOOL_SCHEMAS if t["name"] == "conclude")
    props = conclude["input_schema"]["properties"]
    assert set(props["outcome"]["enum"]) == {"submitted", "rejected", "cancelled", "answered"}
    assert conclude["input_schema"]["required"] == ["outcome", "message"]


import json as _json
from chat_nextseek.pipeline.agent_tools import tool_resolve_samples


class _Cfg:
    pass


def test_resolve_explicit_uids_builds_table_and_caches_refs(monkeypatch):
    raw = {"ok": True, "data": {"data": [
        {"sample_type": "MUS", "samples": [
            {"metadata": {"UID": "MUS-1-PUB"}, "children": [
                {"metadata": {"UID": "D.SEQ-1-PUB", "sample_type": "D.SEQ",
                              "Organ": "Liver", "sra_run": "SRR111"}, "children": []}
            ]}
        ]}
    ]}}
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata", lambda c, u: raw)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes", lambda c, m: m)
    state: dict = {}
    out = _json.loads(tool_resolve_samples(_Cfg(), {}, state, {"kind": "explicit_uids", "uids": ["MUS-1-PUB"]}, "rnaseq"))
    assert out["ok"] is True
    assert out["leaf_count"] == 1
    leaf = out["leaves"][0]
    assert leaf["uid"] == "D.SEQ-1-PUB"
    assert "SRR111" in leaf["accessions"]
    assert "D.SEQ-1-PUB" in state["resolved"]["uids"]
    assert "SRR111" in state["resolved"]["accessions"]


def test_resolve_explicit_uids_empty_is_error():
    out = _json.loads(tool_resolve_samples(_Cfg(), {}, {}, {"kind": "explicit_uids", "uids": []}, "rnaseq"))
    assert out["ok"] is False


def test_resolve_accessions_branch_caches_and_returns_zero_leaves():
    state: dict = {}
    out = _json.loads(tool_resolve_samples(_Cfg(), {}, state, {"kind": "accessions", "accessions": ["SRR222", " "]}, "fetchngs"))
    assert out["ok"] is True
    assert out["leaf_count"] == 0
    assert state["resolved"]["accessions"] == ["SRR222"]  # blank stripped/dropped


def test_resolved_refs_accumulate_across_calls(monkeypatch):
    raw = {"ok": True, "data": {"data": [
        {"sample_type": "MUS", "samples": [
            {"metadata": {"UID": "MUS-1-PUB"}, "children": [
                {"metadata": {"UID": "D.SEQ-1-PUB", "sample_type": "D.SEQ", "sra_run": "SRR111"}, "children": []}
            ]}
        ]}
    ]}}
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata", lambda c, u: raw)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes", lambda c, m: m)
    state: dict = {}
    # first an accessions call, then a uid call — the uid call must NOT drop the earlier accession
    tool_resolve_samples(_Cfg(), {}, state, {"kind": "accessions", "accessions": ["SRR999"]}, "rnaseq")
    tool_resolve_samples(_Cfg(), {}, state, {"kind": "explicit_uids", "uids": ["MUS-1-PUB"]}, "rnaseq")
    assert "SRR999" in state["resolved"]["accessions"]   # retained across calls
    assert "SRR111" in state["resolved"]["accessions"]   # added by the uid call
    assert "D.SEQ-1-PUB" in state["resolved"]["uids"]
