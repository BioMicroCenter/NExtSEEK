"""Tests for Stage 1.5: UID_GEN — UID generation and parent resolution."""
import json
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.errors import ErrorCollector
from nextseek_api.batch_upload.models import InputRowModel
from nextseek_api.batch_upload.uid_gen import (
    _build_identity_to_uid_map,
    _bulk_resolve_from_db,
    _compute_uid_prefix,
    _deduplicate_rows,
    _extract_identity,
    _inject_uid_into_metadata,
    _resolve_parents,
    generate_uids,
    run_uid_gen,
)


def _make_row(uid=None, sample_type="NHP", meta=None, assay_ids=None):
    """Helper to create InputRowModel with sensible defaults."""
    if meta is None:
        meta = '{"Name":"test"}'
    return InputRowModel(
        UID=uid,
        SampleType=sample_type,
        json_metadata=meta,
        assay_ids=assay_ids or [],
    )


# ── _compute_uid_prefix ─────────────────────────────────────────────────────


class TestComputeUidPrefix:
    def test_simple_type(self):
        assert _compute_uid_prefix("NHP") == "NHP"

    def test_type_with_underscore(self):
        assert _compute_uid_prefix("NHP_blood") == "NHP"

    def test_file_type_with_underscore(self):
        assert _compute_uid_prefix("D.IMG_files") == "D.IMG"

    def test_no_underscore(self):
        assert _compute_uid_prefix("A.GEX") == "A.GEX"

    def test_multiple_underscores(self):
        assert _compute_uid_prefix("NHP_blood_sample") == "NHP"


# ── _extract_identity ────────────────────────────────────────────────────────


class TestExtractIdentity:
    def test_name_based(self):
        row = _make_row(sample_type="NHP", meta='{"Name":"Sample_A"}')
        assert _extract_identity(row) == "Sample_A"

    def test_file_based(self):
        row = _make_row(sample_type="D.IMG_files", meta='{"File_PrimaryData":"data.fastq"}')
        assert _extract_identity(row) == "data.fastq"

    def test_file_based_typo(self):
        row = _make_row(sample_type="D.IMG", meta='{"File_PrimartyData":"data.fastq"}')
        assert _extract_identity(row) == "data.fastq"

    def test_file_based_forward(self):
        row = _make_row(sample_type="A.GEX", meta='{"File_PrimaryData_Forward":"fwd.fastq"}')
        assert _extract_identity(row) == "fwd.fastq"

    def test_no_identity(self):
        row = _make_row(sample_type="NHP", meta='{"Protocol":"something"}')
        assert _extract_identity(row) is None

    def test_empty_name(self):
        row = _make_row(sample_type="NHP", meta='{"Name":""}')
        assert _extract_identity(row) is None

    def test_empty_metadata(self):
        row = _make_row(sample_type="NHP", meta='{}')
        assert _extract_identity(row) is None


# ── _deduplicate_rows ────────────────────────────────────────────────────────


class TestDeduplicateRows:
    def test_no_duplicates(self):
        rows = [
            _make_row(meta='{"Name":"A"}'),
            _make_row(meta='{"Name":"B"}'),
        ]
        result, warnings = _deduplicate_rows(rows)
        assert len(result) == 2
        assert len(warnings) == 0

    def test_duplicate_removed(self):
        rows = [
            _make_row(meta='{"Name":"A"}'),
            _make_row(meta='{"Name":"A"}'),
        ]
        result, warnings = _deduplicate_rows(rows)
        assert len(result) == 1
        assert len(warnings) == 1
        assert "Duplicate" in warnings[0]

    def test_rows_with_uid_not_deduped(self):
        """Rows that already have UIDs should not be deduplicated."""
        rows = [
            _make_row(uid="NHP-260225MIT-1", meta='{"Name":"A"}'),
            _make_row(uid="NHP-260225MIT-2", meta='{"Name":"A"}'),
        ]
        result, warnings = _deduplicate_rows(rows)
        assert len(result) == 2
        assert len(warnings) == 0

    def test_mixed_uid_and_no_uid(self):
        rows = [
            _make_row(uid="NHP-260225MIT-1", meta='{"Name":"A"}'),
            _make_row(meta='{"Name":"A"}'),  # no UID, same name
            _make_row(meta='{"Name":"A"}'),  # duplicate
        ]
        result, warnings = _deduplicate_rows(rows)
        assert len(result) == 2  # row with UID + first no-UID
        assert len(warnings) == 1


# ── generate_uids (with mock DB) ────────────────────────────────────────────


