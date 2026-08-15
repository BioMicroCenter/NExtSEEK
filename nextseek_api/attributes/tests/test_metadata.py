"""Unit tests for the pure and mock-orchestrated surface of the metadata kernel.

These tests never touch a real database. Real-boundary semantic verification
against a disposable MariaDB/MySQL server lives in `test_metadata_db.py`, and
the frozen Cartesian benchmark protocol lives in `test_metadata_benchmark.py`
/ `test_performance_metadata.py`.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import orjson
import pytest

from nextseek_api.attributes.metadata import (
    InvalidMetadata,
    RewriteSpec,
    iter_pk_chunks,
    rewrite_document,
    rewrite_type_metadata,
)


@pytest.mark.parametrize(
    ("raw", "spec", "expected"),
    [
        (b'{"UID":"u1"}', RewriteSpec(("UID", "Age"), additions=("Age",)), {"UID": "u1", "Age": ""}),
        (b'{"UID":"u1","Old":0}', RewriteSpec(("UID", "New"), renames=(("Old", "New"),)), {"UID": "u1", "New": 0}),
        (b'{"UID":"u1","Gone":null}', RewriteSpec(("UID",), deletions=("Gone",)), {"UID": "u1"}),
        (b'{"UID":"u1","Stale":2}', RewriteSpec(("UID",)), {"UID": "u1"}),
        ('{"UID":"\u03bb","N":false}'.encode(), RewriteSpec(("UID", "N")), {"UID": "\u03bb", "N": False}),
    ],
)
def test_create_rename_delete_matches_legacy_golden(raw, spec, expected):
    assert json.loads(rewrite_document(raw, spec)) == expected


@pytest.mark.parametrize("raw", [b"", b"null", b"[]", b"{broken", b'"a string"', b"42"])
def test_invalid_metadata_fails_closed(raw):
    with pytest.raises(InvalidMetadata):
        rewrite_document(raw, RewriteSpec(("UID",)))


def test_rename_does_not_overwrite_existing_destination():
    with pytest.raises(InvalidMetadata, match="destination"):
        rewrite_document(b'{"Old":1,"New":2}', RewriteSpec(("New",), renames=(("Old", "New"),)))


def test_rename_onto_self_is_a_no_op_not_a_collision():
    result = rewrite_document(b'{"Same":1}', RewriteSpec(("Same",), renames=(("Same", "Same"),)))
    assert json.loads(result) == {"Same": 1}


def test_chunking_is_deterministic_and_bounded():
    # The third row (8 bytes) exactly saturates max_bytes on its own, so it is
    # accepted as a singleton chunk rather than triggering the byte-ceiling
    # rejection exercised separately below.
    rows = [(1, b"a" * 3), (2, b"b" * 3), (3, b"c" * 8)]
    assert list(iter_pk_chunks(rows, max_rows=2, max_bytes=8)) == [[rows[0], rows[1]], [rows[2]]]


def test_chunking_splits_on_row_count_before_byte_budget_is_exhausted():
    rows = [(1, b"a"), (2, b"b"), (3, b"c")]
    assert list(iter_pk_chunks(rows, max_rows=2, max_bytes=1000)) == [[rows[0], rows[1]], [rows[2]]]


def test_chunking_rejects_non_increasing_primary_keys():
    with pytest.raises(ValueError, match="strict primary-key order"):
        list(iter_pk_chunks([(2, b"x"), (1, b"y")], max_rows=10, max_bytes=1000))


def test_chunking_rejects_non_positive_limits():
    with pytest.raises(ValueError, match="must be positive"):
        list(iter_pk_chunks([(1, b"x")], max_rows=0, max_bytes=10))
    with pytest.raises(ValueError, match="must be positive"):
        list(iter_pk_chunks([(1, b"x")], max_rows=10, max_bytes=0))


def test_single_row_larger_than_byte_ceiling_is_rejected():
    with pytest.raises(ValueError, match="single row exceeds max_bytes"):
        list(iter_pk_chunks([(1, b"x" * 8)], max_rows=2, max_bytes=7))


def test_empty_row_iterable_yields_no_chunks():
    assert list(iter_pk_chunks([], max_rows=10, max_bytes=1000)) == []


def _bulk_update_execute_calls(cursor):
    """Return execute calls that are the kernel's CASE bulk UPDATE statements."""
    return [
        call for call in cursor.execute.call_args_list
        if call.args and isinstance(call.args[0], str)
        and call.args[0].startswith("UPDATE samples SET json_metadata = CASE id")
    ]


def test_invalid_json_fails_closed_without_any_write():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchmany.side_effect = [[(1, b'{"UID":"a"}'), (2, b"not json")], []]
    with pytest.raises(InvalidMetadata):
        rewrite_type_metadata(connection, 9, RewriteSpec(("UID",)), max_rows=10, max_bytes=1000)
    assert _bulk_update_execute_calls(cursor) == []
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    cursor.close.assert_called_once()


