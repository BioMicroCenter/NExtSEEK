"""Pipeline sanity-check step — LLM call that confirms pipeline-vs-data match.

The LLM determines, from the candidate leaves' metadata, which are the right
data type for the requested pipeline. To stay scalable and avoid self-truncation
on large cohorts, candidate leaves are first clustered into a handful of
signature groups; the LLM classifies the *groups* (returning a few group ids)
and code expands the accepted ids back to concrete leaf UIDs. `assay` is just one
metadata signal, never the sole gate. (See feedback-llm-determines-data-type.)

Was: `_pipeline_sanity_check` in agents.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...schemas.schema_helper import call_llm_structured
from .signature import group_leaves_by_signature

if TYPE_CHECKING:
    from ...config import ChatConfig
    from ...schemas.pipeline import SanityCheckOutput


def _format_groups_for_prompt(groups: list[dict]) -> str:
    """Render signature groups as compact lines for the sanity prompt."""
    if not groups:
        return "(no candidate sequencing leaves)"
    lines = []
    for g in groups:
        sig = "; ".join(f"{k}={v}" for k, v in sorted(g["signature"].items()))
        sig = sig or "(no shared descriptor fields)"
        examples = ", ".join(g["leaf_uids"][:3])
        lines.append(
            f"- {g['group_id']}: {g['n_leaves']} leaves | {sig} | e.g. {examples}"
        )
    return "\n".join(lines)


def _expand_accepted(out: "SanityCheckOutput", groups: list[dict],
                     group_uids: dict[str, list[str]]) -> list[str]:
    """Expand the LLM's accepted group ids to deduplicated leaf UIDs. Falls back
    to any explicit leaves_to_use the model returned, else to all candidates."""
    accepted = out.accepted_signature_groups or []
    if accepted:
        seen: set[str] = set()
        expanded: list[str] = []
        for gid in accepted:
            for uid in group_uids.get(gid, []):
                if uid not in seen:
                    seen.add(uid)
                    expanded.append(uid)
        return expanded
    if out.leaves_to_use:
        return list(out.leaves_to_use)
    return [uid for g in groups for uid in g["leaf_uids"]]


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

    candidates = resolution.get("leaves_filtered") or resolution.get("leaves_all") or []
    groups = group_leaves_by_signature(candidates)
    group_uids = {g["group_id"]: list(g["leaf_uids"]) for g in groups}

    bundle = resolution.get("metadata_bundle") or {}
    try:
        from ...helpers import build_metadata_summary, filter_summary_to_sequencing_lineage
        summary = build_metadata_summary({"__sample__": bundle})
        summary = filter_summary_to_sequencing_lineage(summary)
        md_preview = str(summary)[:2000]
    except Exception:
        md_preview = "(metadata summary unavailable)"

    filled = prompt_template
    filled = filled.replace("{pipeline_key}", pipeline_key)
    filled = filled.replace("{pipeline_kind_description}",
                            str(catalog_entry.get("pipeline_kind_description", "")))
    filled = filled.replace("{accepted_assay_patterns}",
                            str(catalog_entry.get("accepted_assay_patterns", [])))
    filled = filled.replace("{source_uid_count}", str(len(resolution.get("source_uids") or [])))
    filled = filled.replace("{candidate_leaf_count}", str(len(candidates)))
    filled = filled.replace("{group_count}", str(len(groups)))
    filled = filled.replace("{signature_groups}", _format_groups_for_prompt(groups))
    filled = filled.replace("{orphans_count}",
                            str(len(resolution.get("source_uids_with_no_leaves") or [])))
    filled = filled.replace("{metadata_summary_preview}", md_preview)

    print(f"[DEBUG][PIPELINE_SANITY] pipeline={pipeline_key} "
          f"candidates={len(candidates)} groups={len(groups)}")

    client, model_name, budget = config.get_agent_model("pipeline_sanity")
    try:
        out = call_llm_structured(
            config,
            filled,
            SanityCheckOutput,
            system=(
                "You are a pipeline-vs-data sanity checker. Decide, from each "
                "group's metadata, which groups are the right data type for the "
                "pipeline. Return only the JSON object."
            ),
            model_name=model_name,
            temperature=0,
            log_label="pipeline_sanity",
            usage_label="PIPELINE_SANITY",
            thinking_budget=budget,
            client=client,
        )
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_SANITY] LLM call failed: {exc!r}; fail-open")
        all_uids = [uid for g in groups for uid in g["leaf_uids"]]
        if not all_uids:
            all_uids = [lf["uid"] for lf in candidates if lf.get("uid")]
        return SanityCheckOutput(
            verdict="proceed",
            accepted_signature_groups=[g["group_id"] for g in groups],
            leaves_to_use=all_uids,
            confidence_note=f"Sanity LLM failed ({exc!r}); proceeding with all "
                            f"{len(all_uids)} candidate leaves.",
        )

    # Expand the LLM's accepted group ids → concrete leaf UIDs for the build step.
    if out.verdict in ("proceed", "proceed_with_subset"):
        out.leaves_to_use = _expand_accepted(out, groups, group_uids)
    return out