class TestGenerateUids:
    def _mock_conn(self, max_index=0):
        """Create a mock connection that returns max_index from queries."""
        conn = MagicMock()

        def execute_side_effect(stmt, params=None):
            sql_str = str(stmt) if not hasattr(stmt, "text") else stmt.text
            if "GET_LOCK" in sql_str:
                result = MagicMock()
                result.scalar.return_value = 1
                return result
            elif "RELEASE_LOCK" in sql_str:
                return MagicMock()
            elif "COALESCE" in sql_str and "MAX" in sql_str:
                # Optimized _query_max_index returns a scalar
                result = MagicMock()
                result.scalar.return_value = max_index
                return result
            return MagicMock()

        conn.execute.side_effect = execute_side_effect
        return conn

    def test_generate_uids_basic(self):
        rows = [
            _make_row(meta='{"Name":"A"}'),
            _make_row(meta='{"Name":"B"}'),
        ]
        conn = self._mock_conn(max_index=0)
        rows, count = generate_uids(rows, "MIT", conn)
        assert count == 2
        assert rows[0].UID is not None
        assert rows[1].UID is not None
        assert rows[0].UID.startswith("NHP-")
        assert rows[0].UID.endswith("MIT-1")
        assert rows[1].UID.endswith("MIT-2")

    def test_uid_format_yymmdd(self):
        """Generated UID should use YYMMDD date format (6 digits, not 8)."""
        import re
        rows = [_make_row(meta='{"Name":"A"}')]
        conn = self._mock_conn(max_index=0)
        rows, count = generate_uids(rows, "MIT", conn)
        uid = rows[0].UID
        # Format: {PREFIX}-{YYMMDD}{LABABBV}-{INDEX}
        # e.g., NHP-260225MIT-1
        match = re.match(r"^[A-Z.]+\-(\d{6})[A-Z]+-\d+$", uid)
        assert match is not None, f"UID '{uid}' does not match expected YYMMDD format"
        date_part = match.group(1)
        assert len(date_part) == 6, f"Date portion '{date_part}' is not 6 digits"

    def test_generate_uids_with_existing(self):
        """Should start after max existing index."""
        rows = [_make_row(meta='{"Name":"A"}')]
        conn = self._mock_conn(max_index=5)
        rows, count = generate_uids(rows, "MIT", conn)
        assert count == 1
        assert rows[0].UID.endswith("MIT-6")

    def test_skip_rows_with_uid(self):
        rows = [
            _make_row(uid="NHP-260225MIT-1", meta='{"Name":"A"}'),
            _make_row(meta='{"Name":"B"}'),
        ]
        conn = self._mock_conn(max_index=0)
        rows, count = generate_uids(rows, "MIT", conn)
        assert count == 1
        assert rows[0].UID == "NHP-260225MIT-1"  # unchanged
        assert rows[1].UID is not None

    def test_multiple_prefixes(self):
        rows = [
            _make_row(sample_type="NHP", meta='{"Name":"A"}'),
            _make_row(sample_type="D.IMG_files", meta='{"File_PrimaryData":"x.fastq"}'),
        ]
        conn = self._mock_conn(max_index=0)
        rows, count = generate_uids(rows, "MIT", conn)
        assert count == 2
        assert "NHP-" in rows[0].UID
        assert "D.IMG-" in rows[1].UID

    def test_advisory_lock_released_on_error(self):
        """Advisory lock should be released even if an error occurs."""
        conn = MagicMock()
        call_log = []

        def execute_side_effect(stmt, params=None):
            sql_str = str(stmt) if not hasattr(stmt, "text") else stmt.text
            call_log.append(sql_str)
            if "GET_LOCK" in sql_str:
                result = MagicMock()
                result.scalar.return_value = 1
                return result
            elif "COALESCE" in sql_str and "MAX" in sql_str:
                raise RuntimeError("DB error")
            elif "RELEASE_LOCK" in sql_str:
                return MagicMock()
            return MagicMock()

        conn.execute.side_effect = execute_side_effect

        rows = [_make_row(meta='{"Name":"A"}')]
        with pytest.raises(RuntimeError, match="DB error"):
            generate_uids(rows, "MIT", conn)

        # Verify RELEASE_LOCK was called
        assert any("RELEASE_LOCK" in c for c in call_log)

    def test_lock_acquisition_failure(self):
        conn = MagicMock()

        def execute_side_effect(stmt, params=None):
            sql_str = str(stmt) if not hasattr(stmt, "text") else stmt.text
            if "GET_LOCK" in sql_str:
                result = MagicMock()
                result.scalar.return_value = 0  # lock failed
                return result
            elif "RELEASE_LOCK" in sql_str:
                return MagicMock()
            return MagicMock()

        conn.execute.side_effect = execute_side_effect

        rows = [_make_row(meta='{"Name":"A"}')]
        with pytest.raises(RuntimeError, match="Could not acquire"):
            generate_uids(rows, "MIT", conn)


