"""Native dispatch for the 7 granular assistant ops.

Port of the dmac sidecar's ``sidecar/app/ops.py``: each op calls the same
chat_nextseek portable function, with the same argument order, so behavior is
preserved and the dmac sidecar can be rewired to call these endpoints
mechanically. chat_nextseek imports are lazy (deferred to call time) so the
viewset module stays import-light and unit tests can patch the agents.

The single intentional **superset** of dmac behavior is ``graph``: per the design
decision for this work it ALSO executes the Cypher plan via Neo4j and returns the
rows alongside the plan (dmac returns the plan only).

Error taxonomy (mirrors dmac _ws_contract.ERROR_EXIT):
* :class:`OpValidationError` -> VALIDATION
* :class:`~nextseek_api.assistant.write_gate.WriteBlockedError` -> WRITE_BLOCKED
Any other exception raised by an agent maps to AGENT_FAILED at the viewset layer.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from nextseek_api.assistant.write_gate import WriteBlockedError  # noqa: F401 (re-exported)


class OpValidationError(ValueError):
    """Bad/missing op arguments. Maps to the canonical VALIDATION error code."""


def _dump(obj: Any) -> Any:
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _load_parser_plan(args: dict) -> Any:
    """Parse ``args['parser_plan']`` as JSON; malformed input -> OpValidationError
    (mirrors the dmac runner's VALIDATION/exit-3 parity for a bad --parser-plan)."""
    try:
        return json.loads(args["parser_plan"])
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise OpValidationError(f"parser_plan is not valid JSON: {exc}") from exc


def run_op(
    op: str,
    args: dict,
    *,
    config: Any,
    session: Any,
    write_gate: Callable,
    neo4j_exec: Callable | None = None,
    outputs_dir: str | None = None,
) -> dict:
    """Dispatch a granular op to its handler and return its result dict."""
    handler = _HANDLERS.get(op)
    if handler is None:
        raise OpValidationError(f"not a sidecar op: {op!r}")
    return handler(args, config, session, write_gate, neo4j_exec, outputs_dir)


def _entity(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek.portable import entity_agent
    return _dump(entity_agent(config, args["query"]))


def _parse(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek.portable import entity_agent, parser_agent
    entity_out = entity_agent(config, args["query"])
    return _dump(parser_agent(session, config, args["query"], entity_out))


def _graph(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek.portable import entity_agent, graph_agent, parser_agent
    entity_out = entity_agent(config, args["query"])
    # Run the parser and pass its plan to graph_agent, mirroring the NS
    # orchestrator (orchestrator.py:869 graph_agent(config, query, entity, plan)).
    # Without the parser_plan the graph agent gets no PARSER PLAN block and emits
    # unbounded, pathological Cypher that overruns the 60s proxy timeout (#20).
    parser_plan = parser_agent(session, config, args["query"], entity_out)
    plan = graph_agent(config, args["query"], entity_out, parser_plan)
    plan_dump = _dump(plan)
    cypher = plan_dump.get("cypher") if isinstance(plan_dump, dict) else getattr(plan, "cypher", None)
    params = (
        plan_dump.get("parameters") if isinstance(plan_dump, dict) else getattr(plan, "parameters", {})
    ) or {}
    exec_fn = neo4j_exec
    if exec_fn is None:
        from chat_nextseek.helpers import tool_neo4j_query
        exec_fn = tool_neo4j_query
    if cypher:
        result = exec_fn(config, cypher, params)
    else:
        result = {"ok": False, "error": "graph agent produced no cypher", "data": []}
    return {"plan": plan_dump, "result": result}


def _api_read(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek import helpers
    from chat_nextseek.portable import api_agent_build_request
    plan = api_agent_build_request(config, _load_parser_plan(args))
    endpoint, method = plan.endpoint, (plan.method or "").upper()
    write_gate("api-read", endpoint, method, False)  # raises WriteBlocked if not read-safe
    result = helpers.tool_nextseek_api_request(
        config, endpoint, method, requestBody=plan.requestBody, queryParameters=plan.queryParameters
    )
    return {"endpoint": endpoint, "method": method, "api_plan": _dump(plan), "response": result}


def _api_write(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek import helpers
    from chat_nextseek.portable import api_agent_build_request
    confirmed = args.get("confirmed_write", False)
    write_gate("api-write", None, None, confirmed)  # raises WriteBlocked unless confirmed is True
    plan = api_agent_build_request(config, _load_parser_plan(args))
    result = helpers.tool_nextseek_api_request(
        config, plan.endpoint, plan.method, requestBody=plan.requestBody,
        queryParameters=plan.queryParameters,
    )
    return {
        "endpoint": plan.endpoint, "method": (plan.method or "").upper(),
        "api_plan": _dump(plan), "response": result,
    }


def _report(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek import helpers
    from chat_nextseek.schemas.chat import ReporterPlan
    mode = args["mode"]
    summary_mode = "RPPR" if mode == "rppr" else mode
    rp = ReporterPlan(project=args["project"], reporter_mode="summary", summary_mode=summary_mode)
    log_dir = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    result, saved, summary = helpers.run_reporter_summary(config, rp, log_dir)
    return {"summary": summary, "saved_files": saved, "rows": result}


def _generate_submission(args, config, session, write_gate, neo4j_exec, outputs_dir):
    from chat_nextseek.portable import report_writer_agent
    from chat_nextseek.schemas.chat import ReportWriterPlan
    from chat_nextseek.helpers import (
        annotate_metadata_with_sampletypes,
        fetch_reporter_metadata,
    )
    uids = [u.strip() for u in args["uids"].split(",") if u.strip()]
    # Hydrate the reporter_context with the samples' real metadata. The report
    # writer is prompted to use ONLY what it is given ("do NOT fetch anything
    # new"), so a bare {"uids": [...]} yields an all-null skeleton even when the
    # UIDs have full json_metadata. Fetch it here — mirroring the combined-report
    # path in reports.outputs.generate_report_outputs — so a standalone --uids
    # call populates the submission fields from the samples' actual metadata.
    metadata = (
        fetch_reporter_metadata(config, uids)
        if uids
        else {"ok": False, "error": "No UID provided"}
    )
    metadata = annotate_metadata_with_sampletypes(config, metadata) if metadata else metadata
    plan = ReportWriterPlan(
        report_type=args["type"],
        reporter_context={"uids": uids, "metadata": metadata},
    )
    # A non-empty user query is required: some providers (Bedrock/Opus Converse)
    # reject a blank message content block. Fall back to a type-aware default when
    # the caller supplies no query, so the op is robust to query=None / "".
    user_query = (args.get("query") or "").strip() or (
        f"Generate a {args['type']} submission report for the provided sample UIDs."
    )
    out = report_writer_agent(config, user_query, plan)
    return _dump(out)


_HANDLERS: dict[str, Callable] = {
    "entity": _entity,
    "parse": _parse,
    "graph": _graph,
    "api-read": _api_read,
    "api-write": _api_write,
    "report": _report,
    "generate-submission": _generate_submission,
}
