"""Pipeline-agent structured-output schemas.

Each LLM step in pipeline_agent returns one of these. The agent's session
state stores them as dicts (Pydantic `.model_dump()`); the per-turn
dispatcher re-instantiates them when reading.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Directive parse
# ---------------------------------------------------------------------------


class SamplesRef(BaseModel):
    """How the user referenced their samples in the directive."""

    kind: Literal["last_search", "explicit_uids", "named_cohort", "accessions"]
    uids: list[str] = Field(default_factory=list)
    cohort_name: str | None = None
    accessions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class DirectiveParseOutput(BaseModel):
    """First LLM step: turn a user message into structured pipeline intent."""

    sub_mode: Literal["build", "question", "reject"]
    pipeline_key: str | None = None
    samples_ref: SamplesRef | None = None
    group_by_phrase: str | None = None
    multi_pipeline_attempt: bool = False
    rejection_reason: str | None = None
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Sanity check (pipeline-vs-data)
# ---------------------------------------------------------------------------


class SanityCheckOutput(BaseModel):
    """Second LLM step: confirm chosen pipeline matches available data."""

    verdict: Literal["proceed", "proceed_with_subset", "mismatch", "ambiguous"]
    leaves_to_use: list[str] = Field(default_factory=list)
    dropped_leaves_summary: str = ""
    suggested_alternative_pipeline: str | None = None
    clarifying_question: str | None = None
    confidence_note: str = ""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Group-by resolution
# ---------------------------------------------------------------------------


class FieldRef(BaseModel):
    """Reference to a metadata field on a specific sample type."""

    sample_type: str
    field_name: str

    model_config = ConfigDict(extra="ignore")


class GroupByResolution(BaseModel):
    """Third LLM step: resolve a user phrase to a canonical metadata field."""

    field: FieldRef
    distinct_values: list[str] = Field(default_factory=list)
    rationale: str = ""
    requires_clarification: bool = False
    candidates: list[FieldRef] = Field(default_factory=list)
    clarifying_question: str | None = None

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Edit-by-reprompt
# ---------------------------------------------------------------------------


class EditDiffOutput(BaseModel):
    """Edit step: apply a user edit message to the current samplesheet rows."""

    action: Literal["apply", "ask", "reject"]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    ask_reply: str | None = None
    reject_reason: str | None = None
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


__all__ = [
    "SamplesRef",
    "DirectiveParseOutput",
    "SanityCheckOutput",
    "FieldRef",
    "GroupByResolution",
    "EditDiffOutput",
]