# ── _build_identity_to_uid_map ──────────────────────────────────────────────


class TestBuildIdentityToUidMap:
    def test_basic_mapping(self):
        rows = [
            _make_row(uid="NHP-260225MIT-1", meta='{"Name":"Sample_A"}'),
            _make_row(uid="D.IMG-260225MIT-1", sample_type="D.IMG", meta='{"File_PrimaryData":"data.fastq"}'),
        ]
        mapping = _build_identity_to_uid_map(rows)
        assert mapping["Sample_A"] == "NHP-260225MIT-1"
        assert mapping["data.fastq"] == "D.IMG-260225MIT-1"

    def test_first_wins_on_collision(self):
        rows = [
            _make_row(uid="NHP-260225MIT-1", meta='{"Name":"A"}'),
            _make_row(uid="NHP-260225MIT-2", meta='{"Name":"A"}'),
        ]
        mapping = _build_identity_to_uid_map(rows)
        assert mapping["A"] == "NHP-260225MIT-1"


# ── _resolve_parents ────────────────────────────────────────────────────────


class TestResolveParents:
    def _mock_conn(self, db_results=None):
        """Mock conn where SELECT uuid, title returns db_results."""
        conn = MagicMock()
        result = MagicMock()
        result.__iter__ = lambda self: iter(db_results or [])
        conn.execute.return_value = result
        return conn

    def test_uid_parent_kept(self):
        rows = [
            _make_row(uid="NHP-260225MIT-2", meta='{"Parent":"NHP-260225MIT-1"}'),
        ]
        identity_map = {}
        conn = self._mock_conn()
        rows, warnings, _count = _resolve_parents(rows, identity_map, conn)
        meta = json.loads(rows[0].json_metadata)
        assert meta["Parent"] == "NHP-260225MIT-1"

    def test_name_resolved_to_uid(self):
        rows = [
            _make_row(uid="NHP-260225MIT-2", meta='{"Parent":"Sample_A"}'),
        ]
        identity_map = {"Sample_A": "NHP-260225MIT-1"}
        conn = self._mock_conn()
        rows, warnings, _count = _resolve_parents(rows, identity_map, conn)
        meta = json.loads(rows[0].json_metadata)
        assert meta["Parent"] == "NHP-260225MIT-1"

    def test_mixed_parents(self):
        """Mix of UID and Name references in same Parent field."""
        rows = [
            _make_row(uid="NHP-260225MIT-3", meta='{"Parent":"NHP-260225MIT-1;Sample_A"}'),
        ]
        identity_map = {"Sample_A": "NHP-260225MIT-2"}
        conn = self._mock_conn()
        rows, warnings, _count = _resolve_parents(rows, identity_map, conn)
        meta = json.loads(rows[0].json_metadata)
        assert "NHP-260225MIT-1" in meta["Parent"]
        assert "NHP-260225MIT-2" in meta["Parent"]

    def test_db_fallback(self):
        """Unresolved names should fall back to DB lookup."""
        rows = [
            _make_row(uid="NHP-260225MIT-2", meta='{"Parent":"DB_Sample"}'),
        ]
        identity_map = {}
        conn = self._mock_conn(db_results=[("NHP-220630FLY-1", "DB_Sample")])
        rows, warnings, _count = _resolve_parents(rows, identity_map, conn)
        meta = json.loads(rows[0].json_metadata)
        assert meta["Parent"] == "NHP-220630FLY-1"

    def test_ambiguous_db_match(self):
        """Ambiguous DB matches should produce a warning."""
        rows = [
            _make_row(uid="NHP-260225MIT-2", meta='{"Parent":"Ambiguous"}'),
        ]
        identity_map = {}
        conn = self._mock_conn(db_results=[
            ("NHP-220630FLY-1", "Ambiguous"),
            ("NHP-220630FLY-2", "Ambiguous"),
        ])
        rows, warnings, _count = _resolve_parents(rows, identity_map, conn)
        assert any("Ambiguous" in w and "ambiguous" in w.lower() for w in warnings)

    def test_unresolvable_parent_warning(self):
        """Completely unresolvable parent should warn but not error."""
        rows = [
            _make_row(uid="NHP-260225MIT-2", meta='{"Parent":"Nonexistent"}'),
        ]
        identity_map = {}
        conn = self._mock_conn(db_results=[])  # nothing found
        rows, warnings, _count = _resolve_parents(rows, identity_map, conn)
        assert any("unresolvable" in w.lower() for w in warnings)

    def test_no_parent_field(self):
        """Rows without Parent field should pass through unchanged."""
        rows = [
            _make_row(uid="NHP-260225MIT-1", meta='{"Name":"test"}'),
        ]
        identity_map = {}
        conn = self._mock_conn()
        rows, warnings, _count = _resolve_parents(rows, identity_map, conn)
        assert len(warnings) == 0
        meta = json.loads(rows[0].json_metadata)
        assert "Parent" not in meta

    def test_parents_resolved_count(self):
        """parents_resolved should count name-to-UID and DB resolutions."""
        rows = [
            _make_row(uid="NHP-260225MIT-3", meta='{"Parent":"Sample_A;Sample_B"}'),
        ]
        identity_map = {"Sample_A": "NHP-260225MIT-1", "Sample_B": "NHP-260225MIT-2"}
        conn = self._mock_conn()
        rows, warnings, count = _resolve_parents(rows, identity_map, conn)
        assert count == 2
        assert len(warnings) == 0


