"""Pipeline question step — LLM call that answers a pipeline-domain question from catalog + capabilities.

Was: `_pipeline_question_step` in agents.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...helpers import log_prompt, log_usage

if TYPE_CHECKING:
    from ...config import ChatConfig


def _pipeline_question_step(
    *,
    config: "ChatConfig",
    user_query: str,
    pinned_bundle_summary: str = "",
) -> str:
    """Fifth LLM step. Answers a pipeline-domain question from catalog + capabilities.

    Returns a plain-text answer (no Pydantic wrapping). Falls back to a short
    error string on LLM failure so the caller can still return a useful reply.
    """
    from ...seqera.catalog import NFCORE_PIPELINE_CATALOG

    catalog_block_lines = []
    for k, entry in NFCORE_PIPELINE_CATALOG.items():
        catalog_block_lines.append(
            f"- {k}: {entry.get('pipeline_kind_description', '')} "
            f"(input={entry.get('samplesheet_input_kind', '?')}, "
            f"leaf_types={entry.get('accepted_leaf_sample_types', [])})"
        )
    catalog_block = "\n".join(catalog_block_lines)

    prompt = (
        config._load_prompt("pipeline_agent_question.txt")
        .replace("{user_query}", user_query)
        .replace("{catalog_block}", catalog_block)
        .replace("{pinned_bundle_summary}", pinned_bundle_summary or "(none)")
    )

    print(f"[DEBUG][PIPELINE_QUESTION] user_query={user_query!r}")

    client, model_name, budget = config.get_agent_model("pipeline_question")
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_query},
    ]
    try:
        resp = client.chat(
            model=model_name,
            temperature=0,
            messages=messages,
            thinking_budget=budget,
        )
        log_usage(resp, "PIPELINE_QUESTION")
        answer = (resp.content or "").strip()
        log_prompt(
            config.LOG_DIR,
            "pipeline_question",
            {
                "user_query": user_query,
                "pinned_bundle_summary": pinned_bundle_summary,
                "messages": messages,
                "response": answer,
            },
        )
        return answer or "I don't have a clear answer for that. Try the regular assistant."
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_QUESTION] LLM call failed: {exc!r}")
        return f"Couldn't answer that (LLM error: {exc!r}). Try asking the regular assistant."
