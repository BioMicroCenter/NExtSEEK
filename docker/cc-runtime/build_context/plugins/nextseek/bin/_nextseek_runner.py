#!/usr/bin/env python
"""Shared entry point for nextseek-* shims.

Thin WS/viewset client: dispatches to the NExtSEEK assistant viewset (query/plan)
or to the sidecar via WebSocket (all other 7 ops). Imports NO chat_nextseek (U-11).

Emits one of:
  - stdout: result JSON (one line)
  - stderr (last line): structured error JSON, exit code != 0

Exit codes:
  0  ok
  2  config / env missing
  3  validation (bad args)
  4  agent failure (LLM error, network, etc.)
  5  write blocked (Layer-2 --confirmed-write missing)
  6  config error (reserved, no longer used by read_safe_endpoints)
  7  transport error (sidecar/viewset unreachable)
  8  auth failed (viewset 401)
  9  staging error

Dry-run mode: when NEXTSEEK_DRY_RUN=1, each dispatcher returns a minimal
valid typed JSON response without invoking any LLM, REST, or Neo4j call.
This is what the image dry-run test exercises to prove wiring without
needing live GCP/NExtSEEK credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import typing

# Ensure sibling bin modules (_ws_contract, _sidecar_client, _assistant_client)
# are importable when invoked as a script (sys.path may only contain the cwd
# and standard library locations, not the plugin bin directory).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _err(code: str, message: str, exit_code: int) -> typing.NoReturn:  # Minor-7
    payload = {"error": {"code": code, "message": message}}
    sys.stderr.write(json.dumps(payload) + "\n")
    sys.exit(exit_code)


def _sanitize_env_quotes() -> None:
    """Strip matching outer quote characters from every env var.

    `docker run --env-file` and `python-dotenv.dotenv_values()` preserve any
    surrounding `"..."` or `'...'` from .env literals, leaving values like
    `'"fairdata-dev.mit.edu"'` (the literal quote characters become part of
    the value). Bash's `set -a; . .env; set +a` strips quotes implicitly, so
    this only bites containerised / library-loaded env paths. We normalise
    here, in one place, before any downstream reads. We only strip when the
    first and last characters are the same quote char and len >= 2 -- never
    partial quotes, never mismatched.
    """
    for key, value in list(os.environ.items()):
        if (len(value) >= 2
                and value[0] == value[-1]
                and value[0] in ('"', "'")):
            os.environ[key] = value[1:-1]


def _dry_run() -> bool:
    return os.environ.get("NEXTSEEK_DRY_RUN") == "1"


# ---------------------------------------------------------------- dispatchers

def _dispatch_entity(args):
    if _dry_run():  # pragma: no branch
        return {"sampletypes": [], "assays": [], "keywords": [], "projects": []}  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op("entity", {"query": args.query},  # pragma: no cover
                          ns_login=(_api_user(), _api_pass()),  # pragma: no cover
                          sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


def _dispatch_parse(args):
    if _dry_run():  # pragma: no branch
        return {"mode": "new_search", "target_endpoint": None}  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op("parse", {"query": args.query},  # pragma: no cover
                          ns_login=(_api_user(), _api_pass()),  # pragma: no cover
                          sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


def _make_client():  # pragma: no cover
    import _assistant_client as ac  # pragma: no cover
    return ac.AssistantClient(  # pragma: no cover
        base_url=os.environ["NEXTSEEK_URL"],  # pragma: no cover
        assistant_prefix=os.environ.get("NEXTSEEK_ASSISTANT_PREFIX", "nextseek_api/assistant"),  # pragma: no cover
        auth=(_api_user(), _api_pass()),  # pragma: no cover
    )  # pragma: no cover


def _scratch_dir() -> str:
    return os.environ.get("NEXTSEEK_SCRATCH_DIR", "/data/scratch")


def _total_and_rows(api_result_full: dict) -> tuple[int | None, int, list]:
    """(total, row_count, rows) — parity with ns_turn_context._total_and_rows."""
    data = api_result_full.get("data") if isinstance(api_result_full, dict) else None
    for container in (data, api_result_full):
        if not isinstance(container, dict):
            continue
        total = (container.get("total") or container.get("total_samples")
                 or container.get("total_nodes"))
        rows = None
        for key in ("rows", "nodes", "data"):
            cand = container.get(key)
            if isinstance(cand, list):
                rows = cand
                break
        if rows is not None:
            return total, len(rows), rows
        if container is data:
            return total, 0, []
    return None, 0, []


def _run_viewset(query: str, mode: str, *, session_id: str | None = None) -> dict:  # pragma: no cover  # Minor-8
    """Shared helper: drive the NExtSEEK assistant viewset for query/plan/pipeline ops.

    Handles 401/HTTP/transport errors uniformly and returns the shaped terminal
    dict {"reply": str, "debug": {...}, "bundle_id": int|None}.
    """
    import httpx  # pragma: no cover
    client = _make_client()  # pragma: no cover
    try:  # pragma: no cover
        terminal, _ = client.run_query(query, mode=mode, session_id=session_id)  # pragma: no cover
    except httpx.HTTPStatusError as e:  # pragma: no cover
        if e.response.status_code == 401:  # pragma: no cover
            _err("AUTH_FAILED", "authentication failed (check NS credentials)", 8)  # pragma: no cover
        _err("AGENT_FAILED", f"HTTP {e.response.status_code}", 4)  # pragma: no cover
    except httpx.TransportError as e:  # pragma: no cover
        _err("TRANSPORT_ERROR", f"viewset unreachable: {type(e).__name__}", 7)  # pragma: no cover
    if "__error__" in terminal:  # pragma: no cover
        _err("AGENT_FAILED", terminal["__error__"], 4)  # pragma: no cover
    return {  # pragma: no cover
        "reply": terminal.get("reply", ""),  # pragma: no cover
        "debug": terminal.get("debug", {}),  # pragma: no cover
        "bundle_id": terminal.get("bundle_id"),  # pragma: no cover
    }  # pragma: no cover


def _dispatch_plan(args):
    """multi_parser + planner advisor via the assistant viewset (plan mode)."""
    if _dry_run():  # pragma: no branch
        return {  # pragma: no cover
            "plan": [],
            "executed_read_steps": [],
            "context_engineer_outputs": [],
            "evaluator": None,
            "skipped_steps": [],
            "recommended_next_actions": [],
        }
    return _run_viewset(args.query, mode="plan")  # pragma: no cover


def _dispatch_api_read(args):
    """Read-only API dispatch. Refuses --confirmed-write locally (exit-3)."""
    if not args.parser_plan:  # pragma: no cover
        _err("VALIDATION", "--parser-plan required", 3)  # pragma: no cover
    if args.confirmed_write:  # pragma: no cover
        _err("VALIDATION",  # pragma: no cover
             "--confirmed-write is not valid on api-read; use api-write", 3)

    if _dry_run():  # pragma: no branch
        return {"endpoint": "/dry-run/", "method": "GET", "response": {}}  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op("api-read", {"parser_plan": args.parser_plan},  # pragma: no cover
                          ns_login=(_api_user(), _api_pass()),  # pragma: no cover
                          sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


def _dispatch_api_write(args):
    """Write-class API dispatch. Layer 2: refuses without --confirmed-write."""
    if not args.parser_plan:  # pragma: no cover
        _err("VALIDATION", "--parser-plan required", 3)  # pragma: no cover
    if not args.confirmed_write:
        _err("WRITE_BLOCKED",
             "nextseek-api-write requires --confirmed-write (Layer 2; advisory; server is the hard floor)", 5)

    if _dry_run():  # pragma: no branch
        return {"endpoint": "/dry-run/", "method": "POST", "response": {}}  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op(  # pragma: no cover
            "api-write",  # pragma: no cover
            {"parser_plan": args.parser_plan, "confirmed_write": args.confirmed_write},  # pragma: no cover
            ns_login=(_api_user(), _api_pass()),  # pragma: no cover
            sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


def _dispatch_graph(args):
    if _dry_run():  # pragma: no branch
        return {"cypher": "", "result": []}  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op("graph", {"query": args.query},  # pragma: no cover
                          ns_login=(_api_user(), _api_pass()),  # pragma: no cover
                          sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


def _dispatch_report(args):
    if args.mode not in ("samples", "protocols", "published", "rppr"):  # pragma: no cover
        _err("VALIDATION",  # pragma: no cover
             f"--mode must be samples|protocols|published|rppr, got {args.mode!r}",
             3)
    if not args.project:  # pragma: no cover
        _err("VALIDATION", "--project required", 3)  # pragma: no cover

    if _dry_run():  # pragma: no branch
        return {"summary": "", "saved_files": [], "rows": []}  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op("report", {"mode": args.mode, "project": args.project},  # pragma: no cover
                          ns_login=(_api_user(), _api_pass()),  # pragma: no cover
                          sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


def _dispatch_generate_submission(args):
    if args.type not in ("GEO", "SRA", "NFCORE_RNASEQ", "NFCORE_SCRNASEQ", "PRIDE"):  # pragma: no cover
        _err("VALIDATION", f"--type unsupported: {args.type!r}", 3)  # pragma: no cover
    if not args.uids:  # pragma: no cover
        _err("VALIDATION", "--uids required (comma-separated)", 3)  # pragma: no cover

    if _dry_run():  # pragma: no branch
        return {"report": "", "type": args.type}  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op(  # pragma: no cover
            "generate-submission",  # pragma: no cover
            {"type": args.type, "uids": args.uids, "query": args.query},  # pragma: no cover
            ns_login=(_api_user(), _api_pass()),  # pragma: no cover
            sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


def _dispatch_query(args):
    """Single-shot orchestrator via the NExtSEEK assistant viewset (PD-5).

    Runs in the LIVE chat session (NEXTSEEK_CHAT_SESSION_ID). On a terminal
    with bundle_id, downloads the bundle and materializes rows to
    scratch/query/result-{bundle_id}.json; returns a unified manifest + reply.
    Without bundle_id, returns a reply-only manifest and writes nothing.
    """
    session_id = os.environ.get("NEXTSEEK_CHAT_SESSION_ID")
    if not session_id:
        _err("CONFIG_MISSING", "NEXTSEEK_CHAT_SESSION_ID not set", 2)

    if _dry_run():  # pragma: no branch
        return {"reply": "[dry-run]", "debug": {}, "bundle_id": None}  # pragma: no cover

    mode = "plan" if args.planner else "standard"
    client = _make_client()
    try:
        terminal, _ = client.run_query(
            args.query,
            mode=mode,
            session_id=session_id,
            force_new=False,
        )
    except Exception as e:
        import httpx  # pragma: no cover
        if isinstance(e, httpx.HTTPStatusError):
            if e.response.status_code == 401:
                _err("AUTH_FAILED", "authentication failed (check NS credentials)", 8)
            _err("AGENT_FAILED", f"HTTP {e.response.status_code}", 4)
        if isinstance(e, httpx.TransportError):
            _err("TRANSPORT_ERROR", f"viewset unreachable: {type(e).__name__}", 7)
        raise

    if "__error__" in terminal:
        _err("AGENT_FAILED", terminal["__error__"], 4)

    reply = terminal.get("reply", "")
    bundle_id = terminal.get("bundle_id")
    if not isinstance(bundle_id, int) or isinstance(bundle_id, bool):
        return {
            "turn_id": None,
            "bundle_id": None,
            "total": None,
            "row_count": None,
            "columns": None,
            "path": None,
            "reply": reply,
        }

    dl_session = terminal.get("session_id") or session_id
    try:
        bundle = client.download_bundle(dl_session, bundle_id)
    except Exception as e:
        import httpx  # pragma: no cover
        if isinstance(e, httpx.HTTPStatusError):
            if e.response.status_code == 401:
                _err("AUTH_FAILED", "authentication failed (check NS credentials)", 8)
            _err("AGENT_FAILED", f"HTTP {e.response.status_code}", 4)
        if isinstance(e, httpx.TransportError):
            _err("TRANSPORT_ERROR", f"viewset unreachable: {type(e).__name__}", 7)
        raise

    api_full = bundle.get("api_result_full") or {}
    total, row_count, rows = _total_and_rows(api_full)
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    columns = [str(k) for k in first.keys()]

    dest_dir = os.path.join(_scratch_dir(), "query")
    dest = os.path.join(dest_dir, f"result-{bundle_id}.json")
    import orjson  # pragma: no cover
    os.makedirs(dest_dir, exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(orjson.dumps(rows))

    return {
        "turn_id": None,
        "bundle_id": bundle_id,
        "total": total,
        "row_count": row_count,
        "columns": columns,
        "path": dest,
        "reply": reply,
    }


def _dispatch_recall(args):
    """Fetch a prior NS turn's raw rows by explicit turn_id (§4.C).

    Resolves turn_id → bundle_id via session detail, downloads the bundle,
    materializes rows to scratch/recall/turn-<N>.json, returns manifest.
    No latest-bundle fallback; errors before any scratch write.
    """
    session_id = os.environ.get("NEXTSEEK_CHAT_SESSION_ID")
    if not session_id:
        _err("CONFIG_MISSING", "NEXTSEEK_CHAT_SESSION_ID not set", 2)

    turn_id = args.turn
    if turn_id is None:
        _err("VALIDATION", "--turn required", 3)

    client = _make_client()
    try:
        detail = client.session_detail(session_id, include_turns=True)
    except Exception as e:
        import httpx  # pragma: no cover
        if isinstance(e, httpx.HTTPStatusError):
            if e.response.status_code == 401:
                _err("AUTH_FAILED", "authentication failed (check NS credentials)", 8)
            _err("AGENT_FAILED", f"HTTP {e.response.status_code}", 4)
        if isinstance(e, httpx.TransportError):
            _err("TRANSPORT_ERROR", f"viewset unreachable: {type(e).__name__}", 7)
        raise

    turns = detail.get("turns") or []
    match = next(
        (t for t in turns
         if isinstance(t, dict) and t.get("turn_id") == turn_id),
        None,
    )
    if match is None:
        _err("RECALL_FAILED", f"turn {turn_id} not found", 5)

    bundle_id = match.get("bundle_id")
    if not isinstance(bundle_id, int) or isinstance(bundle_id, bool):
        _err("RECALL_FAILED", f"turn {turn_id} has no bundle_id", 5)

    try:
        bundle = client.download_bundle(session_id, bundle_id)
    except Exception as e:
        import httpx  # pragma: no cover
        if isinstance(e, httpx.HTTPStatusError):
            if e.response.status_code == 401:
                _err("AUTH_FAILED", "authentication failed (check NS credentials)", 8)
            _err("AGENT_FAILED", f"HTTP {e.response.status_code}", 4)
        if isinstance(e, httpx.TransportError):
            _err("TRANSPORT_ERROR", f"viewset unreachable: {type(e).__name__}", 7)
        raise

    api_full = bundle.get("api_result_full") or {}
    total, row_count, rows = _total_and_rows(api_full)
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    columns = [str(k) for k in first.keys()]

    dest_dir = os.path.join(_scratch_dir(), "recall")
    dest = os.path.join(dest_dir, f"turn-{turn_id}.json")
    import orjson  # pragma: no cover
    os.makedirs(dest_dir, exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(orjson.dumps(rows))

    return {
        "turn_id": turn_id,
        "bundle_id": bundle_id,
        "total": total,
        "row_count": row_count,
        "columns": columns,
        "path": dest,
    }


def _api_user() -> str:  # pragma: no cover
    return os.environ.get("API_USER", "")  # pragma: no cover


def _api_pass() -> str:  # pragma: no cover
    return os.environ.get("API_PASS", "")  # pragma: no cover


def _dispatch_pipeline(args):
    """Hand a CC-composed summary message to NS pipeline_agent (deterministic bridge).

    Posts the message to the async query path with mode='pipeline' + the injected
    chat session id; the server calls pipeline_agent.start directly (no parser) and
    the poll loop surfaces the wizard's real first reply. No 30 s ReadTimeout.
    """
    session_id = os.environ.get("NEXTSEEK_CHAT_SESSION_ID")
    if not session_id:
        _err("CONFIG_MISSING", "NEXTSEEK_CHAT_SESSION_ID not set (need a chat session to seed)", 2)
    if not getattr(args, "message", None):
        _err("VALIDATION", "missing --message", 3)
    if _dry_run():
        return {"reply": "[dry-run]", "debug": {}, "bundle_id": None}
    return _run_viewset(args.message, mode="pipeline", session_id=session_id)


def _dispatch_run_ls(args):
    """Recursive read-only listing of a finished Luria run dir (reingest input)."""
    if _dry_run():  # pragma: no branch
        return {"run_dir": args.run_dir, "truncated": False, "tree": "[dry-run]"}  # pragma: no cover
    if not args.run_dir:  # pragma: no cover
        _err("VALIDATION", "missing --run-dir", 3)  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op("run-ls", {"run_dir": args.run_dir},  # pragma: no cover
                          ns_login=(_api_user(), _api_pass()),  # pragma: no cover
                          sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


def _dispatch_build_upload_xlsx(args):
    """Render NExtSEEK 4-sheet upload workbook(s) from CC-composed reingest rows."""
    if _dry_run():  # pragma: no branch
        return {"saved_files": {}, "qa": {}}  # pragma: no cover
    if not args.rows:  # pragma: no cover
        _err("VALIDATION", "missing --rows", 3)  # pragma: no cover
    import _sidecar_client as sc  # pragma: no cover
    body = {"rows": args.rows}  # pragma: no cover
    if getattr(args, "existing_parent_uids", None):  # pragma: no cover
        body["existing_parent_uids"] = args.existing_parent_uids  # pragma: no cover
    try:  # pragma: no cover
        return sc.call_op("build-upload-xlsx", body,  # pragma: no cover
                          ns_login=(_api_user(), _api_pass()),  # pragma: no cover
                          sidecar_url=sc.sidecar_url_from_env())  # pragma: no cover
    except sc.SidecarCallError as e:  # pragma: no cover
        _err(e.code, e.message, e.exit_code)  # pragma: no cover


_DISPATCH = {
    "query": _dispatch_query,
    "recall": _dispatch_recall,
    "entity": _dispatch_entity,
    "parse": _dispatch_parse,
    "plan": _dispatch_plan,
    "api-read": _dispatch_api_read,
    "api-write": _dispatch_api_write,
    "graph": _dispatch_graph,
    "report": _dispatch_report,
    "generate-submission": _dispatch_generate_submission,
    "pipeline": _dispatch_pipeline,
    "run-ls": _dispatch_run_ls,
    "build-upload-xlsx": _dispatch_build_upload_xlsx,
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", required=True, choices=sorted(_DISPATCH))
    p.add_argument("--query")
    p.add_argument("--parser-plan")  # for api-read / api-write
    p.add_argument("--confirmed-write", action="store_true")
    p.add_argument("--mode")  # for report
    p.add_argument("--project")  # for report
    p.add_argument("--type")  # for generate-submission
    p.add_argument("--uids")  # for generate-submission
    p.add_argument("--pipeline")  # for pipeline (nf-core key)
    p.add_argument("--message")  # for pipeline (CC-composed summary)
    p.add_argument("--run-dir")  # for run-ls (finished Luria run dir)
    p.add_argument("--rows")  # for build-upload-xlsx (JSON rows)
    p.add_argument("--existing-parent-uids")  # for build-upload-xlsx (Parent QA)
    p.add_argument("--planner", action="store_true",  # for query
                   help="Use run_query_plan instead of run_query (multi-step capable)")
    p.add_argument("--turn", type=int)  # for recall
    args = p.parse_args()

    # Normalise env once, before any downstream read.
    # See _sanitize_env_quotes docstring for the rationale (docker --env-file
    # / dotenv_values preserve surrounding quotes from .env literals).
    _sanitize_env_quotes()

    # Important-2: enforce CONFIG_MISSING before any dispatch (matches the old
    # runner's _load_config guard). In dry-run mode the credentials are not
    # exercised, so we skip the check -- matching old runner behavior where
    # _load_config was only called outside the dry-run branch.
    if not _dry_run():
        if not os.environ.get("API_USER") or not os.environ.get("API_PASS"):
            _err("CONFIG_MISSING", "API_USER / API_PASS not set", 2)

    try:
        result = _DISPATCH[args.agent](args)
    except SystemExit:  # pragma: no cover
        raise  # pragma: no cover
    except Exception as exc:
        _err("AGENT_FAILED",
             f"{type(exc).__name__}: {exc}",
             4)
    sys.stdout.write(json.dumps(result, default=str) + "\n")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    main()  # pragma: no cover