# ── _inject_uid_into_metadata ───────────────────────────────────────────────


class TestInjectUidIntoMetadata:
    def test_injection(self):
        rows = [_make_row(uid="NHP-260225MIT-1", meta='{"Name":"A"}')]
        _inject_uid_into_metadata(rows, {"NHP-260225MIT-1"})
        meta = json.loads(rows[0].json_metadata)
        assert meta["UID"] == "NHP-260225MIT-1"

    def test_no_injection_for_existing_uid(self):
        rows = [_make_row(uid="NHP-260225MIT-1", meta='{"Name":"A"}')]
        _inject_uid_into_metadata(rows, set())  # not in generated set
        meta = json.loads(rows[0].json_metadata)
        assert "UID" not in meta


# ── run_uid_gen (integration) ───────────────────────────────────────────────


class TestRunUidGen:
    def _mock_conn(self, max_index=0):
        conn = MagicMock()

        def execute_side_effect(stmt, params=None):
            sql_str = str(stmt) if not hasattr(stmt, "text") else stmt.text
            if "GET_LOCK" in sql_str:
                result = MagicMock()
                result.scalar.return_value = 1
                return result
            elif "RELEASE_LOCK" in sql_str:
                return MagicMock()
            elif "COALESCE" in sql_str and "MAX" in sql_str:
                result = MagicMock()
                result.scalar.return_value = max_index
                return result
            elif "SELECT uuid, title FROM samples WHERE title IN" in sql_str:
                result = MagicMock()
                result.__iter__ = lambda self: iter([])
                return result
            return MagicMock()

        conn.execute.side_effect = execute_side_effect
        return conn

    def test_full_pipeline_mixed(self):
        """Test with mix of UID and no-UID rows."""
        rows = [
            _make_row(uid="NHP-260225MIT-1", meta='{"Name":"A"}'),
            _make_row(meta='{"Name":"B"}'),
            _make_row(meta='{"Name":"C","Parent":"A"}'),
        ]
        ec = ErrorCollector()
        conn = self._mock_conn()
        result_rows, report = run_uid_gen(rows, "MIT", conn, ec)

        assert report["uids_generated"] == 2
        assert report["duplicates_removed"] == 0
        assert all(r.UID is not None for r in result_rows)

    def test_all_rows_have_uid(self):
        """No-op when all rows already have UIDs."""
        rows = [
            _make_row(uid="NHP-260225MIT-1", meta='{"Name":"A"}'),
            _make_row(uid="NHP-260225MIT-2", meta='{"Name":"B"}'),
        ]
        ec = ErrorCollector()
        conn = self._mock_conn()
        result_rows, report = run_uid_gen(rows, "MIT", conn, ec)
        assert report["uids_generated"] == 0

    def test_all_rows_empty_uid(self):
        """All rows need UID generation."""
        rows = [
            _make_row(meta='{"Name":"A"}'),
            _make_row(meta='{"Name":"B"}'),
            _make_row(meta='{"Name":"C"}'),
        ]
        ec = ErrorCollector()
        conn = self._mock_conn()
        result_rows, report = run_uid_gen(rows, "MIT", conn, ec)
        assert report["uids_generated"] == 3
        assert all(r.UID is not None for r in result_rows)

    def test_single_row_empty_uid(self):
        rows = [_make_row(meta='{"Name":"Solo"}')]
        ec = ErrorCollector()
        conn = self._mock_conn()
        result_rows, report = run_uid_gen(rows, "MIT", conn, ec)
        assert report["uids_generated"] == 1
        assert result_rows[0].UID is not None

    def test_uid_injected_into_metadata(self):
        rows = [_make_row(meta='{"Name":"A"}')]
        ec = ErrorCollector()
        conn = self._mock_conn()
        result_rows, report = run_uid_gen(rows, "MIT", conn, ec)
        meta = json.loads(result_rows[0].json_metadata)
        assert "UID" in meta
        assert meta["UID"] == result_rows[0].UID

    def test_parent_resolution_in_batch(self):
        """Parent=Name should be resolved to UID within the batch."""
        rows = [
            _make_row(meta='{"Name":"Parent_Sample"}'),
            _make_row(meta='{"Name":"Child","Parent":"Parent_Sample"}'),
        ]
        ec = ErrorCollector()
        conn = self._mock_conn()
        result_rows, report = run_uid_gen(rows, "MIT", conn, ec)

        child_meta = json.loads(result_rows[1].json_metadata)
        parent_uid = result_rows[0].UID
        assert child_meta["Parent"] == parent_uid

    def test_deduplication_with_warnings(self):
        rows = [
            _make_row(meta='{"Name":"Dup"}'),
            _make_row(meta='{"Name":"Dup"}'),
            _make_row(meta='{"Name":"Unique"}'),
        ]
        ec = ErrorCollector()
        conn = self._mock_conn()
        result_rows, report = run_uid_gen(rows, "MIT", conn, ec)
        assert report["duplicates_removed"] == 1
        assert report["uids_generated"] == 2
        assert len(result_rows) == 2


