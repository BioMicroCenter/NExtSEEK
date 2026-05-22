"""Fix #1 -- original_row_index is assigned on the direct-rows path.

Root cause A: rows passed programmatically bypass CONVERT (the only writer of
original_row_index), so per-row error attribution silently failed for the JSON
path. _run_pre_insert_stages must now backfill the index.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from nextseek_api.batch_upload.models import ValidationResult
from nextseek_api.batch_upload.orchestrator import _run_pre_insert_stages
from nextseek_api.batch_upload.prefetch import clear_caches as _clear_prefetch_caches
from nextseek_api.batch_upload.validation import run_validation_multi
from nextseek_api.batch_upload.tests.test_orchestrator_levels import (
    FakeConnection,
    FakeDB,
)


@pytest.fixture(autouse=True)
def _isolate_prefetch_caches():
    _clear_prefetch_caches()
    yield
    _clear_prefetch_caches()


@contextmanager
def _fake_conn(db):
    yield FakeConnection(db)


# Distinct UIDs so UID_GEN neither generates nor dedups -- the pipeline keeps
# all rows, in order, and pre.valid_rows is a faithful readout. merge_files is
# intentionally NOT patched: the rows= branch of _run_pre_insert_stages never
# reaches it (the file/CONVERT else-branch is dead when rows is not None).
def test_pre_insert_assigns_index_on_rows_path():
    """rows= path with no original_row_index -> backfilled 0,1,2."""
    rows = [
        {"UID": "NHP-260101TST-1", "SampleType": "NHP_blood", "json_metadata": "{}", "assay_ids": []},
        {"UID": "NHP-260101TST-2", "SampleType": "NHP_blood", "json_metadata": "{}", "assay_ids": []},
        {"UID": "NHP-260101TST-3", "SampleType": "NHP_blood", "json_metadata": "{}", "assay_ids": []},
    ]
    # Precondition: no row carries an original_row_index -- the fix must add it.
    assert all("original_row_index" not in r for r in rows)
    with patch("nextseek_api.batch_upload.orchestrator.get_connection",
               lambda: _fake_conn(FakeDB())):
        pre = _run_pre_insert_stages(
            xlsx_paths=[], project_id=1, contributor_id=1,
            rows=rows, run_name_check=False, run_dag=False,
            mutate_project_links=False,
        )
    assert [r.original_row_index for r in pre.valid_rows] == [0, 1, 2]


def test_pre_insert_preserves_client_supplied_index():
    """A client-supplied original_row_index is not overwritten."""
    rows = [
        {"UID": "NHP-260101TST-1", "SampleType": "NHP_blood", "json_metadata": "{}",
         "assay_ids": [], "original_row_index": 99},
        {"UID": "NHP-260101TST-2", "SampleType": "NHP_blood", "json_metadata": "{}",
         "assay_ids": []},
    ]
    with patch("nextseek_api.batch_upload.orchestrator.get_connection",
               lambda: _fake_conn(FakeDB())):
        pre = _run_pre_insert_stages(
            xlsx_paths=[], project_id=1, contributor_id=1,
            rows=rows, run_name_check=False, run_dag=False,
            mutate_project_links=False,
        )
    # index 0 keeps its supplied 99; index 1 (None) is backfilled to its position 1.
    assert [r.original_row_index for r in pre.valid_rows] == [99, 1]


def test_validate_json_path_attributes_error_to_real_row():
    """run_validation_multi(rows=...) with the bad row in the MIDDLE -> error.row
    is its true array index (1 of 3), not None and not a 'first/last/count'
    coincidence. This is the PR #7 hole the fix closes."""
    rows = [
        {"UID": "NHP-260101TST-1", "SampleType": "NHP_blood",
         "json_metadata": "{}", "assay_ids": []},
        {"UID": "NHP-260101TST-2", "SampleType": "NHP_blood",
         "json_metadata": '{"BadKey":"x"}', "assay_ids": []},   # bad row, index 1 of 3
        {"UID": "NHP-260101TST-3", "SampleType": "NHP_blood",
         "json_metadata": "{}", "assay_ids": []},
    ]
    with patch("nextseek_api.batch_upload.orchestrator.get_connection",
               lambda: _fake_conn(FakeDB())):
        result = run_validation_multi(
            xlsx_paths=[], project_id=1, contributor_id=1, lababbv="TST",
            checks=frozenset({"structure"}), rows=rows,
        )
    assert isinstance(result, ValidationResult)
    assert result.valid is False
    attr_errors = [e for e in result.errors if e.type == "VALIDATION_ATTRIBUTE_NAME"]
    assert len(attr_errors) == 1
    assert attr_errors[0].row == 1            # was None before the fix


def test_original_row_index_survives_uid_gen_dedup_drop():
    """The hard case: original_row_index is a PERMANENT stamp, not a value
    recomputed from post-pipeline position. Three null-UID rows where rows 0 and
    1 share a Name -> UID_GEN's _deduplicate_rows drops row 1. The two survivors
    must report their ORIGINAL input positions [0, 2] (non-contiguous), which a
    recompute-from-position bug ([0, 1]) and the no-fix case ([None, None])
    both fail. Exercises the real new-sample path (null UID -> generated UID)."""
    rows = [
        {"UID": None, "SampleType": "NHP_blood", "json_metadata": '{"Name":"dup-a"}', "assay_ids": []},
        {"UID": None, "SampleType": "NHP_blood", "json_metadata": '{"Name":"dup-a"}', "assay_ids": []},
        {"UID": None, "SampleType": "NHP_blood", "json_metadata": '{"Name":"keep-b"}', "assay_ids": []},
    ]
    # UID_GEN injects the generated UID into json_metadata as a "UID" key, so the
    # attribute-name check needs both "UID" and "Name" declared for sample_type 1.
    db = FakeDB(sample_attributes={1: {"Parent", "UID", "Name"}})
    with patch("nextseek_api.batch_upload.orchestrator.get_connection",
               lambda: _fake_conn(db)):
        pre = _run_pre_insert_stages(
            xlsx_paths=[], project_id=1, contributor_id=1, lababbv="TST",
            rows=rows, run_name_check=False, run_dag=False,
            mutate_project_links=False,
        )
    assert [r.original_row_index for r in pre.valid_rows] == [0, 2]
