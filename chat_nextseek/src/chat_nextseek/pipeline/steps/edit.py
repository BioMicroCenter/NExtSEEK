"""Pipeline edit step — LLM call that applies a free-text edit message to in-memory samplesheet rows.

Was: `_pipeline_edit_step` in agents.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...schemas.schema_helper import call_llm_structured

if TYPE_CHECKING:
    from ...config import ChatConfig
    from ...schemas.pipeline import EditDiffOutput


def _pipeline_edit_step(
    *,
    config: "ChatConfig",
    pipeline_key: str,
    current_rows: list[dict],
    user_text: str,
) -> "EditDiffOutput":
    """Fourth LLM step of pipeline_agent. Applies a free-text edit message
    to the in-memory samplesheet rows."""
    import json as _json
    from ...schemas.pipeline import EditDiffOutput
    from ...seqera.catalog import NFCORE_PIPELINE_CATALOG

    catalog_entry = NFCORE_PIPELINE_CATALOG.get(pipeline_key, {})
    required_columns = catalog_entry.get("required_columns") or []

    prompt = (
        config._load_prompt("pipeline_agent_edit.txt")
        .replace("{pipeline_key}", pipeline_key)
        .replace("{current_rows_json}", _json.dumps(current_rows, indent=2))
        .replace("{required_columns}", _json.dumps(required_columns))
        .replace("{user_text}", user_text or "")
    )

    print(f"[DEBUG][PIPELINE_EDIT] pipeline={pipeline_key} rows={len(current_rows)} edit={user_text!r}")

    client, model_name, budget = config.get_agent_model("pipeline_edit")
    try:
        return call_llm_structured(
            config,
            prompt,
            EditDiffOutput,
            system="You are the pipeline_agent's edit step. Return only the JSON object.",
            model_name=model_name,
            temperature=0,
            log_label="pipeline_edit",
            usage_label="PIPELINE_EDIT",
            thinking_budget=budget,
            client=client,
        )
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_EDIT] LLM call failed: {exc!r}")
        return EditDiffOutput(
            action="ask",
            ask_reply=f"Couldn't apply that edit (LLM error: {exc!r}). Please rephrase.",
        )
