"""Tests for the LEVELS stage — topological level assignment and cascade failures."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.levels import (
    LevelAssignment,
    cascade_failures,
    compute_levels,
)
from nextseek_api.batch_upload.models import InputRowModel


# ── helpers ──────────────────────────────────────────────────────────────


def _row(uid: str, parent_uid: str | None = None, sample_type: str = "NHP_blood") -> InputRowModel:
    """Create an InputRowModel with optional parent in json_metadata."""
    if parent_uid:
        meta = f'{{"Parent":"{parent_uid}"}}'
    else:
        meta = "{}"
    return InputRowModel(UID=uid, SampleType=sample_type, json_metadata=meta, assay_ids=[])


def _row_multi_parents(uid: str, parent_uids: list[str]) -> InputRowModel:
    """Create a row with multiple semicolon-separated parents."""
    parent_str = ";".join(parent_uids)
    meta = f'{{"Parent":"{parent_str}"}}'
    return InputRowModel(UID=uid, SampleType="NHP_blood", json_metadata=meta, assay_ids=[])


def _mock_conn_factory(existing_map: dict[str, int] | None = None):
    """Return a conn_factory that makes load_existing_samples return existing_map."""
    if existing_map is None:
        existing_map = {}

    @contextmanager
    def fake_conn():
        yield MagicMock()

    return fake_conn, existing_map


def _compute(rows, parents_of, children_of, cycles=None, existing_map=None, preexisting_map=None):
    """Helper to call compute_levels with mocked DB.

    Args:
        existing_map: UIDs found in DB that are external parents (not in spreadsheet).
        preexisting_map: UIDs found in DB that ARE in the spreadsheet (re-upload duplicates).
    """
    if cycles is None:
        cycles = []
    if existing_map is None:
        existing_map = {}
    if preexisting_map is None:
        preexisting_map = {}

    # The DB returns the merged result of both maps
    merged_db_response = {**existing_map, **preexisting_map}
    conn_factory, _ = _mock_conn_factory(merged_db_response)

    with patch(
        "nextseek_api.batch_upload.levels.load_existing_samples",
        return_value=merged_db_response,
    ):
        return compute_levels(
            rows=rows,
            parents_of=parents_of,
            children_of=children_of,
            cycles=cycles,
            conn_factory=conn_factory,
        )


# ── TestComputeLevels ────────────────────────────────────────────────────


class TestComputeLevels:
    def test_empty_input(self):
        result = _compute([], {}, {})
        assert result.levels == {}
        assert result.max_level == -1
        assert result.cycle_uids == set()
        assert result.orphan_uids == set()
        assert result.external_parents == {}
        assert result.preexisting_uids == {}

    def test_all_roots(self):
        """Samples with no parents all get level 0."""
        rows = [_row("A"), _row("B"), _row("C")]
        result = _compute(rows, {}, {})
        assert result.levels == {"A": 0, "B": 0, "C": 0}
        assert result.max_level == 0
        assert result.preexisting_uids == {}

    def test_linear_chain(self):
        """A -> B -> C: levels 0, 1, 2."""
        rows = [_row("A"), _row("B", "A"), _row("C", "B")]
        parents_of = {"B": {"A"}, "C": {"B"}}
        children_of = {"A": {"B"}, "B": {"C"}}
        result = _compute(rows, parents_of, children_of)
        assert result.levels == {"A": 0, "B": 1, "C": 2}
        assert result.max_level == 2
        assert result.preexisting_uids == {}

    def test_wide_tree(self):
        """A has 5 children, all level 1."""
        children = [f"C{i}" for i in range(5)]
        rows = [_row("A")] + [_row(c, "A") for c in children]
        parents_of = {c: {"A"} for c in children}
        children_of = {"A": set(children)}
        result = _compute(rows, parents_of, children_of)
        assert result.levels["A"] == 0
        for c in children:
            assert result.levels[c] == 1
        assert result.max_level == 1
        assert result.preexisting_uids == {}

    def test_diamond(self):
        """A -> C, B -> C: C is level 1 (max of parents + 1)."""
        rows = [_row("A"), _row("B"), _row_multi_parents("C", ["A", "B"])]
        parents_of = {"C": {"A", "B"}}
        children_of = {"A": {"C"}, "B": {"C"}}
        result = _compute(rows, parents_of, children_of)
        assert result.levels["A"] == 0
        assert result.levels["B"] == 0
        assert result.levels["C"] == 1
        assert result.preexisting_uids == {}

    def test_deep_chain(self):
        """Chain of 5: A->B->C->D->E -> levels 0,1,2,3,4."""
        uids = ["A", "B", "C", "D", "E"]
        rows = [_row("A")]
        parents_of = {}
        children_of = {}
        for i in range(1, 5):
            rows.append(_row(uids[i], uids[i - 1]))
            parents_of[uids[i]] = {uids[i - 1]}
            children_of.setdefault(uids[i - 1], set()).add(uids[i])

        result = _compute(rows, parents_of, children_of)
        for i, uid in enumerate(uids):
            assert result.levels[uid] == i
        assert result.max_level == 4
        assert result.preexisting_uids == {}

    def test_external_parent_in_db(self):
        """Parent not in batch but found in DB -> child is level 0."""
        rows = [_row("B", "EXT-260101MIT-1")]
        parents_of = {"B": {"EXT-260101MIT-1"}}
        children_of = {}
        # EXT parent found in DB with sample_id=99
        result = _compute(
            rows, parents_of, children_of,
            existing_map={"EXT-260101MIT-1": 99},
        )
        assert result.levels["B"] == 0
        assert result.external_parents == {"EXT-260101MIT-1": 99}
        assert "B" not in result.orphan_uids
        assert result.preexisting_uids == {}

    def test_external_parent_not_in_db(self):
        """Parent not in batch AND not in DB -> child is level 0 + orphan."""
        rows = [_row("B", "EXT-260101MIT-1")]
        parents_of = {"B": {"EXT-260101MIT-1"}}
        children_of = {}
        result = _compute(rows, parents_of, children_of, existing_map={})
        assert result.levels["B"] == 0
        assert "B" in result.orphan_uids
        assert result.preexisting_uids == {}

    def test_cycle_excluded(self):
        """Cycle members go in cycle_uids, not in levels."""
        rows = [_row("A"), _row("B", "A"), _row("C", "B")]
        parents_of = {"B": {"A"}, "C": {"B"}, "A": {"C"}}
        children_of = {"A": {"B"}, "B": {"C"}, "C": {"A"}}
        cycles = [["A", "B", "C"]]
        result = _compute(rows, parents_of, children_of, cycles=cycles)
        assert result.cycle_uids == {"A", "B", "C"}
        assert result.levels == {}
        assert result.max_level == -1
        assert result.preexisting_uids == {}

    def test_mixed_parents(self):
        """Child has one in-batch parent (level 0) and one external -> child level 1."""
        rows = [_row("A"), _row_multi_parents("C", ["A", "EXT-260101MIT-1"])]
        parents_of = {"C": {"A", "EXT-260101MIT-1"}}
        children_of = {"A": {"C"}}
        result = _compute(
            rows, parents_of, children_of,
            existing_map={"EXT-260101MIT-1": 99},
        )
        assert result.levels["A"] == 0
        assert result.levels["C"] == 1
        assert result.preexisting_uids == {}

    def test_partial_cycle(self):
        """Some samples in cycle, others not. Non-cycle samples get proper levels."""
        rows = [_row("X"), _row("Y", "X"), _row("A"), _row("B", "A")]
        # X->Y is normal, A->B->A is a cycle
        parents_of = {"Y": {"X"}, "B": {"A"}, "A": {"B"}}
        children_of = {"X": {"Y"}, "A": {"B"}, "B": {"A"}}
        cycles = [["A", "B"]]
        result = _compute(rows, parents_of, children_of, cycles=cycles)
        assert result.levels == {"X": 0, "Y": 1}
        assert result.cycle_uids == {"A", "B"}
        assert result.max_level == 1
        assert result.preexisting_uids == {}

    def test_preexisting_spreadsheet_uid(self):
        """Spreadsheet UID already in DB → appears in preexisting_uids, not external_parents."""
        rows = [_row("A"), _row("B", "A")]
        parents_of = {"B": {"A"}}
        children_of = {"A": {"B"}}
        result = _compute(rows, parents_of, children_of, preexisting_map={"A": 42})
        assert result.preexisting_uids == {"A": 42}
        assert result.external_parents == {}
        assert result.levels == {"A": 0, "B": 1}

    def test_preexisting_and_external_combined(self):
        """Both preexisting spreadsheet UID and external parent in same batch."""
        rows = [_row("A"), _row("B", "EXT-260101MIT-1")]
        parents_of = {"B": {"EXT-260101MIT-1"}}
        children_of = {}
        result = _compute(
            rows, parents_of, children_of,
            existing_map={"EXT-260101MIT-1": 99},
            preexisting_map={"A": 42},
        )
        assert result.preexisting_uids == {"A": 42}
        assert result.external_parents == {"EXT-260101MIT-1": 99}
        assert result.levels["A"] == 0
        assert result.levels["B"] == 0
        assert "B" not in result.orphan_uids


# ── TestCascadeFailures ──────────────────────────────────────────────────


class TestCascadeFailures:
    def test_single_failure(self):
        """Parent fails -> child gets 'parent X failed to insert'."""
        children_of = {"A": {"B"}}
        result = cascade_failures({"A"}, children_of, eligible_uids={"B"})
        assert result == {"B": "parent A failed to insert"}

    def test_deep_cascade(self):
        """A->B->C: A fails -> B='parent A failed', C='parent B failed'."""
        children_of = {"A": {"B"}, "B": {"C"}}
        result = cascade_failures({"A"}, children_of, eligible_uids={"B", "C"})
        assert result["B"] == "parent A failed to insert"
        assert result["C"] == "parent B failed to insert"

    def test_no_children(self):
        """Failed UID has no children -> empty result."""
        result = cascade_failures({"A"}, {}, eligible_uids=set())
        assert result == {}

    def test_only_within_eligible(self):
        """Children outside eligible_uids not cascaded."""
        children_of = {"A": {"B", "C"}}
        result = cascade_failures({"A"}, children_of, eligible_uids={"B"})
        assert "B" in result
        assert "C" not in result

    def test_diamond_cascade(self):
        """A->C, B->C: only A fails -> C gets cascaded."""
        children_of = {"A": {"C"}, "B": {"C"}}
        result = cascade_failures({"A"}, children_of, eligible_uids={"C"})
        assert result == {"C": "parent A failed to insert"}

    def test_both_parents_fail(self):
        """A->C, B->C: both fail -> C gets cascaded (first encountered parent)."""
        children_of = {"A": {"C"}, "B": {"C"}}
        result = cascade_failures({"A", "B"}, children_of, eligible_uids={"C"})
        assert "C" in result
        assert "failed to insert" in result["C"]

    def test_empty_failed_uids(self):
        """No failures -> empty result."""
        children_of = {"A": {"B"}}
        result = cascade_failures(set(), children_of, eligible_uids={"B"})
        assert result == {}

    def test_empty_children_of(self):
        """No children_of -> empty result."""
        result = cascade_failures({"A"}, {}, eligible_uids={"B"})
        assert result == {}
