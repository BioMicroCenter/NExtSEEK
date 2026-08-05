"""bamtofastq: the first pipeline whose cohort leaves are A.* analysis records.

Two things are being guarded here that nothing else in the suite covers:
  1. the emitter can resolve an ALIGNMENT file (not a FASTQ) out of sample metadata
     and emit bamtofastq's sample_id/mapped/index/file_type sheet, and
  2. a bam-input sheet never triggers the fetchngs pre-stage, whose blank-fastq_1
     heuristic would otherwise fire on every row of a sheet that has no fastq_1
     column at all.

Column names and enums were read off assets/schema_input.json @ 2.2.1 and the
reference flags off nextflow_schema.json @ 2.2.1. If the revision is bumped,
re-derive both — a stale --gtf here aborts the run at nf-schema validation.
"""
import csv
import json
from pathlib import Path

import pytest

from chat_nextseek.seqera.catalog import get_pipeline_entry
from chat_nextseek.seqera.emitter import (
    _alignment_from_meta,
    emit_nfcore_artifacts,
)


# Shape taken from a real A.ALN record in seek_production: the path fields are a bare
# basename plus an SRA archive URL, and the checksum carries no path at all.
ARCHIVE_ONLY_META = {
    "UID": "A.ALN-230303ESS-1-PUB",
    "Name": "SRR22993650",
    "File_PrimaryData": "8205.1.consensus.bam",
    "Link_PrimaryData": "https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR22993650/SRR22993650",
    "DataType": "BAM",
    "Parent": "D.SEQ-230303ESS-4-PUB",
    "Genome": "GRCm38.p6",
    "Checksum_PrimaryData": "285fb6d9cc0326380ff6aea74f6c11ca",
}

LOCAL_BAM_META = {
    "UID": "A.ALN-1-PUB",
    "File_PrimaryData": "/net/bmc-pub10/data1/bmc/aln/s1.consensus.bam",
    "File_SecondaryData": "/net/bmc-pub10/data1/bmc/aln/s1.consensus.bam.bai",
    "DataType": "BAM",
}


