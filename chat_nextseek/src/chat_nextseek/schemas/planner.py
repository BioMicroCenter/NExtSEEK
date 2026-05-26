from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StepInputRef(BaseModel):
    from_step: int
    field: str
    required: bool = True
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


class StepExecutionPayload(BaseModel):
    mode: str = ""
    target_endpoint: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    report_mode: str | None = None
    report_type: str | None = None
    tool_query: str = ""
    parser_candidate_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class StepOutputMapping(BaseModel):
    field: str
    source: Literal["rows", "output", "llm_extract"] = "rows"
    keys: list[str] = Field(default_factory=list)
    value_type: str = "list[str]"
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


class StepOutcome(BaseModel):
    proceed: bool = True
    halt_on_empty: bool = False
    halt_on_error: bool = True
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


class PlanStep(BaseModel):
    step_id: int
    tool: Literal[
        "graph_query",
        "new_search",
        "refine_last_search",
        "ask_about_last_results",
        "reporter",
        "report_generation",
        "memory_lookup",
        "coding_filter",
        "system_question",
        "unsupported",
    ]
    context_prompt: str          # compatibility field; superseded by execution.tool_query when present
    target_endpoint: str | None = None    # compatibility field; superseded by execution.target_endpoint when present
    combine_mode: Literal["sequential", "intersect"] = "sequential"
    # sequential: output mapping from prior step is injected into this step via input_mapping
    # intersect:  this step runs independently; orchestrator intersects UID sets from ALL steps afterward
    extraction_hint: str = ""    # compatibility field; use output_mapping / transformation_hint for new plans
    depends_on: int | None = None
    required: bool = True        # if False, failure is non-fatal (step is skipped gracefully)
    execution: StepExecutionPayload = Field(default_factory=StepExecutionPayload)
    input_mapping: dict[str, StepInputRef] = Field(default_factory=dict)
    output_mapping: dict[str, StepOutputMapping] = Field(default_factory=dict)
    transformation_hint: str = ""
    needs_context_engineer: bool = False
    outcome: StepOutcome = Field(default_factory=StepOutcome)
    notes: str = ""

    model_config = ConfigDict(extra="ignore")

    @field_validator("extraction_hint", "transformation_hint", mode="before")
    @classmethod
    def _normalize_nullable_string_fields(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)


class PlannerOutput(BaseModel):
    intent_summary: str
    steps: list[PlanStep]        # ordered; single-step plans are valid and expected for simple queries
    notes: str = ""


class PlannerDecisionOutput(BaseModel):
    intent_summary: str
    action: Literal["execute_step", "halt"] = "execute_step"
    step: PlanStep | None = None
    rationale: str = ""
    uncovered_constraints: list[str] = Field(default_factory=list)
    termination_reason: Literal[
        "answered",
        "step_budget_exhausted",
        "no_viable_next_step",
        "repeated_strategy",
        "missing_required_inputs",
        "hard_step_failure",
    ] | None = None
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


class ContextEngineerOutput(BaseModel):
    extraction_code: str         # Python snippet; exec'd against `data` variable (the step's full output)
    enriched_context: dict       # result of exec; injected into next step's context_prompt
    method: Literal["code", "llm"]
    notes: str = ""


class PlanEvaluatorOutput(BaseModel):
    answered_query: bool
    execution_consistent: bool
    overall_status: Literal["success", "partial", "failure"]
    zero_results_assessment: Literal["not_applicable", "acceptable", "suspicious"] = "not_applicable"
    failure_stage: Literal["multi_parser", "planner", "executor", "reply", "unknown"] | None = None
    reason: str = ""
    what_went_wrong: str = ""
    user_safe_summary: str = ""
    confidence: float | None = None

    model_config = ConfigDict(extra="ignore")
