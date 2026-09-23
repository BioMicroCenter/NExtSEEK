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
        "question whose answer is a FILE the user takes away. And every follow-up to an "
        "earlier turn of the chat, whichever route answered it: a question about 'those' "
        "results, the previous search re-run with a changed filter, what an earlier turn "
        "found or which query it ran, a plot or a download of it. This route is handed "
        "the earlier turns' queries, their search details and their result files, and "
        "in a chat that reached it, a later message that refers back stays here; a "
        "self-contained question is routed on its own merits."
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
        "hold, a produced file, or a follow-up to an earlier turn that belongs here."
    ),
)


# 2026-09-23 ruling: "Retire the memory agent in nextseek_query and have all follow ups
# go to container_cc". The generator (``route_capabilities.apply_followup_ruling``)
# takes these families off the nextseek_query route, these capability labels out of its
# best_for, and adds NS_FOLLOWUP_NOT_FOR to its not_for, so the router prompt reads one
# story. The NS engine's follow-up code is untouched: this is routing only.
FOLLOWUP_FAMILIES_ON_CC: tuple[str, ...] = (
    "followup_over_results",
    "search_refinement",
    "cross_session_memory",
)
NS_CAPABILITY_LABELS_ON_CC: tuple[str, ...] = (
    "Follow-up Questions",
    "Search Refinements",
)
NS_FOLLOWUP_NOT_FOR = (
    "A follow-up to an earlier turn of the chat (a question about its results, a "
    "refinement of its search, or recall of what it found): container_cc answers "
    "every follow-up"
)
