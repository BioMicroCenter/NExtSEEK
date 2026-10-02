"""build_input_inventory: say what primary data a cohort actually points at.

The defect this exists for: a cohort queried by ANALYSIS-PRODUCT UIDs, whose
raw reads sit one lineage step up, was refused with the claim that it had no
raw FASTQs — while its own digest carried 114 FASTQ records. Per-field
statistics stated that fact only implicitly; these tests pin it down as a
count.
"""
from chat_nextseek.pipeline.sample_digest import build_input_inventory, build_sample_digest


def _record(uid, sample_type, **meta):
    return uid, {"sample_type": sample_type, "parent_uid": meta.get("Parent"),
                 "metadata": {"UID": uid, **meta}}


def _fibroblast_index():
    """The shape of the real fibroblast-subtypes cohort, in miniature: the UIDs
    asked about are 10x matrices and a spatial run, and the raw reads are their
    D.SEQ ancestors — GEX and TCR libraries, with LibraryStrategy left empty."""
    records = [
        _record("A.SCXP-1", "A.SCXP", File_PrimaryData="S1_filtered_feature_bc_matrix.h5",
                DataType="h5", Parent="A.ALN-1"),
        _record("A.SCXP-2", "A.SCXP", File_PrimaryData="S2.rds", DataType="rds",
                Parent="A.ALN-1"),
        _record("A.SPTX-1", "A.SPTX", File_PrimaryData="XETG__p1/analysis.zarr.zip",
                Parent="TIS-1"),
        _record("A.ALN-1", "A.ALN", File_PrimaryData="S1_sample_alignments.bam",
                DataType="bam", Parent="D.SEQ-1"),
        _record("D.SEQ-1", "D.SEQ",
                File_PrimaryData="GBM-001-GEX_S1_L001_R1_001.fastq.gz; "
                                 "GBM-001-GEX_S1_L001_R2_001.fastq.gz",
                DataType="FastQ", SequencingType="Single Cell RNAseq", LibraryStrategy=""),
        _record("D.SEQ-2", "D.SEQ", File_PrimaryData="GBM-001-GEX_S2_L001_R1_001.fastq.gz",
                DataType="FastQ", SequencingType="Single Cell RNAseq", LibraryStrategy=""),
        _record("D.SEQ-3", "D.SEQ", File_PrimaryData="GBM-001-TCR_S1_L001_R1_001.fastq.gz",
                DataType="FastQ", SequencingType="Single Cell TCR", LibraryStrategy=""),
        _record("TIS-1", "TIS", Organism="Homo sapiens"),
        _record("PAT-1", "PAT", Sex="F"),
    ]
    return dict(records)


# ---------------------------------------------------------------------------
# The regression this module exists for
# ---------------------------------------------------------------------------


def test_raw_reads_are_counted_even_when_every_queried_uid_is_an_analysis_product():
    inventory = build_input_inventory(
        _fibroblast_index(), ["A.SCXP-1", "A.SCXP-2", "A.SPTX-1"]
    )
    assert inventory["raw_reads"]["n_records"] == 3
    assert inventory["raw_reads"]["by_sample_type"] == {"D.SEQ": 3}


def test_asked_about_separates_the_queried_uids_from_their_lineage():
    inventory = build_input_inventory(
        _fibroblast_index(), ["A.SCXP-1", "A.SCXP-2", "A.SPTX-1"]
    )
    assert inventory["asked_about"] == {
        "n_uids": 3, "by_sample_type": {"A.SCXP": 2, "A.SPTX": 1},
    }


def test_sequencing_type_is_counted_not_just_exemplified():
    """The per-field summary shows up to three distinct values and no counts, so
    a cohort that is mostly TCR and a cohort that is mostly GEX look identical
    there. The split decides whether scrnaseq has anything to run on."""
    inventory = build_input_inventory(_fibroblast_index(), [])
    assert inventory["raw_reads"]["by_sequencing_type"] == {
        "Single Cell RNAseq": 2, "Single Cell TCR": 1,
    }


def test_empty_library_strategy_is_reported_as_not_recorded_rather_than_omitted():
    inventory = build_input_inventory(_fibroblast_index(), [])
    assert inventory["raw_reads"]["by_library_strategy"] == {"(not recorded)": 3}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_alignments_and_matrices_are_not_counted_as_raw_reads():
    inventory = build_input_inventory(_fibroblast_index(), [])
    assert inventory["aligned_reads"]["n_records"] == 1
    assert inventory["aligned_reads"]["by_sample_type"] == {"A.ALN": 1}
    assert inventory["derived_products"]["by_sample_type"] == {"A.SCXP": 2, "A.SPTX": 1}


