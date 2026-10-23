import pytest
from chat_nextseek.pipeline.agent_tools import PIPELINE_TOOL_SCHEMAS


def test_tool_schemas_have_anthropic_shape():
    names = {t["name"] for t in PIPELINE_TOOL_SCHEMAS}
    assert names == {"select_pipeline", "resolve_samples", "write_samplesheet", "configure_run",
                     "submit_to_tower", "conclude", "handoff"}
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
    state = {"messages": [{"role": "user", "content": "go"}, {"role": "user", "content": "yes"}], "launch_built_at_user_msgs": 1, "artifacts": {"launch": "/tmp/launch.yml"}}
    out = _json.loads(tool_submit_to_tower(_Cfg(), state))  # _Cfg has no TOWER_ENV
    assert out["ok"] is False
    assert "/tmp/launch.yml" in out["message"]


def test_submit_calls_submit_launch(monkeypatch):
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.submit_launch", lambda p, *, tower_env: ["https://tower/run/1"])
    state = {"messages": [{"role": "user", "content": "go"}, {"role": "user", "content": "yes"}], "launch_built_at_user_msgs": 1, "artifacts": {"launch": "/tmp/launch.yml"}}
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

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_luria_launch_artifacts", fake_emit_launch)
    state = {"artifacts": {"samplesheet": str(tmp_path / "s.csv"), "base_dir": str(tmp_path)},
             "bundle_key": "GRCm39"}
    out = _json.loads(tool_configure_run(
        _CfgTower(), state, {"pipeline_key": "rnaseq", "params": {"aligner": "hisat2"}}, str(tmp_path)))
    assert out["ok"] is True
    assert out["resolved_params"]["aligner"] == "hisat2"
    assert out["resolved_params"]["genome"] == "GRCm39"     # GRCm39 has a local Luria ref
    assert out["reference_status"] == "local_luria"
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

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_luria_launch_artifacts", fake_emit_launch)
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


def test_configure_run_override_persists_bundle_across_calls(monkeypatch, tmp_path):
    # A steered bundle must survive a follow-up configure_run that doesn't re-supply genome,
    # rather than reverting to the auto-detected one.
    from types import SimpleNamespace

    def fake_emit_launch(out_dir, **kw):
        return SimpleNamespace(saved_files={"params": str(out_dir) + "/params.yml",
                                            "launch": str(out_dir) + "/launch.yml"},
                               launch_entry={"name": "r1"}, fetchngs_launch_entry=None,
                               excluded_accessions=[])

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_luria_launch_artifacts", fake_emit_launch)
    state = {"artifacts": {"samplesheet": str(tmp_path / "s.csv"), "base_dir": str(tmp_path)},
             "bundle_key": "GRCm39"}
    out1 = _json.loads(tool_configure_run(
        _CfgTower(), state, {"pipeline_key": "rnaseq", "params": {"genome": "human"}}, str(tmp_path)))
    assert out1["bundle_key"] == "GRCh38"
    assert state["bundle_key"] == "GRCh38"   # persisted on the override
    # follow-up steer with NO genome must keep human, not revert to mouse
    out2 = _json.loads(tool_configure_run(
        _CfgTower(), state, {"pipeline_key": "rnaseq", "params": {"aligner": "hisat2"}}, str(tmp_path)))
    assert out2["bundle_key"] == "GRCh38"
    assert out2["resolved_params"]["genome"] == "GRCh38"   # did not revert to GRCm39
    assert out2["resolved_params"]["aligner"] == "hisat2"


def test_write_samplesheet_invalidates_stale_configure_run_output(monkeypatch, tmp_path):
    # A (re)built samplesheet must clear any prior configure_run output so a later submit
    # can't pick up a stale params/launch.
    from types import SimpleNamespace

    def fake_emit(out_dir, **kw):
        return SimpleNamespace(saved_files={"samplesheet": str(out_dir) + "/samplesheet.csv"},
                               samplesheet_row_count=1, excluded_accessions=[],
                               launch_entry=None, fetchngs_launch_entry=None)

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_nfcore_artifacts", fake_emit)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.resolve_accessions", lambda accs: [])
    state = {"resolved": {"uids": ["D.SEQ-1-PUB"], "accessions": []},
             "artifacts": {"params": "/old/params.yml", "launch": "/old/launch.yml"},
             "launch_plan": {"run_name": "old"}}
    tool_write_samplesheet(
        _CfgTower(), state,
        {"pipeline_key": "rnaseq", "cohorts": [{"label": "c", "rows": [{"sample": "D.SEQ-1-PUB"}]}]},
        str(tmp_path))
    assert "params" not in state["artifacts"]
    assert "launch" not in state["artifacts"]
    assert "launch_plan" not in state


