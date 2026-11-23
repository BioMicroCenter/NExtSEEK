"""Non-read inputs: a catalog entry declares its input kind, and the emitter, write_samplesheet
and the Luria submitter follow it (assembly FASTA, protein FASTA, expression matrix, one
combined FASTA with no samplesheet, and a TSV samplesheet).

The pipelines here are toy catalog entries, so these tests say nothing about any real
pipeline's schema; the per-pipeline entries carry their own tests."""
import csv
import json
from pathlib import Path

import pytest
import yaml

import chat_nextseek.luria.submitter as sub
from chat_nextseek.seqera import emitter
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.seqera.input_kinds import (
    INPUT_KINDS, check_rows, file_from_meta, input_extensions, input_kind, read_samplesheet,
    run_input, samplesheet_filename, validate_input_arg,
)


def _toy(**over):
    entry = {
        "repo": "https://github.com/nf-core/toy", "default_revision": "1.0.0",
        "description": "toy", "common_assays": [], "default_genome": None,
        "reference_cli_flags": [], "required_columns": ["sample"],
        "samplesheet_input_kind": "fasta", "accepted_leaf_sample_types": ["A.TOY"],
        "accepted_assay_patterns": [], "pipeline_kind_description": "toy",
    }
    entry.update(over)
    return entry


@pytest.fixture
def toy(monkeypatch):
    def install(key="toy", **over):
        monkeypatch.setitem(NFCORE_PIPELINE_CATALOG, key, _toy(**over))
        return key
    return install


# --- the registry ---------------------------------------------------------------------------

def test_an_entry_without_a_kind_is_a_fastq_pipeline():
    assert input_kind({}).name == "fastq"


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="unknown samplesheet_input_kind"):
        input_kind({"samplesheet_input_kind": "telepathy"})


def test_only_fastq_rows_may_be_downloaded():
    assert {k for k, v in INPUT_KINDS.items() if v.fetchable} == {"fastq"}


def test_an_entry_narrows_its_kinds_extensions():
    entry = {"samplesheet_input_kind": "protein_fasta", "input_extensions": [".fasta", ".faa"]}
    assert input_extensions(entry) == (".fasta", ".faa")
    assert ".fasta.gz" in input_extensions({"samplesheet_input_kind": "protein_fasta"})


def test_the_sheet_name_follows_the_format():
    assert samplesheet_filename({}) == "samplesheet.csv"
    assert samplesheet_filename({"samplesheet_format": "tsv"}) == "samplesheet.tsv"
    with pytest.raises(ValueError):
        samplesheet_filename({"samplesheet_format": "xlsx"})


def test_file_from_meta_reads_values_not_field_names_and_prefers_a_local_path():
    meta = {"Link_PrimaryData": "https://x.org/a/asm.fna.gz",
            "Checksum_PrimaryData": "/net/bmc/asm.fna.gz;abc",
            "Whatever": "/net/bmc/lab/asm.fna.gz",
            "File_PrimaryData": "asm.fna.gz"}
    assert file_from_meta(meta, (".fna.gz",)) == "/net/bmc/lab/asm.fna.gz"
    assert file_from_meta({"File_PrimaryData": "asm.fna.gz"}, (".fna.gz",)) == ""


@pytest.mark.parametrize("bad", ["-rf", "a b", "a;b", "../x.fa", "/net/../etc/x.fa", "$(x)", ""])
def test_the_input_argument_is_shell_safe(bad):
    with pytest.raises(ValueError):
        validate_input_arg(bad)


# --- the emitter ---------------------------------------------------------------------------

def _sheet(path):
    return read_samplesheet(path)


def test_a_fasta_row_gets_its_path_and_no_read_columns(tmp_path, toy):
    key = toy()
    meta = {"A.TOY-1": {"Link_PrimaryData": "/net/bmc/lab/s1.contigs.fa.gz"}}
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline=key, samplesheet_rows=[{"sample": "A.TOY-1", "accession": "A.TOY-1"}],
        resolutions=[], accession_metadata=meta, launch_plan=None, tower_env={})
    rows = _sheet(res.saved_files["samplesheet"])
    assert rows[0]["fasta"] == "/net/bmc/lab/s1.contigs.fa.gz"
    assert "fastq_1" not in rows[0] and "fastq_2" not in rows[0]
    assert res.missing_input_files == []
    cohort = json.loads((tmp_path / "cohort.json").read_text())
    assert cohort[0]["d_seq_uid"] == "A.TOY-1" and cohort[0]["nfcore_sample"] == "A.TOY-1"


def test_a_file_kind_row_with_no_usable_path_is_reported_not_guessed(tmp_path, toy):
    key = toy()
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline=key, samplesheet_rows=[{"sample": "A.TOY-2"}], resolutions=[],
        accession_metadata={"A.TOY-2": {"File_PrimaryData": "s2.fa.gz"}}, launch_plan=None,
        tower_env={})
    assert res.missing_input_files == ["A.TOY-2"]