def test_compression_suffixes_do_not_hide_the_real_extension():
    """`.fastq.gz` must read as reads and `.zarr.zip` must not."""
    index = dict([
        _record("D.SEQ-1", "D.SEQ", File_PrimaryData="reads_R1.fastq.gz"),
        _record("A.SPTX-1", "A.SPTX", File_PrimaryData="analysis.zarr.zip"),
    ])
    inventory = build_input_inventory(index, [])
    assert inventory["raw_reads"]["by_sample_type"] == {"D.SEQ": 1}
    assert inventory["derived_products"]["by_sample_type"] == {"A.SPTX": 1}


def test_datatype_spelling_variants_all_count_as_reads():
    """NExtSEEK carries "fastq", "FastQ" and "FASTQ" inside a single cohort."""
    index = dict([
        _record("D.SEQ-1", "D.SEQ", DataType="fastq"),
        _record("D.SEQ-2", "D.SEQ", DataType="FastQ"),
        _record("D.SEQ-3", "D.SEQ", DataType="FASTQ"),
    ])
    inventory = build_input_inventory(index, [])
    assert inventory["raw_reads"]["n_records"] == 3


def test_filenames_beat_datatype_when_they_disagree():
    """The filename is what the record points at; DataType is a typed-in label."""
    index = dict([_record("D.SEQ-1", "D.SEQ", File_PrimaryData="run.bam", DataType="FastQ")])
    inventory = build_input_inventory(index, [])
    assert inventory["raw_reads"]["n_records"] == 0
    assert inventory["aligned_reads"]["n_records"] == 1


def test_link_primary_data_is_used_when_no_filename_is_recorded():
    index = dict([
        _record("D.SEQ-1", "D.SEQ",
                Link_PrimaryData="s3://bucket/run/reads_R1.fastq.gz, "
                                 "s3://bucket/run/reads_R2.fastq.gz"),
    ])
    assert build_input_inventory(index, [])["raw_reads"]["n_records"] == 1


def test_specimen_records_are_counted_separately_from_missing_data():
    """TIS/PAT carry no file because they are material, not data."""
    inventory = build_input_inventory(_fibroblast_index(), [])
    assert inventory["no_primary_data"] == {
        "n_records": 2, "by_sample_type": {"TIS": 1, "PAT": 1},
    }


def test_unresolved_queried_uids_are_named_rather_than_dropped():
    inventory = build_input_inventory(_fibroblast_index(), ["A.SCXP-1", "NOPE-1"])
    assert inventory["asked_about"]["by_sample_type"] == {"A.SCXP": 1, "unresolved": 1}


def test_empty_cohort_yields_zero_counts_not_an_error():
    inventory = build_input_inventory(None, None)
    assert inventory["raw_reads"]["n_records"] == 0
    assert inventory["asked_about"]["n_uids"] == 0


# ---------------------------------------------------------------------------
# Wiring into the digest
# ---------------------------------------------------------------------------


def _deps_with_index(uid_index):
    return {
        "fetch_metadata": lambda config, uids: {"ok": True, "data": {"data": []}},
        "annotate": lambda config, metadata: metadata,
        "summarise": lambda metadata_map: {
            "by_sample_type": {}, "lineage_edges": [], "_uid_index": uid_index,
        },
        "filter_deg": lambda summary: {"by_sample_type": {}},
        "extract_refs": lambda metadata: [],
        "fetch_protocols": lambda config, refs: {},
        "download_blobs": lambda payloads, base_dir, config=None, token_limit=None: {},
        "sanitize": lambda payloads: payloads,
    }


def test_digest_carries_the_inventory_computed_before_uid_index_is_stripped():
    out = build_sample_digest(
        config=None, uids=["A.SCXP-1"], deps=_deps_with_index(_fibroblast_index())
    )
    assert "_uid_index" not in out["metadata_summary"]
    assert out["input_data_inventory"]["raw_reads"]["n_records"] == 3
    assert out["input_data_inventory"]["asked_about"]["by_sample_type"] == {"A.SCXP": 1}


def test_inventory_precedes_the_metadata_summary_in_the_digest():
    """The digest is handed to the model as a JSON dump, so key order is
    reading order, and the summary ahead of it runs to tens of thousands of
    characters."""
    out = build_sample_digest(
        config=None, uids=["A.SCXP-1"], deps=_deps_with_index(_fibroblast_index())
    )
    keys = list(out)
    assert keys.index("input_data_inventory") < keys.index("metadata_summary")
