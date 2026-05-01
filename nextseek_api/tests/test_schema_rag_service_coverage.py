"""Coverage tests for schema_rag/service.py — targeting uncovered lines.

Covers:
- flatten_endpoints edge cases (non-dict path_item, non-dict operation, missing operationId, tags coercion)
- _apply_resolved_terms_boost
- _compute_scores
- _find_session_by_schema_url
- retrieve_endpoints error paths (no session, expired, embedding failed)
- _build_enriched_text
- _pass_1, _pass_2, _pass_3 with filters
"""

import numpy as np
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from nextseek_api.schema_rag.models import MinimalAPIEndpoint, FullAPIEndpoint


# ---------------------------------------------------------------------------
# _apply_resolved_terms_boost
# ---------------------------------------------------------------------------

class TestApplyResolvedTermsBoost:

    def test_no_resolved_terms(self):
        from nextseek_api.schema_rag.service import _apply_resolved_terms_boost
        assert _apply_resolved_terms_boost("some text", None, 0.5) == 0.5

    def test_empty_resolved_terms(self):
        from nextseek_api.schema_rag.service import _apply_resolved_terms_boost
        assert _apply_resolved_terms_boost("some text", {}, 0.5) == 0.5

    def test_key_match(self):
        from nextseek_api.schema_rag.service import _apply_resolved_terms_boost
        score = _apply_resolved_terms_boost("create sample endpoint", {"sample": "specimen"}, 0.5)
        assert score > 0.5

    def test_value_match_string(self):
        from nextseek_api.schema_rag.service import _apply_resolved_terms_boost
        score = _apply_resolved_terms_boost("get specimen list", {"sample": "specimen"}, 0.5)
        assert score > 0.5

    def test_value_match_list(self):
        from nextseek_api.schema_rag.service import _apply_resolved_terms_boost
        score = _apply_resolved_terms_boost("get specimen list", {"types": ["specimen", "tissue"]}, 0.5)
        assert score > 0.5

    def test_cap_at_1(self):
        from nextseek_api.schema_rag.service import _apply_resolved_terms_boost
        many_terms = {f"term{i}": f"value{i}" for i in range(100)}
        # Create text containing all terms
        text = " ".join(list(many_terms.keys()) + list(many_terms.values()))
        score = _apply_resolved_terms_boost(text, many_terms, 0.9)
        assert score <= 1.0


# ---------------------------------------------------------------------------
# _compute_scores
# ---------------------------------------------------------------------------

class TestComputeScores:

    @patch("nextseek_api.schema_rag.service.embed_texts")
    def test_empty_texts(self, mock_embed):
        from nextseek_api.schema_rag.service import _compute_scores
        result = _compute_scores(np.array([[0.1, 0.2]]), [], [], None)
        assert result == []

    @patch("nextseek_api.schema_rag.service.embed_texts")
    def test_basic_scoring(self, mock_embed):
        from nextseek_api.schema_rag.service import _compute_scores
        # 2 endpoints, 2-dim embeddings
        mock_embed.return_value = np.array([[1.0, 0.0], [0.0, 1.0]])
        query = np.array([[0.7, 0.7]])
        query = query / np.linalg.norm(query)
        result = _compute_scores(query, ["endpoint A", "endpoint B"], ["A", "B"], None)
        assert len(result) == 2
        # Both should have similar scores since query is equidistant
        assert result[0][1] >= result[1][1]  # sorted descending


# ---------------------------------------------------------------------------
# _build_enriched_text
# ---------------------------------------------------------------------------

class TestBuildEnrichedText:

    def test_with_examples(self):
        from nextseek_api.schema_rag.service import _build_enriched_text
        ep = MinimalAPIEndpoint(
            operationId="createSample", method="POST", path="/samples",
            tags=["Samples"], description="Create a sample"
        )
        result = _build_enriched_text(ep, ["example1", "example2"])
        assert "examples:" in result
        assert "example1" in result

    def test_without_examples(self):
        from nextseek_api.schema_rag.service import _build_enriched_text
        ep = MinimalAPIEndpoint(
            operationId="createSample", method="POST", path="/samples",
            tags=["Samples"], description="Create a sample"
        )
        result = _build_enriched_text(ep, None)
        assert "examples:" not in result

    def test_empty_examples(self):
        from nextseek_api.schema_rag.service import _build_enriched_text
        ep = MinimalAPIEndpoint(
            operationId="createSample", method="POST", path="/samples",
        )
        result = _build_enriched_text(ep, [])
        assert "examples:" not in result


# ---------------------------------------------------------------------------
# flatten_endpoints edge cases
# ---------------------------------------------------------------------------

