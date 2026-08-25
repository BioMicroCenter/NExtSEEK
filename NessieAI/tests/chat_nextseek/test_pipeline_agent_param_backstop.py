"""Wiring: resolve_samples surfaces seq_type evidence; write_samplesheet enforces it."""
import json

import chat_nextseek.pipeline.agent_tools as at


class _Cfg:
    LOG_DIR = "/tmp"
    TOWER_ENV = {}


def _fake_reporter(monkeypatch, leaves_meta):
    """Make tool_resolve_samples yield one leaf per (uid, metadata) with no network."""
    def fake_fetch(config, uids):
        return {"ok": True, "uids": list(uids)}
    def fake_annotate(config, raw):
        return raw
    def fake_leaves(annotated, accepted_types=None):
        return [{"uid": uid, "sample_type": "D.SEQ", "assay": "", "source_uid": uid,
                 "metadata": md} for uid, md in leaves_meta.items()]
    monkeypatch.setattr(at, "fetch_reporter_metadata", fake_fetch)
    monkeypatch.setattr(at, "annotate_metadata_with_sampletypes", fake_annotate)
    monkeypatch.setattr(at, "enumerate_lineage_leaves", fake_leaves)
    # Summary path is advisory; force the fallback so _flatten_lineage uses leaf metadata.
    monkeypatch.setattr(at, "build_metadata_summary", lambda x: {"by_sample_type": {}, "_uid_index": {}})
    monkeypatch.setattr(at, "filter_summary_to_sequencing_lineage", lambda s: s)


def test_resolve_surfaces_seq_type_verdict(monkeypatch):
    _fake_reporter(monkeypatch, {
        "D.SEQ-1": {"UID": "D.SEQ-1", "Name": "X-GEX", "File_PrimaryData": "X-GEX_R1.fastq.gz",
                    "SequencingType": "Single Cell RNAseq"},
    })
    state = {}
    out = json.loads(at.tool_resolve_samples(_Cfg(), session={}, state=state,
                                             tool_input={"kind": "explicit_uids", "uids": ["D.SEQ-1"]},
                                             pipeline_key="hlatyping"))
    leaf = out["leaves"][0]
    assert leaf["data_driven_params"]["seq_type"]["value"] == "rna"
    assert "SequencingType" in leaf["signals"]
    assert out["data_driven_params"]["seq_type"]["target"] == "row_column"
    assert state["data_driven_evidence"]["D.SEQ-1"]["seq_type"]["verdict"] in (
        "corroborated", "derived_uncorroborated")


def test_resolve_omits_data_driven_params_key_for_non_atlas_pipeline(monkeypatch):
    _fake_reporter(monkeypatch, {
        "D.SEQ-1": {"UID": "D.SEQ-1", "Name": "X", "SequencingType": "RNA-Seq"},
    })
    state = {}
    out = json.loads(at.tool_resolve_samples(_Cfg(), session={}, state=state,
                                             tool_input={"kind": "explicit_uids", "uids": ["D.SEQ-1"]},
                                             pipeline_key="atacseq"))
    assert "data_driven_params" not in out


def _resolved_state(evidence):
    return {"resolved": {"uids": ["D.SEQ-1"], "accessions": []},
            "data_driven_evidence": evidence,
            "accession_file_paths": {}}


def test_write_samplesheet_blocks_on_conflict(monkeypatch):
    state = _resolved_state({"D.SEQ-1": {"seq_type": {"value": None, "verdict": "conflict", "evidence": "e"}}})
    out = json.loads(at.tool_write_samplesheet(_Cfg(), state,
        {"pipeline_key": "hlatyping",
         "cohorts": [{"label": "c", "rows": [{"sample": "D.SEQ-1", "seq_type": "rna"}]}]},
        log_dir="/tmp"))
    assert out["ok"] is False
    assert "seq_type" in out.get("needs_user_input", [])
    assert "ask_the_user" in out


def test_write_samplesheet_corrects_wrong_seq_type(monkeypatch):
    state = _resolved_state({"D.SEQ-1": {"seq_type": {"value": "rna", "verdict": "corroborated", "evidence": "e"}}})
    out = json.loads(at.tool_write_samplesheet(_Cfg(), state,
        {"pipeline_key": "hlatyping",
         "cohorts": [{"label": "c", "rows": [{"sample": "D.SEQ-1", "seq_type": "dna"}]}]},
        log_dir="/tmp"))
    assert out["ok"] is False
    assert any("seq_type" in e and "rna" in e for e in out["errors"])


