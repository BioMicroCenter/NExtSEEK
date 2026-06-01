from chat_nextseek.pipeline.agent_tools import PIPELINE_TOOL_SCHEMAS


def test_five_tools_with_anthropic_shape():
    names = {t["name"] for t in PIPELINE_TOOL_SCHEMAS}
    assert names == {"resolve_samples", "write_samplesheet", "configure_run", "submit_to_tower", "conclude"}
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


from chat_nextseek.pipeline.agent_tools import tool_write_samplesheet


def test_write_rejects_hallucinated_sample():
    state = {"resolved": {"uids": ["D.SEQ-1-PUB"], "accessions": ["SRR111"]}}
    out = _json.loads(tool_write_samplesheet(
        _Cfg(), state,
        {"pipeline_key": "rnaseq", "cohorts": [
            {"label": "rnaseq-all", "rows": [{"sample": "GHOST-9", "accession": "SRR999"}]}]},
        "/tmp/does-not-matter"))
    assert out["ok"] is False
    assert any("GHOST-9" in e or "SRR999" in e for e in out["errors"])


def test_write_emits_when_refs_valid(monkeypatch, tmp_path):
    captured = {}

    def fake_emit(out_dir, **kw):
        captured["out_dir"] = str(out_dir)
        captured["rows"] = list(kw["samplesheet_rows"])
        from types import SimpleNamespace
        return SimpleNamespace(saved_files={"samplesheet": str(out_dir) + "/samplesheet.csv"},
                               samplesheet_row_count=len(kw["samplesheet_rows"]),
                               launch_entry=None, fetchngs_launch_entry=None)

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_nfcore_artifacts", fake_emit)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.resolve_accessions", lambda accs: [])
    state = {"resolved": {"uids": ["D.SEQ-1-PUB"], "accessions": ["SRR111"]}, "log_dir": str(tmp_path)}
    out = _json.loads(tool_write_samplesheet(
        _Cfg(), state,
        {"pipeline_key": "rnaseq", "cohorts": [
            {"label": "rnaseq-all", "rows": [{"sample": "D.SEQ-1-PUB", "accession": "SRR111", "strandedness": "auto"}]}]},
        str(tmp_path)))
    assert out["ok"] is True
    assert out["cohorts"][0]["row_count"] == 1
    assert "samplesheet" in state["artifacts"]["cohorts"][0]


def test_write_rejects_empty_row():
    state = {"resolved": {"uids": ["D.SEQ-1-PUB"], "accessions": []}}
    out = _json.loads(tool_write_samplesheet(
        _Cfg(), state,
        {"pipeline_key": "rnaseq", "cohorts": [{"label": "c", "rows": [{"strandedness": "auto"}]}]},
        "/tmp/x"))
    assert out["ok"] is False
    assert any("no sample or accession" in e for e in out["errors"])


def test_write_sanitizes_label_path_traversal(monkeypatch, tmp_path):
    from types import SimpleNamespace
    seen = {}

    def fake_emit(out_dir, **kw):
        seen["out_dir"] = str(out_dir)
        return SimpleNamespace(saved_files={"samplesheet": str(out_dir) + "/s.csv"},
                               samplesheet_row_count=1, excluded_accessions=[],
                               launch_entry=None, fetchngs_launch_entry=None)

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_nfcore_artifacts", fake_emit)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.resolve_accessions", lambda accs: [])
    state = {"resolved": {"uids": ["D.SEQ-1-PUB"], "accessions": []}}
    out = _json.loads(tool_write_samplesheet(
        _Cfg(), state,
        {"pipeline_key": "rnaseq", "cohorts": [{"label": "../../etc/evil", "rows": [{"sample": "D.SEQ-1-PUB"}]}]},
        str(tmp_path)))
    assert out["ok"] is True
    assert str(tmp_path) in state["artifacts"]["base_dir"]
    assert ".." not in state["artifacts"]["base_dir"]
    assert str(tmp_path) in seen["out_dir"] and ".." not in seen["out_dir"]


def test_write_multi_cohort_single_samplesheet_with_cohort_column(monkeypatch, tmp_path):
    """A grouped build emits ONE samplesheet whose rows carry a 'cohort' column —
    not one samplesheet per cohort."""
    from types import SimpleNamespace
    calls = []

    def fake_emit(out_dir, **kw):
        calls.append((str(out_dir), list(kw["samplesheet_rows"])))
        return SimpleNamespace(saved_files={"samplesheet": str(out_dir) + "/samplesheet.csv",
                                            "launch": str(out_dir) + "/launch.yml"},
                               samplesheet_row_count=len(kw["samplesheet_rows"]),
                               excluded_accessions=[],
                               launch_entry=None, fetchngs_launch_entry=None)

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_nfcore_artifacts", fake_emit)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.resolve_accessions", lambda accs: [])
    state = {"resolved": {"uids": ["D.SEQ-1-PUB", "D.SEQ-2-PUB"], "accessions": []}}
    out = _json.loads(tool_write_samplesheet(
        _Cfg(), state,
        {"pipeline_key": "rnaseq", "cohorts": [
            {"label": "ndma", "rows": [{"sample": "D.SEQ-1-PUB"}]},
            {"label": "saline", "rows": [{"sample": "D.SEQ-2-PUB"}]}]},
        str(tmp_path)))
    assert out["ok"] is True
    assert out["grouped_by_cohort"] is True
    assert out["total_rows"] == 2
    # exactly ONE emit (single samplesheet), not one per cohort
    assert len(calls) == 1
    out_dir, rows = calls[0]
    assert len(rows) == 2
    # the cohort label rides along as a 'cohort' column per row
    assert {r["sample"]: r.get("cohort") for r in rows} == {"D.SEQ-1-PUB": "ndma", "D.SEQ-2-PUB": "saline"}
    # one set of artifacts, one launch
    assert len(state["artifacts"]["cohorts"]) == 1


