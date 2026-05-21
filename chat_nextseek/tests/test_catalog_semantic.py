"""Unit tests for catalog_semantic: SemanticIndex, fuse_rrf, dynamic_cutoff."""
import hashlib
from pathlib import Path

import numpy as np

from chat_nextseek.helpers.tools.catalog_semantic import (
    SemanticIndex,
    doc_assay,
    doc_endpoint,
    doc_sampletype,
    dynamic_cutoff,
    fuse_rrf,
)


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


def test_doc_endpoint_includes_real_schema_fields():
    """Verify doc_endpoint pulls intent_patterns + example_intents + llm_hint
    from the real catalog schema, not a hypothetical summary/tags shape."""
    item = {
        "path": "/samples/advanced_search/",
        "method": "POST",
        "category": "samples",
        "description": "Search samples with filters.",
        "intent_patterns": ["retrieve", "find", "search"],
        "example_intents": ["find me mice", "show samples with NDMA"],
        "llm_hint": "Use this endpoint for filtered sample retrieval.",
    }
    doc = doc_endpoint(item)
    assert "/samples/advanced_search/" in doc
    assert "samples" in doc                     # category
    assert "Search samples with filters" in doc # description
    assert "retrieve" in doc                    # intent_patterns
    assert "find me mice" in doc                # example_intents
    assert "filtered sample retrieval" in doc   # llm_hint


def test_doc_endpoint_works_on_real_catalog_row():
    """Smoke test against the live catalog file: every row produces a
    non-empty doc string with the path included."""
    import json
    from pathlib import Path
    path = Path("src/chat_nextseek/context/min_api_endpoints_enriched.json")
    data = json.loads(path.read_text())
    assert data, "catalog is empty"
    for ep in data:
        doc = doc_endpoint(ep)
        assert doc, f"empty doc for {ep.get('path')}"
        assert ep.get("path") in doc


