"""granular._build_upload_xlsx (Task 7): manifest_id, not values.

CC sends only ``manifest_id`` + ``mode``; the server loads its own copy of the
manifest (``store.load_manifest``), maps it (``mapper.apply``), and derives
every row itself -- a measured value never round-trips through the model.
Legacy ``rows``-based calls (the pre-Task-7 shape) keep working unchanged.

Openpyxl is used directly (not ``parse_traditional_file``) to read the
Provenance sheet: the traditional parser deliberately never reads it (see
``NessieAI/ns/CLAUDE.md``'s "An optional fifth Provenance sheet is inert to
the parser").
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

import openpyxl
import pytest

import NessieAI.ns.granular as g
from NessieAI.ns.reingest import manifest as manifest_mod
from NessieAI.ns.reingest import store as store_mod

pytestmark = pytest.mark.django_db


def _meta(row) -> dict:
    """A parsed InputRowModel's json_metadata is a JSON STRING
    (nextseek_api/batch_upload/models.py), not a dict -- decode it."""
    import json
    return json.loads(row.json_metadata)


def _dispatch(op: str, args: dict, config=None, session=None, write_gate=None,
              neo4j_exec=None, outputs_dir=None):
    """The brief's sketch calls a `granular.dispatch(...)` that does not exist
    (the real entry point is `run_op`, keyword-only past `args`; see
    `NessieAI/tests/ns/reingest/test_run_harvest_op.py`'s identical note).
    This wrapper keeps the brief's positional call shape at the test call
    sites below while calling the real function correctly."""
    return g.run_op(op, args, config=config, session=session, write_gate=write_gate,
                     neo4j_exec=neo4j_exec, outputs_dir=outputs_dir)


# A D.SEQ catalog row that declares MappedPercent (and Genome, which the
# rnaseq map's qc_attributes ALSO backfills unconditionally from
# $params.genome, present or not) as real attributes, but NOT rRNAPercent --
# so a manifest carrying all three metrics exercises both branches of the
# attribute_exists parking decision in one run, with Genome kept boring
# (always resolvable, always declared) so it never contaminates a test's
# parked-attribute count. Shape mirrors
# NessieAI/tests/ns/reingest/test_proposals.py's SAMPLE_TYPE_ROW fixture (the
# committed pattern for exercising the real context_catalog loader without a
# seeded database).
_D_SEQ_ROW = {
    "sample_type": "D.SEQ", "sampletype_id": 1, "name": "Sequencing Data",
    "description": "Raw sequencing reads.", "clade": "Raw", "tags": "",
    # required_metadata VERBATIM from the committed seed (startup/seed/dmac.sql.gz,
    # table sample_types_context, the D.SEQ row) -- un-blanked for the same
    # reason A.ALN/A.GEX below are: it is harmless today (update mode only
    # checks present-but-blank, never absence) but leaving it as the bare
    # "UID" this fixture used to declare hides that from the next reader
    # (Minor 7, 2026-09-17 review).
    "required_metadata": "UID, File_PrimaryData, Link_PrimaryData, "
                          "Checksum_PrimaryData, Scientist, Parent",
    "standard_metadata": "MappedPercent, Genome",
    "possible_metadata_fields": "",
    "parent_sampletypes": "", "child_sampletypes": "",
    "associated_assay_parents": "", "associated_assay_children": "",
}

# Catalog rows for the two analysis-children sample types the rnaseq map's
# mode=new output rules always produce (see
# NessieAI/ns/reingest_maps/rnaseq.outputs.json), with required_metadata /
# standard_metadata / possible_metadata_fields taken VERBATIM from the
# committed seed (startup/seed/dmac.sql.gz, table sample_types_context,
# rows 43 and 66) -- not blanked. An empty required_metadata here used to
# remove the exact gate production applies (the 2026-09-17 whole-branch
# review's finding: these tests passed only because their fixtures deleted
# the thing under test), so a HARD_REJECT this fixture would have hidden is
# now something these tests can actually hit. See
# test_reingest_qa_server_required.py::_real_attrs_for /
# test_build_upload_xlsx_op.py's own `_real_attrs_for` for the matching real
# SEEK `sample_attributes.required` set this file's `_patch_seek_required`
# below mirrors for these two types (UID is the only server-required title
# on A.ALN/A.GEX; the rest -- including Scientist -- are catalog-required
# only, so their absence SOFT-flags rather than blocking the workbook).
_A_ALN_ROW = {
    "sample_type": "A.ALN", "sampletype_id": 2, "name": "Alignment",
    "description": "Aligned reads.", "clade": "Analysis", "tags": "",
    "required_metadata": "UID, File_PrimaryData, Link_PrimaryData, Scientist, "
                          "Parent, Checksum_PrimaryData",
    "standard_metadata": "Protocol, Software, DataType, Type, Aligner, Genome",
    "possible_metadata_fields": "Name, SampleCreationDate, File_SecondaryData, "
                                 "Link_SecondaryData, Checksum_SecondaryData, Publish_uri, "
                                 "Checksum_PrimaryType, Checksum_SecondaryType",
    "parent_sampletypes": "D.SEQ", "child_sampletypes": "",
    "associated_assay_parents": "Genome Alignment", "associated_assay_children": "",
}
_A_GEX_ROW = {
    "sample_type": "A.GEX", "sampletype_id": 3, "name": "Gene Expression",
    "description": "Gene expression matrix.", "clade": "Analysis", "tags": "",
    "required_metadata": "UID, File_PrimaryData, Link_PrimaryData, Scientist, "
                          "Parent, Checksum_PrimaryData",
    "standard_metadata": "Protocol, Pipeline, PipelineVersion, Link_QualityControl, "
                          "ReferenceGenome, Link_ReferenceGenome, Aligner, Software, Accession",
    "possible_metadata_fields": "SampleCreationDate, Checksum_PrimaryType, Name, Notes, "
                                 "AnnotationGTF, Link_GTF, Pseudo_Aligner, Metadata, "
                                 "MetadataDataType, Link_Metadata, Matrix, MatrixDataType, "
                                 "Link_Matrix, Publish_uri, DataType, Lab, DESeqFile, "
                                 "DESeqFile_Link, DemultiplexingTool, Repository",
    "parent_sampletypes": "D.SEQ", "child_sampletypes": "",
    "associated_assay_parents": "Gene Expression Analysis", "associated_assay_children": "",
}


def _patch_seek_required(monkeypatch):
    """Mirror the real seek_production seed's `sample_attributes.required`
    flags for A.ALN / A.GEX (verified against startup/seed/seek_production.sql.gz
    in test_build_upload_xlsx_op.py's own `_real_attrs_for`): UID is the only
    server-required title on either type; File_PrimaryData, Link_PrimaryData,
    Scientist, Parent and Checksum_PrimaryData are all required=0.

    Patched onto `_seek_required_map`, not `attributes_for` itself, so
    `attributes_for`'s real required/others merge against the
    `_sample_type_rows`-mocked catalog above still runs for real -- only the
    SEEK side, which this module's bare django_db has no sample_attributes
    rows loaded for, is supplied. Without this, `attributes_for`'s
    fail-safe-STRICTER fallback (an unreachable/empty SEEK table treats
    every catalog-required title as server-required too) would make
    `Scientist` a HARD blocker here, which the real system does not: a
    fallback answer that is stricter than the production system is a false
    positive for these tests specifically, not a safety margin worth
    preserving in a test that exists to exercise the REAL gate. Returns `{}`
    for any other sample_type, i.e. the same untouched fallback behaviour
    every other test in this file already relies on for D.SEQ.
    """
    monkeypatch.setattr(
        "nextseek_api.services.reingest_lookups._seek_required_map",
        lambda st: {"UID": True, "File_PrimaryData": False, "Link_PrimaryData": False,
                    "Scientist": False, "Parent": False, "Checksum_PrimaryData": False}
                   if st in ("A.ALN", "A.GEX") else {})


# Default per-sample output inventory a real `run-harvest` would have found
# for a `star_salmon`-aligned run -- matching rnaseq.outputs.json's own
# globs -- so a caller that does not care about outputs still gets an
# honestly-shaped manifest: `File_PrimaryData` is now filled from THIS
# inventory (mapper._attach_checksum), and the real A.ALN/A.GEX
# required_metadata declares File_PrimaryData/Link_PrimaryData as an
# ALTERNATIVE_REQUIRED_GROUPS pair (see reingest_qa.py), so a manifest with
# no outputs at all would HARD_REJECT on the very gate this fixture switch
# is meant to exercise honestly -- and no real harvested manifest is ever
# actually shaped that way (run-harvest always populates `outputs`; only
# `checksums` is optional, filled in later by run-checksum). One BAM per
# sample (A.ALN, per_sample) plus one shared gene-counts matrix (A.GEX,
# per_run, sample=None matches any/every sample) is the minimum that
# satisfies both output rules.
def _default_outputs(sample_names: list[str]) -> list[manifest_mod.OutputRecord]:
    return [
        manifest_mod.OutputRecord(
            path=f"star_salmon/{name}.markdup.sorted.bam", bytes=123, sample=name)
        for name in sample_names
    ] + [manifest_mod.OutputRecord(
        path="star_salmon/all.merged.gene_counts.tsv", bytes=456, sample=None)]


def _save_manifest(tmp_path, monkeypatch, *, metrics=None, outputs=None, checksums=None):
    # store._ROOT is resolved once at module-import time from an env var; a
    # module already imported by an earlier test would ignore a later
    # monkeypatch.setenv, so patch the module attribute directly instead
    # (same pattern as test_run_harvest_op.py's _patch_harvest).
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))
    _patch_seek_required(monkeypatch)
    run_manifest = manifest_mod.RunManifest(
        run_dir="/net/cluster/runs/r1",
        pipeline=manifest_mod.PipelineInfo(
            name="nf-core/rnaseq", version="3.18.0", run_name="test_run_1"),
        params={"genome": "GRCh38", "aligner": "star_salmon"},
        samples=[manifest_mod.SampleRecord(
            nfcore_sample="SAMPLE_1", d_seq_uid="D.SEQ-EXAMPLE-1",
            uid_resolution=manifest_mod.RESOLUTION_LAUNCH_RECORD,
            # Explicit, not the field's own "" default: real harvest always
            # assigns this (a batched DB lookup keyed on d_seq_uid -- see
            # harvest.py's `sample_type_lookup` wiring) whenever d_seq_uid is
            # set, and mapper.apply now skips the whole D.SEQ backfill row
            # when it is empty (see NessieAI/ns/CLAUDE.md / manifest.py's
            # SampleRecord.parent_sample_type docstring). These tests are
            # about that very backfill row, so the fixture must say what
            # harvest would have found, not fall back to "unknown".
            parent_sample_type="D.SEQ",
            metrics=metrics or {})],
        outputs=outputs if outputs is not None else _default_outputs(["SAMPLE_1"]),
        checksums=checksums or {},
        sources={"metrics": "multiqc/star_salmon/multiqc_data/multiqc_general_stats.txt",
                 "params": "params.json"},
    )
    return store_mod.save_manifest(run_manifest)


def _save_manifest_multi(tmp_path, monkeypatch, *, n=3, metrics=None, outputs=None):
    """Same shape as `_save_manifest`, but with `n` distinct samples -- the
    only way to tell "iterate result.rows" (correct, one row per sample)
    apart from "iterate output rules" (the brief's buggy sketch, one row per
    rule, carrying only the last-seen sample's values): with a single sample
    (every other manifest in this file) both loops produce identical output.
    See test_mode_new_fans_out_one_row_per_sample / test_mode_update_fans_out
    below, and the mutation-testing evidence in task-7-report.md.

    Also carries `_default_outputs`' per-sample BAM + shared gene-counts
    matrix by default, for the same reason `_save_manifest` does -- see that
    fixture's comment. `outputs` overrides that default (mirroring
    `_save_manifest`'s own kwarg) for a caller that needs a specific
    inventory shape, e.g. an ambiguous per_run rule shared by every sample."""
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))
    _patch_seek_required(monkeypatch)
    sample_names = [f"SAMPLE_{i}" for i in range(1, n + 1)]
    samples = [
        manifest_mod.SampleRecord(
            nfcore_sample=name, d_seq_uid=f"D.SEQ-EXAMPLE-{i}",
            uid_resolution=manifest_mod.RESOLUTION_LAUNCH_RECORD,
            # See _save_manifest's identical field for why this is explicit
            # rather than left at the model's own "" default.
            parent_sample_type="D.SEQ",
            metrics=metrics or {})
        for i, name in enumerate(sample_names, start=1)
    ]
    run_manifest = manifest_mod.RunManifest(
        run_dir="/net/cluster/runs/r1",
        pipeline=manifest_mod.PipelineInfo(
            name="nf-core/rnaseq", version="3.18.0", run_name="test_run_multi"),
        params={"genome": "GRCh38", "aligner": "star_salmon"},
        samples=samples,
        outputs=outputs if outputs is not None else _default_outputs(sample_names),
        sources={"metrics": "multiqc/star_salmon/multiqc_data/multiqc_general_stats.txt",
                 "params": "params.json"},
    )
    return store_mod.save_manifest(run_manifest)


# Fields these two fixture helpers deliberately leave at SampleRecord's own
# default -- the manifest shapes this file exercises (build-upload-xlsx
# rendering) genuinely do not need them explicit. Every OTHER field of
# SampleRecord must appear as a literal kwarg in both `_save_manifest` and
# `_save_manifest_multi` above, not fall back to a default silently.
_DEFAULTED_SAMPLE_RECORD_FIELDS = {
    "fastq_1", "fastq_2", "d_seq_uid_multirun",
    "strandedness_declared", "strandedness_inferred", "derived",
}
# Fields both helpers above DO set explicitly, right now.
_EXPLICIT_SAMPLE_RECORD_FIELDS = {
    "nfcore_sample", "d_seq_uid", "uid_resolution", "parent_sample_type", "metrics",
}


def test_fixture_sample_records_account_for_every_samplerecord_field():
    """Closes the gap that let six tests go red silently at the merge that
    added `parent_sample_type`: `_save_manifest` / `_save_manifest_multi`
    hand-build `SampleRecord` instead of running a real harvest, so a new
    field silently takes its pydantic default instead of failing loudly --
    exactly what happened here (parent_sample_type stayed "" and
    mapper.apply correctly, but silently to this file's fixtures, skipped
    the D.SEQ backfill row).

    This does not try to know which fields production harvest "guarantees"
    (that would need running harvest itself, or duplicating its logic) --
    it just refuses to let a new SampleRecord field pass by unmentioned.
    When SampleRecord gains one, this fails until someone puts it in
    exactly one place: `_EXPLICIT_SAMPLE_RECORD_FIELDS` (and actually adds
    it to both constructor calls above) if these tests need a real value
    for it, or `_DEFAULTED_SAMPLE_RECORD_FIELDS` if leaving it at default
    is genuinely fine here -- a conscious choice instead of a silent
    multi-test regression."""
    accounted = _EXPLICIT_SAMPLE_RECORD_FIELDS | _DEFAULTED_SAMPLE_RECORD_FIELDS
    all_fields = set(manifest_mod.SampleRecord.model_fields)
    missing = all_fields - accounted
    assert not missing, (
        f"SampleRecord gained field(s) {sorted(missing)} that this file's "
        "_save_manifest/_save_manifest_multi fixtures do not account for -- "
        "add each to _EXPLICIT_SAMPLE_RECORD_FIELDS (and the two "
        "SampleRecord(...) call sites above) or to "
        "_DEFAULTED_SAMPLE_RECORD_FIELDS, whichever is correct.")
    extra = accounted - all_fields
    assert not extra, (
        f"Stale field name(s) {sorted(extra)} in this test's bookkeeping "
        "sets -- SampleRecord no longer declares them.")


# ---------------------------------------------------------------------------
# Step 1 (brief): validation
# ---------------------------------------------------------------------------

def test_a_bad_manifest_id_is_rejected_without_touching_disk():
    with pytest.raises(g.OpValidationError):
        _dispatch("build-upload-xlsx", {"manifest_id": "../../etc/passwd", "mode": "update"})


def test_an_unknown_mode_is_rejected():
    with pytest.raises(g.OpValidationError):
        _dispatch("build-upload-xlsx", {"manifest_id": "abc123", "mode": "sideways"})


def test_neither_manifest_id_nor_rows_is_rejected():
    with pytest.raises(g.OpValidationError):
        _dispatch("build-upload-xlsx", {})


def test_the_op_returns_proposals_rather_than_writing_them():
    """build-upload-xlsx never writes to NExtSEEK. Proposal rows are persisted by
    the service layer, which already persists the artifact bundle."""
    source = inspect.getsource(g._build_upload_xlsx)
    assert "proposals.record" not in source
    assert "proposals" in source  # returned in the envelope


def test_the_manifest_path_returns_proposals_rather_than_writing_them():
    """The dispatcher above is a thin manifest_id/rows router (see
    _build_upload_xlsx's own docstring); the real invariant belongs on the
    manifest-driven implementation, so it is checked there too."""
    source = inspect.getsource(g._build_upload_xlsx_from_manifest)
    assert "proposals.record" not in source
    assert "proposals" in source


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_the_manifest_path_never_actually_calls_proposals_record(rows, tmp_path, monkeypatch):
    """The source-grep checks above are evadable: `from
    NessieAI.ns.reingest.proposals import record` then a bare `record(...)`
    call contains neither the literal `proposals.record` nor breaks the
    `"proposals" in source` check, so a regression that started writing
    proposals directly from this op would sail past both greps. Patch the
    real target and assert it is never invoked, which cannot be evaded by
    renaming the import."""
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr(
        "nextseek_api.services.reingest_lookups.notes_for_uids",
        lambda uids: {u: "" for u in uids})
    manifest_id = _save_manifest(
        tmp_path, monkeypatch,
        metrics={"star-uniquely_mapped_percent": 91.4,
                 "custom_content_biotype_counts-percent_rRNA": 2.1})

    with patch("NessieAI.ns.reingest.proposals.record") as mock_record:
        result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update"},
                            outputs_dir=str(tmp_path))

    mock_record.assert_not_called()
    # Sanity: this run genuinely produced a needs_definition proposal (the
    # thing that would have been recorded, had this op recorded anything).
    assert any(p.get("status") == "needs_definition" for p in result["proposals"])


# ---------------------------------------------------------------------------
# mode=new: analysis children, never the D.SEQ backfill
# ---------------------------------------------------------------------------

@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_mode_new_renders_analysis_children_not_dseq(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "new"},
                        outputs_dir=str(tmp_path))

    assert set(result["saved_files"]) == {"reingest_A_ALN", "reingest_A_GEX"}
    assert result["qa"]["A.ALN"]["disposition"] in ("CLEAN", "SOFT_FLAG")
    assert result["qa"]["A.GEX"]["disposition"] in ("CLEAN", "SOFT_FLAG")
    assert "D.SEQ" not in result["qa"]
    assert result["reply"]  # rendered by report.render_qa_for_user, non-empty
    assert result["proposals"] == []  # every metric in this manifest is mapped


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_mode_new_a_dot_aln_workbook_round_trips(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "new"},
                        outputs_dir=str(tmp_path))

    from nextseek_api.batch_upload.convert import parse_traditional_file
    batch = parse_traditional_file(result["saved_files"]["reingest_A_ALN"])
    assert len(batch.rows) == 1
    assert batch.rows[0].SampleType == "A.ALN"
    assert _meta(batch.rows[0])["Parent"] == "D.SEQ-EXAMPLE-1"


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_mode_new_fans_out_one_row_per_sample_not_one_row_total(rows, tmp_path, monkeypatch):
    """Regression guard for the plan's own headline correction: the
    manifest-driven path must iterate `result.rows` (one row per sample,
    each carrying its own Parent) -- never output RULES (the brief's
    published, buggy sketch: one row per rule, carrying only the
    last-seen sample's values). Every other test in this file uses a
    single-sample manifest, where both loops produce identical output; this
    is the one test that can tell them apart. See task-7-report.md for the
    mutation check that proves it (temporarily reverting the loop to
    iterate output rules and confirming this test fails)."""
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest_multi(tmp_path, monkeypatch, n=3)
    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "new"},
                        outputs_dir=str(tmp_path))

    from nextseek_api.batch_upload.convert import parse_traditional_file
    batch = parse_traditional_file(result["saved_files"]["reingest_A_ALN"])
    assert len(batch.rows) == 3
    parents = {_meta(row)["Parent"] for row in batch.rows}
    assert parents == {"D.SEQ-EXAMPLE-1", "D.SEQ-EXAMPLE-2", "D.SEQ-EXAMPLE-3"}


# ---------------------------------------------------------------------------
# Checksum wiring reaches the rendered workbook, not just MappedRow.attributes
# ---------------------------------------------------------------------------
#
# mapper.py's own unit tests (test_mapper.py) stop at MappedRow.attributes --
# one layer short of the thing a curator actually opens. These read the
# Samples sheet of the real, saved xlsx with openpyxl (the pattern the
# Provenance-sheet tests above already use), the same file
# render_upload_workbook wrote and parse_traditional_file round-trips on
# upload, so a break anywhere between mapper.apply and the saved cell -- a
# fields-list mismatch, a stale header, a _cell() serialization bug -- would
# fail here even though it could not fail a MappedRow-level assertion.

def _cell_by_header(ws, header_name):
    """{row_index (1-based, header excluded) -> that column's value} for one
    Samples-sheet column, located by its header text rather than a hard-coded
    index -- the column position depends on json_metadata's first-seen key
    order (see render_upload_workbook), which is not this test's concern."""
    header = [c.value for c in ws[1]]
    col = header.index(header_name) + 1
    return {r: row[col - 1].value
            for r, row in enumerate(ws.iter_rows(min_row=2, values_only=False), start=2)}


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_a_checksummed_manifest_puts_checksum_primarydata_in_the_rendered_cell(
        rows, tmp_path, monkeypatch):
    """A manifest whose `outputs`/`checksums` name the SAME path each output
    rule's own `glob`/`primary_data` resolves (rnaseq.outputs.json: A.ALN's
    aligned BAM, A.GEX's merged gene-counts matrix) must produce an actual
    xlsx cell carrying that checksum -- not just a MappedRow attribute that
    render_upload_workbook could still have dropped."""
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    aln_path = "star_salmon/SAMPLE_1.markdup.sorted.bam"
    gex_path = "star_salmon/all.merged.gene_counts.tsv"
    manifest_id = _save_manifest(
        tmp_path, monkeypatch,
        outputs=[
            manifest_mod.OutputRecord(path=aln_path, bytes=123, sample="SAMPLE_1"),
            manifest_mod.OutputRecord(path=gex_path, bytes=456, sample=None),
        ],
        checksums={aln_path: "aln0checksum0abc123", gex_path: "gex0checksum0def456"})

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "new"},
                        outputs_dir=str(tmp_path))

    wb_aln = openpyxl.load_workbook(result["saved_files"]["reingest_A_ALN"])
    aln_checksums = _cell_by_header(wb_aln["Samples"], "Checksum_PrimaryData")
    assert set(aln_checksums.values()) == {"aln0checksum0abc123"}

    wb_gex = openpyxl.load_workbook(result["saved_files"]["reingest_A_GEX"])
    gex_checksums = _cell_by_header(wb_gex["Samples"], "Checksum_PrimaryData")
    assert set(gex_checksums.values()) == {"gex0checksum0def456"}


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_a_manifest_with_no_checksums_still_renders_a_workbook(rows, tmp_path, monkeypatch):
    """Negative control: checksumming is advisory, never a hard dependency
    (mapper._attach_checksum's own docstring). A manifest that never ran
    run-checksum -- `_save_manifest`'s default outputs (a harvested
    inventory, as run-harvest always produces), no `checksums` at all --
    must still render both workbooks, with `File_PrimaryData` filled from
    that inventory but no Checksum_PrimaryData column at all rather than a
    blank/error one."""
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "new"},
                        outputs_dir=str(tmp_path))

    assert set(result["saved_files"]) == {"reingest_A_ALN", "reingest_A_GEX"}
    for key in ("reingest_A_ALN", "reingest_A_GEX"):
        wb = openpyxl.load_workbook(result["saved_files"][key])
        header = [c.value for c in wb["Samples"][1]]
        assert "Checksum_PrimaryData" not in header

    # Minor 3 (2026-09-17 review): the docstring above has promised
    # "File_PrimaryData filled from that inventory" since this test was
    # written, but nothing checked the actual cell -- only that the
    # workbooks exist and Checksum_PrimaryData is absent. Assert the real
    # value, for both A.ALN and A.GEX, the same way the checksummed test
    # above does for Checksum_PrimaryData.
    wb_aln = openpyxl.load_workbook(result["saved_files"]["reingest_A_ALN"])
    aln_files = _cell_by_header(wb_aln["Samples"], "File_PrimaryData")
    assert set(aln_files.values()) == {"SAMPLE_1.markdup.sorted.bam"}

    wb_gex = openpyxl.load_workbook(result["saved_files"]["reingest_A_GEX"])
    gex_files = _cell_by_header(wb_gex["Samples"], "File_PrimaryData")
    assert set(gex_files.values()) == {"all.merged.gene_counts.tsv"}


# ---------------------------------------------------------------------------
# ambiguous_primary: the join between mapper.py (sets `candidates`) and
# granular.py (collects + dedupes + renders via report.py) -- 2026-09-17
# review, Cheap 4/Important 2.
# ---------------------------------------------------------------------------

@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_ambiguous_primary_data_reaches_the_reply_through_the_full_dispatch(
        rows, tmp_path, monkeypatch):
    """Cheap 4 (2026-09-17 review): the mapper half (test_mapper.py) and the
    report half (test_report.py) are each tested on their own, but nothing
    previously proved `granular.py` actually WIRES one to the other -- if
    that join broke (the condition that folds `attr.candidates` into
    `ambiguous_primary` stopped firing, or the kwarg into
    `render_qa_for_user` got dropped), both halves would stay green while
    the "PRIMARY-FILE PICKS TO CONFIRM" section silently vanished from the
    real, dispatched reply. Two same-basename A.GEX outputs (the shared
    per_run gene-counts matrix, once under each of two aligner directories)
    is the minimal manifest that makes `_attach_checksum` set `candidates`
    at all."""
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    gex_a = "star_salmon/all.merged.gene_counts.tsv"
    gex_b = "salmon/all.merged.gene_counts.tsv"
    manifest_id = _save_manifest(
        tmp_path, monkeypatch,
        outputs=[
            manifest_mod.OutputRecord(path="star_salmon/SAMPLE_1.markdup.sorted.bam",
                                      bytes=123, sample="SAMPLE_1"),
            manifest_mod.OutputRecord(path=gex_a, bytes=456, sample=None),
            manifest_mod.OutputRecord(path=gex_b, bytes=456, sample=None),
        ])

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "new"},
                        outputs_dir=str(tmp_path))

    assert "PRIMARY-FILE PICK" in result["reply"]
    assert gex_a in result["reply"]
    assert gex_b in result["reply"]
    # The envelope shape (Global Constraints: build-upload-xlsx never writes
    # to NExtSEEK) must stay exactly these four keys -- `ambiguous_primary`
    # is a local that feeds the reply text, never a key of its own.
    assert set(result) == {"saved_files", "qa", "reply", "proposals"}


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_ambiguous_primary_dedupes_the_same_pick_across_many_samples(
        rows, tmp_path, monkeypatch):
    """Important 2 (2026-09-17 review): the pre-fix collection appended one
    `ambiguous_primary` entry per ROW, so a per-sample rule whose ambiguity
    is the SAME two candidates on every sample (e.g. every sample published
    under both `star_salmon/` and `hisat2/` with no per-sample-distinguishing
    filename) rendered one near-identical block per sample -- ~20 entries
    for one genuine ambiguity on a 20-sample run, exactly the enumerate-
    instead-of-count failure report.py's module docstring (rule 1) exists to
    prevent. Three samples, each with the identical two-candidate BAM pick,
    must fold into ONE entry that says it affects all three -- never three."""
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    sample_names = ["SAMPLE_1", "SAMPLE_2", "SAMPLE_3"]
    outputs = []
    for name in sample_names:
        # Deliberately the SAME two literal paths for every sample -- the
        # filename itself carries no sample-distinguishing text, only the
        # OutputRecord's own `sample` field attributes it -- so the resulting
        # `candidates` tuple is byte-identical across all three rows.
        outputs.append(manifest_mod.OutputRecord(
            path="star_salmon/aligned.markdup.sorted.bam", bytes=123, sample=name))
        outputs.append(manifest_mod.OutputRecord(
            path="hisat2/aligned.markdup.sorted.bam", bytes=123, sample=name))
    outputs.append(manifest_mod.OutputRecord(
        path="star_salmon/all.merged.gene_counts.tsv", bytes=456, sample=None))
    manifest_id = _save_manifest_multi(tmp_path, monkeypatch, n=3, outputs=outputs)

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "new"},
                        outputs_dir=str(tmp_path))

    # Singular header -- ONE distinct ambiguity, not three.
    assert "ONE PRIMARY-FILE PICK TO CONFIRM" in result["reply"]
    assert "3 PRIMARY-FILE PICKS" not in result["reply"]
    assert "on 3 samples" in result["reply"]
    # Not three near-identical numbered entries.
    assert result["reply"].count("could be A.ALN's primary data") == 1


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_mode_update_fans_out_one_row_per_sample_with_distinct_uids(rows, tmp_path, monkeypatch):
    """Same regression guard as the mode=new test above, for the D.SEQ
    backfill path: three samples must produce three distinct UIDs, never one
    row (whichever sample the buggy "iterate output rules" loop happened to
    see last)."""
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr(
        "nextseek_api.services.reingest_lookups.notes_for_uids",
        lambda uids: {u: "" for u in uids})
    manifest_id = _save_manifest_multi(
        tmp_path, monkeypatch, n=3,
        metrics={"star-uniquely_mapped_percent": 91.4})

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update"},
                        outputs_dir=str(tmp_path))

    from nextseek_api.batch_upload.convert import parse_traditional_file
    batch = parse_traditional_file(result["saved_files"]["reingest_D_SEQ_update"])
    assert len(batch.rows) == 3
    uids = {row.UID for row in batch.rows}
    assert uids == {"D.SEQ-EXAMPLE-1", "D.SEQ-EXAMPLE-2", "D.SEQ-EXAMPLE-3"}
    assert all(_meta(row)["MappedPercent"] == 91.4 for row in batch.rows)