from chat_nextseek.pipeline.agent_tools import tool_submit_to_tower, dispatch_pipeline_tool_call


class _CfgTower:
    TOWER_ENV = {"access_token": "x", "workspace": "w", "compute_env": "c", "work_bucket": "b"}


def test_submit_not_configured_returns_path():
    state = {"artifacts": {"launch": "/tmp/launch.yml"}}
    out = _json.loads(tool_submit_to_tower(_Cfg(), state))  # _Cfg has no TOWER_ENV
    assert out["ok"] is False
    assert "/tmp/launch.yml" in out["message"]


def test_submit_calls_submit_launch(monkeypatch):
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.submit_launch", lambda p, *, tower_env: ["https://tower/run/1"])
    state = {"artifacts": {"launch": "/tmp/launch.yml"}}
    out = _json.loads(tool_submit_to_tower(_CfgTower(), state))
    assert out["ok"] is True
    assert out["run_urls"] == ["https://tower/run/1"]


def test_dispatch_routes_known_tools():
    out = dispatch_pipeline_tool_call(config=_Cfg(), session={}, state={"resolved": {}},
                                      name="submit_to_tower", tool_input={}, log_dir="/tmp")
    assert _json.loads(out)["ok"] is False  # no artifacts -> graceful error


def test_dispatch_rejects_conclude():
    import pytest
    with pytest.raises(ValueError):
        dispatch_pipeline_tool_call(config=_Cfg(), session={}, state={}, name="conclude",
                                    tool_input={}, log_dir="/tmp")


def test_resolve_accessions_misclassified_uids_corrected():
    out = _json.loads(tool_resolve_samples(_Cfg(), {}, {}, {"kind": "accessions", "accessions": ["D.SEQ-221031SHA-67-PUB"]}, "scrnaseq"))
    assert out["ok"] is False
    assert "explicit_uids" in out["error"]


def test_resolve_per_leaf_fields_include_inherited_parent_value(monkeypatch):
    raw = {"ok": True, "data": {"data": [
        {"sample_type": "MUS", "samples": [
            {"metadata": {"UID": "MUS-1-PUB", "Treatment1": "NDMA"}, "children": [
                {"metadata": {"UID": "D.SEQ-1-PUB", "sample_type": "D.SEQ", "Parent": "MUS-1-PUB"}, "children": []}
            ]}
        ]}
    ]}}
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata", lambda c, u: raw)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes", lambda c, m: m)
    out = _json.loads(tool_resolve_samples(_Cfg(), {}, {}, {"kind": "explicit_uids", "uids": ["MUS-1-PUB"]}, "rnaseq"))
    assert out["ok"] is True and out["leaf_count"] == 1
    leaf = out["leaves"][0]
    assert leaf["uid"] == "D.SEQ-1-PUB"
    assert leaf["fields"].get("Treatment1") == "NDMA"  # inherited from the MUS parent via lineage flatten


def test_resolve_caps_oversized_leaf_sets(monkeypatch):
    # > MAX_RESOLVE_LEAVES leaves would overflow the model context: stop with guidance, don't dump.
    from chat_nextseek.pipeline.agent_tools import MAX_RESOLVE_LEAVES
    big = [{"uid": f"D.SEQ-{i}-PUB", "sample_type": "D.SEQ", "assay": "", "source_uid": f"S-{i}", "metadata": {}}
           for i in range(MAX_RESOLVE_LEAVES + 50)]
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata", lambda c, u: {"ok": True, "data": {"data": []}})
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes", lambda c, m: m)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.enumerate_lineage_leaves", lambda ann, accepted_types: big)
    out = _json.loads(tool_resolve_samples(_Cfg(), {}, {}, {"kind": "explicit_uids", "uids": ["X-1-PUB"]}, "rnaseq"))
    assert out["ok"] is False
    assert out["leaf_count"] == len(big)
    assert "narrow" in out["error"].lower()
    assert "leaves" not in out  # did not build the per-leaf table


