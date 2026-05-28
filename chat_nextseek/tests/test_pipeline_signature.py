"""group_leaves_by_signature: cluster candidate sequencing leaves by their
low-cardinality descriptor metadata so the sanity step classifies a few groups
instead of hundreds of leaves. Cardinality-driven — no field names hardcoded;
per-leaf identity fields (UID, filenames) fall out automatically."""
from chat_nextseek.pipeline.steps.signature import group_leaves_by_signature


def _leaf(uid, **md):
    return {
        "uid": uid,
        "sample_type": "D.SEQ",
        "assay": md.get("SequencingType", ""),
        "source_uid": "MUS-1",
        "metadata": {"UID": uid, **md},
    }


def test_empty_leaves_returns_empty():
    assert group_leaves_by_signature([]) == []


def test_homogeneous_cohort_is_one_group():
    leaves = [
        _leaf(f"D.SEQ-{i}", SequencingType="RNA-seq", LibraryStrategy="RNA-Seq",
              File=f"s{i}_R1.fastq.gz")
        for i in range(1, 6)
    ]
    groups = group_leaves_by_signature(leaves)
    assert len(groups) == 1
    assert groups[0]["n_leaves"] == 5
    assert set(groups[0]["leaf_uids"]) == {f"D.SEQ-{i}" for i in range(1, 6)}


def test_mixed_cohort_splits_on_descriptor_field():
    """RNA-seq and WGS D.SEQ under the same mice must land in separate groups,
    discriminated by SequencingType — not lumped together."""
    rna = [_leaf(f"D.SEQ-R{i}", SequencingType="RNA-seq", LibraryStrategy="RNA-Seq",
                 File=f"r{i}_R1.fastq.gz") for i in range(1, 4)]
    wgs = [_leaf(f"D.SEQ-W{i}", SequencingType="WGS", LibraryStrategy="WGS",
                 File=f"w{i}_R1.fastq.gz") for i in range(1, 3)]
    groups = group_leaves_by_signature(rna + wgs)
    assert len(groups) == 2
    sets = {frozenset(g["leaf_uids"]) for g in groups}
    assert frozenset({"D.SEQ-R1", "D.SEQ-R2", "D.SEQ-R3"}) in sets
    assert frozenset({"D.SEQ-W1", "D.SEQ-W2"}) in sets
    # Largest group first.
    assert groups[0]["n_leaves"] == 3


def test_identity_fields_excluded_from_signature():
    leaves = [_leaf(f"D.SEQ-R{i}", SequencingType="RNA-seq", File=f"r{i}_R1.fastq.gz")
              for i in range(1, 4)]
    groups = group_leaves_by_signature(leaves)
    sig = groups[0]["signature"]
    assert sig.get("SequencingType") == "RNA-seq"   # shared descriptor → in signature
    assert "UID" not in sig                          # unique per leaf → identity
    assert "File" not in sig                         # unique per leaf → identity


def test_each_group_has_stable_id():
    leaves = [_leaf(f"D.SEQ-{i}", SequencingType="RNA-seq", File=f"s{i}.fq") for i in range(1, 4)]
    groups = group_leaves_by_signature(leaves)
    assert groups[0]["group_id"] == "g1"
