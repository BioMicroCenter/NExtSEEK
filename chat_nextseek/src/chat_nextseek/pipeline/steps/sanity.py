"""Pipeline sanity-check step — LLM call that confirms pipeline-vs-data match.

Was: `_pipeline_sanity_check` in agents.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...schemas.schema_helper import call_llm_structured

if TYPE_CHECKING:
    from ...config import ChatConfig
    from ...schemas.pipeline import SanityCheckOutput


def _pipeline_sanity_check(
    *,
    config: "ChatConfig",
    pipeline_key: str,
    resolution: dict,
) -> "SanityCheckOutput":
    """Second LLM step of pipeline_agent. Confirms pipeline-vs-data match."""
    from ...schemas.pipeline import SanityCheckOutput
    from ...seqera.catalog import NFCORE_PIPELINE_CATALOG

    catalog_entry = NFCORE_PIPELINE_CATALOG.get(pipeline_key, {})
    prompt_template = config._load_prompt("pipeline_agent_sanity.txt")

    leaves_filtered = resolution.get("leaves_filtered") or []
    dropped = resolution.get("dropped_by_assay_mismatch") or []
    bundle = resolution.get("metadata_bundle") or {}

    try:
        from ...helpers import build_metadata_summary, filter_summary_to_sequencing_lineage
        summary = build_metadata_summary({"__sample__": bundle})
        summary = filter_summary_to_sequencing_lineage(summary)
        md_preview = str(summary)[:2000]
    except Exception:
        md_preview = "(metadata summary unavailable)"

    def _preview(items, n=20):
        return "\n".join(
            f"  - {item.get('uid')} ({item.get('assay', '?')})" for item in items[:n]
        )

    filled = prompt_template
    filled = filled.replace("{pipeline_key}", pipeline_key)
    filled = filled.replace("{pipeline_kind_description}",
                            str(catalog_entry.get("pipeline_kind_description", "")))
    filled = filled.replace("{accepted_assay_patterns}",
                            str(catalog_entry.get("accepted_assay_patterns", [])))
    filled = filled.replace("{source_uid_count}", str(len(resolution.get("source_uids") or [])))
    filled = filled.replace("{leaves_all_count}", str(len(resolution.get("leaves_all") or [])))
    filled = filled.replace("{leaves_filtered_count}", str(len(leaves_filtered)))
    filled = filled.replace("{dropped_by_assay_count}", str(len(dropped)))
    filled = filled.replace("{orphans_count}",
                            str(len(resolution.get("source_uids_with_no_leaves") or [])))
    filled = filled.replace("{leaves_filtered_preview}", _preview(leaves_filtered))
    filled = filled.replace("{dropped_by_assay_preview}", _preview(dropped))
    filled = filled.replace("{metadata_summary_preview}", md_preview)

    print(f"[DEBUG][PIPELINE_SANITY] pipeline={pipeline_key} "
          f"filtered={len(leaves_filtered)} dropped={len(dropped)}")

    client, model_name, budget = config.get_agent_model("pipeline_sanity")
    try:
        return call_llm_structured(
            config,
            filled,
            SanityCheckOutput,
            system="You are a pipeline-vs-data sanity checker. Return only the JSON object.",
            model_name=model_name,
            temperature=0,
            log_label="pipeline_sanity",
            usage_label="PIPELINE_SANITY",
            thinking_budget=budget,
            client=client,
        )
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_SANITY] LLM call failed: {exc!r}; fail-open")
        leaf_uids = [leaf["uid"] for leaf in leaves_filtered if leaf.get("uid")]
        return SanityCheckOutput(
            verdict="proceed",
            leaves_to_use=leaf_uids,
            confidence_note=f"Sanity LLM failed ({exc!r}); proceeding with the {len(leaf_uids)} filtered leaves.",
        )
