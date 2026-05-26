from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from ..config import ChatConfig
from ..artifacts import ArtifactStore
from ..llm_clients import LLMAPIConnectionError
from ..helpers import (
    build_memory_data_profile,
    collect_bundle_files,
    execute_memory_code,
    load_json_for_memory,
    load_file_for_memory,
    log_prompt,
    log_usage,
    strip_html_recursive,
)
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    MemoryCoderOutput,
)


def _strip_python_code_fences(code: str) -> str:
    """Remove common markdown code fences from generated Python snippets."""
    text = (code or "").strip()
    match = re.match(r"^```(?:python)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _load_memory_json_payload(result_bundle: dict) -> tuple[str, Any, list[tuple[str, str]]]:
    """
    Load the most relevant JSON payload for memory analysis.
    Prefers canonical in-memory payloads, then saved JSON artifacts, then inline bundle data.
    """
    file_entries = collect_bundle_files(result_bundle)
    memory_payload = result_bundle.get("memory_payload")
    if isinstance(memory_payload, dict) and memory_payload:
        return "bundle memory_payload", strip_html_recursive(memory_payload), file_entries

    def _memory_file_priority(entry: tuple[str, str]) -> int:
        label, path = entry
        text = f"{label} {path}".lower()
        if "api result" in text or "/api/" in text:
            return 0
        if "graph" in text:
            return 1
        if "report" in text and "plan debug" not in text:
            return 2
        if "plan debug" in text:
            return 9
        return 5

    for label, path in sorted(file_entries, key=_memory_file_priority):
        if not str(path).lower().endswith(".json"):
            continue
        try:
            return label, load_json_for_memory(path), file_entries
        except Exception as e:
            print(f"[DEBUG][MEMORY_CODER] Failed to load JSON artifact {path}: {e!r}")

    step_results = result_bundle.get("step_results") or {}
    if isinstance(step_results, dict):
        for step_id, sr in step_results.items():
            if not isinstance(sr, dict) or not sr.get("ok"):
                continue
            output = sr.get("output") or {}
            if not isinstance(output, dict):
                continue
            rows = output.get("data")
            if isinstance(rows, list):
                return (
                    f"inline planner step {step_id} rows",
                    {
                        "data": {
                            "rows": strip_html_recursive(rows),
                            "total": output.get("count", len(rows)),
                        },
                        "api_plan": output.get("api_plan"),
                        "endpoint": output.get("endpoint"),
                        "tool": sr.get("tool"),
                    },
                    file_entries,
                )
            reply = output.get("reply")
            if isinstance(reply, str) and reply.strip():
                return (
                    f"inline planner step {step_id} reply",
                    {"reply": reply, "count": output.get("count"), "tool": sr.get("tool")},
                    file_entries,
                )

    inline = (
        result_bundle.get("memory_payload")
        or result_bundle.get("model_outputs", {}).get("memory_payload")
        or result_bundle.get("graph_result")
        or result_bundle.get("api_result_full")
        or result_bundle.get("api_result_slim")
        or {}
    )
    return "inline bundle data", strip_html_recursive(inline), file_entries


def memory_coder_agent(
    config: ChatConfig,
    *,
    original_query: str,
    user_query: str,
    data_profile: dict,
    log_dir: str | None = None,
) -> MemoryCoderOutput:
    """Ask the memory coder to write a deterministic extraction snippet for prior JSON results."""
    profile_text = json.dumps(data_profile, indent=2, default=str)
    if len(profile_text) > 50000:
        profile_text = profile_text[:50000] + "\n...[profile truncated]"

    messages = [
        {"role": "system", "content": config.MEMORY_CODER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Original search question:\n"
                f"{original_query}\n\n"
                "Follow-up question about those results:\n"
                f"{user_query}\n\n"
                "JSON skeleton and examples:\n"
                f"{profile_text}\n\n"
                "Write Python extraction code that computes the answer from the full `data` object. "
                "Assign the final JSON-serializable answer to `result`."
            ),
        },
    ]

    coder_client, coder_model, coder_budget = config.get_agent_model("memory_coder")
    coder_output = call_llm_structured(
        config=config,
        prompt=user_query,
        model=MemoryCoderOutput,
        system=config.MEMORY_CODER_SYSTEM_PROMPT,
        messages=messages,
        model_name=coder_model,
        temperature=0,
        log_label="memory_coder",
        thinking_budget=coder_budget,
        client=coder_client,
    )
    coder_output = coder_output.model_copy(update={"extraction_code": _strip_python_code_fences(coder_output.extraction_code)})
    log_prompt(
        log_dir or config.LOG_DIR,
        "memory_coder",
        {
            "user_query": user_query,
            "messages": messages,
            "response": coder_output.model_dump(),
        },
    )
    return coder_output


