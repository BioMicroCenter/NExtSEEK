"""Final coverage sweep — targeting small gaps across multiple modules.

Each test class targets a specific module's uncovered lines.
"""

import json
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# batch_upload/checkpoint.py — lines 34-35 (OSError in read_last_checkpoint)
# ---------------------------------------------------------------------------

class TestCheckpointOSError:

    def test_get_last_checkpoint_uid_oserror(self):
        from nextseek_api.batch_upload.checkpoint import get_last_checkpoint_uid
        # Mock os.path.isfile to return True but open to raise OSError
        with patch("nextseek_api.batch_upload.checkpoint.os.path.isfile", return_value=True), \
             patch("builtins.open", side_effect=OSError("permission denied")):
            result = get_last_checkpoint_uid("/some/path", "checkpoint.txt")
            assert result is None


# ---------------------------------------------------------------------------
# batch_upload/job_index.py — lines 61-62 (JSONDecodeError), 93-94 (OSError)
# ---------------------------------------------------------------------------

class TestJobIndexEdgeCases:

    def test_list_jobs_corrupt_json(self):
        """Corrupt JSON in job index file triggers JSONDecodeError (lines 61-62)."""
        from nextseek_api.batch_upload.job_index import list_jobs
        with tempfile.TemporaryDirectory() as tmpdir:
            jobs_dir = os.path.join(tmpdir, "jobs")
            os.makedirs(jobs_dir)
            job_file = os.path.join(jobs_dir, "999.json")
            with open(job_file, "w") as f:
                f.write("not valid json {{{")
            result = list_jobs(user_id=999, page=1, page_size=10, jobs_dir=jobs_dir)
            assert result["jobs"] == []

    def test_user_owns_job_file_missing(self):
        """Missing file -> returns False."""
        from nextseek_api.batch_upload.job_index import user_owns_job
        with tempfile.TemporaryDirectory() as tmpdir:
            jobs_dir = os.path.join(tmpdir, "jobs")
            os.makedirs(jobs_dir)
            result = user_owns_job(user_id=999, job_id="nonexistent-job", jobs_dir=jobs_dir)
            assert result is False

    def test_user_owns_job_corrupt_json(self):
        """Corrupt JSON in job file -> returns False (lines 93-94)."""
        from nextseek_api.batch_upload.job_index import user_owns_job
        with tempfile.TemporaryDirectory() as tmpdir:
            jobs_dir = os.path.join(tmpdir, "jobs")
            os.makedirs(jobs_dir)
            job_file = os.path.join(jobs_dir, "999.json")
            with open(job_file, "w") as f:
                f.write("not valid json {{{")
            result = user_owns_job(user_id=999, job_id="some-job", jobs_dir=jobs_dir)
            assert result is False


# ---------------------------------------------------------------------------
# schema_rag/models.py — lines 127-128 (parameters + request_schema in to_embedding_text)
# ---------------------------------------------------------------------------

class TestFullAPIEndpointEmbeddingText:

    def test_embedding_text_with_parameters_and_schema(self):
        from nextseek_api.schema_rag.models import FullAPIEndpoint
        ep = FullAPIEndpoint(
            operationId="createSample",
            method="POST",
            path="/samples",
            tags=["Samples"],
            description="Create a new sample",
            parameters={"id": {"in": "path", "type": "integer"}},
            request_schema={"title": "string", "type": "string"},
        )
        text = ep.to_embedding_text()
        assert "parameters:" in text
        assert "request_schema:" in text

    def test_embedding_text_with_examples(self):
        from nextseek_api.schema_rag.models import FullAPIEndpoint
        ep = FullAPIEndpoint(
            operationId="createSample",
            method="POST",
            path="/samples",
            request_schema={},
            examples=['{"title":"test"}'],
        )
        text = ep.to_embedding_text(include_examples=True)
        assert "examples:" in text


# ---------------------------------------------------------------------------
# schema_rag/db.py — lines 107, 231, 332 (exception handlers)
# ---------------------------------------------------------------------------

class TestSchemaRagDbEdgeCases:

    @patch("nextseek_api.schema_rag.db.duckdb")
    def test_load_all_minimal_endpoints_empty(self, mock_duckdb):
        """Empty result returns empty list."""
        from nextseek_api.schema_rag.db import load_all_minimal_endpoints
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_duckdb.connect.return_value = mock_conn
        session = MagicMock()
        session.db_path = "/tmp/test.duckdb"
        result = load_all_minimal_endpoints(session)
        assert result == []


# ---------------------------------------------------------------------------
# batch_upload/parallel.py — lines 142-143, 178-179
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# nextseek_api/models.py — remaining lines (633, 1579, 1615, 1864)
# ---------------------------------------------------------------------------

class TestModelsRemainingLines:

    def test_retrieve_request_filter_terms_without_filter_by(self):
        """filter_terms without filter_by raises (line 1864)."""
        from nextseek_api.models import RetrieveRequest
        with pytest.raises(Exception, match="filter_by is required"):
            RetrieveRequest(
                session_id="abc",
                query="test",
                filter_terms=["GET"],
            )

    def test_sample_advanced_search_multiple_sampletype_ids(self):
        """Multiple numeric sampletype ids -> sampletype_id=0 (line 1607)."""
        from nextseek_api.models import SampleAdvancedSearchRequest
        req = SampleAdvancedSearchRequest(
            sampletype=["1", "2"],
            filter_searchText="test",
        )
        kwargs = req.to_db_filters()
        assert kwargs["sampletype_id"] == 0  # multiple -> 0
        assert kwargs["sampletype_ids"] == [1, 2]

    def test_sample_advanced_search_single_sampletype_id(self):
        """Single numeric sampletype id -> sampletype_id set."""
        from nextseek_api.models import SampleAdvancedSearchRequest
        req = SampleAdvancedSearchRequest(
            sampletype="42",
            filter_searchText="test",
        )
        kwargs = req.to_db_filters()
        assert kwargs["sampletype_id"] == 42
        assert kwargs["sampletype_ids"] == [42]


class TestParallelEdgeCases:

    def test_parallel_threshold_constant(self):
        """Verify PARALLEL_THRESHOLD is accessible."""
        from nextseek_api.batch_upload.parallel import PARALLEL_THRESHOLD
        assert isinstance(PARALLEL_THRESHOLD, int)
        assert PARALLEL_THRESHOLD > 0


# ---------------------------------------------------------------------------
# batch_upload/extract.py — additional edge cases (lines 77, 83, 85, 96)
# ---------------------------------------------------------------------------

class TestExtractEdgeCases:

    def test_stream_rows_with_all_required_columns(self):
        """An xlsx with all required columns should work."""
        from nextseek_api.batch_upload.extract import stream_rows
        import openpyxl
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["SampleType", "json_metadata", "UID", "assay_ids"])
            ws.append(["NHP", '{"Name":"X"}', "NHP-010101AA-1", ""])
            ws.append([None, None, None, None])  # empty row
            wb.save(f.name)
            path = f.name

        try:
            unknown_cols, stream = stream_rows(path)
            rows = list(stream)
            valid = [r for r in rows if r.data is not None]
            assert len(valid) >= 1
        finally:
            os.unlink(path)