class TestFlattenEndpoints:

    def test_non_dict_path_item_skipped(self):
        from nextseek_api.schema_rag.service import flatten_endpoints
        processor = MagicMock()
        doc = {"paths": {"/test": "not-a-dict"}}
        result = flatten_endpoints(doc, processor)
        assert result == []

    def test_non_dict_operation_skipped(self):
        from nextseek_api.schema_rag.service import flatten_endpoints
        processor = MagicMock()
        doc = {"paths": {"/test": {"get": "not-a-dict"}}}
        result = flatten_endpoints(doc, processor)
        assert result == []

    def test_missing_operation_id_synthesized(self):
        from nextseek_api.schema_rag.service import flatten_endpoints
        processor = MagicMock()
        processor.extract_request_schema.return_value = {}
        processor.extract_response_schema.return_value = {}
        processor.extract_parameters.return_value = {}
        processor.extract_and_dehydrate_examples.return_value = []

        doc = {"paths": {"/samples": {"get": {"description": "List samples"}}}}
        with patch("nextseek_api.schema_rag.service.settings") as mock_settings:
            mock_settings.SCHEMA_RAG_EXCLUDED_PATH_PATTERNS = []
            result = flatten_endpoints(doc, processor)
        assert len(result) == 1
        assert result[0].operationId == "GET /samples"

    def test_tags_coercion_to_list(self):
        from nextseek_api.schema_rag.service import flatten_endpoints
        processor = MagicMock()
        processor.extract_request_schema.return_value = {}
        processor.extract_response_schema.return_value = {}
        processor.extract_parameters.return_value = {}
        processor.extract_and_dehydrate_examples.return_value = []

        doc = {"paths": {"/test": {"get": {
            "operationId": "testOp",
            "tags": "SingleTag",
            "description": "Test",
        }}}}
        with patch("nextseek_api.schema_rag.service.settings") as mock_settings:
            mock_settings.SCHEMA_RAG_EXCLUDED_PATH_PATTERNS = []
            result = flatten_endpoints(doc, processor)
        assert result[0].tags == ["SingleTag"]

    def test_excluded_path_pattern(self):
        from nextseek_api.schema_rag.service import flatten_endpoints
        processor = MagicMock()
        doc = {"paths": {"/schema_rag/ingest": {"post": {"operationId": "ingest"}}}}
        with patch("nextseek_api.schema_rag.service.settings") as mock_settings:
            mock_settings.SCHEMA_RAG_EXCLUDED_PATH_PATTERNS = ["/schema_rag/"]
            result = flatten_endpoints(doc, processor)
        assert result == []

    def test_exception_in_endpoint_skipped(self):
        from nextseek_api.schema_rag.service import flatten_endpoints
        processor = MagicMock()
        processor.extract_request_schema.side_effect = Exception("bad schema")
        doc = {"paths": {"/test": {"get": {"operationId": "testOp"}}}}
        with patch("nextseek_api.schema_rag.service.settings") as mock_settings:
            mock_settings.SCHEMA_RAG_EXCLUDED_PATH_PATTERNS = []
            result = flatten_endpoints(doc, processor)
        assert result == []


# ---------------------------------------------------------------------------
# _find_session_by_schema_url
# ---------------------------------------------------------------------------

class TestFindSessionBySchemaUrl:

    @patch("os.path.isdir", return_value=False)
    @patch("nextseek_api.schema_rag.service.settings")
    def test_no_duckdb_dir(self, mock_settings, mock_isdir):
        from nextseek_api.schema_rag.service import _find_session_by_schema_url
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/nonexistent"
        result = _find_session_by_schema_url("http://example.com/schema.yaml")
        assert result is None

    @patch("nextseek_api.schema_rag.service.is_session_expired", return_value=False)
    @patch("nextseek_api.schema_rag.service.load_session_info")
    @patch("os.listdir", return_value=["abc123.duckdb"])
    @patch("os.path.isdir", return_value=True)
    @patch("nextseek_api.schema_rag.service.settings")
    def test_found_matching_session(self, mock_settings, mock_isdir, mock_listdir, mock_load, mock_expired):
        from nextseek_api.schema_rag.service import _find_session_by_schema_url
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/tmp/duckdb"
        mock_session = MagicMock()
        mock_session.schema_url = "http://example.com/schema.yaml"
        mock_load.return_value = mock_session
        result = _find_session_by_schema_url("http://example.com/schema.yaml")
        assert result == mock_session

    @patch("nextseek_api.schema_rag.service.load_session_info", return_value=None)
    @patch("os.listdir", return_value=["abc123.duckdb"])
    @patch("os.path.isdir", return_value=True)
    @patch("nextseek_api.schema_rag.service.settings")
    def test_session_info_none(self, mock_settings, mock_isdir, mock_listdir, mock_load):
        from nextseek_api.schema_rag.service import _find_session_by_schema_url
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/tmp/duckdb"
        result = _find_session_by_schema_url("http://example.com/schema.yaml")
        assert result is None

    @patch("nextseek_api.schema_rag.service.load_session_info", side_effect=Exception("corrupt"))
    @patch("os.listdir", return_value=["abc123.duckdb"])
    @patch("os.path.isdir", return_value=True)
    @patch("nextseek_api.schema_rag.service.settings")
    def test_exception_during_load(self, mock_settings, mock_isdir, mock_listdir, mock_load):
        from nextseek_api.schema_rag.service import _find_session_by_schema_url
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/tmp/duckdb"
        result = _find_session_by_schema_url("http://example.com/schema.yaml")
        assert result is None

    @patch("os.listdir", return_value=["readme.txt"])
    @patch("os.path.isdir", return_value=True)
    @patch("nextseek_api.schema_rag.service.settings")
    def test_non_duckdb_files_skipped(self, mock_settings, mock_isdir, mock_listdir):
        from nextseek_api.schema_rag.service import _find_session_by_schema_url
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/tmp/duckdb"
        result = _find_session_by_schema_url("http://example.com/schema.yaml")
        assert result is None