def test_rename_conflict_detected_in_validate_pass_before_any_write():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchmany.side_effect = [[(1, b'{"Old":1,"New":2}')], []]
    with pytest.raises(InvalidMetadata, match="destination"):
        rewrite_type_metadata(
            connection, 9, RewriteSpec(("New",), renames=(("Old", "New"),)), max_rows=10, max_bytes=1000,
        )
    assert _bulk_update_execute_calls(cursor) == []


def test_kernel_bulk_updates_in_pk_order_without_opening_a_transaction():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    ordered_rows = [(1, b'{"UID":"a"}'), (2, b'{"UID":"b"}')]
    cursor.fetchmany.side_effect = [ordered_rows, [], ordered_rows, []]
    cursor.rowcount = 2
    result = rewrite_type_metadata(
        connection, 9, RewriteSpec(("UID", "X"), additions=("X",)), max_rows=2, max_bytes=1000,
    )
    assert result.scanned == 2
    assert result.updated == 2
    assert result.statements == 1
    writes = _bulk_update_execute_calls(cursor)
    assert len(writes) == 1
    sql, flat = writes[0].args[0], writes[0].args[1]
    assert sql.startswith("UPDATE samples SET json_metadata = CASE id")
    assert "WHERE id IN (%s,%s)" in sql
    # CASE params are (pk, rewritten, pk, rewritten, ..., pk, pk) for the IN list
    assert flat[0] == 1 and flat[2] == 2
    assert flat[-2:] == [1, 2]
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    connection.cursor.assert_called_once()


def test_kernel_splits_write_pass_into_multiple_bulk_statements():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    rows = [(1, b'{"UID":"a"}'), (2, b'{"UID":"b"}'), (3, b'{"UID":"c"}')]
    cursor.fetchmany.side_effect = [rows, [], rows, []]
    cursor.rowcount = 1
    result = rewrite_type_metadata(connection, 9, RewriteSpec(("UID",)), max_rows=1, max_bytes=1000)
    assert result.scanned == 3
    assert result.statements == 3
    assert result.updated == 3
    assert len(_bulk_update_execute_calls(cursor)) == 3


def test_row_count_mismatch_raises_without_committing():
    from nextseek_api.attributes.metadata import RewriteCountMismatch

    connection = MagicMock()
    cursor = connection.cursor.return_value
    rows = [(1, b'{"UID":"a"}'), (2, b'{"UID":"b"}')]
    cursor.fetchmany.side_effect = [rows, [], rows, []]
    cursor.rowcount = 1  # server claims only one row updated though two were sent
    with pytest.raises(RewriteCountMismatch):
        rewrite_type_metadata(connection, 9, RewriteSpec(("UID",)), max_rows=10, max_bytes=1000)
    connection.commit.assert_not_called()


def test_idempotent_zero_rowcount_requires_targets_to_still_exist():
    from nextseek_api.attributes.metadata import RewriteCountMismatch

    connection = MagicMock()
    cursor = connection.cursor.return_value
    rows = [(1, b'{"UID":"a"}'), (2, b'{"UID":"b"}')]
    cursor.fetchmany.side_effect = [rows, [], rows, []]
    cursor.rowcount = 0
    cursor.fetchone.return_value = (1,)  # only one of two ids still present
    with pytest.raises(RewriteCountMismatch, match="only 1 target ids exist"):
        rewrite_type_metadata(connection, 9, RewriteSpec(("UID",)), max_rows=10, max_bytes=1000)
    connection.commit.assert_not_called()


def test_idempotent_zero_rowcount_counts_when_all_targets_exist():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    rows = [(1, b'{"UID":"a"}'), (2, b'{"UID":"b"}')]
    cursor.fetchmany.side_effect = [rows, [], rows, []]
    cursor.rowcount = 0
    cursor.fetchone.return_value = (2,)
    result = rewrite_type_metadata(connection, 9, RewriteSpec(("UID",)), max_rows=10, max_bytes=1000)
    assert result.updated == 2
    connection.commit.assert_not_called()

def test_fault_hook_runs_before_each_bulk_statement_with_ordinal_and_total():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    rows = [(1, b'{"UID":"a"}'), (2, b'{"UID":"b"}')]
    cursor.fetchmany.side_effect = [rows, [], rows, []]
    cursor.rowcount = 1
    seen = []
    rewrite_type_metadata(
        connection, 3, RewriteSpec(("UID",)), max_rows=1, max_bytes=100,
        fault_hook=lambda point, ordinal, total: seen.append((point, ordinal, total)),
    )
    assert seen == [("before_bulk_update", 1, 2), ("before_bulk_update", 2, 2)]