def test_an_entry_extension_list_keeps_a_gzipped_file_out(tmp_path, toy):
    key = toy(samplesheet_input_kind="protein_fasta", input_extensions=[".fasta", ".fa", ".faa"])
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline=key, samplesheet_rows=[{"sample": "A.TOY-3"}], resolutions=[],
        accession_metadata={"A.TOY-3": {"Link_PrimaryData": "/net/bmc/p.faa.gz"}},
        launch_plan=None, tower_env={})
    assert res.missing_input_files == ["A.TOY-3"]


def test_the_path_column_is_renamed_by_the_pipeline_alias(tmp_path, toy, monkeypatch):
    key = toy(samplesheet_input_kind="matrix", required_columns=["sample", "filtered"])
    monkeypatch.setitem(emitter.PIPELINE_COLUMN_ALIASES, key, {"matrix": "filtered"})
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline=key, samplesheet_rows=[{"sample": "A.TOY-4"}], resolutions=[],
        accession_metadata={"A.TOY-4": {"Link_PrimaryData": "/net/bmc/x/filtered.h5ad"}},
        launch_plan=None, tower_env={})
    rows = _sheet(res.saved_files["samplesheet"])
    assert rows[0]["filtered"] == "/net/bmc/x/filtered.h5ad" and "matrix" not in rows[0]


def test_a_tsv_entry_writes_a_tab_separated_samplesheet_tsv(tmp_path, toy):
    key = toy(samplesheet_input_kind="fastq", samplesheet_format="tsv",
              required_columns=["sample", "fastq_1"])
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline=key, samplesheet_rows=[{"sample": "D.SEQ-1"}], resolutions=[],
        accession_metadata={"D.SEQ-1": {"Link_PrimaryData": "/net/bmc/r_R1.fastq.gz"}},
        launch_plan=None, tower_env={})
    path = Path(res.saved_files["samplesheet"])
    assert path.name == "samplesheet.tsv"
    assert path.read_text().splitlines()[0].split("\t")[:2] == ["sample", "fastq_1"]
    assert (tmp_path / "cohort.json").exists()


def test_fastq_pipelines_still_write_samplesheet_csv(tmp_path):
    res = emitter.emit_nfcore_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_rows=[{"sample": "D.SEQ-1"}], resolutions=[],
        accession_metadata={}, launch_plan=None, tower_env={})
    assert Path(res.saved_files["samplesheet"]).name == "samplesheet.csv"


# --- write_samplesheet ----------------------------------------------------------------------

def test_the_single_file_kind_takes_exactly_one_row():
    entry = {"samplesheet_input_kind": "combined_fasta"}
    assert check_rows(entry, [{"sample": "a"}]) == []
    assert "exactly one" in check_rows(entry, [{"sample": "a"}, {"sample": "b"}])[0]
    assert check_rows({"samplesheet_input_kind": "fasta"}, [{}, {}]) == []


def _write_tool(tmp_path, key, rows, uids):
    from chat_nextseek.pipeline.agent_tools import tool_write_samplesheet

    class _Cfg:
        pass
    state = {"resolved": {"uids": uids, "accessions": []},
             "accession_file_paths": {"A.TOY-1": {"Link_PrimaryData": "/net/bmc/g.fa.gz"},
                                      "A.TOY-2": {"File_PrimaryData": "g2.fa.gz"}}}
    out = json.loads(tool_write_samplesheet(_Cfg(), state, {
        "pipeline_key": key, "cohorts": [{"label": "c", "rows": rows}]}, str(tmp_path)))
    return out, state


def test_write_samplesheet_refuses_two_samples_for_the_single_file_kind(tmp_path, toy):
    key = toy(samplesheet_input_kind="combined_fasta")
    out, _ = _write_tool(tmp_path, key, [{"sample": "A.TOY-1"}, {"sample": "A.TOY-2"}],
                         ["A.TOY-1", "A.TOY-2"])
    assert out["ok"] is False and "exactly one" in out["errors"][0]


def test_write_samplesheet_refuses_a_file_kind_row_with_no_path(tmp_path, toy):
    key = toy()
    out, state = _write_tool(tmp_path, key, [{"sample": "A.TOY-1"}, {"sample": "A.TOY-2"}],
                             ["A.TOY-1", "A.TOY-2"])
    assert out["ok"] is False
    assert any("A.TOY-2" in e for e in out["errors"])
    assert not (state.get("artifacts") or {}).get("samplesheet")


# --- the submitter ---------------------------------------------------------------------------

LE = {"user": "u", "host": "luria.mit.edu", "key": "/k", "working_path": "/work"}


def _write_csv(path, rows, delimiter=","):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=delimiter, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _launch(tmp_path, key, revision="1.0.0"):
    path = tmp_path / "launch.yml"
    path.write_text(yaml.safe_dump({"launch": [{"name": "r", "pipeline": f"nf-core/{key}",
                                                "revision": revision}]}))
    return path