def _format_memory_coder_answer(
    config: ChatConfig,
    *,
    original_query: str,
    user_query: str,
    source_label: str,
    coder_output: MemoryCoderOutput,
    computed_result: dict,
    row_count: int | None,
    log_dir: str | None = None,
) -> str:
    """Use the chatter route to turn deterministic memory-code output into a user-facing answer."""
    computed_text = json.dumps(computed_result, indent=2, default=str)
    if len(computed_text) > 60000:
        computed_text = computed_text[:60000] + "\n...[computed result truncated]"

    messages = [
        {"role": "system", "content": config.CHATTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "MODE: memory_computed\n\n"
                "You are answering a follow-up question using a deterministic computation over prior NExtSEEK results. "
                "Use ONLY the computed result below. Do not claim you queried an external database.\n\n"
                f"Original search question:\n{original_query}\n\n"
                f"Follow-up question:\n{user_query}\n\n"
                f"Source data label:\n{source_label}\n"
                f"Rows convenience count, if known:\n{row_count}\n\n"
                f"Computation description:\n{coder_output.result_description}\n"
                f"Fields used:\n{json.dumps(coder_output.fields_used, default=str)}\n"
                f"Coder notes:\n{coder_output.notes}\n\n"
                f"Computed result JSON:\n{computed_text}\n\n"
                "Write a concise answer. If the computed result indicates missing fields or no matches, say that directly."
            ),
        },
    ]

    chatter_client, chatter_model, chatter_budget = config.get_agent_model("chatter")
    resp = chatter_client.chat(
        model=chatter_model,
        temperature=0,
        messages=messages,
        thinking_budget=chatter_budget,
    )
    log_usage(resp, "MEMORY_CODER_CHATTER")
    answer = resp.content
    log_prompt(
        log_dir or config.LOG_DIR,
        "memory_coder_chatter",
        {
            "user_query": user_query,
            "messages": messages,
            "response": answer,
        },
    )
    return answer


def _legacy_memory_agent_answer(config: ChatConfig, user_query: str, result_bundle: dict, log_dir: str | None = None) -> str:
    """
    Existing fallback path: pass saved result content directly to the memory model.
    """
    orig_query = result_bundle.get("user_query", "")
    memory_client, memory_model, memory_budget = config.get_agent_model("memory")

    # --- Build result context from saved files ---
    file_entries = collect_bundle_files(result_bundle)
    context_parts: list[str] = []
    for label, path in file_entries:
        try:
            content = load_file_for_memory(path)
            context_parts.append(f"=== {label} ===\n{content}")
            print(f"[DEBUG][MEMORY] Loaded file: {label} ({path}), {len(content)} chars")
        except Exception as e:
            print(f"[DEBUG][MEMORY] Failed to read file {path}: {repr(e)}")

    # Fallback: use inline bundle data if no files were found
    if not context_parts:
        inline = (
            result_bundle.get("graph_result")
            or result_bundle.get("api_result_full")
            or result_bundle.get("api_result_slim")
            or {}
        )
        context_parts.append(json.dumps(inline, indent=2))
        print(f"[DEBUG][MEMORY] No files found; using inline bundle data ({len(context_parts[0])} chars)")

    result_context = "\n\n".join(context_parts)

    messages = [
        {"role": "system", "content": config.MEMORY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Original search question:\n"
                f"{orig_query}\n\n"
                "Follow-up user question (about those results):\n"
                f"{user_query}\n\n"
                "Prior result data (from saved files):\n"
                f"{result_context}\n\n"
                "Answer the follow-up question using ONLY this result data."
            ),
        },
    ]

    try:
        resp = memory_client.chat(
            model=memory_model,
            temperature=0,
            messages=messages,
            thinking_budget=memory_budget,
        )
        log_usage(resp, "MEMORY")
        answer = resp.content
        print("[DEBUG][MEMORY][MEMORY] Answer:", answer)
        log_prompt(
            log_dir or config.LOG_DIR,
            "memory",
            {
                "user_query": user_query,
                "messages": messages,
                "response": answer,
                "bundle_id": result_bundle.get("id"),
                "files_loaded": [label for label, _ in file_entries],
            },
        )
        return answer
    except LLMAPIConnectionError as e:
        print("[DEBUG][MEMORY] APIConnectionError:", repr(e))

    # Degraded response if LLM call fails
    if isinstance(result_bundle.get("graph_result"), dict):
        count = result_bundle["graph_result"].get("count", "unknown")
        return (
            "I have the prior graph query results loaded, but had a connection issue. "
            f"The result contained {count} records."
        )
    return (
        "I have the prior results loaded, but had a connection issue. "
        "Please try your follow-up question again."
    )


