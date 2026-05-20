"""Agents package — per-agent functions for the chat_nextseek pipeline.

PUBLIC API: external consumers (notably dmac_assistant's nextseek plugin)
import via `chat_nextseek.agents.<name>`. DO NOT remove re-exports from
this file without coordinating with downstream consumers. See CLAUDE.md
"Portability Contract" section.
"""
from __future__ import annotations

# Test-compat re-export: some tests patch chat_nextseek.agents.call_llm_structured.
# Preserve the legacy module-level attribute even though all callers now use
# their own per-file imports of this symbol.
from ..schemas.schema_helper import call_llm_structured  # noqa: F401

from .entity import entity_agent  # noqa: F401
from .api import api_agent_build_request  # noqa: F401
from .reporter import reporter_agent, report_writer_agent  # noqa: F401
from .chatter import chatter_agent_answer, chatter_agent_plan  # noqa: F401
from .memory import (  # noqa: F401
    _strip_python_code_fences,
    _load_memory_json_payload,
    memory_coder_agent,
    _format_memory_coder_answer,
    _legacy_memory_agent_answer,
    memory_agent_answer,
)
from .system import system_agent  # noqa: F401
from .graph import graph_agent  # noqa: F401
from .seqera import seqera_agent  # noqa: F401
from .wizard import (  # noqa: F401
    WizardToolLoopError,
    _wizard_agent_builder,
    _build_wizard_builder_system_prompt,
    wizard_agent,
    MAX_TOOL_ITERATIONS,
)
from .planner.agent import (  # noqa: F401
    _normalize_planner_output,
    _planner_runtime_state,
    _normalize_planner_decision,
    multi_parser_agent,
    planner_agent,
    context_engineer_step,
)
from .planner.tools import (  # noqa: F401
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
from .planner.execution import (  # noqa: F401
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
from .planner.evaluator import plan_evaluator_agent  # noqa: F401
from .parser import (  # noqa: F401
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
from ..pipeline.steps.directive import _pipeline_directive_parse  # noqa: F401
from ..pipeline.steps.sanity import _pipeline_sanity_check  # noqa: F401
from ..pipeline.steps.groupby import _pipeline_groupby_resolution  # noqa: F401
from ..pipeline.steps.edit import _pipeline_edit_step  # noqa: F401
from ..pipeline.steps.question import _pipeline_question_step  # noqa: F401
