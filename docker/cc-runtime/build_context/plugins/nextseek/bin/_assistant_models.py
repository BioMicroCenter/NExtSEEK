"""Mirrored Pydantic models for NExtSEEK assistant viewset (OD-6, origin/dev@935f5fa)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    mode: str
    session_id: str | None = None
    force_new: bool = False
    use_prod: bool = False


class ArtifactTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["table"]
    key: str
    label: str
    columns: list[str]
    data: list[dict[str, Any]]


class ArtifactFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["file"]
    key: str
    label: str
    file_format: str


Artifact = ArtifactTable | ArtifactFile


class QueryCompleteEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    session_id: str | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    debug: dict[str, Any] | None = None
    # Amendment A-4 (2026-06-11): the LOCAL E2E stack (worktree dmac-integration,
    # branch integration/dmac-assistant) emits query_complete data carrying
    # bundle_id + files (chat_nextseek.orchestrator._emit_query_complete; live-verified
    # keys: bundle_id, debug, files, reply, session_id). The pinned origin/dev@935f5fa
    # mirror lacked them, so query/plan tripped extra_forbidden -> exit 4. Added as
    # OPTIONAL; extra="forbid" is preserved for all other unknown keys. files items are
    # file-manifest dicts (key/label/path/filename/mime + optional kind/bundle_id/step_id
    # per chat_nextseek.artifacts.build_file_manifest_entry) -> modeled as raw dicts, the
    # same raw-dict treatment the local stack gives Turn.artifacts for the same reason.
    bundle_id: int | None = None
    files: list[dict[str, Any]] | None = None


class QueryErrorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str
    agent: str | None = None
    session_id: str | None = None


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_id: int
    user_query: str
    reply: str
    mode: str
    # T13 (2026-06-11): live `session_detail(include_turns=True)` against the
    # LOCAL E2E stack (worktree dmac-integration) emits Turn objects carrying `ts`
    # (ISO-8601 timestamp string) and `artifacts` (None, or a list of artifact
    # manifest dicts) that the pinned origin/dev@935f5fa mirror lacked — same drift
    # class A-4 fixed for QueryCompleteEvent. Added as OPTIONAL; extra="forbid" is
    # preserved for every OTHER unknown key so future drift still fails loudly.
    # `artifacts` is modeled as raw dicts (the same treatment QueryCompleteEvent.files
    # gets) since the item shape is a file/table manifest, not a fixed schema.
    ts: str | None = None
    artifacts: list[dict[str, Any]] | None = None


class SessionDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    created_at: datetime
    query_count: int
    has_results: bool
    title: str | None = None
    turns: list[Turn] | None = None


class BundleDownloadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    bundle_id: int


class AsyncQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    session_id: UUID


class ProgressEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class TaskProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    session_id: UUID
    status: str
    progress: list[ProgressEvent] = Field(default_factory=list)
    result: dict[str, Any] | None = None


SessionDetailResponse.model_rebuild()
