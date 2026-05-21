"""Unit tests for catalog_semantic: SemanticIndex, fuse_rrf, dynamic_cutoff."""
from chat_nextseek.helpers.tools.catalog_semantic import dynamic_cutoff


def test_dynamic_cutoff_respects_ratio():
    """Items below 0.7 * top_score are dropped."""
    scored = [("a", 1.0), ("b", 0.8), ("c", 0.6), ("d", 0.5)]
    # top=1.0; ratio=0.7 → threshold=0.7; b(0.8)>=0.7 stays, c/d drop
    assert dynamic_cutoff(scored, ratio=0.7, min_k=1, max_k=10) == ["a", "b"]


def test_dynamic_cutoff_floor_min_k():
    """If ratio filter keeps fewer than min_k, expand to min_k."""
    scored = [("a", 1.0), ("b", 0.1), ("c", 0.05), ("d", 0.01)]
    # ratio=0.7 alone keeps only "a"; min_k=3 → expand
    assert dynamic_cutoff(scored, ratio=0.7, min_k=3, max_k=10) == ["a", "b", "c"]


def test_dynamic_cutoff_ceiling_max_k():
    """If ratio filter keeps more than max_k, cap at max_k."""
    scored = [(c, 0.95) for c in "abcdefghij"]  # all close to top
    assert dynamic_cutoff(scored, ratio=0.7, min_k=1, max_k=3) == ["a", "b", "c"]


def test_dynamic_cutoff_empty():
    assert dynamic_cutoff([], ratio=0.7, min_k=1, max_k=10) == []


def test_dynamic_cutoff_zero_top_score():
    """All scores 0 → fall back to min_k items (no ratio meaning)."""
    scored = [("a", 0.0), ("b", 0.0), ("c", 0.0)]
    assert dynamic_cutoff(scored, ratio=0.7, min_k=2, max_k=10) == ["a", "b"]


from chat_nextseek.helpers.tools.catalog_semantic import fuse_rrf


def test_fuse_rrf_single_ranker():
    """Single ranker → fused score is just its 1/(k+rank)."""
    ranking = [({"id": "a"}, 0.9, 1), ({"id": "b"}, 0.5, 2)]
    result = fuse_rrf(ranking, id_fn=lambda x: x["id"], k=60)
    # a: 1/61, b: 1/62; a > b
    assert [item["id"] for item, _ in result] == ["a", "b"]
    # spot-check score values
    assert abs(result[0][1] - 1 / 61) < 1e-9
    assert abs(result[1][1] - 1 / 62) < 1e-9


def test_fuse_rrf_combines_two_rankers():
    """Item ranked well in both rankers beats item ranked well in only one."""
    lex = [({"id": "a"}, 1.0, 1), ({"id": "b"}, 0.9, 2), ({"id": "c"}, 0.8, 3)]
    sem = [({"id": "b"}, 0.95, 1), ({"id": "a"}, 0.85, 2), ({"id": "d"}, 0.7, 3)]
    result = fuse_rrf(lex, sem, id_fn=lambda x: x["id"], k=60)
    ids = [item["id"] for item, _ in result]
    # a: 1/61 + 1/62; b: 1/62 + 1/61; both equal — tied at top
    # c: 1/63 only; d: 1/63 only — tied lower
    assert set(ids[:2]) == {"a", "b"}
    assert set(ids[2:]) == {"c", "d"}


def test_fuse_rrf_partial_overlap():
    """Item in only one ranker still contributes that ranker's score."""
    lex = [({"id": "lex_only"}, 0.99, 1)]
    sem = [({"id": "sem_only"}, 0.99, 1)]
    result = fuse_rrf(lex, sem, id_fn=lambda x: x["id"], k=60)
    assert len(result) == 2
    # Both rank-1 in their own list → fused score 1/61 each
    assert all(abs(score - 1 / 61) < 1e-9 for _, score in result)


def test_fuse_rrf_empty_inputs():
    assert fuse_rrf(id_fn=lambda x: x["id"], k=60) == []
    assert fuse_rrf([], [], id_fn=lambda x: x["id"], k=60) == []


def test_fuse_rrf_preserves_item_object():
    """Output items are the original dicts, not just ids."""
    lex = [({"id": "a", "name": "Alpha"}, 0.9, 1)]
    result = fuse_rrf(lex, id_fn=lambda x: x["id"], k=60)
    item, _ = result[0]
    assert item == {"id": "a", "name": "Alpha"}


from chat_nextseek.helpers.tools.catalog_semantic import (
    doc_sampletype,
    doc_assay,
    doc_endpoint,
)


def test_doc_sampletype_concatenates_fields():
    item = {
        "SampleType": "MUS",
        "Name": "Mouse",
        "Tags": "mouse, murine, rodent",
        "Description": "A mouse sample represents an experimental animal.",
    }
    doc = doc_sampletype(item)
    assert "MUS" in doc
    assert "Mouse" in doc
    assert "murine" in doc
    assert "experimental animal" in doc
    assert " | " in doc


def test_doc_sampletype_skips_empty_fields():
    item = {"SampleType": "X", "Name": "", "Tags": None}
    doc = doc_sampletype(item)
    assert doc == "X"


def test_doc_assay_prefers_canonical_alternative_key():
    item = {
        "Name": "Flow Cytometry",
        "Alternative Assay Names": "FACS, immunophenotyping",
        "Tags": "single cell",
        "Description": "A laser-based technique.",
    }
    doc = doc_assay(item)
    assert "FACS" in doc
    assert "Flow Cytometry" in doc


def test_doc_assay_falls_back_to_snake_case_alternative_key():
    item = {
        "Name": "Foo",
        "alternative_assay_names": "Bar, Baz",
    }
    doc = doc_assay(item)
    assert "Bar" in doc


def test_doc_endpoint_includes_path_and_tags_list():
    item = {
        "path": "/samples/advanced_search/",
        "summary": "Search for samples by criteria",
        "description": "Returns paginated rows.",
        "tags": ["samples", "search"],
    }
    doc = doc_endpoint(item)
    assert "/samples/advanced_search/" in doc
    assert "samples search" in doc or "samples" in doc
    assert "paginated" in doc