# ---------------------------------------------------------------------------
# mode=update: the D.SEQ backfill, attribute_exists parking, and Notes
# ---------------------------------------------------------------------------

@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_mode_update_renders_only_dseq_with_mapped_percent(rows, tmp_path, monkeypatch):
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr(
        "nextseek_api.services.reingest_lookups.notes_for_uids",
        lambda uids: {u: "" for u in uids})
    manifest_id = _save_manifest(
        tmp_path, monkeypatch,
        metrics={"star-uniquely_mapped_percent": 91.4})

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update"},
                        outputs_dir=str(tmp_path))

    assert set(result["saved_files"]) == {"reingest_D_SEQ_update"}
    assert "A.ALN" not in result["qa"]
    assert "A.GEX" not in result["qa"]

    from nextseek_api.batch_upload.convert import parse_traditional_file
    batch = parse_traditional_file(result["saved_files"]["reingest_D_SEQ_update"])
    assert len(batch.rows) == 1
    row = batch.rows[0]
    assert row.UID == "D.SEQ-EXAMPLE-1"
    meta = _meta(row)
    assert meta["MappedPercent"] == 91.4
    assert "Parent" not in meta  # update mode: no Parent column


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_an_attribute_not_on_the_schema_is_parked_in_notes_not_a_column(rows, tmp_path, monkeypatch):
    """rRNAPercent maps cleanly (a committed qc_attributes rule) but is not
    declared on _D_SEQ_ROW's schema -- design section 8's "attributes that do
    not exist": parked into Notes, never invented as a real column, and a
    needs_definition proposal is queued for it."""
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr(
        "nextseek_api.services.reingest_lookups.notes_for_uids",
        lambda uids: {u: "" for u in uids})
    manifest_id = _save_manifest(
        tmp_path, monkeypatch,
        metrics={"star-uniquely_mapped_percent": 91.4,
                 "custom_content_biotype_counts-percent_rRNA": 2.1})

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update"},
                        outputs_dir=str(tmp_path))

    from nextseek_api.batch_upload.convert import parse_traditional_file
    batch = parse_traditional_file(result["saved_files"]["reingest_D_SEQ_update"])
    row = batch.rows[0]
    meta = _meta(row)
    assert "rRNAPercent" not in meta
    assert meta["MappedPercent"] == 91.4
    notes = meta.get("Notes", "")
    assert "rRNAPercent=2.1" in notes
    assert "[nfcore-reingest" in notes

    needs_def = [p for p in result["proposals"] if p.get("status") == "needs_definition"]
    assert len(needs_def) == 1
    assert needs_def[0]["proposed_attribute"] == "rRNAPercent"
    assert needs_def[0]["proposed_target"] == "D.SEQ"
    assert needs_def[0]["pipeline"] == "nf-core/rnaseq"
    assert needs_def[0]["manifest_digest"] == manifest_id

    # QA must soft-flag the parked value (ATTRIBUTE_NOT_DEFINED), never come
    # back CLEAN on a workbook carrying one -- Plan 3's own "Done when" bar.
    assert result["qa"]["D.SEQ"]["disposition"] == "SOFT_FLAG"
    assert any("attribute_not_defined" in h for h in result["qa"]["D.SEQ"]["soft"])


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_a_notes_fetch_failure_drops_the_parked_value_rather_than_guessing(rows, tmp_path, monkeypatch):
    """notes_for_uids omits a UID whose fetch failed (its own documented
    contract). Parking must honour that and write no Notes at all for that
    row rather than risk clobbering text it never read."""
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr(
        "nextseek_api.services.reingest_lookups.notes_for_uids", lambda uids: {})
    manifest_id = _save_manifest(
        tmp_path, monkeypatch,
        metrics={"custom_content_biotype_counts-percent_rRNA": 2.1})

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update"},
                        outputs_dir=str(tmp_path))

    from nextseek_api.batch_upload.convert import parse_traditional_file
    batch = parse_traditional_file(result["saved_files"]["reingest_D_SEQ_update"])
    row = batch.rows[0]
    assert "Notes" not in _meta(row)
    # The proposal is still raised even though the Notes write did not
    # happen -- the schema gap is real regardless of whether this run could
    # safely record it in this sample's Notes.
    assert any(p.get("proposed_attribute") == "rRNAPercent" for p in result["proposals"])