def memory_agent_answer(config: ChatConfig, user_query: str, result_bundle: dict, log_dir: str | None = None) -> str:
    """
    Answer follow-up questions about prior results.
    Fast path: profile JSON shape, generate guarded Python extraction code, execute it locally,
    and ask chatter to phrase the computed result. Any failure falls back to the legacy memory path.
    """
    orig_query = result_bundle.get("user_query", "")
    try:
        source_label, memory_data, file_entries = _load_memory_json_payload(result_bundle)
        profile = build_memory_data_profile(memory_data, sample_limit=5)
        row_count = None
        if isinstance(memory_data, dict):
            rows = ((memory_data.get("data") or {}).get("rows") if isinstance(memory_data.get("data"), dict) else None)
            if isinstance(rows, list):
                row_count = len(rows)

        coder_output = memory_coder_agent(
            config,
            original_query=orig_query,
            user_query=user_query,
            data_profile=profile,
            log_dir=log_dir,
        )
        computed_result = execute_memory_code(coder_output.extraction_code, memory_data)

        artifact_entry = None
        try:
            # Each follow-up gets a unique filename so multiple memory queries against
            # the same source bundle don't overwrite each other's debug trail.
            # Format: memory_coder_bundle_<bid>_<UTC-ts>.json
            from datetime import datetime, timezone

            artifact_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            artifact_name = f"memory_coder_bundle_{result_bundle.get('id', 'latest')}_{artifact_ts}.json"
            store = ArtifactStore(log_dir or config.LOG_DIR)
            artifact_entry = store.write_json(
                key=f"memory_coder_result_{artifact_ts}",
                label="Memory coder execution result",
                filename=artifact_name,
                payload={
                    "bundle_id": result_bundle.get("id"),
                    "source_label": source_label,
                    "original_query": orig_query,
                    "followup_query": user_query,
                    "profile": profile,
                    "memory_coder": coder_output.model_dump(),
                    "computed_result": computed_result,
                    "row_count": row_count,
                    "files_loaded": [label for label, _ in file_entries],
                    "artifact_ts": artifact_ts,
                },
                kind="memory",
                bundle_id=result_bundle.get("id"),
            )
            print(f"[DEBUG][MEMORY_CODER] Saved artifact: {artifact_entry}")
            if artifact_entry:
                existing_files = result_bundle.setdefault("files", [])
                if isinstance(existing_files, list) and artifact_entry not in existing_files:
                    existing_files.append(artifact_entry)
                result_bundle["memory_coder_artifact"] = artifact_entry
        except Exception as artifact_err:
            print(f"[DEBUG][MEMORY_CODER] Failed to save artifact: {artifact_err!r}")

        answer = _format_memory_coder_answer(
            config,
            original_query=orig_query,
            user_query=user_query,
            source_label=source_label,
            coder_output=coder_output,
            computed_result=computed_result,
            row_count=row_count,
            log_dir=log_dir,
        )
        log_prompt(
            log_dir or config.LOG_DIR,
            "memory_coder_execution",
            {
                "user_query": user_query,
                "bundle_id": result_bundle.get("id"),
                "source_label": source_label,
                "coder_output": coder_output.model_dump(),
                "computed_result": computed_result,
                "artifact": artifact_entry,
                "answer": answer,
            },
        )
        return answer
    except Exception as e:
        print(f"[DEBUG][MEMORY_CODER] Fast path failed; falling back to legacy memory agent: {e!r}")
        try:
            return _legacy_memory_agent_answer(config, user_query, result_bundle, log_dir=log_dir)
        except Exception as legacy_err:
            print(f"[DEBUG][MEMORY] Legacy fallback failed: {legacy_err!r}")
            return (
                "I found the prior results, but the follow-up analysis failed before I could produce a reliable answer. "
                f"Fast-path error: {e!r}; fallback error: {legacy_err!r}"
            )


