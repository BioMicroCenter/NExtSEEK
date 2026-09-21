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
        "question whose answer is a FILE the user takes away."
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
        "hold, or a produced file, that belongs here."
    ),
)
