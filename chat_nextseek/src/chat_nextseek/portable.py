"""Plugin-portable agent surface.

Stable public API for external consumers (e.g. dmac_assistant's nextseek plugin).
Every symbol in __all__ satisfies the Portable Agent Contract:

  - Signature: f(config, [session,] *, ...) -> PydanticModel  (or dict for tool_*)
  - All I/O routed through `config` or `helpers/tools/*`
  - `session` is read-only; no cross-agent state hand-off through session
  - No input(), no streaming, no interactive loops
  - Errors raise normally (dispatcher maps to exit codes)

Adding to __all__ is a public-API commitment. Removing or changing a
signature is a breaking change for downstream consumers and must be
coordinated.
"""
from .agents import (
    api_agent_build_request,
    entity_agent,
    graph_agent,
    multi_parser_agent,
    parser_agent,
    planner_agent,
    reporter_agent,
    report_writer_agent,
)
from .helpers import (
    run_reporter_summary,
    tool_neo4j_query,
    tool_nextseek_api_request,
)

__all__ = [
    # Agents — one function, one Pydantic return
    "entity_agent",
    "parser_agent",
    "multi_parser_agent",
    "planner_agent",
    "graph_agent",
    "reporter_agent",
    "report_writer_agent",
    "api_agent_build_request",
    # Helper orchestrator (one tool wraps a deterministic chain)
    "run_reporter_summary",
    # Side-effect tools (REST + graph)
    "tool_nextseek_api_request",
    "tool_neo4j_query",
]
