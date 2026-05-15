"""enumerate_lineage_leaves: walk a metadata bundle and return leaf records
whose sample_type matches the catalog filter."""
from chat_nextseek.helpers import enumerate_lineage_leaves


def _bundle(*sample_type_blocks):
    return {"data": list(sample_type_blocks)}


def _block(sample_type, *samples):
    return {"sample_type": sample_type, "samples": list(samples)}


def _sample(uuid, metadata=None, children=None):
    return {
        "uuid": uuid,
        "metadata": metadata or {"UID": uuid},
        "children": children or [],
    }


def test_returns_empty_when_bundle_empty():
    out = enumerate_lineage_leaves({}, accepted_types=["D.SEQ"])
    assert out == []


def test_returns_empty_when_no_matching_type():
    bundle = _bundle(_block("TIS", _sample("TIS-1")))
    out = enumerate_lineage_leaves(bundle, accepted_types=["D.SEQ"])
    assert out == []


def test_returns_leaves_at_top_level():
    bundle = _bundle(_block("D.SEQ",
        _sample("D.SEQ-1", metadata={"UID": "D.SEQ-1", "assay_name": "RNA-seq"}),
        _sample("D.SEQ-2", metadata={"UID": "D.SEQ-2", "assay_name": "RNA-seq"}),
    ))
    out = enumerate_lineage_leaves(bundle, accepted_types=["D.SEQ"])
    assert len(out) == 2
    assert {l["uid"] for l in out} == {"D.SEQ-1", "D.SEQ-2"}
    assert all(l["sample_type"] == "D.SEQ" for l in out)
    assert {l["assay"] for l in out} == {"RNA-seq"}


def test_returns_leaves_nested_in_children():
    bundle = _bundle(_block("NHP",
        _sample("NHP-1", children=[
            _sample("TIS-1", children=[
                _sample("D.SEQ-1", metadata={"UID": "D.SEQ-1", "assay_name": "RNA-seq"}),
                _sample("D.SEQ-2", metadata={"UID": "D.SEQ-2", "assay_name": "WGS"}),
            ]),
        ]),
    ))
    out = enumerate_lineage_leaves(bundle, accepted_types=["D.SEQ"])
    assert len(out) == 2
    assert {l["uid"] for l in out} == {"D.SEQ-1", "D.SEQ-2"}


def test_accepted_types_filter_excludes_other_leaves():
    bundle = _bundle(_block("NHP",
        _sample("NHP-1", children=[
            _sample("D.SEQ-1", metadata={"UID": "D.SEQ-1", "assay_name": "RNA-seq"}),
            _sample("A.GSEA-1", metadata={"UID": "A.GSEA-1"}),
        ]),
    ))
    out = enumerate_lineage_leaves(bundle, accepted_types=["D.SEQ"])
    assert {l["uid"] for l in out} == {"D.SEQ-1"}


def test_carries_source_uid_provenance():
    bundle = _bundle(_block("NHP",
        _sample("NHP-1", children=[_sample("D.SEQ-1", metadata={"UID": "D.SEQ-1"})]),
        _sample("NHP-2", children=[_sample("D.SEQ-2", metadata={"UID": "D.SEQ-2"})]),
    ))
    out = enumerate_lineage_leaves(bundle, accepted_types=["D.SEQ"])
    by_uid = {l["uid"]: l for l in out}
    assert by_uid["D.SEQ-1"]["source_uid"] == "NHP-1"
    assert by_uid["D.SEQ-2"]["source_uid"] == "NHP-2"


def test_handles_assay_field_aliases():
    """Different API response shapes put the assay under different keys."""
    bundle = _bundle(_block("D.SEQ",
        _sample("D.SEQ-1", metadata={"UID": "D.SEQ-1", "assay": "ATAC-seq"}),
        _sample("D.SEQ-2", metadata={"UID": "D.SEQ-2", "AssayName": "ChIP-seq"}),
        _sample("D.SEQ-3", metadata={"UID": "D.SEQ-3"}),  # no assay
    ))
    out = enumerate_lineage_leaves(bundle, accepted_types=["D.SEQ"])
    by_uid = {l["uid"]: l for l in out}
    assert by_uid["D.SEQ-1"]["assay"] == "ATAC-seq"
    assert by_uid["D.SEQ-2"]["assay"] == "ChIP-seq"
    assert by_uid["D.SEQ-3"]["assay"] == ""


def test_returns_empty_when_accepted_types_empty():
    """Empty accepted_types disables enumeration entirely (e.g. fetchngs)."""
    bundle = _bundle(_block("D.SEQ", _sample("D.SEQ-1")))
    out = enumerate_lineage_leaves(bundle, accepted_types=[])
    assert out == []