def test_fault_hook_exception_propagates_before_the_faulted_statement_executes():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    rows = [(1, b'{"UID":"a"}'), (2, b'{"UID":"b"}')]
    cursor.fetchmany.side_effect = [rows, [], rows, []]
    cursor.rowcount = 1

    def fault(point, ordinal, total):
        if ordinal == total:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        rewrite_type_metadata(
            connection, 3, RewriteSpec(("UID",)), max_rows=1, max_bytes=100, fault_hook=fault,
        )
    assert len(_bulk_update_execute_calls(cursor)) == 1
    connection.commit.assert_not_called()


def test_zero_rows_is_a_no_op_that_never_touches_write_path():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchmany.side_effect = [[], []]
    result = rewrite_type_metadata(connection, 9, RewriteSpec(("UID",)), max_rows=10, max_bytes=1000)
    assert (result.scanned, result.updated, result.statements) == (0, 0, 0)
    assert _bulk_update_execute_calls(cursor) == []


def _independent_oracle(document: dict, spec: RewriteSpec) -> dict:
    """A hand-written reference for legacy metadata rewrite semantics.

    This function must never import or call `rewrite_document` (or anything
    built on it): it is the independent ground truth that the real
    implementation is checked against, not a copy of it.
    """
    result = dict(document)
    for old_title, new_title in spec.renames:
        if old_title in result and new_title in result and old_title != new_title:
            raise InvalidMetadata(f"rename destination already exists: {new_title!r}")
        if old_title in result:
            moved_value = result[old_title]
            del result[old_title]
            result[new_title] = moved_value
    for title in spec.deletions:
        if title in result:
            del result[title]
    for title in spec.additions:
        if title not in result:
            result[title] = ""
    projected = {}
    for title in spec.resulting_titles:
        projected[title] = result[title] if title in result else ""
    return projected


PROPERTY_CORPUS = [
    ({"UID": "u1", "Legacy": None}, RewriteSpec(("UID", "Legacy")), "null_value"),
    ({"UID": "u1", "Flag": False}, RewriteSpec(("UID", "Flag")), "false_value"),
    ({"UID": "u1", "Count": 0}, RewriteSpec(("UID", "Count")), "zero_value"),
    ({"UID": "\u03bb\u03bc\u2605", "Note": "caf\u00e9"}, RewriteSpec(("UID", "Note")), "unicode"),
    ({"UID": "u1", "Stale": "gone"}, RewriteSpec(("UID",)), "stale_key_dropped"),
    (
        {"UID": "u1", "Old": 1, "New": 2},
        RewriteSpec(("New",), renames=(("Old", "New"),)),
        "destination_conflict",
    ),
    (
        {"UID": "u1", "Old": 1, "Gone": "x", "Untouched": "keep"},
        RewriteSpec(("UID", "New", "Untouched", "Extra"), renames=(("Old", "New"),), deletions=("Gone",), additions=("Extra",)),
        "simultaneous_rename_delete_create",
    ),
    (
        {"Old": 0, "New": 0},
        RewriteSpec(("New",), renames=(("Old", "New"),)),
        "falsy_values_still_conflict",
    ),
]


def test_rewrite_property_corpus_matches_independent_oracle():
    """Exact Chain-C primary node (unparametrized umbrella) over the frozen corpus."""
    from nextseek_api.attributes.tests.chain_c_t06 import record_chain_c_case

    assertion_count = 0
    observed_cases = []
    expected_cases = []
    for document, spec, case_id in PROPERTY_CORPUS:
        raw = orjson.dumps(document)
        try:
            expected = _independent_oracle(document, spec)
        except InvalidMetadata:
            with pytest.raises(InvalidMetadata):
                rewrite_document(raw, spec)
            assertion_count += 1
            observed_cases.append({"case": case_id, "error": "InvalidMetadata"})
            expected_cases.append({"case": case_id, "error": "InvalidMetadata"})
            continue
        observed = json.loads(rewrite_document(raw, spec))
        assert observed == expected
        assertion_count += 1
        observed_cases.append({"case": case_id, "document": observed})
        expected_cases.append({"case": case_id, "document": expected})

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_metadata.py::test_rewrite_property_corpus_matches_independent_oracle",
        runner_lane="unit",
        fixture_id="task06-property-corpus",
        independent_oracle={"corpus": [{"case": c, "document": d, "spec": s.__dict__} for d, s, c in PROPERTY_CORPUS]},
        before_semantic={"corpus_size": len(PROPERTY_CORPUS)},
        expected_semantic=expected_cases,
        observed_semantic=observed_cases,
        fresh_connection_id=None,
        sql_source_id=None,
        lock_source_id=None,
        packet_source_id=None,
        rss_sampler_pid=None,
        rss_window=None,
        atomic_event_ids=[],
        atomic_not_applicable_reason="unit-lane pure oracle has no T07 atomic caller",
        assertion_count=assertion_count,
        result="passed",
    )
