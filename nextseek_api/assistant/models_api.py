"""Pydantic request/response models for the Assistant endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict, field_validator


# --- Request models ---

class QueryRequest(BaseModel):
    """POST /assistant/query/ request body."""
    session_id: Optional[UUID] = Field(None, description=(
        "Chat session UUID; it must belong to the caller. If omitted, the routed Nessie endpoints (cc-assistant/query/async/, "
        "cc-assistant/cc/query/async/, nessie/query/, nessie/query/cc/) always open a new chat, while the legacy assistant/query/ "
        "and assistant/query/async/ reuse the most recently updated session (unless force_new) or auto-create one."))
    query: str = Field(..., min_length=1, max_length=32000, description="Natural language query")
    mode: str = Field(..., description="What mode to execute the query as. E.g. standard, plan, etc.")
    force_new: bool = Field(False, description=(
        "If true and session_id is omitted, always create a new ChatSession instead of reusing the most recent one. Only the "
        "legacy assistant/query/ routes reuse; on the routed Nessie endpoints an omitted session_id is already a new chat."))
    use_prod: bool = Field(False, description="If true and a NEXTSEEK_CHAT_CONFIG_PROD is configured, route this query through the prod ChatConfig (real production tables) instead of the default dev/docker one. Admin-only on the UI; ignored if a prod config wasn't built.")
    fresh_session: bool = Field(False, description="If true, run this turn as a clean room: skip the Step-1c cross-session memory layer (no rendered ~/.claude/CLAUDE.md, no raw-transcript mount). 1b resume within this chat still applies.")
    force_route: Optional[Literal["auto", "ns", "cc"]] = Field(None, description="Admin-only: supersede the BAML router for this query. 'ns' forces the core chat_nextseek path, 'cc' forces Container-Claude-Code, 'auto'/None uses the router. Ignored for non-admins (the server re-checks is_staff/is_superuser).")
    max_turn_length_s: Optional[int] = Field(None, ge=1, description="Admin-only: per-turn wall-clock cap (seconds) for a Container-CC turn. Clamped server-side to [30, NEXTSEEK_CC_TIMEOUT_HARD_MAX]; None uses the configured default. Ignored for non-admins (the server re-checks is_staff/is_superuser).")
    force_parser_mode: Optional[Literal["graph", "api"]] = Field(None, description=(
        "Admin-only and evaluation-only: force the NExtSEEK parser to the graph or the API path for a retrieval question. "
        "Ignored unless the caller is a superuser and the server process sets NEXTSEEK_EVAL_PARSER_FORCE=1."))
    prompt_variant: Optional[Literal["v2_apoc"]] = Field(None, description=(
        "Admin-only and evaluation-only: run this NExtSEEK turn on an alternative prompt set "
        "(chat_nextseek/prompts/variants/<name>/), with or without force_parser_mode. The turn's debug payload "
        "records it as prompt_variant. Ignored unless the caller is a superuser and the server process sets "
        "NEXTSEEK_EVAL_PARSER_FORCE=1."))

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
    #: Search Details for NExtSEEK-engine turns, rebuilt from the bundle. The
    #: Container-CC counterpart is `cc_traces`, which is mirrored at write time;
    #: the NS progress events are ephemeral, so these are reconstructed on read.
    #: Entries are {agent, summary}; the frontend supplies the timestamp.
    debug_entries: Optional[List[Dict[str, Any]]] = None

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
    files: Optional[List[Dict[str, Any]]] = Field(
        None, description="The turn's downloadable files (a manifest entry per file)"
    )
    total_cost_usd: Optional[float] = Field(None, description=(
        "What the turn's model calls cost in USD, priced on NessieAI/chat_nextseek/model_prices.json; "
        "null when no call could be priced"))
    cost_partial: Optional[bool] = Field(None, description=(
        "True when some of the turn's spend was not seen (a call abandoned by its time limit, "
        "an unpriced model), so total_cost_usd is a floor"))
    models_used: Optional[List[str]] = Field(None, description="The model ids that answered the turn's calls")
    model_fallback: Optional[List[Dict[str, Any]]] = Field(None, description=(
        "Each move to a second model this turn: {agent, from, to, reason}; empty when nothing fell back"))

    model_config = ConfigDict(extra="forbid")


class QueryErrorEvent(BaseModel):
    """SSE event: query_error"""
    error: str
    agent: Optional[str] = None
    session_id: Optional[str] = None
    fatal: Optional[bool] = Field(None, description="True when a model failure ended the turn")
    reason: Optional[str] = Field(None, description=(
        "Why the turn ended, when known: model_unavailable when the AI models did not answer"))
    detail: Optional[str] = Field(None, description="The raw technical error behind the plain `error` text")
    model_fallback: Optional[List[Dict[str, Any]]] = Field(None, description=(
        "Each move to a second model before the turn ended: {agent, from, to, reason}"))
    total_cost_usd: Optional[float] = Field(None, description="What the turn spent before it ended, in USD")
    cost_partial: Optional[bool] = Field(None, description="True when total_cost_usd is a floor")
    models_used: Optional[List[str]] = Field(None, description="The model ids that answered before the turn ended")

    model_config = ConfigDict(extra="forbid")


# --- Bundle / test cases ---

class BundleDownloadParams(BaseModel):
    """Query params for GET /assistant/sessions/{id}/bundles/{bid}/.

    Selection is ``part``, not ``format``: DRF owns ``format`` for content
    negotiation, so ``?format=metadata`` 404s in ``initial()`` before the view
    body runs. That is why the panel's Metadata button never worked.
    """
    part: str = Field("full", description="'full' (whole bundle) or 'metadata' (provenance only)")

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


class AggregateOpRequest(BaseModel):
    """POST /assistant/aggregate/ body.

    ``parts`` is a JSON array of 1 to 4 plain-language sub-questions, sent as text (one shim flag); empty means
    the question itself is the one part. The body carries no Cypher and no project scope: the scope comes from
    the caller's account on the server, and any extra field is refused.
    """
    query: str = Field(..., min_length=1, max_length=32000)
    parts: str = Field("", max_length=16000,
                       description="JSON array of 1 to 4 sub-questions, as text; empty means the question alone.")
    use_prod: bool = Field(False, description="Admin-only: route through the prod ChatConfig.")
    session_id: Optional[UUID] = Field(None, description="Optional session for parser continuity.")
    model_config = ConfigDict(extra="forbid")


class GraphSchemaOpRequest(BaseModel):
    """POST /assistant/graph-schema/ body.

    Both fields are optional: with neither, the answer is the structure, the sample type
    index and the always-on vocabulary. ``types`` is a comma-separated list of sample type
    codes to render in full; ``query`` only gates the keyword-driven vocabulary blocks and
    is never sent to a model.
    """
    types: str = Field("", max_length=2000,
                       description="Comma-separated sample type codes to render in full.")
    query: str = Field("", max_length=32000,
                       description="Gates the vocabulary blocks; no model call is made.")
    use_prod: bool = Field(False, description="Admin-only: route through the prod ChatConfig.")
    model_config = ConfigDict(extra="forbid")


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


class RunLsRequest(BaseModel):
    """POST /assistant/run-ls/ body — recursive read-only listing of a Luria run dir."""
    run_dir: str = Field(..., description="Absolute path under <LURIA working_path>/runs.")
    use_prod: bool = False
    model_config = ConfigDict(extra="forbid")


class BuildUploadXlsxRequest(BaseModel):
    """POST /assistant/build-upload-xlsx/ body — render 4-sheet upload workbook(s)."""
    rows: str = Field(..., description="JSON array of {SampleType, json_metadata, assay_ids} rows.")
    existing_parent_uids: str = Field("", description="Comma-separated existing parent UIDs (for Parent QA).")
    use_prod: bool = False
    session_id: Optional[UUID] = Field(
        None, description="Optional chat session to attach the workbook bundle to.")
    model_config = ConfigDict(extra="forbid")


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


class AggregatePart(BaseModel):
    """One part's answer: a small table (``groups``) with the sum of its group counts and its missing-value bucket
    (``null_group``).

    ``status`` is ok, empty, fallback (graph_search's scoped total only, no breakdown), refused, error or
    timed_out; ``kind`` is count, breakdown (group columns then a trailing count) or rows (returned as is).
    ``sum_of_group_counts`` adds the groups' counts, so a sample in several groups counts once in each; it is not a
    number of samples when ``groups_may_overlap`` is true (every breakdown of two groups or more).
    """
    part: int
    question: str
    status: Literal["ok", "empty", "fallback", "refused", "error", "timed_out"]
    kind: Optional[Literal["count", "breakdown", "rows"]] = None
    columns: List[str] = Field(default_factory=list)
    groups: List[Dict[str, Any]] = Field(default_factory=list)
    group_count: Optional[int] = None
    sum_of_group_counts: Optional[float] = None
    groups_may_overlap: Optional[bool] = None
    null_group: Optional[int] = None
    truncated: bool = False
    cypher: Optional[str] = None
    scope: Optional[Dict[str, Any]] = None
    attempts: List[Dict[str, Any]] = Field(default_factory=list)
    fallback: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class AggregateResult(BaseModel):
    """Every part, in order, with the notes the agent must relay; ``complete`` is false when a part timed out."""
    question: str
    complete: bool
    elapsed_s: float
    deadline_s: float
    parts: List[AggregatePart]
    notes: List[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


class AggregateOpResponse(BaseModel):
    op: Literal["aggregate"] = "aggregate"
    result: AggregateResult
    model_config = ConfigDict(extra="forbid")


class GraphSchemaResult(BaseModel):
    """The live graph schema as text, or the committed fallback, saying which it is.

    ``source`` is the load-bearing field: ``catalog`` means the deployed graph answered,
    ``fallback`` means the committed ``context/neo4j_schema.json`` did, and then
    ``unavailable_reason`` says why and ``fallback_fetched_at`` how stale it is.
    """
    source: Literal["catalog", "fallback"]
    schema_version: Optional[str] = None
    catalog_hash: Optional[str] = None
    synced_at: Optional[str] = None
    sample_types: int = 0
    resolved_types: List[str] = Field(default_factory=list)
    unknown_types: List[str] = Field(default_factory=list)
    graph_schema: str = Field("", alias="schema")
    vocabulary: str = ""
    unavailable_reason: Optional[str] = None
    fallback_fetched_at: Optional[str] = None
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class GraphSchemaOpResponse(BaseModel):
    op: Literal["graph-schema"] = "graph-schema"
    result: GraphSchemaResult
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
