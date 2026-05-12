"""Coverage tests for batch_upload/dag.py — targeting uncovered lines.

Covers:
- _duckdb_path: DuckDB direction computation
- detect_cycles: cycle detection with normalize
- _normalize_cycle: empty cycle
- compute_directions with large dataset (if threshold)
"""

import pytest
from unittest.mock import patch, MagicMock

from nextseek_api.batch_upload.models import InputRowModel


# ---------------------------------------------------------------------------
# _normalize_cycle
# ---------------------------------------------------------------------------

class TestNormalizeCycle:

    def test_empty_cycle(self):
        from nextseek_api.batch_upload.dag import _normalize_cycle
        assert _normalize_cycle([]) == []

    def test_single_element(self):
        from nextseek_api.batch_upload.dag import _normalize_cycle
        assert _normalize_cycle(["A"]) == ["A"]

    def test_rotation(self):
        from nextseek_api.batch_upload.dag import _normalize_cycle
        result = _normalize_cycle(["C", "A", "B"])
        assert result == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# detect_cycles
# ---------------------------------------------------------------------------

class TestDetectCycles:

    def test_no_cycles(self):
        from nextseek_api.batch_upload.dag import detect_cycles
        edges = {("A", "B"), ("B", "C")}
        cycles = detect_cycles(edges)
        assert cycles == []

    def test_simple_cycle(self):
        from nextseek_api.batch_upload.dag import detect_cycles
        edges = {("A", "B"), ("B", "A")}
        cycles = detect_cycles(edges)
        assert len(cycles) >= 1

    def test_triangle_cycle(self):
        from nextseek_api.batch_upload.dag import detect_cycles
        edges = {("A", "B"), ("B", "C"), ("C", "A")}
        cycles = detect_cycles(edges)
        assert len(cycles) >= 1


# ---------------------------------------------------------------------------
# _duckdb_path
# ---------------------------------------------------------------------------

class TestDuckdbPath:

    def test_basic_direction_computation(self):
        from nextseek_api.batch_upload.dag import _duckdb_path
        assays_records = [
            {"uid": "A", "assay": 1},
            {"uid": "B", "assay": 1},
        ]
        edges_records = [
            {"parent": "A", "child": "B"},
        ]
        direction_by_pair, child_uids_by_assay, conflicts = _duckdb_path(assays_records, edges_records)
        assert ("B", 1) in direction_by_pair
        assert direction_by_pair[("B", 1)] == 2  # Both have assay -> child/output

    def test_child_only_assay(self):
        from nextseek_api.batch_upload.dag import _duckdb_path
        assays_records = [
            {"uid": "B", "assay": 2},
        ]
        edges_records = [
            {"parent": "A", "child": "B"},
        ]
        direction_by_pair, child_uids_by_assay, conflicts = _duckdb_path(assays_records, edges_records)
        assert ("B", 2) in direction_by_pair
        assert direction_by_pair[("B", 2)] == 1  # Only child has assay -> parent/input

    def test_no_matching_edges(self):
        from nextseek_api.batch_upload.dag import _duckdb_path
        assays_records = [{"uid": "A", "assay": 1}]
        edges_records = [{"parent": "X", "child": "Y"}]
        direction_by_pair, child_uids_by_assay, conflicts = _duckdb_path(assays_records, edges_records)
        assert direction_by_pair == {}


# ---------------------------------------------------------------------------
# _pandas_path
# ---------------------------------------------------------------------------

class TestPandasPath:

    def test_empty_merge(self):
        from nextseek_api.batch_upload.dag import _pandas_path
        assays_records = [{"uid": "A", "assay": 1}]
        edges_records = [{"parent": "X", "child": "Y"}]
        direction_by_pair, child_uids_by_assay, conflicts = _pandas_path(assays_records, edges_records)
        assert direction_by_pair == {}


# ---------------------------------------------------------------------------
# compute_directions
# ---------------------------------------------------------------------------

class TestComputeDirections:

    def test_with_edges_and_assays(self):
        from nextseek_api.batch_upload.dag import compute_directions
        rows = [
            InputRowModel(SampleType="NHP", json_metadata='{"Name":"A","Parent":""}', UID="NHP-010101AA-1", assay_ids=[1]),
            InputRowModel(SampleType="NHP", json_metadata='{"Name":"B","Parent":"NHP-010101AA-1"}', UID="NHP-010101AA-2", assay_ids=[1]),
        ]
        result = compute_directions(rows)
        assert ("NHP-010101AA-2", 1) in result.direction_by_pair

    def test_no_assays(self):
        from nextseek_api.batch_upload.dag import compute_directions
        rows = [
            InputRowModel(SampleType="NHP", json_metadata='{"Name":"A"}', UID="NHP-010101AA-1"),
        ]
        result = compute_directions(rows)
        assert result.direction_by_pair == {}
