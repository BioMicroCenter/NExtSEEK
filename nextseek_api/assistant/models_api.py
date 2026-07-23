"""Pydantic request/response models for the Assistant endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict, field_validator


# --- Request models ---

class QueryRequest(BaseModel):
    """POST /assistant/query/ request body."""
    session_id: Optional[UUID] = Field(None, description="Chat session UUID. If omitted (and force_new is False), reuses the most recently updated session or auto-creates one.")
    query: str = Field(..., min_length=1, max_length=32000, description="Natural language query")
    mode: str = Field(..., description="What mode to execute the query as. E.g. standard, plan, etc.")
    force_new: bool = Field(False, description="If true and session_id is omitted, always create a new ChatSession instead of reusing the most recent one.")
    use_prod: bool = Field(False, description="If true and a NEXTSEEK_CHAT_CONFIG_PROD is configured, route this query through the prod ChatConfig (real production tables) instead of the default dev/docker one. Admin-only on the UI; ignored if a prod config wasn't built.")
    fresh_session: bool = Field(False, description="If true, run this turn as a clean room: skip the Step-1c cross-session memory layer (no rendered ~/.claude/CLAUDE.md, no raw-transcript mount). 1b resume within this chat still applies.")

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


# Turn is defined below ArtifactTable/ArtifactFile so it can reference them.


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


class Turn(BaseModel):
    """One projected turn from a session's results_history or chat_log.

    `artifacts` mirrors the SSE QueryCompleteEvent.artifacts shape so the live
    and hydrated paths produce identically-shaped messages on the frontend.
    Stored as raw dicts (not strict Pydantic models) to accommodate extra
    metadata fields emitted by extract_table_artifacts (e.g. truncated,
    total_rows, rows_returned).
    """
    bundle_id: int
    turn_id: Optional[int] = None
    user_query: str
    reply: str
    mode: str
    ts: Optional[str] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    cc_traces: Optional[List[Dict[str, Any]]] = None

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


# ======================================================================
# Granular ops (native NExtSEEK assistant endpoints)
#
# Request + response models for the 7 granular ops (entity, parse, graph,
# api-read, api-write, report, generate-submission). These are designed to be
# copied verbatim into dmac_assistant when its sidecar is rewired to call these
# endpoints. Request models mirror the dmac _ws_contract arg schemas; response
# models are a typed envelope ({op, result}) over a lenient (extra="allow")
# result so the rich real agent output still validates while the load-bearing
# fields stay type-checked. Optional ``use_prod`` / ``session_id`` are native
# extensions (default-safe; dmac may ignore them).
# ======================================================================

_REPORT_MODES = ("samples", "protocols", "published", "rppr")
_SUBMISSION_TYPES = ("GEO", "SRA", "NFCORE_RNASEQ", "NFCORE_SCRNASEQ", "PRIDE")


# --- Request models ---

class EntityOpRequest(BaseModel):
    """POST /assistant/entity/ body (also parse/graph share this shape)."""
    query: str = Field(..., min_length=1, max_length=32000)
    use_prod: bool = Field(False, description="Admin-only: route through the prod ChatConfig.")
    session_id: Optional[UUID] = Field(None, description="Optional session for parser continuity.")
    model_config = ConfigDict(extra="forbid")


class ParseOpRequest(EntityOpRequest):
    """POST /assistant/parse/ body."""


class GraphOpRequest(EntityOpRequest):
    """POST /assistant/graph/ body."""


class ApiReadRequest(BaseModel):
    """POST /assistant/api-read/ body."""
    parser_plan: str = Field(..., description="A parser plan as a JSON string.")
    use_prod: bool = False
    model_config = ConfigDict(extra="forbid")


class ApiWriteRequest(BaseModel):
    """POST /assistant/api-write/ body.

    ``confirmed_write`` is **strict bool**: the string "true" or integer 1 are
    rejected at validation (they must never coerce to a confirmed write). The
    server-side write gate independently re-checks ``is True``.
    """
    parser_plan: str
    confirmed_write: bool = Field(False, strict=True)
    query: Optional[str] = None
    use_prod: bool = False
    model_config = ConfigDict(extra="forbid")


class ReportOpRequest(BaseModel):
    """POST /assistant/report/ body."""
    mode: str = Field(..., description="One of: samples | protocols | published | rppr")
    project: str
    use_prod: bool = False
    session_id: Optional[UUID] = Field(
        None, description="Optional chat session to attach the result bundle to; a new one is created if omitted.")
    model_config = ConfigDict(extra="forbid")

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in _REPORT_MODES:
            raise ValueError(f"bad report mode: {v!r}")
        return v


class SubmissionRequest(BaseModel):
    """POST /assistant/generate-submission/ body."""
    type: str = Field(..., description="One of: GEO | SRA | NFCORE_RNASEQ | NFCORE_SCRNASEQ | PRIDE")
    uids: str = Field(..., description="Comma-separated UID list.")
    query: Optional[str] = None
    use_prod: bool = False
    session_id: Optional[UUID] = Field(
        None, description="Optional chat session to attach the result bundle to; a new one is created if omitted.")
    model_config = ConfigDict(extra="forbid")

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in _SUBMISSION_TYPES:
            raise ValueError(f"unsupported submission type: {v!r}")
        return v

    @field_validator("uids")
    @classmethod
    def _uids(cls, v: str) -> str:
        if not [u for u in v.split(",") if u.strip()]:
            raise ValueError("uids required (comma-separated)")
        return v


# --- Response models (typed envelope over a lenient result) ---

class EntityItemModel(BaseModel):
    code: str
    name: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class EntityResult(BaseModel):
    sampletypes: List[EntityItemModel] = Field(default_factory=list)
    assays: List[EntityItemModel] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    projects: List[Any] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


class EntityOpResponse(BaseModel):
    op: Literal["entity"] = "entity"
    result: EntityResult
    model_config = ConfigDict(extra="forbid")


class ParseResult(BaseModel):
    mode: str = ""
    target_endpoint: Optional[str] = None
    intent_summary: str = ""
    filters: Dict[str, Any] = Field(default_factory=dict)
    resolved: Dict[str, Any] = Field(default_factory=dict)
    report_mode: Optional[str] = None
    report_type: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class ParseOpResponse(BaseModel):
    op: Literal["parse"] = "parse"
    result: ParseResult
    model_config = ConfigDict(extra="forbid")


class GraphPlanModel(BaseModel):
    cypher: str
    explanation: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")


class GraphResult(BaseModel):
    plan: GraphPlanModel
    result: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")


class GraphOpResponse(BaseModel):
    op: Literal["graph"] = "graph"
    result: GraphResult
    model_config = ConfigDict(extra="forbid")


class ApiPlanModel(BaseModel):
    endpoint: Optional[str] = None
    method: Optional[str] = None
    requestBody: Dict[str, Any] = Field(default_factory=dict)
    queryParameters: Dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    model_config = ConfigDict(extra="allow")


class ApiCallResult(BaseModel):
    endpoint: Optional[str] = None
    method: Optional[str] = None
    api_plan: ApiPlanModel
    response: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")


class ApiReadResponse(BaseModel):
    op: Literal["api-read"] = "api-read"
    result: ApiCallResult
    model_config = ConfigDict(extra="forbid")


class ApiWriteResponse(BaseModel):
    op: Literal["api-write"] = "api-write"
    result: ApiCallResult
    model_config = ConfigDict(extra="forbid")


class ArtifactRef(BaseModel):
    """A downloadable artifact produced by a report/generate-submission op."""
    key: str
    url: str = Field(..., description="Relative GET URL for the bundle artifact endpoint.")
    model_config = ConfigDict(extra="forbid")


class DownloadRef(BaseModel):
    """Where a report/generate-submission op's outputs were registered so they can
    be fetched over HTTP via GET /assistant/sessions/{session_id}/bundles/{bundle_id}/artifacts/{key}/."""
    session_id: UUID
    bundle_id: int
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class ReportResult(BaseModel):
    summary: Dict[str, Any] = Field(default_factory=dict)
    saved_files: Dict[str, Any] = Field(default_factory=dict)
    rows: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")


class ReportOpResponse(BaseModel):
    op: Literal["report"] = "report"
    result: ReportResult
    download: Optional[DownloadRef] = Field(
        None, description="Bundle + URLs for fetching the report's saved files over HTTP.")
    model_config = ConfigDict(extra="forbid")


class SubmissionResult(BaseModel):
    report_type: Optional[str] = None
    report: Dict[str, Any] = Field(default_factory=dict)
    narrative: Optional[str] = None
    notes: str = ""
    model_config = ConfigDict(extra="allow")


class SubmissionResponse(BaseModel):
    op: Literal["generate-submission"] = "generate-submission"
    result: SubmissionResult
    download: Optional[DownloadRef] = Field(
        None, description="Bundle + URLs for fetching the submission output over HTTP.")
    model_config = ConfigDict(extra="forbid")


class OpErrorResponse(BaseModel):
    """Error envelope for a granular op.

    Carries the NExtSEEK ``errors`` list AND the canonical dmac error ``code``
    (CONFIG_MISSING / VALIDATION / AGENT_FAILED / WRITE_BLOCKED / CONFIG_ERROR /
    AUTH_FAILED) so the dmac thin client can map it to its CLI exit taxonomy.
    """
    code: str
    errors: List[Dict[str, Any]]
    model_config = ConfigDict(extra="forbid")
