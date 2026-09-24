"""Stable authored route-level records for generated router context."""
from __future__ import annotations

from NessieAI.cc.op_registry.models import RouteSpec

GENERIC_CC_BUILTINS: tuple[str, ...] = ("bash", "filesystem", "skill-runner")

# Fallback BAML RouteQuery interpolates route.tools as "Tools / plugins / skills".
# nextseek_query.tools is that router-facing vocabulary (chat_nextseek pipeline
# stages), not capabilities.md H3 labels. Labels belong in best_for / not_for.
NEXTSEEK_QUERY_TOOLS: tuple[str, ...] = (
    "entity_agent",
    "parser_agent",
    "api_agent",
    "graph_agent",
    "reporter_agent",
    "memory_agent",
    "system_agent",
    "pipeline_agent",
)

CONTAINER_CC_ROUTE = RouteSpec(
    route_name="container_cc",
    description=(
        "Container Claude Code: full agentic environment with file I/O, code "
        "execution, and arbitrary plugins / skills (including the "
        "nextseek-batch-upload skill)."
    ),
    best_for=(
        "Open-ended reasoning, file I/O, code, multi-tool workflows, and "
        "building/validating NExtSEEK batch-upload create/update sheets. Also a "
        "question that must JOIN two sources the NS route reads separately -- "
        "comparing a REST catalog against sample metadata in the graph, such as "
        "the registered people against the scientists named on samples -- and any "
        "question whose answer is a FILE the user takes away. And the follow-ups the "
        "follow-up rule sends here: this route is handed the earlier turns' queries, their "
        "search details and their result files, so it can export, plot, compare or analyse "
        "an earlier result, and in a chat that reached it, a later message that refers back "
        "stays here; a self-contained question is routed on its own merits. And an open-ended summary of "
        "a whole project or investigation -- how much data it holds, an inventory, an overview, "
        "the span of its collection dates -- written up with a file; formal NIH/RPPR/progress "
        "reports, upload statistics, submissions and 'what is X' stay on the NS route."
    ),
    not_for=(
        "Pure deterministic NExtSEEK lookups that the NS route handles without "
        "container tools; caller identity, session, or access-scope questions "
        "the NS route already resolves; catalog bookkeeping the NS route answers "
        "without shell access; and nf-core pipeline build/launch work that belongs "
        "on the NS route. Having a shell is not a reason to route here when "
        "NExtSEEK already holds the answer. The dividing line: a question "
        "answerable from sample metadata alone is the NS route, however large or "
        "analytical -- counts, breakdowns and harmonisation over metadata all run "
        "in the graph. It is only a question needing a source the graph does not "
        "hold, a produced file, an open-ended project or investigation summary, or a "
        "follow-up the follow-up rule sends here."
    ),
)


# 2026-09-24 follow-up split (routing review 5.3), replacing the 2026-09-23 "every follow-up
# to container_cc" text: the rule lives in the router prompt paragraph only
# (NessieAI/router/followup.py, filled from NESSIE_FOLLOWUP_ROUTING), so these descriptions
# stop restating it and are right under either setting. The generator
# (``route_capabilities.apply_followup_ruling``) keeps cross_session_memory off the
# nextseek_query route (memory across chats is container_cc's) and adds NS_FOLLOWUP_NOT_FOR.
FOLLOWUP_FAMILIES_ON_CC: tuple[str, ...] = (
    "cross_session_memory",
)
NS_CAPABILITY_LABELS_ON_CC: tuple[str, ...] = ()
# 2026-09-23 ruling: open-ended project and investigation summaries go to container_cc;
# formal reports, upload statistics, submissions and "what is X" stay on the NS route.
NS_SUMMARY_NOT_FOR = (
    "An open-ended summary of a whole project or investigation (how much data it holds, an "
    "inventory, an overview, the span of its collection dates): container_cc writes it up with "
    "a file. Formal NIH/RPPR/progress reports, upload statistics and 'what is X' stay here"
)
NS_FOLLOWUP_NOT_FOR = (
    "A follow-up the follow-up rule sends to container_cc (a file, a chart, code, "
    "analysis, or any follow-up once the chat has used container_cc)"
)
