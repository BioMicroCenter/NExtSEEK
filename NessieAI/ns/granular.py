"""Native dispatch for the granular assistant ops.

Port of the dmac sidecar's ``sidecar/app/ops.py``: each op calls the same
chat_nextseek portable function, with the same argument order, so behavior is
preserved and the dmac sidecar can be rewired to call these endpoints
mechanically. chat_nextseek imports are lazy (deferred to call time) so the
viewset module stays import-light and unit tests can patch the agents.

The single intentional **superset** of dmac behavior is ``graph``: per the design
decision for this work it ALSO executes the Cypher plan via Neo4j and returns the
rows alongside the plan (dmac returns the plan only). The Neo4j tool holds that
statement to the caller's project scope, which the view puts on the config. A
statement refused for its scope is answered through graph_search, the
project-scoped sample search, exactly as the NS orchestrator falls back
(``_fall_back_to_graph_search``): the parser plan retargeted to graph_search, built
by the API agent, gated as a read and run, returned under ``fallback``.

Error taxonomy (mirrors dmac _ws_contract.ERROR_EXIT):
* :class:`OpValidationError` -> VALIDATION
* :class:`~NessieAI.ns.write_gate.WriteBlockedError` -> WRITE_BLOCKED
Any other exception raised by an agent maps to AGENT_FAILED at the viewset layer.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from NessieAI.ns.write_gate import WriteBlockedError  # noqa: F401 (re-exported)


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


#: The project-scoped sample search a refused graph question is answered through.
GRAPH_SEARCH_ENDPOINT = "/nextseek_api/samples/graph_search/"

#: Added to the error of a ``graph`` op result refused for its project scope.
GRAPH_SCOPE_FALLBACK_HINT = (
    f"The op asked {GRAPH_SEARCH_ENDPOINT} instead, which applies the caller's project scope on the server; "
    "its answer is under fallback."
)

#: What the CC agent must tell the user when it answers from ``fallback`` (the NS chatter gets the same note).
GRAPH_SCOPE_FALLBACK_NOTE = (
    "The graph query written for this question could not be confirmed to stay within the user's projects, so it "
    "was not run. This answer comes from the project-scoped sample search instead. Say so, and say which "
    "conditions of the question that search could not apply."
)


def _graph_search_fallback(config, parser_plan, refused: dict, write_gate) -> dict:
    """Answer a scope-refused graph question through graph_search, as the NS orchestrator does.

    Never raises: a fallback that cannot run reports why, and the refusal it answers stays in the
    op's ``result``. Only graph_search is ever called here, and only through the read gate.
    """
    from chat_nextseek import helpers
    from chat_nextseek.portable import api_agent_build_request

    scope = refused.get("scope") if isinstance(refused.get("scope"), dict) else {}
    out: dict[str, Any] = {
        "ok": False, "endpoint": GRAPH_SEARCH_ENDPOINT, "note": GRAPH_SCOPE_FALLBACK_NOTE,
        "codes": list(scope.get("codes") or ()), "reasons": list(scope.get("reasons") or ()),
    }
    retarget = {"mode": "new_search", "target_endpoint": GRAPH_SEARCH_ENDPOINT}
    if hasattr(parser_plan, "model_copy"):
        plan = parser_plan.model_copy(update=retarget)
    elif isinstance(parser_plan, dict):
        plan = {**parser_plan, **retarget}
    else:
        plan = retarget
    try:
        api_plan = api_agent_build_request(config, plan)
        endpoint, method = api_plan.endpoint, (api_plan.method or "").upper()
        out["api_plan"] = _dump(api_plan)
        if endpoint != GRAPH_SEARCH_ENDPOINT:
            out["error"] = f"the API agent built a request for {endpoint!r}, not {GRAPH_SEARCH_ENDPOINT}; nothing ran"
            return out
        write_gate("api-read", endpoint, method, False)
        response = helpers.tool_nextseek_api_request(
            config, endpoint, method, requestBody=api_plan.requestBody, queryParameters=api_plan.queryParameters,
        )
    except Exception as exc:  # the refusal is still the op's answer; say why its fallback did not run
        out["error"] = f"graph_search fallback failed: {type(exc).__name__}: {exc}"
        return out
    out.update(ok=True, method=method, response=response)
    return out


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
    from chat_nextseek.helpers.tools.neo4j import is_scope_refusal
    if is_scope_refusal(result):
        fallback = _graph_search_fallback(config, parser_plan, result, write_gate)
        result = {**result, "error": f"{result.get('error') or ''} {GRAPH_SCOPE_FALLBACK_HINT}".strip()}
        return {"plan": plan_dump, "result": result, "fallback": fallback}
    return {"plan": plan_dump, "result": result}


def _graph_schema(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """The deployed graph's schema, read live, with no model call and no Cypher.

    The op that replaces the graph snapshot the cc-agent image used to bake: the agent
    asks NExtSEEK, which reads the live catalog through ``graph_catalog``, so a catalog
    change no longer needs an image rebuild. ``types`` arrives as a comma-separated
    string over the wire (one shim flag) and is split here; ``query`` only gates the
    vocabulary blocks.
    """
    from chat_nextseek.portable import graph_schema_snapshot
    types = [code.strip() for code in str(args.get("types") or "").split(",") if code.strip()]
    return graph_schema_snapshot(config, types=types, question=args.get("query") or "")


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
    # Route through the SAME orchestration the NS run_query report_generation
    # path uses (generate_report_outputs), rather than calling the leaf
    # report_writer_agent directly. That gives the op, for every report type:
    #   * the type-specific template (load_report_template) -> bounded output
    #     (a template-less call free-forms and overruns the writer's output-token
    #     cap, truncating the JSON -> AGENT_FAILED);
    #   * the full reporter_context (metadata hydration, protocols, plans);
    #   * the emitters that persist the REAL submission workbooks under
    #     saved_files (geo_seq_workbooks / sra_* / pride_* / nfcore_* / ...),
    #     which the bundle/download + CC staging then serve.
    # See GitHub issue #21 (reporter port defect / drift).
    from chat_nextseek.portable import generate_report_outputs, report_writer_agent
    from chat_nextseek.schemas.chat import ReporterPlan

    uids = [u.strip() for u in args["uids"].split(",") if u.strip()]
    report_type = args["type"]
    # A non-empty user query is required: some providers (Bedrock/Opus Converse)
    # reject a blank message content block. Fall back to a type-aware default when
    # the caller supplies no query, so the op is robust to query=None / "".
    user_query = (args.get("query") or "").strip() or (
        f"Generate a {report_type} submission report for the provided sample UIDs."
    )
    reporter_plan = ReporterPlan(
        report_type=report_type,
        uids=uids,
        reporter_mode="report_generation",
        reporter_context={"per_sample_reports": False},
    )
    log_dir = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    _reporter_result, report_writer_output, saved_files, _reply = generate_report_outputs(
        config=config,
        user_query=user_query,
        parser_plan={"report_type": report_type},
        reporter_plan=reporter_plan,
        uids=uids,
        log_dir=log_dir,
        report_writer_fn=report_writer_agent,
        per_sample_reports=False,
    )
    # Combined mode wraps the writer output as {"all_samples": <writer output>}.
    # Unwrap to the flat writer dict to preserve the op's existing result shape,
    # and attach the real saved_files so the download bundle + CC staging serve
    # the actual generated report file.
    flat = report_writer_output
    if isinstance(report_writer_output, dict) and "all_samples" in report_writer_output:
        flat = report_writer_output["all_samples"]
    result = dict(flat) if isinstance(flat, dict) else {"report_type": report_type, "report": flat}
    result["saved_files"] = saved_files or {}
    return result



_RUN_LS_CAP = 2_000_000  # bytes of `ls -laR` returned to CC before truncation (well under the 16 MiB WS cap)


def _run_ls(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Read-only recursive listing of a finished Luria run dir (reingest input).

    Validates ``run_dir`` is under ``<LURIA working_path>/runs`` (no traversal),
    then SSHes Luria and runs ``ls -laR``. Returns the tree text (capped). Never
    writes to Luria.
    """
    import shlex
    luria_env = getattr(config, "LURIA_ENV", None) or {}
    working_path = str(luria_env.get("working_path") or "").rstrip("/")
    if not working_path or not luria_env.get("key"):
        raise OpValidationError("Luria is not configured (LURIA_ENV incomplete)")
    runs_root = working_path + "/runs"
    run_dir = os.path.normpath(str(args["run_dir"]))
    if run_dir != runs_root and not run_dir.startswith(runs_root + "/"):
        raise OpValidationError(f"run_dir must be under {runs_root}")
    from chat_nextseek.luria.ssh import prepare_key, ssh_run
    key_path = prepare_key(luria_env["key"])
    out = ssh_run(luria_env, f"ls -laR {shlex.quote(run_dir)}", key_path=key_path)
    return {"run_dir": run_dir, "truncated": len(out) > _RUN_LS_CAP, "tree": out[:_RUN_LS_CAP]}


