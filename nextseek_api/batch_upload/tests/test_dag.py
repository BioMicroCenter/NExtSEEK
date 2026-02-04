"""Tests for the DAG stage."""
import pytest

from nextseek_api.batch_upload.dag import (
    build_relationships,
    compute_directions,
    detect_cycles,
    extract_parents,
)
from nextseek_api.batch_upload.models import InputRowModel


class TestExtractParents:
    def test_single_parent(self):
        meta = '{"Parent":"NHP-220630FLY-1-PUB"}'
        result = extract_parents(meta)
        assert result == frozenset({"NHP-220630FLY-1-PUB"})

    def test_multiple_parents(self):
        meta = '{"Parent":"NHP-220630FLY-1-PUB;NHP-220630FLY-2-PUB"}'
        result = extract_parents(meta)
        assert len(result) == 2

    def test_no_parent_field(self):
        meta = '{"Name":"test"}'
        assert extract_parents(meta) == frozenset()

    def test_empty_parent(self):
        meta = '{"Parent":""}'
        assert extract_parents(meta) == frozenset()

    def test_invalid_json(self):
        assert extract_parents("not json") == frozenset()

    def test_invalid_uid_filtered(self):
        meta = '{"Parent":"invalid-uid"}'
        assert extract_parents(meta) == frozenset()

    def test_cache_returns_same(self):
        meta = '{"Parent":"NHP-220630FLY-1-PUB"}'
        r1 = extract_parents(meta)
        r2 = extract_parents(meta)
        assert r1 is r2  # same frozen set from cache


class TestBuildRelationships:
    def _make_row(self, uid, parent_meta="{}"):
        return InputRowModel(
            UID=uid, SampleType="T", json_metadata=parent_meta, assay_ids=[]
        )

    def test_basic(self):
        rows = [
            self._make_row("NHP-220630FLY-2-PUB", '{"Parent":"NHP-220630FLY-1-PUB"}'),
            self._make_row("NHP-220630FLY-1-PUB"),
        ]
        parents_of, children_of, edges = build_relationships(rows)
        assert "NHP-220630FLY-1-PUB" in parents_of.get("NHP-220630FLY-2-PUB", set())
        assert ("NHP-220630FLY-1-PUB", "NHP-220630FLY-2-PUB") in edges

    def test_no_parents(self):
        rows = [self._make_row("NHP-220630FLY-1-PUB")]
        parents_of, children_of, edges = build_relationships(rows)
        assert len(edges) == 0


class TestComputeDirections:
    def _make_row(self, uid, assay_ids, parent_meta="{}"):
        return InputRowModel(
            UID=uid, SampleType="T", json_metadata=parent_meta, assay_ids=assay_ids
        )

    def test_source_direction(self):
        """Child has assay, parent doesn't -> direction 0 (source)."""
        rows = [
            self._make_row("NHP-220630FLY-1-PUB", []),  # parent, no assays
            self._make_row("NHP-220630FLY-2-PUB", [100], '{"Parent":"NHP-220630FLY-1-PUB"}'),
        ]
        dc = compute_directions(rows)
        assert dc.direction_by_pair.get(("NHP-220630FLY-2-PUB", 100)) == 0

    def test_target_direction(self):
        """Both have same assay -> direction 1 (target)."""
        rows = [
            self._make_row("NHP-220630FLY-1-PUB", [100]),  # parent has assay
            self._make_row("NHP-220630FLY-2-PUB", [100], '{"Parent":"NHP-220630FLY-1-PUB"}'),
        ]
        dc = compute_directions(rows)
        assert dc.direction_by_pair.get(("NHP-220630FLY-2-PUB", 100)) == 1

    def test_empty_rows(self):
        dc = compute_directions([])
        assert dc.direction_by_pair == {}


class TestDetectCycles:
    def test_no_cycle(self):
        edges = {("A", "B"), ("B", "C")}
        assert detect_cycles(edges) == []

    def test_simple_cycle(self):
        edges = {("A", "B"), ("B", "A")}
        cycles = detect_cycles(edges)
        assert len(cycles) >= 1

    def test_longer_cycle(self):
        edges = {("A", "B"), ("B", "C"), ("C", "A")}
        cycles = detect_cycles(edges)
        assert len(cycles) >= 1