def _fake_encoder(dim: int = 8):
    """Deterministic stand-in for sentence-transformers.

    Maps each input string to a unit-norm vector by hashing to bytes,
    scaling to [0,1] floats, then L2-normalizing. Stable across calls,
    distinguishable per input. Avoids reinterpreting raw sha256 bytes
    as float32 (which produces overflow warnings + NaN norms).
    """
    def encode(texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, text in enumerate(texts):
            h = hashlib.sha256(text.encode("utf-8")).digest()
            vec = np.frombuffer(h[:dim], dtype=np.uint8).astype(np.float32) / 255.0
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            out[i] = vec
        return out
    return encode


def test_semantic_index_ready_with_fake_encoder(tmp_path: Path):
    catalog = [{"id": "a", "text": "apple"}, {"id": "b", "text": "banana"}]
    idx = SemanticIndex(
        catalog=catalog,
        name="testcat",
        doc_fn=lambda item: item["text"],
        id_fn=lambda item: item["id"],
        cache_dir=tmp_path,
        encoder=_fake_encoder(),
    )
    assert idx.ready is True


def test_semantic_index_writes_npz(tmp_path: Path):
    catalog = [{"id": "a", "text": "apple"}]
    SemanticIndex(
        catalog=catalog, name="testcat",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=_fake_encoder(),
    )
    cached = tmp_path / "testcat.embeddings.npz"
    assert cached.exists()
    data = np.load(cached, allow_pickle=False)
    assert "embeddings" in data
    assert "item_ids" in data
    assert "catalog_hash" in data
    assert "model_name" in data
    assert data["embeddings"].shape == (1, 8)


def test_semantic_index_reuses_npz_when_hash_matches(tmp_path: Path):
    catalog = [{"id": "a", "text": "apple"}]
    encoder_calls = []

    def counting_encoder(texts):
        encoder_calls.append(list(texts))
        return _fake_encoder()(texts)

    SemanticIndex(
        catalog=catalog, name="testcat",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=counting_encoder,
    )
    assert len(encoder_calls) == 1  # initial embed

    # Second instantiation with SAME catalog content
    SemanticIndex(
        catalog=catalog, name="testcat",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=counting_encoder,
    )
    assert len(encoder_calls) == 1  # cache hit, no re-embed


def test_semantic_index_rebuilds_on_hash_mismatch(tmp_path: Path):
    catalog_v1 = [{"id": "a", "text": "apple"}]
    catalog_v2 = [{"id": "a", "text": "apricot"}]  # changed
    encoder_calls = []

    def counting_encoder(texts):
        encoder_calls.append(list(texts))
        return _fake_encoder()(texts)

    SemanticIndex(catalog=catalog_v1, name="testcat",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=counting_encoder)
    SemanticIndex(catalog=catalog_v2, name="testcat",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=counting_encoder)
    assert len(encoder_calls) == 2  # rebuilt


def test_semantic_index_query_returns_top_n(tmp_path):
    catalog = [
        {"id": "apple",  "text": "apple"},
        {"id": "banana", "text": "banana"},
        {"id": "cherry", "text": "cherry"},
    ]
    idx = SemanticIndex(
        catalog=catalog, name="fruit",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=_fake_encoder(),
    )
    results = idx.query("apple", top_n=2)
    assert len(results) == 2
    # All entries are (item_dict, float_score, int_rank)
    for entry in results:
        item, score, rank = entry
        assert isinstance(item, dict)
        assert isinstance(score, float)
        assert isinstance(rank, int)
    # Ranks are 1-indexed and ordered
    assert [r for _, _, r in results] == [1, 2]
    # Top-1 should be "apple" because the query text exactly matches its doc
    assert results[0][0]["id"] == "apple"


def test_semantic_index_query_scores_sorted_desc(tmp_path):
    catalog = [{"id": str(i), "text": f"item-{i}"} for i in range(20)]
    idx = SemanticIndex(
        catalog=catalog, name="many",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=_fake_encoder(),
    )
    results = idx.query("item-5", top_n=10)
    scores = [s for _, s, _ in results]
    assert scores == sorted(scores, reverse=True)


def test_semantic_index_query_top_n_caps_at_catalog_size(tmp_path):
    catalog = [{"id": "only", "text": "only"}]
    idx = SemanticIndex(
        catalog=catalog, name="solo",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=_fake_encoder(),
    )
    assert len(idx.query("anything", top_n=50)) == 1


def test_semantic_index_query_empty_catalog(tmp_path):
    idx = SemanticIndex(
        catalog=[], name="empty",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=_fake_encoder(),
    )
    assert idx.query("anything", top_n=5) == []


def test_semantic_index_unhealthy_when_encoder_raises_at_init(tmp_path):
    def broken_encoder(texts):
        raise RuntimeError("model load failed")
    catalog = [{"id": "a", "text": "apple"}]
    idx = SemanticIndex(
        catalog=catalog, name="broken",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=broken_encoder,
    )
    assert idx.ready is False
    assert idx.fail_reason is not None
    assert "model load failed" in idx.fail_reason


def test_semantic_index_query_returns_empty_when_not_ready(tmp_path):
    def broken_encoder(texts):
        raise RuntimeError("nope")
    idx = SemanticIndex(
        catalog=[{"id": "a", "text": "apple"}], name="broken2",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=broken_encoder,
    )
    assert idx.query("apple", top_n=5) == []


def test_semantic_index_query_returns_empty_on_query_time_failure(tmp_path):
    """If the encoder works at init but fails on query-time encoding,
    query returns empty rather than raising."""
    call_count = {"n": 0}

    def encoder(texts):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_encoder()(texts)  # init succeeds
        raise RuntimeError("query failed")

    idx = SemanticIndex(
        catalog=[{"id": "a", "text": "apple"}], name="flaky",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=encoder,
    )
    assert idx.ready is True
    assert idx.query("apple", top_n=5) == []


def test_semantic_index_rebuilds_on_corrupt_cache(tmp_path):
    """Corrupt .npz file → rebuild silently."""
    cached = tmp_path / "corrupt.embeddings.npz"
    cached.write_bytes(b"not a real npz file")
    idx = SemanticIndex(
        catalog=[{"id": "a", "text": "apple"}], name="corrupt",
        doc_fn=lambda x: x["text"], id_fn=lambda x: x["id"],
        cache_dir=tmp_path, encoder=_fake_encoder(),
    )
    assert idx.ready is True
    assert len(idx.query("apple", top_n=5)) == 1
