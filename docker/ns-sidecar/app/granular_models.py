"""Vendored copy of the granular-op request/response models from NExtSEEK.

Source: nextseek_api/assistant/models_api.py (repo: BMCBCC/NExtSEEK,
branch: feat/native-assistant-granular-ops, as of 2026-06-12).
This is a LOCAL COPY so the sidecar does NOT import the NExtSEEK package
(cross-repo). Keep in sync with CONTRACT.md; any drift must be audited.

T15 / Amendment A-5: sidecar → NExtSEEK granular HTTP client + copied models.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ======================================================================
# Validated sets for field_validators
# ======================================================================

_REPORT_MODES = ("samples", "protocols", "published", "rppr")
_SUBMISSION_TYPES = ("GEO", "SRA", "NFCORE_RNASEQ", "NFCORE_SCRNASEQ", "PRIDE")


# ======================================================================
# Request models — mirror the dmac _ws_contract arg schemas.
# Optional use_prod / session_id are native extensions (default-safe;
# the sidecar may ignore them in T15 — they are forwarded as-is).
# ======================================================================

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


# ======================================================================
# Response models — typed envelope ({op, result}) over a lenient result
# so rich real-agent output still validates while load-bearing fields
# stay type-checked. result models use extra="allow" for forward compat.
# ======================================================================

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