from pathlib import Path as _P


def test_prompt_mentions_data_driven_params():
    txt = (_P(__file__).resolve().parent.parent
           / "src" / "chat_nextseek" / "prompts" / "pipeline_agent.txt").read_text()
    assert "data_driven_params" in txt
    assert "conflict" in txt and "absent" in txt


def test_rnasplice_blocks_when_condition_is_blank():
    state = {"resolved": {"uids": ["D.SEQ-1"], "accessions": []}, "accession_file_paths": {}}
    out = json.loads(at.tool_write_samplesheet(_Cfg(), state,
        {"pipeline_key": "rnasplice",
         "cohorts": [{"label": "c", "rows": [{"sample": "D.SEQ-1", "strandedness": "auto"}]}]},
        log_dir="/tmp"))
    assert out["ok"] is False
    assert any("condition" in e for e in out["errors"])


def test_rnasplice_passes_when_condition_present():
    state = {"resolved": {"uids": ["D.SEQ-1", "D.SEQ-2"], "accessions": []}, "accession_file_paths": {}}
    out = json.loads(at.tool_write_samplesheet(_Cfg(), state,
        {"pipeline_key": "rnasplice",
         "cohorts": [{"label": "c", "rows": [
             {"sample": "D.SEQ-1", "strandedness": "auto", "condition": "treated"},
             {"sample": "D.SEQ-2", "strandedness": "auto", "condition": "control"}]}]},
        log_dir="/tmp"))
    assert out["ok"] is True


# --- configure_run must auto-fill an inferred run_param, not silently drop it --------
#
# Bug being guarded: build_run_params merges curated menu defaults <- bundle params
# <- agent_params. A data-driven run_param (scrnaseq protocol, smrnaseq
# three_prime_adapter) that the agent never set used to fall through to the curated
# default (e.g. the Illumina adapter) even when the evidence unanimously derived a
# different value (e.g. a QIAseq kit's adapter) -- defeating the whole feature.

def _configure_run_state(tmp_path, evidence):
    samplesheet = tmp_path / "s.csv"
    samplesheet.write_text("sample,fastq_1,fastq_2\n")
    return {"artifacts": {"samplesheet": str(samplesheet), "base_dir": str(tmp_path)},
            "bundle_key": None,
            "data_driven_evidence": evidence}


def test_configure_run_autofills_inferred_smrnaseq_adapter(tmp_path):
    from chat_nextseek.pipeline import agent_tools as at
    state = _configure_run_state(tmp_path, {
        "D.SEQ-1": {"three_prime_adapter": {
            "value": "AACTGTAGGCACCATCAAT", "verdict": "derived_uncorroborated", "evidence": "e"}}})
    out = json.loads(at.tool_configure_run(
        _Cfg(), state,
        {"pipeline_key": "smrnaseq", "params": {"mirtrace_species": "hsa"}},
        str(tmp_path)))
    assert out["ok"] is True, out
    assert out["resolved_params"]["three_prime_adapter"] == "AACTGTAGGCACCATCAAT"
    assert out["resolved_params"]["three_prime_adapter"] != "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"


def test_configure_run_autofills_inferred_scrnaseq_protocol(tmp_path):
    from chat_nextseek.pipeline import agent_tools as at
    state = _configure_run_state(tmp_path, {
        "D.SEQ-1": {"protocol": {"value": "10XV3", "verdict": "derived_uncorroborated", "evidence": "e"}}})
    out = json.loads(at.tool_configure_run(_Cfg(), state, {"pipeline_key": "scrnaseq"}, str(tmp_path)))
    assert out["ok"] is True, out
    assert out["resolved_params"]["protocol"] == "10XV3"
    assert out["resolved_params"]["protocol"] != "auto"


def test_configure_run_does_not_clobber_an_agent_supplied_value(tmp_path):
    """setdefault must not overwrite an explicit agent value; a disagreeing explicit
    value is still left for check_run_params to flag as a correction, not silently
    replaced by the inferred one and not silently accepted either."""
    from chat_nextseek.pipeline import agent_tools as at
    state = _configure_run_state(tmp_path, {
        "D.SEQ-1": {"three_prime_adapter": {
            "value": "AACTGTAGGCACCATCAAT", "verdict": "derived_uncorroborated", "evidence": "e"}}})
    out = json.loads(at.tool_configure_run(
        _Cfg(), state,
        {"pipeline_key": "smrnaseq",
         "params": {"mirtrace_species": "hsa", "three_prime_adapter": "TGGAATTCTCGGGTGCCAAGG"}},
        str(tmp_path)))
    # check_run_params flags the mismatch as an error rather than accepting either
    # value silently -- proving the agent's explicit value reached validation
    # unmodified (setdefault did not overwrite it with the inferred one).
    assert out["ok"] is False, out
    assert any("AACTGTAGGCACCATCAAT" in e for e in out.get("errors", []))