# ── DAG regex tests for non-PUB UIDs ────────────────────────────────────────


class TestDagRegexFix:
    """Verify the DAG regex fix (T1) accepts non-PUB UIDs."""

    def test_non_pub_uid(self):
        from nextseek_api.batch_upload.dag import extract_parents
        # Clear LRU cache for clean test
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-6"}'
        result = extract_parents(meta)
        assert "NHP-260225MIT-6" in result

    def test_pub_uid_still_works(self):
        from nextseek_api.batch_upload.dag import extract_parents
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-6-PUB"}'
        result = extract_parents(meta)
        assert "NHP-260225MIT-6-PUB" in result

    def test_pub_with_number(self):
        from nextseek_api.batch_upload.dag import extract_parents
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MIT-6-PUB2"}'
        result = extract_parents(meta)
        assert "NHP-260225MIT-6-PUB2" in result

    def test_short_lab_abbreviation(self):
        from nextseek_api.batch_upload.dag import extract_parents
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MI-6"}'
        result = extract_parents(meta)
        assert "NHP-260225MI-6" in result

    def test_long_lab_abbreviation(self):
        from nextseek_api.batch_upload.dag import extract_parents
        extract_parents.cache_clear()
        meta = '{"Parent":"NHP-260225MITLL-6"}'
        result = extract_parents(meta)
        assert "NHP-260225MITLL-6" in result


# ── Model tests for Optional UID ────────────────────────────────────────────


class TestOptionalUid:
    def test_none_uid_validates(self):
        row = InputRowModel(
            UID=None,
            SampleType="NHP",
            json_metadata='{"Name":"test"}',
            assay_ids=[1],
        )
        assert row.UID is None
        assert row.SampleType == "NHP"

    def test_none_uid_with_meta_uid_ok(self):
        """When UID is None, json_metadata.UID should not cause validation error."""
        row = InputRowModel(
            UID=None,
            SampleType="NHP",
            json_metadata='{"Name":"test","UID":"NHP-260225MIT-1"}',
            assay_ids=[],
        )
        assert row.UID is None

    def test_provided_uid_still_validates(self):
        row = InputRowModel(
            UID="NHP-260225MIT-1",
            SampleType="NHP",
            json_metadata='{"Name":"test"}',
            assay_ids=[1],
        )
        assert row.UID == "NHP-260225MIT-1"

    def test_uid_mismatch_still_errors(self):
        """When UID is provided, mismatch with json_metadata.UID should error."""
        with pytest.raises(Exception):
            InputRowModel(
                UID="NHP-260225MIT-1",
                SampleType="NHP",
                json_metadata='{"UID":"NHP-260225MIT-DIFFERENT"}',
                assay_ids=[],
            )