def test_attribute_exists_outage_propagates_rather_than_defaulting_false(tmp_path, monkeypatch):
    """An entirely empty catalog is an outage signal (proposals.attribute_exists'
    own contract), not "nothing is defined". The op must let this raise, never
    catch it and default to False -- that would fabricate a schema gap and
    park a good value in Notes."""
    manifest_id = _save_manifest(
        tmp_path, monkeypatch,
        metrics={"star-uniquely_mapped_percent": 91.4})

    with patch("nextseek_api.services.context_catalog._sample_type_rows",
               side_effect=RuntimeError("sample_types_context table unreachable")):
        with pytest.raises(RuntimeError):
            _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update"},
                      outputs_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# Provenance sheet wiring (brief Step 3's third requirement)
# ---------------------------------------------------------------------------

@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_provenance_sheet_is_written_when_an_attribute_has_a_non_map_origin(rows, tmp_path, monkeypatch):
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr(
        "nextseek_api.services.reingest_lookups.notes_for_uids",
        lambda uids: {u: "" for u in uids})
    manifest_id = _save_manifest(
        tmp_path, monkeypatch,
        metrics={"star-uniquely_mapped_percent": 91.4,
                 "custom_content_biotype_counts-percent_rRNA": 2.1})

    result = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update"},
                        outputs_dir=str(tmp_path))

    wb = openpyxl.load_workbook(result["saved_files"]["reingest_D_SEQ_update"])
    assert "Provenance" in wb.sheetnames
    sheet = wb["Provenance"]
    header = [c.value for c in sheet[1]]
    assert header == ["UID", "Attribute", "Value", "Origin", "Raw key", "Source file"]
    rows_by_attr = {r[1].value: r for r in sheet.iter_rows(min_row=2)}
    assert rows_by_attr["MappedPercent"][3].value == "map"
    assert rows_by_attr["rRNAPercent"][3].value == "parked"
    assert rows_by_attr["rRNAPercent"][4].value == "custom_content_biotype_counts-percent_rRNA"