def test_resolve_injects_protocol_text_when_an_entry_needs_it(monkeypatch):
    # a throwaway atlas entry for a real pipeline key, referencing __protocol_text__
    import chat_nextseek.seqera.param_atlas as pa
    spec = {"target": "run_param", "scope": "sample", "allowed": ["a", "b"],
            "derive_from": {"signal": "Kit", "map": {"a": ["ka"], "b": ["kb"]}},
            "corroborate_with": [{"signal": "__protocol_text__", "a_markers": ["ka"], "b_markers": ["kb"]}],
            "ask": {"definition": "d", "on_conflict": "c", "on_absent": "e"}}
    monkeypatch.setattr(pa, "_CACHE", {})
    monkeypatch.setattr(pa, "load_param_atlas",
                        lambda path=None: {"guidance": {"notes": ["x"]},
                                           "pipelines": {"scrnaseq": {"params": {"protocol": spec}}}})
    monkeypatch.setattr(at, "gather_protocol_text",
                        lambda config, annotated, base_dir=None: {"text": "kit ka in methods", "status": {"n_ok": 1}})
    _fake_reporter(monkeypatch, {"D.SEQ-1": {"UID": "D.SEQ-1", "Kit": ""}})
    state = {}
    out = json.loads(at.tool_resolve_samples(_Cfg(), session={}, state=state,
                                             tool_input={"kind": "explicit_uids", "uids": ["D.SEQ-1"]},
                                             pipeline_key="scrnaseq"))
    leaf = out["leaves"][0]
    assert leaf["signals"]["__protocol_text__"] == "kit ka in methods"
    assert leaf["data_driven_params"]["protocol"]["value"] == "a"     # derived from protocol text
    assert "protocol_text_status" in out


def test_prompt_warns_that_umi_discard_read_is_paired_end_only():
    """umi_discard_read on single-end data discards the only read — the agent must be told."""
    txt = (_P(__file__).resolve().parent.parent
           / "src" / "chat_nextseek" / "prompts" / "pipeline_agent.txt").read_text()
    assert "umi_discard_read" in txt
    assert "single-end" in txt


def test_configure_run_writes_no_length_correction_for_a_three_prime_cohort(tmp_path):
    """End-to-end: a 3' DGE verdict must reach params.yml, not be lost to the menu default."""
    state = {"resolved": {"uids": ["D.SEQ-1"], "accessions": []},
             "accession_file_paths": {},
             "artifacts": {"samplesheet": str(tmp_path / "samplesheet.csv"),
                           "base_dir": str(tmp_path)},
             "data_driven_evidence": {
                 "D.SEQ-1": {"extra_salmon_quant_args": {
                     "value": "--noLengthCorrection",
                     "verdict": "derived_uncorroborated",
                     "evidence": "__protocol_text__=--noLengthCorrection"}}}}
    (tmp_path / "samplesheet.csv").write_text("sample,fastq_1,fastq_2,strandedness\n")
    out = json.loads(at.tool_configure_run(_Cfg(), state,
        {"pipeline_key": "rnaseq", "params": {}}, log_dir=str(tmp_path)))
    assert out["ok"] is True, out
    assert out["resolved_params"]["extra_salmon_quant_args"] == "--noLengthCorrection"


def test_configure_run_leaves_length_correction_on_for_a_standard_cohort(tmp_path):
    state = {"resolved": {"uids": ["D.SEQ-1"], "accessions": []},
             "accession_file_paths": {},
             "artifacts": {"samplesheet": str(tmp_path / "samplesheet.csv"),
                           "base_dir": str(tmp_path)},
             "data_driven_evidence": {
                 "D.SEQ-1": {"extra_salmon_quant_args": {
                     "value": "", "verdict": "defaulted", "evidence": "no signal"}}}}
    (tmp_path / "samplesheet.csv").write_text("sample,fastq_1,fastq_2,strandedness\n")
    out = json.loads(at.tool_configure_run(_Cfg(), state,
        {"pipeline_key": "rnaseq", "params": {}}, log_dir=str(tmp_path)))
    assert out["ok"] is True, out
    assert out["resolved_params"].get("extra_salmon_quant_args", "") == ""
