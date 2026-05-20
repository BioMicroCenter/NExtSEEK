from __future__ import annotations

import json
import re
import calendar
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from .session import SessionState

from .config import ChatConfig
from .artifacts import ArtifactStore

from .llm_clients import LLMAPIConnectionError, LLMRateLimitError, LLMTimeoutError
from .helpers import (
    _retry_advanced_search_if_empty,
    build_memory_data_profile,
    build_recent_results_summary,
    collect_bundle_files,
    execute_memory_code,
    fix_sample_endpoint,
    load_json_for_memory,
    load_file_for_memory,
    log_prompt,
    log_usage,
    run_reporter_summary,
    safe_parse_json,
    strip_html,
    strip_html_recursive,
    tool_nextseek_api_request,
    tool_neo4j_query,
    generate_report_outputs,
    normalize_report_type,
)
from .schemas.schema_helper import StructuredOutputError, call_llm_structured
from .schemas import (
    APIRequestPlan,
    ContextEngineerOutput,
    EntityAgentOutput,
    GraphAgentPlan,
    MemoryCoderOutput,
    MultiParserPlan,
    PipelineCohort,
    PlannerDecisionOutput,
    PlanEvaluatorOutput,
    ParserCandidate,
    ParserFilters,
    ParserPlan,
    PlannerOutput,
    PlanStep,
    ReporterPlan,
    ReportWriterOutput,
    ReportWriterPlan,
    SeqeraLaunchPlan,
    StepExecutionPayload,
    StepInputRef,
    StepOutcome,
    StepOutputMapping,
    SystemAgentOutput,
    WizardAgentOutput,
)
from .seqera.catalog import get_pipeline_entry












# Symbols moved to agents_new in Phase 3 — re-exported for backward compat
from .agents_new.entity import entity_agent  # noqa: E402,F401
from .agents_new.api import api_agent_build_request  # noqa: E402,F401
from .agents_new.reporter import reporter_agent, report_writer_agent  # noqa: E402,F401
from .agents_new.chatter import chatter_agent_answer, chatter_agent_plan  # noqa: E402,F401
from .agents_new.memory import (  # noqa: E402,F401
    _strip_python_code_fences,
    _load_memory_json_payload,
    memory_coder_agent,
    _format_memory_coder_answer,
    _legacy_memory_agent_answer,
    memory_agent_answer,
)
from .agents_new.system import system_agent  # noqa: E402,F401
from .agents_new.graph import graph_agent  # noqa: E402,F401
from .agents_new.seqera import seqera_agent  # noqa: E402,F401
from .agents_new.wizard import (  # noqa: E402,F401
    WizardToolLoopError,
    _wizard_agent_builder,
    _build_wizard_builder_system_prompt,
    wizard_agent,
    MAX_TOOL_ITERATIONS,
)
from .agents_new.planner.agent import (  # noqa: E402,F401
    _normalize_planner_output,
    _planner_runtime_state,
    _normalize_planner_decision,
    multi_parser_agent,
    planner_agent,
    context_engineer_step,
)
from .agents_new.planner.tools import (  # noqa: E402,F401
    _plan_tool_graph_query,
    _plan_tool_new_search,
    _plan_tool_reporter,
    _plan_tool_refine_last_search,
    _plan_tool_report_generation,
    _select_memory_bundle_for_step,
    _plan_tool_memory_lookup,
    _plan_tool_ask_about_last_results,
    _plan_tool_coding_filter,
    _plan_tool_system_question,
    _plan_tool_unsupported,
    _PLAN_TOOL_DISPATCH,
)
from .agents_new.planner.execution import (  # noqa: E402,F401
    _extract_uids_from_output,
    _extract_fields_from_output,
    _inject_context,
    _step_signature,
    _run_plan_tool,
    _execute_single_plan_step,
    _materialize_intersection_result,
    _execute_plan_steps,
    _PLAN_TOOL_AGENT_NAMES,
    _PLAN_TOOL_SEARCH_SOURCES,
    _TERMINAL_REPLY_TOOLS,
)
from .agents_new.planner.evaluator import plan_evaluator_agent  # noqa: E402,F401
from .agents_new.parser import (  # noqa: E402,F401
    _infer_report_type_from_query,
    _filters_have_any_value,
    _is_unscoped_bulk_export_request,
    _unsupported_bulk_export_candidate,
    _candidate_output_mapping,
    _fill_candidate_defaults,
    _build_step_from_candidate,
    _normalize_plan_step,
    _finalize_plan_steps,
    _resolve_step_inputs,
    _step_query,
    _fallback_multi_parser_plan,
    _canonical_multi_parse,
    _candidate_to_parser_plan,
    _apply_parser_guardrails,
    _apply_multi_parser_guardrails,
    parser_agent,
    _synthesize_top_candidate_plan,
    _filters_have_substance,
    _scope_only_graph_candidate,
    _find_mixed_scope_intersection_plan,
    _append_coding_filter_step_if_needed,
)

# Pipeline step shims — moved to pipeline/steps/* in Phase 1
from .pipeline.steps.directive import _pipeline_directive_parse  # noqa: E402,F401
from .pipeline.steps.sanity import _pipeline_sanity_check  # noqa: E402,F401
from .pipeline.steps.groupby import _pipeline_groupby_resolution  # noqa: E402,F401
from .pipeline.steps.edit import _pipeline_edit_step  # noqa: E402,F401
from .pipeline.steps.question import _pipeline_question_step  # noqa: E402,F401
