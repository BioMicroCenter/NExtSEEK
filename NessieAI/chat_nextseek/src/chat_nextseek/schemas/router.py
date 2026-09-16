from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .entity import EntityAgentOutput


class ParserFilters(BaseModel):
    sampletype_code: str | None = None
    assay_codes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    uids: list[str] = Field(default_factory=list)
    lab_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class EndpointCandidate(BaseModel):
    endpoint: str
    rationale: str | None = None
    confidence: float | None = None

    model_config = ConfigDict(extra="ignore")


#: The modes the orchestrator actually dispatches on, in one place so the schema the
#: model is handed and the branches that consume it cannot drift apart.
#: ``memory_lookup`` is an accepted alias the parser normalises to
#: ``ask_about_last_results``.
PARSER_MODES: tuple[str, ...] = (
    "new_search",
    "refine_last_search",
    "ask_about_last_results",
    "memory_lookup",
    "system_question",
    "reporter",
    "graph_query",
    "unsupported",
)


class ParserPlan(BaseModel):
    # The field stays `str`, not a Literal: an unrecognised mode must reach the
    # orchestrator's "unexpected mode" branch and get a civil reply, not fail
    # validation and burn the repair loop. The enum is published in the JSON schema
    # instead, where it constrains a schema-shaped (forced tool call) request and is a
    # strong hint everywhere else. Guard: tests/chat_nextseek/test_structured_via_tools.py.
    mode: str = Field(default="unsupported", json_schema_extra={"enum": list(PARSER_MODES)})
    target_endpoint: str | None = None
    intent_summary: str = ""
    filters: ParserFilters = Field(default_factory=ParserFilters)
    resolved: EntityAgentOutput = Field(default_factory=EntityAgentOutput)
    target_result_id: int | None = None
    endpoint_candidates: list[str | EndpointCandidate] = Field(default_factory=list)
    notes: str = ""
    previous_api_plan: dict[str, Any] | None = None
    previous_user_query: str | None = None
    report_mode: str | None = None
    report_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class RouterDecision(BaseModel):
    chosen_agent: str
    rationale: str = ""
    confidence: float | None = None
    required_context: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class ParserCandidate(BaseModel):
    """One viable execution path identified by the multi-path parser."""
    candidate_id: str | None = None
    mode: str  # new_search | refine_last_search | ask_about_last_results | graph_query | reporter | system_question | unsupported
    target_endpoint: str | None = None
    filters: ParserFilters = Field(default_factory=ParserFilters)
    report_mode: str | None = None
    report_type: str | None = None
    tool_query: str = ""
    criterion_scope: str = ""  # structural | attribute | aggregate | memory | system | unsupported
    can_intersect: bool = False
    requires_input: list[str] = Field(default_factory=list)
    output_fields: list[str] = Field(default_factory=list)
    covers_constraints: list[str] = Field(default_factory=list)
    missing_constraints: list[str] = Field(default_factory=list)
    fully_answers_query: bool | None = None
    composition_role: str = ""
    compatible_with: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    confidence: float | None = None

    model_config = ConfigDict(extra="ignore")


class MultiParserPlan(BaseModel):
    """Output of the multi-path parser: all viable execution paths, most preferred first."""
    intent_summary: str = ""
    resolved: EntityAgentOutput = Field(default_factory=EntityAgentOutput)
    candidates: list[ParserCandidate] = Field(default_factory=list)
    notes: str = ""

    model_config = ConfigDict(extra="ignore")
