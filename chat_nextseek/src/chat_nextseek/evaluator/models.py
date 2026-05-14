from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class EvaluatorLookup(BaseModel):
    """Identify whether evaluator context came from a task record or a bundle lookup."""

    task_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    bundle_id: Optional[int] = None
    source: Literal["task", "bundle"] = Field(
        ..., description="Whether this lookup resolved from a task or a bundle."
    )

    model_config = ConfigDict(extra="forbid")


class EvaluatorRunMeta(BaseModel):
    """Normalized run metadata mirrored from the service contract."""

    status: str = Field(..., description="pending | running | completed | error | partial")
    query: Optional[str] = None
    reply: Optional[str] = None
    created_at: Optional[datetime] = None
    user_id: Optional[int] = None

    model_config = ConfigDict(extra="forbid")


class EvaluatorRouting(BaseModel):
    """Normalized execution path classification."""

    execution_mode: str = Field(..., description="standard | plan")
    path_mode: str = Field(
        ...,
        description=(
            "new_search | refine_last_search | graph_query | reporter | "
            "system_question | ask_about_last_results | unsupported | plan"
        ),
    )
    path_subtype: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class EvaluatorRetrySignals(BaseModel):
    """Facts about the run that the evaluator can use to judge and retry."""

    assistant_status: Optional[str] = None
    query_error_present: bool = False
    raw_error_excerpt: Optional[str] = None
    bundle_present: bool = False
    path_mode: Optional[str] = None
    api_ok: Optional[bool] = None
    api_status_code: Optional[int] = None
    rows_returned: Optional[int] = None
    graph_ok: Optional[bool] = None
    has_artifacts: bool = False
    plan_steps_total: Optional[int] = None
    plan_steps_executed: Optional[int] = None
    plan_steps_ok: Optional[int] = None
    plan_steps_failed: Optional[int] = None
    plan_stop_reason: Optional[str] = None
    has_prior_context: bool = False

    model_config = ConfigDict(extra="forbid")


class EvaluatorRetryContext(BaseModel):
    """Compact retry-oriented view over the normalized run."""

    retryable: bool
    retry_signals: EvaluatorRetrySignals = Field(default_factory=EvaluatorRetrySignals)
    assistant_context: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class EvaluatorRawPayloads(BaseModel):
    """Source payloads preserved for debugging and later normalization passes."""

    task_progress: Optional[list[dict[str, Any]]] = None
    task_result: Optional[dict[str, Any]] = None
    bundle: Optional[dict[str, Any]] = None
    last_debug: Optional[dict[str, Any]] = None

    model_config = ConfigDict(extra="forbid")


class EvaluatorRetryContextResponse(BaseModel):
    """Unified response shape for task- and bundle-based evaluator lookups."""

    lookup: EvaluatorLookup
    run: EvaluatorRunMeta
    routing: EvaluatorRouting
    retry_context: EvaluatorRetryContext
    raw: EvaluatorRawPayloads

    model_config = ConfigDict(extra="forbid")


class RetryRequest(BaseModel):
    """Input shape for an explicit retry request."""

    task_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    bundle_id: Optional[int] = None
    query: str = Field(..., min_length=1, max_length=4000)
    mode: str

    model_config = ConfigDict(extra="forbid")


class RetryResponse(BaseModel):
    """Response shape for a retry launch record."""

    task_id: UUID
    session_id: UUID
    source_task_id: Optional[UUID] = None
    source_session_id: UUID
    source_bundle_id: Optional[int] = None
    credential_source: Literal["basic_auth", "service_account"]

    model_config = ConfigDict(extra="forbid")


class EvaluatorRunSummary(BaseModel):
    """Minimal row returned by the local runs-listing surface."""

    task_id: UUID
    session_id: UUID
    status: str
    query: str
    has_bundle: bool
    user_id: int
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class EvaluatorRunsListResponse(BaseModel):
    """Paginated response shape for local runs listing."""

    count: int
    results: list[EvaluatorRunSummary]
    next: Optional[str] = None
    previous: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class EvaluatorRunRecord(BaseModel):
    """Persisted local evaluator run record used by later workflow tasks."""

    task_id: UUID = Field(default_factory=uuid4)
    session_id: UUID = Field(default_factory=uuid4)
    source_task_id: Optional[UUID] = None
    source_session_id: Optional[UUID] = None
    source_bundle_id: Optional[int] = None
    mode: Literal["standard", "plan"]
    status: str = "pending"
    query: str
    reply: Optional[str] = None
    user_id: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lookup: Optional[EvaluatorLookup] = None
    normalized_payload: Optional[dict[str, Any]] = None
    evaluation_payload: Optional[dict[str, Any]] = None
    retry_request: Optional[dict[str, Any]] = None
    retry_result: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    model_config = ConfigDict(extra="forbid")