def test_no_provenance_sheet_wiring_means_no_sheet_would_appear(tmp_path, monkeypatch):
    """Negative control for the wiring check above: render_upload_workbook
    only emits Provenance when it is actually PASSED a provenance list (see
    its own docstring) -- confirms the assertion above is exercising real
    wiring, not something render_upload_workbook does unconditionally."""
    from NessieAI.ns.upload_workbook import render_upload_workbook

    out = tmp_path / "no_provenance.xlsx"
    render_upload_workbook(
        "A.ALN", [{"json_metadata": {"Parent": "D.SEQ-1", "Type": "BAM"}, "assay_ids": []}],
        str(out))
    wb = openpyxl.load_workbook(out)
    assert "Provenance" not in wb.sheetnames


# ---------------------------------------------------------------------------
# Legacy rows path stays untouched (brief: "Keep --rows working")
# ---------------------------------------------------------------------------

def test_legacy_rows_path_still_works_without_a_manifest_id(tmp_path):
    import json as _json

    rows = _json.dumps([{
        "SampleType": "A.SCXP",
        "json_metadata": {"Parent": "D.SEQ-1", "Scientist": "Marie Floryan"},
        "assay_ids": [12],
    }])
    result = _dispatch("build-upload-xlsx", {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
                        outputs_dir=str(tmp_path))
    assert set(result["saved_files"]) == {"reingest_A_SCXP"}
    assert "reply" not in result  # legacy envelope shape, unchanged
    assert "proposals" not in result