import json
from pathlib import Path
from chat_nextseek.pipeline import agent_tools as at


class _WSCfg:
    def __init__(self, log_dir):
        self.LOG_DIR = str(log_dir)
        self.TOWER_ENV = {}


def test_write_samplesheet_does_not_resolve_ena(monkeypatch, tmp_path):
    calls = {"n": 0}
    monkeypatch.setattr(at, "resolve_accessions",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or [])
    state = {"resolved": {"uids": ["D.SEQ-2"], "accessions": ["SRR300"]},
             "accession_file_paths": {}}
    tool_input = {"pipeline_key": "rnaseq",
                  "cohorts": [{"label": "c", "rows": [
                      {"sample": "D.SEQ-2", "accession": "SRR300", "strandedness": "auto"}]}]}
    out = json.loads(at.tool_write_samplesheet(_WSCfg(tmp_path), state, tool_input, str(tmp_path)))
    assert out["ok"] is True
    assert calls["n"] == 0                      # ENA route is off
    assert out["total_rows"] == 1               # SRR row kept, not dropped


def test_write_samplesheet_keeps_path_only_row_without_accession(tmp_path):
    # Design Thread 1, bullet 3: a pure-local sample with a /net/bmc-* path and NO accession
    # must survive _validate_rows_against_resolved AND reach the emitter (kept, not dropped).
    # Its uid IS a resolved uid, and the uid-keyed accession_file_paths carries the local path.
    # Today the emitter drops any row without a resolved ENA run (acc_to_runs.get("") is None),
    # so this fails pre-change (total_rows == 0) and passes once Task 3's row loop is rewritten.
    state = {"resolved": {"uids": ["D.SEQ-1"], "accessions": []},
             "accession_file_paths": {
                 "D.SEQ-1": {"Link_PrimaryData": "/net/bmc-x/1_1.fastq.gz",
                             "Link_SecondaryData": "/net/bmc-x/1_2.fastq.gz"}}}
    tool_input = {"pipeline_key": "rnaseq",
                  "cohorts": [{"label": "c", "rows": [
                      {"sample": "D.SEQ-1", "strandedness": "auto"}]}]}   # no accession key
    out = json.loads(at.tool_write_samplesheet(_WSCfg(tmp_path), state, tool_input, str(tmp_path)))
    assert out["ok"] is True
    assert out["total_rows"] == 1   # path-only, accession-less row is NOT dropped


def test_configure_run_local_luria_without_tower(tmp_path):
    class Cfg:
        LOG_DIR = str(tmp_path)
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1\nD.SEQ-1,/net/bmc/1.fastq.gz\n")
    state = {"artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)},
             "bundle_key": "GRCm39", "pipeline_key": "rnaseq"}
    out = json.loads(at.tool_configure_run(
        Cfg(), state, {"pipeline_key": "rnaseq", "params": {}}, str(tmp_path)))
    assert out["ok"] is True
    assert out["reference_status"] == "local_luria"
    assert out["reference_files"]["fasta"].endswith(".fa.gz")
    assert Path(out["launch_yml"]).exists() and Path(out["params_yml"]).exists()
    assert state["artifacts"]["launch"] and state["artifacts"]["params"]


def test_configure_run_names_the_files_sarek_is_actually_run_on(tmp_path):
    # sarek runs GRCh38 on the GATK bundle (luria_run_genome), so the confirmation must name
    # that fasta and no gtf, not the GENCODE pair the species-resolved key would give.
    class Cfg:
        LOG_DIR = str(tmp_path)
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("patient,sample,lane,fastq_1\nP1,S1,L1,/net/bmc/1.fastq.gz\n")
    state = {"artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)},
             "bundle_key": "GRCh38", "pipeline_key": "sarek"}
    out = json.loads(at.tool_configure_run(
        Cfg(), state, {"pipeline_key": "sarek", "params": {}}, str(tmp_path)))
    assert out["ok"] is True
    assert out["reference_status"] == "local_luria"
    assert out["reference_files"] == {"fasta": "GATK.GRCh38/Homo_sapiens_assembly38.fasta"}


