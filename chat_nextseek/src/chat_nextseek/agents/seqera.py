from __future__ import annotations

import json
import time
from typing import Any

from ..config import ChatConfig
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    SeqeraLaunchPlan,
)
from ..seqera.catalog import get_pipeline_entry


def seqera_agent(
    config: ChatConfig,
    user_query: str,
    pipeline: str,
    samplesheet_preview: dict | None = None,
    reporter_context_summary: dict | None = None,
) -> SeqeraLaunchPlan:
    """Produce a SeqeraLaunchPlan (run name + params + revision/profile overrides).

    Falls back to a minimal catalog-default plan on failure so emission still
    proceeds. The emitter wires workspace/compute-env/work-dir from env vars.
    """
    entry = get_pipeline_entry(pipeline)
    fallback = SeqeraLaunchPlan(
        run_name=f"{pipeline}-run-{int(time.time())}",
        params={"genome": entry.get("default_genome")} if entry.get("default_genome") else {},
        outdir_suffix="",
        work_dir_suffix="",
        pipeline_revision=None,
        profile=entry.get("default_profile"),
        notes="catalog default",
    )

    messages = [
        {"role": "system", "content": config.SEQERA_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": f"PIPELINE: {pipeline}"},
        {"role": "system", "content": f"PIPELINE_ENTRY:\n{json.dumps(entry, indent=2)}"},
        {"role": "system", "content": f"SAMPLESHEET_PREVIEW:\n{json.dumps(samplesheet_preview or {}, indent=2)}"},
        {"role": "system", "content": f"REPORTER_CONTEXT_SUMMARY:\n{json.dumps(reporter_context_summary or {}, indent=2)}"},
        {"role": "user", "content": user_query},
    ]

    print(f"\n[DEBUG][SEQERA_AGENT] pipeline={pipeline} cohort_label={(samplesheet_preview or {}).get('cohort_label') or '(single)'}")

    client, model_name, budget = config.get_agent_model("seqera_agent")
    try:
        result = call_llm_structured(
            config=config,
            prompt="Produce a Tower launch plan for the chosen pipeline.",
            model=SeqeraLaunchPlan,
            messages=messages,
            model_name=model_name,
            temperature=0,
            log_label="seqera_agent",
            usage_label="SEQERA_AGENT",
            thinking_budget=budget,
            client=client,
        )
        # Strip any forbidden keys the LLM might have emitted.
        params = dict(result.params or {})
        for forbidden in ("input", "outdir"):
            params.pop(forbidden, None)
        final = result.model_copy(update={"params": params})
        print(
            f"[DEBUG][SEQERA_AGENT] Parsed plan: run_name={final.run_name!r} "
            f"params={final.params} revision={final.pipeline_revision} profile={final.profile}"
        )
        return final
    except Exception as e:
        print(f"[DEBUG][SEQERA_AGENT] LLM failed: {e!r}; using fallback plan.")
        return fallback

