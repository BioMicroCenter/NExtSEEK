"""
Tests for evaluator domain contract: Pydantic models and description constants.

Covers:
- EvaluatorLookup: source validation, field presence
- EvaluatorRunMeta: status values, optional fields
- EvaluatorRouting: path_mode taxonomy, execution_mode values
- EvaluatorRetryContext: retryable flag, retry_signals list
- EvaluatorRawPayloads: all-optional raw fields
- EvaluatorRetryContextResponse: full nested construction, round-trip
- RetryRequest: task_id vs session_id+bundle_id validation
- RetryResponse: all fields, credential_source values
- EvaluatorRunSummary: listing row model
- EvaluatorRunsListResponse: paginated wrapper
- Description constants: non-empty, follow format pattern
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from nextseek_api.assistant.models_evaluator import (
    EvaluatorLookup,
    EvaluatorRunMeta,
    EvaluatorRouting,
    EvaluatorRetryContext,
    EvaluatorRawPayloads,
    EvaluatorRetryContextResponse,
    RetryRequest,
    RetryResponse,
    EvaluatorRunSummary,
    EvaluatorRunsListResponse,
)
from nextseek_api.assistant.descriptions_evaluator import (
    EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC,
    EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC,
    EVALUATOR_RUNS_LIST_DESC,
    EVALUATOR_RETRY_EXECUTE_DESC,
)


# ---------------------------------------------------------------------------
# EvaluatorLookup
# ---------------------------------------------------------------------------

class TestEvaluatorLookup:

    def test_task_source(self):
        lookup = EvaluatorLookup(
            task_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            bundle_id=None,
            source="task",
        )
        assert lookup.source == "task"
        assert lookup.bundle_id is None

    def test_bundle_source(self):
        lookup = EvaluatorLookup(
            task_id=None,
            session_id=uuid.uuid4(),
            bundle_id=42,
            source="bundle",
        )
        assert lookup.source == "bundle"
        assert lookup.task_id is None

    def test_invalid_source_rejected(self):
        with pytest.raises(ValidationError):
            EvaluatorLookup(
                task_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                bundle_id=None,
                source="invalid",
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            EvaluatorLookup(
                task_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                bundle_id=None,
                source="task",
                extra_field="bad",
            )


# ---------------------------------------------------------------------------
# EvaluatorRunMeta
# ---------------------------------------------------------------------------

class TestEvaluatorRunMeta:

    def test_completed_run(self):
        now = datetime.now(timezone.utc)
        meta = EvaluatorRunMeta(
            status="completed",
            query="Find mice",
            reply="Found 5 mice samples",
            created_at=now,
            user_id=1,
        )
        assert meta.status == "completed"
        assert meta.query == "Find mice"

    def test_pending_run_minimal_fields(self):
        meta = EvaluatorRunMeta(
            status="pending",
            query=None,
            reply=None,
            created_at=None,
            user_id=None,
        )
        assert meta.status == "pending"
        assert meta.reply is None

    def test_all_status_values(self):
        for s in ("pending", "running", "completed", "error", "partial"):
            meta = EvaluatorRunMeta(
                status=s, query=None, reply=None, created_at=None, user_id=None,
            )
            assert meta.status == s

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            EvaluatorRunMeta(
                status="completed", query="q", reply="r",
                created_at=None, user_id=1, unknown="x",
            )


# ---------------------------------------------------------------------------
# EvaluatorRouting
# ---------------------------------------------------------------------------

class TestEvaluatorRouting:

    def test_standard_new_search(self):
        routing = EvaluatorRouting(
            execution_mode="standard",
            path_mode="new_search",
            path_subtype=None,
        )
        assert routing.execution_mode == "standard"
        assert routing.path_mode == "new_search"

    def test_plan_mode(self):
        routing = EvaluatorRouting(
            execution_mode="plan",
            path_mode="plan",
            path_subtype="plan.step.1",
        )
        assert routing.path_subtype == "plan.step.1"

    def test_reporter_summary_subtype(self):
        routing = EvaluatorRouting(
            execution_mode="standard",
            path_mode="reporter",
            path_subtype="reporter.summary",
        )
        assert routing.path_subtype == "reporter.summary"

    def test_all_path_modes(self):
        modes = [
            "new_search", "refine_last_search", "graph_query",
            "reporter", "system_question", "ask_about_last_results",
            "unsupported", "plan",
        ]
        for mode in modes:
            routing = EvaluatorRouting(
                execution_mode="standard", path_mode=mode, path_subtype=None,
            )
            assert routing.path_mode == mode

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            EvaluatorRouting(
                execution_mode="standard", path_mode="new_search",
                path_subtype=None, extra="bad",
            )


# ---------------------------------------------------------------------------
# EvaluatorRetryContext
# ---------------------------------------------------------------------------

class TestEvaluatorRetryContext:

    def test_retryable_with_signals(self):
        ctx = EvaluatorRetryContext(
            retryable=True,
            retry_signals=["empty_result", "partial_data"],
            assistant_context="Search returned 0 results for mice query",
        )
        assert ctx.retryable is True
        assert len(ctx.retry_signals) == 2

    def test_not_retryable_empty_signals(self):
        ctx = EvaluatorRetryContext(
            retryable=False,
            retry_signals=[],
            assistant_context=None,
        )
        assert ctx.retryable is False
        assert ctx.retry_signals == []


# ---------------------------------------------------------------------------
# EvaluatorRawPayloads
# ---------------------------------------------------------------------------

class TestEvaluatorRawPayloads:

    def test_all_none(self):
        raw = EvaluatorRawPayloads(
            task_progress=None,
            task_result=None,
            bundle=None,
            last_debug=None,
        )
        assert raw.task_progress is None

    def test_all_populated(self):
        raw = EvaluatorRawPayloads(
            task_progress=[{"event": "agent_started", "data": {}}],
            task_result={"reply": "hello", "bundle_id": 1},
            bundle={"id": 1, "query": "test", "mode": "standard"},
            last_debug={"key": "val"},
        )
        assert len(raw.task_progress) == 1
        assert raw.task_result["bundle_id"] == 1


# ---------------------------------------------------------------------------
# EvaluatorRetryContextResponse (full nested)
# ---------------------------------------------------------------------------

class TestEvaluatorRetryContextResponse:

    def _make_full_response(self):
        return EvaluatorRetryContextResponse(
            lookup=EvaluatorLookup(
                task_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                bundle_id=1,
                source="task",
            ),
            run=EvaluatorRunMeta(
                status="completed",
                query="Find mice",
                reply="Found 5 mice",
                created_at=datetime.now(timezone.utc),
                user_id=1,
            ),
            routing=EvaluatorRouting(
                execution_mode="standard",
                path_mode="new_search",
                path_subtype=None,
            ),
            retry_context=EvaluatorRetryContext(
                retryable=True,
                retry_signals=["low_result_count"],
                assistant_context="Search returned few results",
            ),
            raw=EvaluatorRawPayloads(
                task_progress=[{"event": "query_complete", "data": {}}],
                task_result={"reply": "Found 5 mice"},
                bundle={"id": 1, "mode": "standard"},
                last_debug={"debug": True},
            ),
        )

    def test_full_construction(self):
        resp = self._make_full_response()
        assert resp.lookup.source == "task"
        assert resp.run.status == "completed"
        assert resp.routing.path_mode == "new_search"
        assert resp.retry_context.retryable is True
        assert resp.raw.bundle is not None

    def test_round_trip_json(self):
        resp = self._make_full_response()
        data = resp.model_dump(mode="json")
        reconstructed = EvaluatorRetryContextResponse.model_validate(data)
        assert reconstructed.lookup.source == resp.lookup.source
        assert reconstructed.run.status == resp.run.status

    def test_minimal_bundle_source(self):
        resp = EvaluatorRetryContextResponse(
            lookup=EvaluatorLookup(
                task_id=None, session_id=uuid.uuid4(), bundle_id=3, source="bundle",
            ),
            run=EvaluatorRunMeta(
                status="completed", query="test", reply="reply",
                created_at=None, user_id=2,
            ),
            routing=EvaluatorRouting(
                execution_mode="standard", path_mode="graph_query", path_subtype=None,
            ),
            retry_context=EvaluatorRetryContext(
                retryable=False, retry_signals=[], assistant_context=None,
            ),
            raw=EvaluatorRawPayloads(
                task_progress=None, task_result=None,
                bundle={"id": 3, "mode": "standard"}, last_debug=None,
            ),
        )
        assert resp.lookup.task_id is None
        assert resp.lookup.bundle_id == 3


# ---------------------------------------------------------------------------
# RetryRequest
# ---------------------------------------------------------------------------

class TestRetryRequest:

    def test_retry_by_task_id(self):
        req = RetryRequest(
            task_id=uuid.uuid4(),
            session_id=None,
            bundle_id=None,
            query="Retry: find mice with updated parameters",
            mode="standard",
        )
        assert req.task_id is not None
        assert req.session_id is None

    def test_retry_by_session_and_bundle(self):
        req = RetryRequest(
            task_id=None,
            session_id=uuid.uuid4(),
            bundle_id=5,
            query="Retry with refined query",
            mode="plan",
        )
        assert req.session_id is not None
        assert req.bundle_id == 5

    def test_query_required(self):
        with pytest.raises(ValidationError):
            RetryRequest(
                task_id=uuid.uuid4(),
                session_id=None,
                bundle_id=None,
                query="",  # min_length=1
                mode="standard",
            )

    def test_mode_required(self):
        with pytest.raises(ValidationError):
            RetryRequest(
                task_id=uuid.uuid4(),
                session_id=None,
                bundle_id=None,
                query="test",
                # mode missing
            )

    def test_no_source_identifiers_accepted(self):
        """Model allows no identifiers — endpoint validation catches this."""
        req = RetryRequest(
            task_id=None,
            session_id=None,
            bundle_id=None,
            query="test",
            mode="standard",
        )
        assert req.task_id is None

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            RetryRequest(
                task_id=uuid.uuid4(),
                query="test",
                mode="standard",
                extra="bad",
            )


# ---------------------------------------------------------------------------
# RetryResponse
# ---------------------------------------------------------------------------

class TestRetryResponse:

    def test_full_response(self):
        tid = uuid.uuid4()
        sid = uuid.uuid4()
        src_tid = uuid.uuid4()
        resp = RetryResponse(
            task_id=tid,
            session_id=sid,
            source_task_id=src_tid,
            source_session_id=sid,
            source_bundle_id=1,
            credential_source="basic_auth",
        )
        assert resp.task_id == tid
        assert resp.credential_source == "basic_auth"

    def test_service_account_credential_source(self):
        resp = RetryResponse(
            task_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            source_task_id=None,
            source_session_id=uuid.uuid4(),
            source_bundle_id=None,
            credential_source="service_account",
        )
        assert resp.credential_source == "service_account"

    def test_invalid_credential_source_rejected(self):
        with pytest.raises(ValidationError):
            RetryResponse(
                task_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                source_task_id=None,
                source_session_id=uuid.uuid4(),
                source_bundle_id=None,
                credential_source="invalid",
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            RetryResponse(
                task_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                source_task_id=None,
                source_session_id=uuid.uuid4(),
                source_bundle_id=None,
                credential_source="basic_auth",
                extra="bad",
            )


# ---------------------------------------------------------------------------
# EvaluatorRunSummary (listing row)
# ---------------------------------------------------------------------------

class TestEvaluatorRunSummary:

    def test_full_summary(self):
        now = datetime.now(timezone.utc)
        summary = EvaluatorRunSummary(
            task_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            status="completed",
            query="Find mice",
            has_bundle=True,
            user_id=1,
            created_at=now,
        )
        assert summary.has_bundle is True
        assert summary.status == "completed"

    def test_pending_no_bundle(self):
        summary = EvaluatorRunSummary(
            task_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            status="pending",
            query="running",
            has_bundle=False,
            user_id=2,
            created_at=datetime.now(timezone.utc),
        )
        assert summary.has_bundle is False


# ---------------------------------------------------------------------------
# EvaluatorRunsListResponse (paginated)
# ---------------------------------------------------------------------------

class TestEvaluatorRunsListResponse:

    def test_empty_list(self):
        resp = EvaluatorRunsListResponse(
            count=0, results=[], next=None, previous=None,
        )
        assert resp.count == 0
        assert resp.results == []

    def test_with_results(self):
        now = datetime.now(timezone.utc)
        summary = EvaluatorRunSummary(
            task_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            status="completed",
            query="test",
            has_bundle=True,
            user_id=1,
            created_at=now,
        )
        resp = EvaluatorRunsListResponse(
            count=1,
            results=[summary],
            next="http://example.com/nextseek_api/evaluator/runs/?page=2",
            previous=None,
        )
        assert resp.count == 1
        assert len(resp.results) == 1

    def test_round_trip_json(self):
        now = datetime.now(timezone.utc)
        summary = EvaluatorRunSummary(
            task_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            status="error",
            query="broken",
            has_bundle=False,
            user_id=3,
            created_at=now,
        )
        resp = EvaluatorRunsListResponse(
            count=1, results=[summary], next=None, previous=None,
        )
        data = resp.model_dump(mode="json")
        reconstructed = EvaluatorRunsListResponse.model_validate(data)
        assert reconstructed.results[0].status == "error"


# ---------------------------------------------------------------------------
# Description constants
# ---------------------------------------------------------------------------

class TestDescriptionConstants:

    def test_retry_context_by_task_desc(self):
        assert isinstance(EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC, str)
        assert len(EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC) > 50
        assert "SUMMARY" in EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC

    def test_retry_context_by_bundle_desc(self):
        assert isinstance(EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC, str)
        assert len(EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC) > 50
        assert "SUMMARY" in EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC

    def test_runs_list_desc(self):
        assert isinstance(EVALUATOR_RUNS_LIST_DESC, str)
        assert len(EVALUATOR_RUNS_LIST_DESC) > 50
        assert "SUMMARY" in EVALUATOR_RUNS_LIST_DESC

    def test_retry_execute_desc(self):
        assert isinstance(EVALUATOR_RETRY_EXECUTE_DESC, str)
        assert len(EVALUATOR_RETRY_EXECUTE_DESC) > 50
        assert "SUMMARY" in EVALUATOR_RETRY_EXECUTE_DESC

    def test_all_descriptions_follow_format(self):
        """All descriptions should contain the standard sections."""
        descs = [
            EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC,
            EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC,
            EVALUATOR_RUNS_LIST_DESC,
            EVALUATOR_RETRY_EXECUTE_DESC,
        ]
        for desc in descs:
            assert "**SUMMARY:**" in desc
            assert "**RETURNS:**" in desc
