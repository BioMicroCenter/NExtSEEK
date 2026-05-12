"""Pydantic request/response models for the Assistant endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


# --- Request models ---

class QueryRequest(BaseModel):
    """POST /assistant/query/ request body."""
    session_id: Optional[UUID] = Field(None, description="Chat session UUID. If omitted (and force_new is False), reuses the most recently updated session or auto-creates one.")
    query: str = Field(..., min_length=1, max_length=4000, description="Natural language query")
    mode: str = Field(..., description="What mode to execute the query as. E.g. standard, plan, etc.")
    force_new: bool = Field(False, description="If true and session_id is omitted, always create a new ChatSession instead of reusing the most recent one.")

    model_config = ConfigDict(extra="forbid")


# --- Response models ---

class AssistantUserResponse(BaseModel):
    """GET /assistant/me/ response."""
    username: str
    is_admin: bool

    model_config = ConfigDict(extra="forbid")


class SessionCreateResponse(BaseModel):
    """POST /assistant/sessions/ response."""
    session_id: UUID
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class SessionDetailResponse(BaseModel):
    """GET /assistant/sessions/{id}/ response."""
    session_id: UUID
    created_at: datetime
    query_count: int = Field(..., description="Number of queries in results_history")
    has_results: bool = Field(..., description="Whether any results exist")
    # Populated when the request includes ?include=turns
    title: Optional[str] = None
    turns: Optional[List["Turn"]] = None

    model_config = ConfigDict(extra="forbid")


class Turn(BaseModel):
    """One projected turn from a session's results_history."""
    bundle_id: int
    user_query: str
    reply: str
    mode: str
    ts: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SessionListItem(BaseModel):
    """One row in the sessions list view."""
    session_id: UUID
    title: str = Field(..., description="Display title; 'New chat' when no title is set")
    created_at: datetime
    updated_at: datetime
    query_count: int
    preview: str = Field("", description="First user query, trimmed to <=80 chars")

    model_config = ConfigDict(extra="forbid")


class SessionListResponse(BaseModel):
    """GET /assistant/sessions/ response."""
    total: int
    sessions: List[SessionListItem]

    model_config = ConfigDict(extra="forbid")


class SessionPatchRequest(BaseModel):
    """PATCH /assistant/sessions/{id}/ body."""
    title: str = Field(..., min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid")


# --- SSE event payloads ---

class AgentStartedEvent(BaseModel):
    """SSE event: agent_started"""
    agent: str
    mode: str = ""

    model_config = ConfigDict(extra="forbid")


class AgentCompleteEvent(BaseModel):
    """SSE event: agent_complete"""
    agent: str
    summary: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="forbid")


class ArtifactTable(BaseModel):
    """Inline table artifact with data for frontend rendering."""
    artifact_type: Literal["table"] = "table"
    key: str = Field(..., description="Unique artifact key, e.g. 'samples_table'")
    label: str = Field(..., description="Human-readable label, e.g. 'Samples'")
    columns: List[str] = Field(..., description="Column headers in display order")
    data: List[Dict[str, Any]] = Field(..., description="Row data as list of dicts")
    model_config = ConfigDict(extra="forbid")


class ArtifactFile(BaseModel):
    """File-based artifact (download only, no inline data)."""
    artifact_type: Literal["file"] = "file"
    key: str = Field(..., description="Unique artifact key, e.g. 'geo_seq_workbooks'")
    label: str = Field(..., description="Human-readable label")
    file_format: str = Field("xlsx", description="File extension/format")
    model_config = ConfigDict(extra="forbid")


class QueryCompleteEvent(BaseModel):
    """SSE event: query_complete"""
    reply: str
    debug: Optional[Dict[str, Any]] = None
    bundle_id: Optional[int] = None
    session_id: Optional[str] = None
    artifacts: Optional[List[Union[ArtifactTable, ArtifactFile]]] = Field(
        None, description="Table data and file download references for the frontend"
    )

    model_config = ConfigDict(extra="forbid")


class QueryErrorEvent(BaseModel):
    """SSE event: query_error"""
    error: str
    agent: Optional[str] = None
    session_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


# --- Bundle / test cases ---

class BundleDownloadParams(BaseModel):
    """Query params for GET /assistant/sessions/{id}/bundles/{bid}/."""
    format: str = Field("json", description="Download format")

    model_config = ConfigDict(extra="forbid")


class TestCaseItem(BaseModel):
    """Single test case from chat_nextseek.TEST_CASES."""
    id: str
    prompt: str

    model_config = ConfigDict(extra="forbid")


class TestCaseListResponse(BaseModel):
    """GET /assistant/test-cases/ response."""
    total: int
    test_cases: List[TestCaseItem]

    model_config = ConfigDict(extra="forbid")


# --- Async query / task progress ---

class AsyncQueryResponse(BaseModel):
    """POST /assistant/query/async/ response (HTTP 202)."""
    task_id: UUID
    session_id: UUID

    model_config = ConfigDict(extra="forbid")


class ProgressEvent(BaseModel):
    """A single progress event stored in QueryTask.progress."""
    event: str
    data: Dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class TaskProgressResponse(BaseModel):
    """GET /assistant/tasks/{task_id}/progress/ response."""
    task_id: UUID
    session_id: UUID
    status: str = Field(..., description="pending | running | completed | error")
    progress: List[ProgressEvent]
    result: Optional[Dict[str, Any]] = Field(None, description="Final payload (set when status is completed or error)")

    model_config = ConfigDict(extra="forbid")


SessionDetailResponse.model_rebuild()