def test_resolve_detects_species_and_returns_param_menu(monkeypatch):
    raw = {"ok": True, "data": {"data": [
        {"sample_type": "MUS", "samples": [
            {"metadata": {"UID": "MUS-1-PUB", "Organism": "Mus musculus"}, "children": [
                {"metadata": {"UID": "D.SEQ-1-PUB", "sample_type": "D.SEQ", "Parent": "MUS-1-PUB", "sra_run": "SRR1"}, "children": []}
            ]}
        ]}
    ]}}
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata", lambda c, u: raw)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes", lambda c, m: m)
    state: dict = {}
    out = _json.loads(tool_resolve_samples(_Cfg(), {}, state, {"kind": "explicit_uids", "uids": ["MUS-1-PUB"]}, "rnaseq"))
    assert out["ok"] is True
    assert out["detected_species"].lower() == "mus musculus"
    assert out["bundle_key"] == "GRCm39"
    assert "aligner" in out["param_menu"]                 # curated menu surfaced to the agent
    assert out["reference_resources"][0] == "fasta"
    assert state["detected_species"].lower() == "mus musculus"
    assert state["bundle_key"] == "GRCm39"


from chat_nextseek.pipeline.agent_tools import tool_configure_run


def test_configure_run_requires_samplesheet_first():
    state = {"artifacts": {}, "bundle_key": "GRCm39"}
    out = _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "rnaseq", "params": {}}, "/tmp"))
    assert out["ok"] is False
    assert "samplesheet" in out["error"].lower()


def test_configure_run_rejects_bad_param():
    state = {"artifacts": {"samplesheet": "/tmp/s.csv", "base_dir": "/tmp"}, "bundle_key": "GRCm39"}
    out = _json.loads(tool_configure_run(
        _Cfg(), state, {"pipeline_key": "rnaseq", "params": {"aligner": "bowtie"}}, "/tmp"))
    assert out["ok"] is False
    assert any("aligner" in e for e in out["errors"])


def test_configure_run_emits_yamls_and_caches(monkeypatch, tmp_path):
    from types import SimpleNamespace
    captured = {}

    def fake_emit_launch(out_dir, **kw):
        captured["launch_plan"] = kw["launch_plan"]
        return SimpleNamespace(
            saved_files={"params": str(out_dir) + "/params.yml", "launch": str(out_dir) + "/launch.yml"},
            launch_entry={"name": "r1"}, fetchngs_launch_entry=None, excluded_accessions=[])

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_launch_artifacts", fake_emit_launch)
    state = {"artifacts": {"samplesheet": str(tmp_path / "s.csv"), "base_dir": str(tmp_path)},
             "bundle_key": "GRCm39"}
    out = _json.loads(tool_configure_run(
        _CfgTower(), state, {"pipeline_key": "rnaseq", "params": {"aligner": "hisat2"}}, str(tmp_path)))
    assert out["ok"] is True
    assert out["resolved_params"]["aligner"] == "hisat2"
    assert out["resolved_params"]["genome"] == "GRCm39"     # igenomes fallback
    assert out["reference_status"] == "igenomes_fallback"
    assert captured["launch_plan"]["params"]["aligner"] == "hisat2"
    assert state["artifacts"]["params"].endswith("params.yml")
    assert state["artifacts"]["launch"].endswith("launch.yml")


def test_configure_run_genome_override_reselects_bundle(monkeypatch, tmp_path):
    from types import SimpleNamespace
    captured = {}

    def fake_emit_launch(out_dir, **kw):
        captured["launch_plan"] = kw["launch_plan"]
        return SimpleNamespace(saved_files={"params": str(out_dir) + "/params.yml",
                                            "launch": str(out_dir) + "/launch.yml"},
                               launch_entry={"name": "r1"}, fetchngs_launch_entry=None,
                               excluded_accessions=[])

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_launch_artifacts", fake_emit_launch)
    # detected bundle is mouse, but the user steers to human via a species name
    state = {"artifacts": {"samplesheet": str(tmp_path / "s.csv"), "base_dir": str(tmp_path)},
             "bundle_key": "GRCm39"}
    out = _json.loads(tool_configure_run(
        _CfgTower(), state, {"pipeline_key": "rnaseq", "params": {"genome": "human"}}, str(tmp_path)))
    assert out["ok"] is True
    assert out["bundle_key"] == "GRCh38"               # 'human' species name re-selected the bundle
    assert out["resolved_params"]["genome"] == "GRCh38"  # igenomes fallback for the new bundle
    assert "genome" not in captured["launch_plan"]["params"] or captured["launch_plan"]["params"]["genome"] == "GRCh38"


def test_write_samplesheet_no_longer_emits_launch(monkeypatch, tmp_path):
    from types import SimpleNamespace
    seen = {}

    def fake_emit(out_dir, **kw):
        seen["launch_plan"] = kw.get("launch_plan")
        return SimpleNamespace(saved_files={"samplesheet": str(out_dir) + "/samplesheet.csv"},
                               samplesheet_row_count=1, excluded_accessions=[],
                               launch_entry=None, fetchngs_launch_entry=None)

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_nfcore_artifacts", fake_emit)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.resolve_accessions", lambda accs: [])
    state = {"resolved": {"uids": ["D.SEQ-1-PUB"], "accessions": []}}
    tool_write_samplesheet(
        _CfgTower(), state,
        {"pipeline_key": "rnaseq", "cohorts": [{"label": "c", "rows": [{"sample": "D.SEQ-1-PUB"}]}]},
        str(tmp_path))
    assert seen["launch_plan"] is None