# --- a run named by its UID prefix (R3 A3) ---------------------------------------------------------------------

def _prefix_graph(monkeypatch, matches):
    """Stub the scoped graph read: `matches` maps an upper-case prefix to the UIDs stored under it."""
    seen = {"queries": []}

    def run(config, cypher, params=None, **kw):
        seen["queries"].append(params)
        rows = [{"prefix": p + "-", "uuid": u} for p in params["prefixes"] for u in matches.get(p, [])]
        return {"ok": True, "count": len(rows), "data": rows}

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.tool_neo4j_query", run)
    return seen


def _capture_fetch(monkeypatch):
    fetched = []

    def fake_fetch(c, uids):
        fetched.append(list(uids))
        return {"ok": True, "data": {"data": []}}

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata", fake_fetch)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes", lambda c, m: m)
    return fetched


def _resolve(uids):
    return _json.loads(tool_resolve_samples(_Cfg(), {}, {}, {"kind": "explicit_uids", "uids": uids}, "rnaseq"))


def test_a_uid_prefix_resolves_to_the_samples_it_prefixes(monkeypatch):
    """The counter-example's shape: a run named by its prefix reached the metadata fetch as if it were a UID."""
    _prefix_graph(monkeypatch, {"D.SEQ-250101ABC": ["D.SEQ-250101ABC-1", "D.SEQ-250101ABC-2", "D.SEQ-250101ABC-3"]})
    fetched = _capture_fetch(monkeypatch)

    _resolve(["D.SEQ-250101ABC"])

    assert fetched == [["D.SEQ-250101ABC-1", "D.SEQ-250101ABC-2", "D.SEQ-250101ABC-3"]]


def test_a_prefix_of_another_type_and_lab_resolves_the_same_way(monkeypatch):
    """Other entity of the same kind: a different type and lab, typed in lower case."""
    _prefix_graph(monkeypatch, {"MUS-240202XYZ": ["MUS-240202XYZ-1", "MUS-240202XYZ-2"]})
    fetched = _capture_fetch(monkeypatch)

    _resolve(["mus-240202xyz"])

    assert fetched == [["MUS-240202XYZ-1", "MUS-240202XYZ-2"]]


def test_a_prefix_that_matches_nothing_is_a_plain_error_naming_it(monkeypatch):
    _prefix_graph(monkeypatch, {})
    fetched = _capture_fetch(monkeypatch)

    out = _resolve(["MUS-240202XYZ"])

    assert out["ok"] is False
    assert "MUS-240202XYZ" in out["error"]
    assert fetched == []


def test_a_full_uid_beside_a_prefix_expands_only_the_prefix(monkeypatch):
    seen = _prefix_graph(monkeypatch, {"D.SEQ-250101ABC": ["D.SEQ-250101ABC-1", "D.SEQ-250101ABC-2"]})
    fetched = _capture_fetch(monkeypatch)

    _resolve(["MUS-1-PUB", "D.SEQ-250101ABC"])

    assert [q["prefixes"] for q in seen["queries"]] == [["D.SEQ-250101ABC"]]
    assert fetched == [["MUS-1-PUB", "D.SEQ-250101ABC-1", "D.SEQ-250101ABC-2"]]


def test_a_prefix_with_more_matches_than_the_leaf_cap_gets_the_narrow_it_error(monkeypatch):
    from chat_nextseek.pipeline.agent_tools import MAX_RESOLVE_LEAVES
    _prefix_graph(monkeypatch, {"D.SEQ-250101ABC": [f"D.SEQ-250101ABC-{i}" for i in range(MAX_RESOLVE_LEAVES + 5)]})
    fetched = _capture_fetch(monkeypatch)

    out = _resolve(["D.SEQ-250101ABC"])

    assert out["ok"] is False
    assert "narrow" in out["error"].lower()
    assert str(MAX_RESOLVE_LEAVES) in out["error"]
    assert fetched == []


