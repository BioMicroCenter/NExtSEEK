"""Pipeline directive-parse step — LLM call that turns a user message into a structured pipeline directive.

Was: `_pipeline_directive_parse` in agents.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...schemas.schema_helper import call_llm_structured

if TYPE_CHECKING:
    from ...config import ChatConfig
    from ...schemas.pipeline import DirectiveParseOutput


def _pipeline_directive_parse(
    *,
    config: ChatConfig,
    user_query: str,
    pinned_bundle_summary: str = "",
) -> "DirectiveParseOutput":
    """First LLM step of pipeline_agent. Parses a user message into structured intent."""
    from ...schemas.pipeline import DirectiveParseOutput
    from ...seqera.catalog import NFCORE_PIPELINE_CATALOG

    catalog_block = "\n".join(
        f"- {k}: {entry.get('description', '')} "
        f"(input: {entry.get('samplesheet_input_kind', '?')})"
        for k, entry in NFCORE_PIPELINE_CATALOG.items()
    )
    prompt_template = config._load_prompt("pipeline_agent_directive.txt")
    system_prompt = prompt_template.replace("{available_pipelines_catalog}", catalog_block)

    user_block_parts = [f"USER MESSAGE: {user_query!r}"]
    if pinned_bundle_summary:
        user_block_parts.append(f"PINNED BUNDLE SUMMARY: {pinned_bundle_summary}")
    user_block = "\n".join(user_block_parts)

    print(f"[DEBUG][PIPELINE_DIRECTIVE] parsing user_query={user_query!r}")

    client, model_name, budget = config.get_agent_model("pipeline_directive")
    try:
        return call_llm_structured(
            config,
            user_block,
            DirectiveParseOutput,
            system=system_prompt,
            model_name=model_name,
            temperature=0,
            log_label="pipeline_directive",
            usage_label="PIPELINE_DIRECTIVE",
            thinking_budget=budget,
            client=client,
        )
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_DIRECTIVE] LLM call failed: {exc!r}")
        return DirectiveParseOutput(
            sub_mode="reject",
            rejection_reason=f"Couldn't parse the directive (LLM error: {exc!r}). Try rephrasing.",
        )
