"""Pipeline group-by resolution step — function-calling loop that resolves a group-by phrase to a canonical metadata field.

Was: `_pipeline_groupby_resolution` in agents.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...config import ChatConfig
    from ...schemas.pipeline import GroupByResolution


def _pipeline_groupby_resolution(
    *,
    config: "ChatConfig",
    pipeline_key: str,
    group_by_phrase: str,
    metadata_summary: dict,
    user_hint: str = "",
) -> "GroupByResolution":
    """Third LLM step of pipeline_agent: function-calling loop to resolve a
    group-by phrase to a canonical metadata field.

    Uses up to MAX_ITER=5 iterations of tool_use/tool_result exchange.
    The LLM MUST call finalize_groupby before the cap; otherwise raises RuntimeError.
    """
    from ...schemas.pipeline import GroupByResolution, FieldRef
    from ..tools import GROUPBY_TOOL_SCHEMAS, dispatch_groupby_tool_call

    MAX_ITER = 5

    prompt_template = config._load_prompt("pipeline_agent_groupby.txt")
    system_prompt = (
        prompt_template
        .replace("{group_by_phrase}", group_by_phrase or "")
        .replace("{pipeline_key}", pipeline_key or "")
        .replace("{user_hint}", user_hint or "")
    )

    client, model_name, _budget = config.get_agent_model("pipeline_groupby")

    if not callable(getattr(client, "chat_with_tools", None)):
        raise RuntimeError(
            f"[PIPELINE_GROUPBY] Resolved LLM client {type(client).__name__!r} does not "
            "support chat_with_tools; pipeline_groupby must be mapped to a "
            "function-calling-capable model."
        )

    messages: list[dict] = [
        {"role": "user", "content": f"Resolve group-by phrase: {group_by_phrase!r}"}
    ]

    print(
        f"[DEBUG][PIPELINE_GROUPBY] enter phrase={group_by_phrase!r} "
        f"hint={user_hint!r} model={model_name!r}"
    )

    try:
        for i in range(MAX_ITER):
            print(f"[DEBUG][PIPELINE_GROUPBY] iter={i} messages_len={len(messages)}")
            resp = client.chat_with_tools(
                messages=messages,
                tools=GROUPBY_TOOL_SCHEMAS,
                system=system_prompt,
                model=model_name,
            )
            stop_reason = resp.get("stop_reason")
            print(
                f"[DEBUG][PIPELINE_GROUPBY] iter={i} stop_reason={stop_reason!r} "
                f"content_blocks={len(resp.get('content', []))}"
            )

            if stop_reason != "tool_use":
                raise RuntimeError(
                    f"[PIPELINE_GROUPBY] LLM stopped without calling finalize_groupby "
                    f"(stop_reason={stop_reason!r})"
                )

            tool_use_blocks = [
                b for b in resp.get("content", []) if b.get("type") == "tool_use"
            ]
            assistant_content = resp.get("content", [])
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results: list[dict] = []
            finalize_payload: dict | None = None

            for block in tool_use_blocks:
                name = block.get("name")
                tool_input = block.get("input", {})
                tool_use_id = block.get("id")

                if name == "finalize_groupby":
                    finalize_payload = tool_input
                    print(
                        f"[DEBUG][PIPELINE_GROUPBY] finalize_groupby "
                        f"requires_clarification={tool_input.get('requires_clarification')} "
                        f"field={tool_input.get('field_name')!r}"
                    )
                    continue

                print(
                    f"[DEBUG][PIPELINE_GROUPBY] dispatching tool={name!r} "
                    f"input_keys={list(tool_input.keys()) if isinstance(tool_input, dict) else 'non-dict'}"
                )
                result = dispatch_groupby_tool_call(
                    name=name,
                    tool_input=tool_input,
                    bundle=metadata_summary,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result if isinstance(result, str) else str(result),
                })

            if finalize_payload is not None:
                requires_clarification = bool(finalize_payload.get("requires_clarification"))
                if requires_clarification:
                    raw_candidates = finalize_payload.get("candidates") or []
                    candidates = [
                        FieldRef(
                            sample_type=c.get("sample_type", ""),
                            field_name=c.get("field_name", ""),
                        )
                        for c in raw_candidates
                    ]
                    return GroupByResolution(
                        requires_clarification=True,
                        candidates=candidates,
                        clarifying_question=finalize_payload.get("clarifying_question") or "",
                        rationale=finalize_payload.get("rationale") or "",
                    )

                field_ref = FieldRef(
                    sample_type=finalize_payload.get("sample_type", ""),
                    field_name=finalize_payload.get("field_name", ""),
                )
                return GroupByResolution(
                    field=field_ref,
                    distinct_values=finalize_payload.get("distinct_values") or [],
                    rationale=finalize_payload.get("rationale") or "",
                )

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(
            f"[PIPELINE_GROUPBY] LLM did not call finalize_groupby within "
            f"{MAX_ITER} iterations."
        )

    except RuntimeError:
        raise
    except Exception as exc:
        print(f"[DEBUG][PIPELINE_GROUPBY] unexpected error: {exc!r}")
        raise RuntimeError(
            f"[PIPELINE_GROUPBY] Tool-use loop failed: {exc!r}"
        ) from exc