# ── check_name_exists_in_db ─────────────────────────────────────────────────


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    return conn


class TestCheckNameExistsInDb:
    """Tests for the pre-UID-gen Name/File_PrimaryData idempotence check."""

    def test_name_match_skips_row(self, mock_conn):
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        rows = [
            InputRowModel(SampleType="NHP_blood", json_metadata='{"Name":"Blood Sample A"}'),
        ]
        mock_conn.execute.return_value.fetchall.return_value = [
            ("BLD-250101MIT-1", 42, "Blood Sample A"),
        ]
        remaining, matches, matched_rows = check_name_exists_in_db(rows, mock_conn)
        assert len(remaining) == 0
        assert len(matches) == 1
        assert matches["Blood Sample A"]["uid"] == "BLD-250101MIT-1"
        assert matches["Blood Sample A"]["sample_id"] == 42
        assert len(matched_rows) == 1
        assert matched_rows[0] is rows[0]

    def test_no_match_keeps_row(self, mock_conn):
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        rows = [
            InputRowModel(SampleType="NHP_blood", json_metadata='{"Name":"New Sample"}'),
        ]
        mock_conn.execute.return_value.fetchall.return_value = []
        remaining, matches, matched_rows = check_name_exists_in_db(rows, mock_conn)
        assert len(remaining) == 1
        assert len(matches) == 0

    def test_rows_with_uid_skip_check(self, mock_conn):
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        rows = [
            InputRowModel(UID="NHP-250101MIT-1", SampleType="NHP_blood", json_metadata='{"Name":"Existing"}'),
        ]
        remaining, matches, matched_rows = check_name_exists_in_db(rows, mock_conn)
        assert len(remaining) == 1
        assert len(matches) == 0
        mock_conn.execute.assert_not_called()

    def test_file_primarydata_match_for_d_type(self, mock_conn):
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        rows = [
            InputRowModel(SampleType="D.IMG_files", json_metadata='{"File_PrimaryData":"image001.tif"}'),
        ]
        mock_conn.execute.return_value.fetchall.return_value = [
            ("D.IMG-250101MIT-1", 99, "image001.tif"),
        ]
        remaining, matches, matched_rows = check_name_exists_in_db(rows, mock_conn)
        assert len(remaining) == 0
        assert "image001.tif" in matches

    def test_case_insensitive_match(self, mock_conn):
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        rows = [
            InputRowModel(SampleType="NHP_blood", json_metadata='{"Name":"blood SAMPLE a"}'),
        ]
        mock_conn.execute.return_value.fetchall.return_value = [
            ("BLD-250101MIT-1", 42, "Blood Sample A"),
        ]
        remaining, matches, matched_rows = check_name_exists_in_db(rows, mock_conn)
        assert len(remaining) == 0
        assert len(matches) == 1

    def test_mixed_rows_uid_and_no_uid(self, mock_conn):
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        rows = [
            InputRowModel(UID="NHP-250101MIT-1", SampleType="NHP_blood", json_metadata='{"Name":"Has UID"}'),
            InputRowModel(SampleType="NHP_blood", json_metadata='{"Name":"Exists In DB"}'),
            InputRowModel(SampleType="NHP_blood", json_metadata='{"Name":"Brand New"}'),
        ]
        mock_conn.execute.return_value.fetchall.return_value = [
            ("NHP-250101MIT-5", 55, "Exists In DB"),
        ]
        remaining, matches, matched_rows = check_name_exists_in_db(rows, mock_conn)
        assert len(remaining) == 2  # UID row + "Brand New"
        assert len(matches) == 1
        assert "Exists In DB" in matches

    def test_no_identity_rows_pass_through(self, mock_conn):
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        rows = [
            InputRowModel(SampleType="NHP_blood", json_metadata='{}'),  # no Name
        ]
        remaining, matches, matched_rows = check_name_exists_in_db(rows, mock_conn)
        assert len(remaining) == 1
        assert len(matches) == 0
        mock_conn.execute.assert_not_called()

    def test_empty_rows_list(self, mock_conn):
        from nextseek_api.batch_upload.uid_gen import check_name_exists_in_db
        remaining, matches, matched_rows = check_name_exists_in_db([], mock_conn)
        assert len(remaining) == 0
        assert len(matches) == 0