def test_an_overflowing_prefix_is_narrowed_even_when_another_prefix_got_no_rows(monkeypatch):
    """The shared LIMIT filled with one prefix, so the other reads as empty: the overflow is the true answer."""
    from chat_nextseek.pipeline.agent_tools import MAX_RESOLVE_LEAVES
    _prefix_graph(monkeypatch, {"D.SEQ-250101ABC": [f"D.SEQ-250101ABC-{i}" for i in range(MAX_RESOLVE_LEAVES + 1)]})
    fetched = _capture_fetch(monkeypatch)

    out = _resolve(["D.SEQ-250101ABC", "MUS-240202XYZ"])

    assert "narrow" in out["error"].lower()
    assert "No samples were found" not in out["error"]
    assert fetched == []


def test_an_empty_prefix_beside_a_small_one_is_still_named(monkeypatch):
    """Other entity of the same kind: no overflow, so the empty prefix is reported."""
    _prefix_graph(monkeypatch, {"D.SEQ-250101ABC": ["D.SEQ-250101ABC-1"]})
    _capture_fetch(monkeypatch)

    out = _resolve(["D.SEQ-250101ABC", "MUS-240202XYZ"])

    assert "No samples were found under the prefix MUS-240202XYZ" in out["error"]


def test_a_failed_fetch_clips_a_whole_html_body(monkeypatch):
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata",
                        lambda c, u: {"ok": False, "status_code": 502, "data": {"_raw": "<html>" + "x" * 5000}})

    out = _resolve(["MUS-1-PUB"])

    assert "502" in out["error"] and len(out["error"]) < 500


def test_an_http_failure_from_the_metadata_fetch_reports_status_and_detail(monkeypatch):
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata",
                        lambda c, u: {"ok": False, "status_code": 404, "data": {"detail": "No samples found"}})

    out = _resolve(["MUS-1-PUB"])

    assert out["ok"] is False
    assert "404" in out["error"] and "No samples found" in out["error"]
    assert "unknown error" not in out["error"]


def test_configure_run_asks_for_the_pairing_before_tumour_tools(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("patient,sex,status,sample,lane,fastq_1,fastq_2\n"
                     "P1,NA,1,T1,L1,/net/bmc-pub10/x/T1_1.fq.gz,\n"
                     "P1,NA,0,N1,L1,/net/bmc-pub10/x/N1_1.fq.gz,\n")
    state = {"artifacts": {"samplesheet": str(sheet)}, "bundle_key": "GRCh38",
             "data_driven_evidence": {"T1": {"status": {"verdict": "corroborated", "value": "1"}},
                                      "N1": {"status": {"verdict": "defaulted", "value": "0"}}}}
    out = _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "sarek",
                                                         "params": {"tools": "mutect2"}}, str(tmp_path)))
    assert out["ok"] is False and out["needs_user_input"] == ["status"]
    assert "N1" in out["ask_the_user"]


def test_configure_run_schema_offers_pairing_confirmed():
    from chat_nextseek.pipeline.agent_tools import _SCHEMA_BY_NAME
    props = _SCHEMA_BY_NAME["configure_run"]["input_schema"]["properties"]
    assert props["pairing_confirmed"]["type"] == "boolean"


# --- the pairing confirmation is enforced mechanically, not trusted from the model ---------

def _pairing_state(tmp_path, n_user_msgs=1):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("patient,sex,status,sample,lane,fastq_1,fastq_2\n"
                     "P1,NA,1,T1,L1,/net/bmc-pub10/x/T1_1.fq.gz,\n"
                     "P1,NA,0,N1,L1,/net/bmc-pub10/x/N1_1.fq.gz,\n")
    return {"artifacts": {"samplesheet": str(sheet)}, "bundle_key": "GRCh38",
            "messages": [{"role": "user", "content": f"m{i}"} for i in range(n_user_msgs)],
            "data_driven_evidence": {"T1": {"status": {"verdict": "corroborated", "value": "1"}},
                                     "N1": {"status": {"verdict": "defaulted", "value": "0"}}}}


def _configure(state, tmp_path, **kw):
    tool_input = {"pipeline_key": "sarek", "params": {"tools": "mutect2"}, **kw}
    return _json.loads(tool_configure_run(_Cfg(), state, tool_input, str(tmp_path)))


def _is_pairing_refusal(out):
    return out.get("ok") is False and out.get("needs_user_input") == ["status"]


def test_a_confirmation_with_no_prior_ask_still_asks(tmp_path):
    state = _pairing_state(tmp_path)
    out = _configure(state, tmp_path, pairing_confirmed=True)
    assert _is_pairing_refusal(out) and "N1" in out["ask_the_user"]
    assert state["pairing_asked_at_user_msgs"] == 1


