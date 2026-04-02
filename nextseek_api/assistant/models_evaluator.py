"""Pydantic request/response models for the Evaluator endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


# --- Normalized response components ---

class EvaluatorLookup(BaseModel):
    """Identifies which source resolved this evaluator record."""
    task_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    bundle_id: Optional[int] = None
    source: Literal["task", "bundle"] = Field(
        ..., description="Whether this was resolved from a task_id or session_id+bundle_id"
    )

    model_config = ConfigDict(extra="forbid")


class EvaluatorRunMeta(BaseModel):
    """Current/historical run metadata."""
    status: str = Field(
        ..., description="pending | running | completed | error | partial"
    )
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
    path_subtype: Optional[str] = Field(
        None,
        description="e.g. reporter.summary, reporter.report_generation, plan.step.*",
    )

    model_config = ConfigDict(extra="forbid")


class EvaluatorRetryContext(BaseModel):
    """Compact evaluator-focused retry inputs."""
    retryable: bool = Field(..., description="Whether this run is retryable")
    retry_signals: List[str] = Field(
        default_factory=list,
        description="Signals indicating why a retry may be warranted",
    )
    assistant_context: Optional[str] = Field(
        None, description="Normalized debug/bundle summary for evaluator"
    )

    model_config = ConfigDict(extra="forbid")


class EvaluatorRawPayloads(BaseModel):
    """Embedded source payloads for debugging and fallbacks."""
    task_progress: Optional[List[Dict[str, Any]]] = None
    task_result: Optional[Dict[str, Any]] = None
    bundle: Optional[Dict[str, Any]] = None
    last_debug: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="forbid")


# --- Unified read response ---

class EvaluatorRetryContextResponse(BaseModel):
    """Unified response for both task-based and bundle-based retry context endpoints."""
    lookup: EvaluatorLookup
    run: EvaluatorRunMeta
    routing: EvaluatorRouting
    retry_context: EvaluatorRetryContext
    raw: EvaluatorRawPayloads

    model_config = ConfigDict(extra="forbid")


# --- Retry execution ---

class RetryRequest(BaseModel):
    """POST /evaluator/retry/ request body."""
    task_id: Optional[UUID] = Field(
        None, description="Source task to retry (mutually exclusive with session_id+bundle_id)"
    )
    session_id: Optional[UUID] = Field(
        None, description="Source session (used with bundle_id)"
    )
    bundle_id: Optional[int] = Field(
        None, description="Source bundle within the session"
    )
    query: str = Field(
        ..., min_length=1, max_length=4000,
        description="Final retry query text",
    )
    mode: str = Field(..., description="Target assistant mode (standard, plan, etc.)")

    model_config = ConfigDict(extra="forbid")


class RetryResponse(BaseModel):
    """POST /evaluator/retry/ response."""
    task_id: UUID = Field(..., description="New retry task ID")
    session_id: UUID = Field(..., description="Reused session ID")
    source_task_id: Optional[UUID] = Field(None, description="Original task ID if source was a task")
    source_session_id: UUID = Field(..., description="Original session ID")
    source_bundle_id: Optional[int] = Field(None, description="Original bundle ID if applicable")
    credential_source: Literal["basic_auth", "service_account"] = Field(
        ..., description="Which credentials were used for the retry execution"
    )

    model_config = ConfigDict(extra="forbid")


# --- Listing ---

class EvaluatorRunSummary(BaseModel):
    """Single row in the runs listing."""
    task_id: UUID
    session_id: UUID
    status: str
    query: str
    has_bundle: bool
    user_id: int
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class EvaluatorRunsListResponse(BaseModel):
    """GET /evaluator/runs/ paginated response."""
    count: int
    results: List[EvaluatorRunSummary]
    next: Optional[str] = None
    previous: Optional[str] = None

    model_config = ConfigDict(extra="forbid")
