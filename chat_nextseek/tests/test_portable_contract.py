"""Pins the plugin-portable surface.

Failures here = breaking change for dmac_assistant's nextseek plugin.
Coordinate with the plugin maintainer before modifying assertions in
this file.
"""
import inspect

from pydantic import BaseModel

import chat_nextseek.portable as portable

PLUGIN_FACING = {
    "entity_agent",
    "parser_agent",
    "multi_parser_agent",
    "planner_agent",
    "graph_agent",
    "reporter_agent",
    "report_writer_agent",
    "api_agent_build_request",
    "run_reporter_summary",
    "generate_report_outputs",
    "tool_nextseek_api_request",
    "tool_neo4j_query",
}

# Helper orchestrators return a tuple (not a Pydantic model). Enumerate them
# explicitly rather than prefix-match, so a new orchestrator without a `run_`
# prefix (e.g. generate_report_outputs) is classified correctly.
HELPER_ORCHESTRATORS = {"run_reporter_summary", "generate_report_outputs"}


def test_portable_all_complete():
    """The __all__ list must exactly match the documented plugin-facing set."""
    assert set(portable.__all__) == PLUGIN_FACING, (
        f"portable.__all__ drift detected.\n"
        f"  Missing: {PLUGIN_FACING - set(portable.__all__)}\n"
        f"  Extra:   {set(portable.__all__) - PLUGIN_FACING}"
    )


def test_portable_config_in_first_two_params():
    """Every portable symbol takes `config` as one of its first two positional parameters.

    Most agents are `f(config, ...)`. The parser-family agents
    (parser_agent, multi_parser_agent, planner_agent) currently take
    `(session, config, ...)` — that signature is preserved for backward
    compatibility with dmac_assistant's nextseek plugin, which calls
    them positionally with session first.

    Future cleanup (post-refactor): normalize all agents to
    `f(config, [session,] ...)` and tighten this test to assert config
    is strictly the first parameter. Until then, this looser assertion
    is what reality supports.
    """
    for name in portable.__all__:
        fn = getattr(portable, name)
        sig = inspect.signature(fn)
        params = [p.name for p in sig.parameters.values()]
        assert params, f"{name}: has no parameters"
        assert "config" in params[:2], (
            f"{name}: 'config' must be one of the first two positional parameters, "
            f"got params: {params[:3]}"
        )


def test_portable_returns_pydantic_or_dict():
    """Three return-type categories matching portable.py's own classification:

    - Side-effect tools (tool_*) → dict
    - Helper orchestrator (run_reporter_summary) → tuple of dicts
    - Agents (everything else) → Pydantic model

    `run_reporter_summary` is grouped with the dict-returning helpers here
    because its signature is `-> tuple[dict, dict[str, str], dict]`, which
    matches the str-contains-'dict' assertion. If the helper category grows,
    bias toward explicit enumeration rather than prefix matching.
    """
    for name in portable.__all__:
        fn = getattr(portable, name)
        sig = inspect.signature(fn)
        ret = sig.return_annotation
        if name.startswith("tool_") or name in HELPER_ORCHESTRATORS:
            # tool_neo4j_query, tool_nextseek_api_request → dict
            # run_reporter_summary → tuple[dict, dict[str, str], dict]
            # generate_report_outputs → tuple[dict, dict|Any, dict[str, str], str]
            assert ret is dict or ret is inspect.Signature.empty or "dict" in str(ret), (
                f"{name}: return annotation {ret!r} should contain dict"
            )
        else:
            # Agents return Pydantic models (annotation may be a forward ref)
            ret_repr = repr(ret)
            assert (
                ret is inspect.Signature.empty
                or (inspect.isclass(ret) and issubclass(ret, BaseModel))
                or any(token in ret_repr for token in ("Output", "Plan", "Decision", "Summary"))
            ), f"{name}: return annotation {ret!r} should be a Pydantic model"


def test_legacy_import_paths_resolve():
    """The dmac_assistant plugin imports from chat_nextseek.agents and chat_nextseek.helpers.
    These paths MUST keep resolving — the forwarder modules are permanent public API.
    """
    from chat_nextseek.agents import (
        api_agent_build_request,
        entity_agent,
        graph_agent,
        multi_parser_agent,
        parser_agent,
        planner_agent,
        reporter_agent,
        report_writer_agent,
    )
    from chat_nextseek.helpers import (
        run_reporter_summary,
        tool_neo4j_query,
        tool_nextseek_api_request,
    )

    # Same function objects, not divergent copies
    assert entity_agent is portable.entity_agent
    assert parser_agent is portable.parser_agent
    assert tool_nextseek_api_request is portable.tool_nextseek_api_request
    assert run_reporter_summary is portable.run_reporter_summary