def _capture(monkeypatch):
    seen = {"scp": []}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/key")
    monkeypatch.setattr(sub, "ssh_run", lambda env, cmd, **kw: "Submitted batch job 9")
    monkeypatch.setattr(sub, "scp_file",
                        lambda env, src, dst, **kw: seen["scp"].append((str(src), dst)))
    real = sub.render_run_script
    monkeypatch.setattr(sub, "render_run_script",
                        lambda **kw: seen.update(kw) or real(**kw))
    return seen


def test_a_fasta_sheet_never_starts_the_download_even_with_an_accession(tmp_path, toy, monkeypatch):
    key = toy()
    sheet = tmp_path / "samplesheet.csv"
    _write_csv(sheet, [{"sample": "A.TOY-1", "accession": "SRR1", "fasta": "/net/bmc/g.fa.gz"}])
    seen = _capture(monkeypatch)
    runs = sub.submit_luria(_launch(tmp_path, key), luria_env=LE, samplesheet_local=str(sheet))
    assert runs and seen["needs_fetch"] is False
    assert not any(dst.endswith("fetchngs_helpers.py") for _, dst in seen["scp"])
    assert seen["input_arg"] == "samplesheet.csv"


def test_the_single_file_kind_passes_its_one_path_as_input(tmp_path, toy, monkeypatch):
    key = toy(samplesheet_input_kind="combined_fasta")
    sheet = tmp_path / "samplesheet.csv"
    _write_csv(sheet, [{"sample": "A.TOY-1", "fasta": "/net/bmc/lab/all_genomes.fa.gz"}])
    seen = _capture(monkeypatch)
    runs = sub.submit_luria(_launch(tmp_path, key), luria_env=LE, samplesheet_local=str(sheet))
    assert runs
    assert seen["input_arg"] == "/net/bmc/lab/all_genomes.fa.gz"


def test_the_single_file_kind_refuses_a_sheet_with_no_path(tmp_path, toy, monkeypatch):
    key = toy(samplesheet_input_kind="combined_fasta")
    sheet = tmp_path / "samplesheet.csv"
    _write_csv(sheet, [{"sample": "A.TOY-1", "fasta": ""}])
    _capture(monkeypatch)
    reasons = []
    assert sub.submit_luria(_launch(tmp_path, key), luria_env=LE, samplesheet_local=str(sheet),
                            reasons=reasons) == []
    assert any("fasta" in r for r in reasons)


def test_a_tsv_sheet_is_staged_as_samplesheet_tsv_and_named_as_input(tmp_path, toy, monkeypatch):
    key = toy(samplesheet_input_kind="fastq", samplesheet_format="tsv")
    sheet = tmp_path / "samplesheet.tsv"
    _write_csv(sheet, [{"sample": "D.SEQ-1", "fastq_1": "/net/bmc/r_R1.fastq.gz"}], delimiter="\t")
    seen = _capture(monkeypatch)
    assert sub.submit_luria(_launch(tmp_path, key), luria_env=LE, samplesheet_local=str(sheet))
    assert any(s == str(sheet) and d == seen["run_dir"] + "/samplesheet.tsv" for s, d in seen["scp"])
    assert seen["input_arg"] == "samplesheet.tsv"


def test_a_tsv_sheet_that_needs_a_download_is_refused(tmp_path, toy, monkeypatch):
    """The cluster-side fill edits samplesheet.csv only."""
    key = toy(samplesheet_input_kind="fastq", samplesheet_format="tsv")
    sheet = tmp_path / "samplesheet.tsv"
    _write_csv(sheet, [{"sample": "D.SEQ-1", "accession": "SRR5", "fastq_1": ""}], delimiter="\t")
    _capture(monkeypatch)
    reasons = []
    assert sub.submit_luria(_launch(tmp_path, key), luria_env=LE, samplesheet_local=str(sheet),
                            reasons=reasons) == []
    assert any("download" in r for r in reasons)


def test_run_input_for_a_samplesheet_kind_is_the_sheet_name(tmp_path):
    assert run_input({"samplesheet_input_kind": "fasta"}, tmp_path / "x.csv") == "samplesheet.csv"


def test_a_renamed_read_column_with_a_local_path_needs_no_download(tmp_path, toy, monkeypatch):
    """Rows carry their accession beside the read path. With fastq_1 renamed (eager's R1),
    the fetch check must read the renamed column, or every such row looks blank."""
    key = toy(samplesheet_input_kind="fastq", samplesheet_format="tsv")
    monkeypatch.setitem(emitter.PIPELINE_COLUMN_ALIASES, key, {"fastq_1": "R1", "fastq_2": "R2"})
    sheet = tmp_path / "samplesheet.tsv"
    _write_csv(sheet, [{"sample": "D.SEQ-1", "accession": "SRR5", "R1": "/net/bmc/r_R1.fastq.gz",
                        "R2": ""}], delimiter="\t")
    seen = _capture(monkeypatch)
    assert sub.submit_luria(_launch(tmp_path, key), luria_env=LE, samplesheet_local=str(sheet))
    assert seen["needs_fetch"] is False