def test_a_confirmation_after_the_user_replied_goes_ahead(tmp_path):
    state = _pairing_state(tmp_path)
    assert _is_pairing_refusal(_configure(state, tmp_path))
    state["messages"].append({"role": "user", "content": "yes, N1 is the normal"})
    out = _configure(state, tmp_path, pairing_confirmed=True)
    assert not _is_pairing_refusal(out)
    assert out["ok"] is True, out
    assert state["pairing_confirmed_for"] == state["artifacts"]["samplesheet"]


def test_a_confirmation_in_the_same_turn_as_the_ask_still_asks(tmp_path):
    state = _pairing_state(tmp_path)
    assert _is_pairing_refusal(_configure(state, tmp_path))
    out = _configure(state, tmp_path, pairing_confirmed=True)   # no new user message
    assert _is_pairing_refusal(out)
    assert "pairing_confirmed_for" not in state


def test_a_later_configure_on_the_same_sheet_is_not_asked_again(tmp_path):
    state = _pairing_state(tmp_path)
    _configure(state, tmp_path)
    state["messages"].append({"role": "user", "content": "yes"})
    assert not _is_pairing_refusal(_configure(state, tmp_path, pairing_confirmed=True))
    assert not _is_pairing_refusal(_configure(state, tmp_path))   # e.g. an aligner tweak


def test_rebuilding_the_samplesheet_forgets_the_confirmation(monkeypatch, tmp_path):
    from types import SimpleNamespace

    def fake_emit(out_dir, **kw):
        return SimpleNamespace(saved_files={"samplesheet": str(out_dir) + "/samplesheet.csv"},
                               samplesheet_row_count=len(kw["samplesheet_rows"]),
                               launch_entry=None, fetchngs_launch_entry=None)

    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.emit_nfcore_artifacts", fake_emit)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.resolve_accessions", lambda accs: [])
    state = {"resolved": {"uids": ["D.SEQ-1-PUB"], "accessions": ["SRR111"]}, "log_dir": str(tmp_path),
             "pairing_asked_at_user_msgs": 1, "pairing_confirmed_for": "/x/samplesheet.csv"}
    out = _json.loads(tool_write_samplesheet(
        _Cfg(), state,
        {"pipeline_key": "rnaseq", "cohorts": [
            {"label": "rnaseq-all", "rows": [{"sample": "D.SEQ-1-PUB", "accession": "SRR111",
                                              "strandedness": "auto"}]}]},
        str(tmp_path)))
    assert out["ok"] is True
    assert "pairing_asked_at_user_msgs" not in state and "pairing_confirmed_for" not in state


def test_configure_run_refuses_tumour_tools_for_an_unpaired_patient(tmp_path):
    state = _pairing_state(tmp_path)
    (tmp_path / "samplesheet.csv").write_text(
        "patient,sex,status,sample,lane,fastq_1,fastq_2\n"
        "P1,NA,1,T1,L1,/net/bmc-pub10/x/T1_1.fq.gz,\n")
    state["messages"].append({"role": "user", "content": "yes"})
    out = _configure(state, tmp_path, pairing_confirmed=True)
    assert _is_pairing_refusal(out) and "P1" in out["ask_the_user"]


def test_a_refusal_does_not_arm_the_confirmation_for_a_later_table(tmp_path):
    # The unpaired-patient refusal is not a table the user can answer "yes" to: a "yes" after
    # it must not count as the confirmation of the table that comes once the sheet is fixed.
    state = _pairing_state(tmp_path)
    good = (tmp_path / "samplesheet.csv").read_text()
    (tmp_path / "samplesheet.csv").write_text(
        "patient,sex,status,sample,lane,fastq_1,fastq_2\n"
        "P1,NA,1,T1,L1,/net/bmc-pub10/x/T1_1.fq.gz,\n")
    refusal = _configure(state, tmp_path)
    assert _is_pairing_refusal(refusal) and "P1" in refusal["ask_the_user"]
    assert "pairing_asked_at_user_msgs" not in state
    # The user answers, the sheet is fixed, and the model passes pairing_confirmed=True while
    # an undecided sample (N1, defaulted) is still there: it must still be asked.
    state["messages"].append({"role": "user", "content": "I added the normal"})
    (tmp_path / "samplesheet.csv").write_text(good)
    out = _configure(state, tmp_path, pairing_confirmed=True)
    assert _is_pairing_refusal(out) and "| N1 |" in out["ask_the_user"]
    assert "pairing_confirmed_for" not in state
    assert state["pairing_asked_at_user_msgs"] == 2