def _read_sheet(tmp_path: Path) -> tuple[list[str], list[dict]]:
    with (tmp_path / "samplesheet.csv").open(newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


# --- catalog -----------------------------------------------------------------


def test_catalog_entry_matches_the_pinned_2_2_1_schemas():
    entry = get_pipeline_entry("bamtofastq")
    assert entry["default_revision"] == "2.2.1"
    # 2.2.1 declares genome/fasta/fasta_fai and NO gtf.
    assert entry["reference_cli_flags"] == ["genome", "fasta"]
    # schema_input.json: required sample_id, mapped, file_type; index optional.
    assert entry["required_columns"] == ["sample_id", "mapped", "file_type"]
    assert entry["samplesheet_input_kind"] == "bam"
    # The whole point: inputs are analysis records, not raw data.
    assert entry["accepted_leaf_sample_types"] == ["A.ALN"]
    assert "D.SEQ" not in entry["accepted_leaf_sample_types"]


def test_template_agrees_with_the_catalog():
    doc = json.loads(
        (Path(__file__).resolve().parent.parent
         / "src/chat_nextseek/reports/templates/nfcore/bamtofastq.json").read_text())
    entry = get_pipeline_entry("bamtofastq")
    assert doc["pipeline"]["required_columns"] == entry["required_columns"]
    assert doc["reference_resources"] == []
    for key, spec in doc["params"].items():
        assert "type" in spec and "default" in spec and "steerable" in spec, key
    # A gtf param would mean the catalog's flag list is wrong.
    assert "gtf" not in doc["params"]


def test_report_type_aliases_resolve():
    from chat_nextseek.reports.templates_meta import (
        get_report_template_basename, nfcore_pipeline_from_report_type, normalize_report_type,
    )
    for alias in ("NFCORE_BAMTOFASTQ", "nf-core bamtofastq", "NFCORE_BAMTOFASTQ_SAMPLESHEET"):
        assert normalize_report_type(alias) == "NFCORE_BAMTOFASTQ"
        assert nfcore_pipeline_from_report_type(alias) == "bamtofastq"
        assert get_report_template_basename(alias) == "nfcore/bamtofastq"


# --- the resolver ------------------------------------------------------------


def test_resolver_finds_a_local_bam_and_its_index():
    mapped, file_type, index = _alignment_from_meta(LOCAL_BAM_META)
    assert mapped == "/net/bmc-pub10/data1/bmc/aln/s1.consensus.bam"
    assert file_type == "bam"
    assert index == "/net/bmc-pub10/data1/bmc/aln/s1.consensus.bam.bai"


def test_resolver_derives_cram_and_prefers_a_crai_over_a_bai():
    mapped, file_type, index = _alignment_from_meta({
        "File_PrimaryData": "/net/x/s.cram",
        "File_SecondaryData": "/net/x/s.cram.crai",
        # A stray .bai in the record must not be attached to a cram.
        "Other": "/net/x/unrelated.bai",
    })
    assert (mapped, file_type, index) == ("/net/x/s.cram", "cram", "/net/x/s.cram.crai")


def test_resolver_prefers_a_local_path_over_a_remote_url():
    mapped, _ft, _ix = _alignment_from_meta({
        "Link_PrimaryData": "https://example.org/archive/s1.bam",
        "File_PrimaryData": "/net/bmc-pub10/data1/bmc/aln/s1.bam",
    })
    assert mapped == "/net/bmc-pub10/data1/bmc/aln/s1.bam"


def test_resolver_returns_nothing_for_an_archive_only_record():
    # A bare basename is not a path, and the SRA URL is the run archive, not the BAM.
    # Returning '' is the honest answer; the row still gets written so the gap shows up.
    assert _alignment_from_meta(ARCHIVE_ONLY_META) == ("", "", "")


def test_resolver_ignores_a_checksum_field_that_happens_to_look_pathlike():
    mapped, _ft, _ix = _alignment_from_meta({
        "Checksum_PrimaryData": "abc/def.bam",
        "File_PrimaryData": "/net/x/real.bam",
    })
    assert mapped == "/net/x/real.bam"


# --- the emitted samplesheet -------------------------------------------------


def test_emitted_sheet_has_bamtofastq_columns_not_fastq_ones(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="bamtofastq",
        samplesheet_rows=[{"sample": "A.ALN-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"A.ALN-1-PUB": LOCAL_BAM_META},
    )
    header, rows = _read_sheet(tmp_path)
    assert header[:3] == ["sample_id", "mapped", "file_type"]
    assert "fastq_1" not in header and "fastq_2" not in header
    assert "sample" not in header  # renamed to sample_id
    assert rows[0]["sample_id"] == "A.ALN-1-PUB"
    assert rows[0]["mapped"] == LOCAL_BAM_META["File_PrimaryData"]
    assert rows[0]["file_type"] == "bam"
    assert rows[0]["index"] == LOCAL_BAM_META["File_SecondaryData"]


def test_file_type_always_matches_the_mapped_extension(tmp_path):
    # file_type is enum-checked by nf-schema and a mismatch is not auto-corrected,
    # so it is derived from the path and never from the record's DataType text.
    emit_nfcore_artifacts(
        tmp_path, pipeline="bamtofastq",
        samplesheet_rows=[{"sample": "A.ALN-1-PUB"}, {"sample": "A.ALN-2-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={
            # DataType deliberately disagrees with the extension on both rows.
            "A.ALN-1-PUB": {"File_PrimaryData": "/net/x/a.cram", "DataType": "BAM"},
            "A.ALN-2-PUB": {"File_PrimaryData": "/net/x/b.bam", "DataType": "CRAM"},
        },
    )
    _header, rows = _read_sheet(tmp_path)
    for row in rows:
        assert row["file_type"] == Path(row["mapped"]).suffix.lstrip(".")


def test_row_survives_with_blank_mapped_when_nothing_resolves(tmp_path):
    # Never drop a row: an empty `mapped` is visible in the sheet and catchable before
    # launch, whereas a silently missing row is not.
    emit_nfcore_artifacts(
        tmp_path, pipeline="bamtofastq",
        samplesheet_rows=[{"sample": "A.ALN-230303ESS-1-PUB"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"A.ALN-230303ESS-1-PUB": ARCHIVE_ONLY_META},
    )
    _header, rows = _read_sheet(tmp_path)
    assert len(rows) == 1
    assert rows[0]["sample_id"] == "A.ALN-230303ESS-1-PUB"
    assert rows[0]["mapped"] == ""
    assert rows[0]["file_type"] == ""


def test_an_agent_supplied_path_is_not_overwritten_by_a_resolver_miss(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="bamtofastq",
        samplesheet_rows=[{"sample": "A.ALN-1-PUB", "mapped": "/net/x/hand.bam", "file_type": "bam"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"A.ALN-1-PUB": ARCHIVE_ONLY_META},
    )
    _header, rows = _read_sheet(tmp_path)
    assert rows[0]["mapped"] == "/net/x/hand.bam"
    assert rows[0]["file_type"] == "bam"


def test_fastq_columns_are_stripped_even_if_the_agent_supplies_them(tmp_path):
    emit_nfcore_artifacts(
        tmp_path, pipeline="bamtofastq",
        samplesheet_rows=[{"sample": "A.ALN-1-PUB", "fastq_1": "/net/x/r1.fastq.gz"}],
        resolutions=[], launch_plan=None, tower_env=None,
        accession_metadata={"A.ALN-1-PUB": LOCAL_BAM_META},
    )
    header, _rows = _read_sheet(tmp_path)
    assert "fastq_1" not in header


# --- the agent has to be able to SEE that this one is different --------------


def test_catalog_prompt_line_names_the_input_sample_types():
    from chat_nextseek.seqera.catalog import catalog_for_prompt

    lines = {ln.split(":", 1)[0].lstrip("- "): ln for ln in catalog_for_prompt().splitlines()}
    assert "input samples: A.ALN" in lines["bamtofastq"]
    assert "input samples: D.SEQ" in lines["rnaseq"]
    # fetchngs resolves no lineage at all; saying "input samples: " with nothing after
    # it would read as a bug rather than as "this one takes accessions".
    assert "raw archive accessions" in lines["fetchngs"]


def _resolve(monkeypatch, pipeline_key):
    from chat_nextseek.pipeline.agent_tools import tool_resolve_samples

    raw = {"ok": True, "data": {"data": [
        {"sample_type": "MUS", "samples": [
            {"metadata": {"UID": "MUS-1-PUB"}, "children": [
                {"metadata": {"UID": "D.SEQ-1-PUB", "sample_type": "D.SEQ"}, "children": [
                    {"metadata": {"UID": "A.ALN-1-PUB", "sample_type": "A.ALN",
                                  "File_PrimaryData": "/net/x/s1.bam"}, "children": []},
                ]},
            ]},
        ]},
    ]}}
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata",
                        lambda c, u: raw)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes",
                        lambda c, m: m)

    class _Cfg:
        pass

    state: dict = {}
    out = json.loads(tool_resolve_samples(
        _Cfg(), {}, state, {"kind": "explicit_uids", "uids": ["MUS-1-PUB"]}, pipeline_key))
    return out, state


def test_resolve_walks_past_dseq_to_the_alignment_leaf(monkeypatch):
    out, state = _resolve(monkeypatch, "bamtofastq")
    assert out["leaf_count"] == 1
    assert out["leaves"][0]["uid"] == "A.ALN-1-PUB"
    assert out["leaves"][0]["sample_type"] == "A.ALN"
    # The emitter looks the metadata up by leaf UID, so the bam path must land there.
    assert state["accession_file_paths"]["A.ALN-1-PUB"]["File_PrimaryData"] == "/net/x/s1.bam"


def test_zero_leaves_explains_the_type_mismatch(monkeypatch):
    # Same bundle, but ampliseq wants D.SEQ... which IS present, so use a bundle with
    # only alignments to force the miss.
    from chat_nextseek.pipeline.agent_tools import tool_resolve_samples

    raw = {"ok": True, "data": {"data": [
        {"sample_type": "A.ALN", "samples": [
            {"metadata": {"UID": "A.ALN-1-PUB", "sample_type": "A.ALN"}, "children": []},
        ]},
    ]}}
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.fetch_reporter_metadata",
                        lambda c, u: raw)
    monkeypatch.setattr("chat_nextseek.pipeline.agent_tools.annotate_metadata_with_sampletypes",
                        lambda c, m: m)

    class _Cfg:
        pass

    out = json.loads(tool_resolve_samples(
        _Cfg(), {}, {}, {"kind": "explicit_uids", "uids": ["A.ALN-1-PUB"]}, "rnaseq"))
    assert out["leaf_count"] == 0
    assert out["accepted_leaf_sample_types"] == ["D.SEQ"]
    assert "D.SEQ" in out["no_leaf_hint"]


def test_no_hint_when_leaves_were_found(monkeypatch):
    out, _state = _resolve(monkeypatch, "bamtofastq")
    assert "no_leaf_hint" not in out


# --- the fetchngs pre-stage must not fire ------------------------------------


@pytest.mark.parametrize("pipeline_key,should_fetch", [("bamtofastq", False), ("rnaseq", True)])
def test_fetch_pre_stage_is_gated_on_the_input_kind(tmp_path, monkeypatch, pipeline_key, should_fetch):
    """A bam sheet has no fastq_1 column, so needs_fetch_accessions() sees every row as
    'blank fastq with an accession' and would download reads bamtofastq cannot consume.
    The gate is the catalog's samplesheet_input_kind, not the sheet's contents."""
    from chat_nextseek.luria import submitter as sub

    sheet = tmp_path / "samplesheet.csv"
    # Deliberately fastq-shaped-and-blank so the heuristic WOULD fire if consulted.
    sheet.write_text("sample,fastq_1,accession\nS1,,SRR22993650\n", encoding="utf-8")
    assert sub._sheet_needs_fetch(str(sheet)) is True

    captured: dict = {}

    def _fake_render(**kwargs):
        captured.update(kwargs)
        return "#!/bin/bash\n"

    monkeypatch.setattr(sub, "render_run_script", _fake_render)
    monkeypatch.setattr(sub, "ssh_run", lambda *a, **k: "Submitted batch job 1")
    monkeypatch.setattr(sub, "scp_file", lambda *a, **k: None)

    sub._submit_one(
        {"name": "r", "pipeline": f"nf-core/{pipeline_key}", "revision": "1.0.0"},
        0, tmp_path, "/work", {}, None, "job", None,
        samplesheet_local=str(sheet), genome="GRCh38",
    )
    assert captured["needs_fetch"] is should_fetch