def _build_upload_xlsx(args, config, session, write_gate, neo4j_exec, outputs_dir):
    """Render one 4-sheet upload workbook per A.* sample type from CC-composed rows.

    args["rows"]: JSON array of {"SampleType", "json_metadata", "assay_ids"}. Runs QA
    per type (a HARD_REJECT type is skipped, its report returned). Returns the rendered
    workbooks under ``saved_files`` plus the per-type QA reports. No NExtSEEK write —
    the user reviews the workbook(s) and uploads them via the batch-upload UI.
    """
    from NessieAI.ns.reingest_qa import HARD_REJECT, qa_rows
    from NessieAI.ns.upload_workbook import render_upload_workbook

    try:
        rows = json.loads(args["rows"])
    except ValueError as exc:
        raise OpValidationError(f"rows is not valid JSON: {exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise OpValidationError("rows must be a non-empty JSON array")

    existing = {u.strip() for u in str(args.get("existing_parent_uids") or "").split(",") if u.strip()}

    by_type: dict[str, list] = {}
    for row in rows:
        st = str((row or {}).get("SampleType") or "").strip()
        if not st:
            raise OpValidationError("every row needs a SampleType")
        by_type.setdefault(st, []).append(row)

    out_root = outputs_dir or os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs"
    known = set(by_type)  # permissive here; the real catalog validates on upload
    saved_files: dict[str, str] = {}
    qa: dict[str, dict] = {}
    for st, st_rows in by_type.items():
        report = qa_rows(st_rows, sample_type=st, known_sampletypes=known,
                         existing_parent_uids=existing)
        qa[st] = {"disposition": report.disposition, "hard": report.hard, "soft": report.soft}
        if report.disposition == HARD_REJECT:
            continue
        safe_name = st.replace("/", "_").replace(" ", "_")          # readable filename (keeps the dot)
        # The artifact KEY is the download URL segment, which the route only
        # accepts as [\w]+ — so it must be word-chars only (A.SCXP -> A_SCXP).
        # The file on disk keeps the dot; download serves it by its real name.
        safe_key = safe_name.replace(".", "_").replace("-", "_")
        path = os.path.join(out_root, f"reingest_{safe_name}.xlsx")
        render_upload_workbook(st, st_rows, path)
        saved_files[f"reingest_{safe_key}"] = path
    return {"saved_files": saved_files, "qa": qa}

_HANDLERS: dict[str, Callable] = {
    "entity": _entity,
    "parse": _parse,
    "graph": _graph,
    "graph-schema": _graph_schema,
    "api-read": _api_read,
    "api-write": _api_write,
    "report": _report,
    "generate-submission": _generate_submission,
    "run-ls": _run_ls,
    "build-upload-xlsx": _build_upload_xlsx,
}