# --- a status the metadata reads both ways is confirmed in the pairing table, not asked forever ---------

_PAIR_ROWS = [
    {"patient": "P1", "sex": "NA", "status": "1", "sample": "T1", "lane": "L1",
     "fastq_1": "/net/bmc-pub10/x/T1_1.fq.gz", "fastq_2": ""},
    {"patient": "P1", "sex": "NA", "status": "0", "sample": "N1", "lane": "L1",
     "fastq_1": "/net/bmc-pub10/x/N1_1.fq.gz", "fastq_2": ""},
]


def _conflict_state(rows):
    evidence = {r["sample"]: {"status": {"verdict": "corroborated", "value": r["status"],
                                         "evidence": "Type=x; Name=x"}} for r in rows}
    evidence["N1"] = {"status": {"verdict": "conflict", "value": None, "evidence": "ambiguous in Type"}}
    return {"resolved": {"uids": [r["sample"] for r in rows], "accessions": []},
            "bundle_key": "GRCh38", "messages": [{"role": "user", "content": "run sarek on these"}],
            "data_driven_evidence": evidence}


def _write_sheet(state, rows, tmp_path):
    return _json.loads(tool_write_samplesheet(
        _Cfg(), state, {"pipeline_key": "sarek", "cohorts": [{"label": "sarek-all", "rows": rows}]},
        str(tmp_path)))


def test_a_status_conflict_is_resolved_through_the_pairing_table(tmp_path):
    state = _conflict_state(_PAIR_ROWS)
    wrote = _write_sheet(state, _PAIR_ROWS, tmp_path)
    assert wrote["ok"] is True, wrote
    asked = _configure(state, tmp_path)
    assert _is_pairing_refusal(asked), asked
    assert "| P1 | N1 | normal (metadata unclear: ambiguous in Type) |" in asked["ask_the_user"]
    state["messages"].append({"role": "user", "content": "yes, N1 is the blood normal"})
    out = _configure(state, tmp_path, pairing_confirmed=True)
    assert out["ok"] is True, out


def test_a_germline_run_with_a_status_conflict_asks_for_the_table(tmp_path):
    rows = [dict(_PAIR_ROWS[1]), dict(_PAIR_ROWS[1], patient="P2", sample="N2",
                                      fastq_1="/net/bmc-pub10/x/N2_1.fq.gz")]
    state = _conflict_state(rows)
    assert _write_sheet(state, rows, tmp_path)["ok"] is True
    out = _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "sarek",
                                                         "params": {"tools": "haplotypecaller"}}, str(tmp_path)))
    assert _is_pairing_refusal(out), out
    assert "| P1 | N1 | normal (metadata unclear: ambiguous in Type) |" in out["ask_the_user"]


def test_the_pairing_check_reads_the_tools_the_run_will_use(monkeypatch, tmp_path):
    # A tumour tool that arrives through the curated defaults, not from the model, is still checked.
    real = at.build_run_params

    def with_default_mutect2(pipeline_key, agent_params, bundle_key):
        merged, errors, status = real(pipeline_key, agent_params, bundle_key)
        if "tools" not in (agent_params or {}):
            merged["tools"] = "mutect2"
        return merged, errors, status

    monkeypatch.setattr(at, "build_run_params", with_default_mutect2)
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("patient,sex,status,sample,lane,fastq_1,fastq_2\n"
                     "P1,NA,0,N1,L1,/net/bmc-pub10/x/N1_1.fq.gz,\n")
    state = {"artifacts": {"samplesheet": str(sheet)}, "bundle_key": "GRCh38",
             "data_driven_evidence": {"N1": {"status": {"verdict": "defaulted", "value": "0"}}}}
    out = _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "sarek", "params": {}}, str(tmp_path)))
    assert _is_pairing_refusal(out), out
    assert "mutect2" in out["ask_the_user"] and "P1" in out["ask_the_user"]


def test_configure_run_refuses_sarek_on_a_genome_with_no_staged_dna_resources(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("patient,sex,status,sample,lane,fastq_1,fastq_2\n"
                     "P1,NA,0,N1,L1,/net/bmc-pub10/x/N1_1.fq.gz,\n")
    state = {"artifacts": {"samplesheet": str(sheet)}, "bundle_key": "GRCm39",
             "data_driven_evidence": {"N1": {"status": {"verdict": "corroborated", "value": "0"}}}}
    out = _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "sarek", "params": {}}, str(tmp_path)))
    assert out["ok"] is False
    assert "GATK GRCh38 bundle" in out["error"] and "GRCm39" in out["error"]
    assert "params" not in state["artifacts"]


# --- sarek tools and outputs Luria cannot serve are refused, whatever the menu says ---------

def _germline_state(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("patient,sex,status,sample,lane,fastq_1,fastq_2\n"
                     "P1,NA,0,N1,L1,/net/bmc-pub10/x/N1_1.fq.gz,\n")
    return {"artifacts": {"samplesheet": str(sheet)}, "bundle_key": "GRCh38",
            "data_driven_evidence": {"N1": {"status": {"verdict": "corroborated", "value": "0"}}}}


@pytest.mark.parametrize("tools", ["haplotypecaller,ascat", "ControlFREEC", "manta, ASCAT "])
def test_configure_run_refuses_sarek_tools_whose_files_are_not_staged(tmp_path, tools):
    state = _germline_state(tmp_path)
    out = _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "sarek",
                                                         "params": {"tools": tools}}, str(tmp_path)))
    assert out["ok"] is False and "not staged on Luria" in out["error"]
    assert "params" not in state["artifacts"]


@pytest.mark.parametrize("value", [True, "true"])
def test_configure_run_refuses_sarek_bam_output(tmp_path, value):
    state = _germline_state(tmp_path)
    out = _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "sarek",
                                                         "params": {"save_output_as_bam": value}}, str(tmp_path)))
    assert out["ok"] is False and "CRAM" in out["error"]
    assert "params" not in state["artifacts"]


def test_a_normal_sarek_germline_run_is_not_refused(tmp_path):
    state = _germline_state(tmp_path)
    out = _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "sarek",
                                                         "params": {"tools": "haplotypecaller,manta",
                                                                    "save_output_as_bam": False}}, str(tmp_path)))
    assert out["ok"] is True, out


# --- a refused configure_run leaves nothing to submit ---------

class _CfgLuria(_Cfg):
    LURIA_ENV_COMPLETE = True
    LURIA_ENV = {}


def _sarek(state, tmp_path, tools):
    return _json.loads(tool_configure_run(_Cfg(), state, {"pipeline_key": "sarek",
                                                          "params": {"tools": tools}}, str(tmp_path)))


def test_a_refused_configure_run_leaves_no_launch_to_submit(monkeypatch, tmp_path):
    # The first setup is good; the user then asks for a tool Luria cannot run, the change is
    # refused, and their next "yes" must not submit the first setup.
    submitted = []
    monkeypatch.setattr(at, "submit_luria", lambda *a, **kw: submitted.append(a) or [])
    state = _germline_state(tmp_path)
    state["messages"] = [{"role": "user", "content": "call germline variants"}]
    assert _sarek(state, tmp_path, "haplotypecaller")["ok"] is True
    state["messages"].append({"role": "user", "content": "add ascat"})
    refused = _sarek(state, tmp_path, "haplotypecaller,ascat")
    assert refused["ok"] is False and "not staged on Luria" in refused["error"]
    state["messages"].append({"role": "user", "content": "yes"})

    out = _json.loads(at.tool_submit_to_luria(_CfgLuria(), state))

    assert out["ok"] is False and "configure_run" in out["message"], out
    assert submitted == []
    assert "launch" not in state["artifacts"] and "params" not in state["artifacts"]
    assert "launch_plan" not in state and "launch_built_at_user_msgs" not in state


def test_a_pairing_refusal_after_a_good_setup_clears_it_too(tmp_path):
    state = _germline_state(tmp_path)
    assert _sarek(state, tmp_path, "haplotypecaller")["ok"] is True
    out = _sarek(state, tmp_path, "mutect2")   # N1's patient has no tumour
    assert _is_pairing_refusal(out) and "P1" in out["ask_the_user"]
    assert "launch" not in state["artifacts"] and "launch_plan" not in state
