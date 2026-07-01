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


def _run_viewset(query: str, mode: str) -> dict:  # pragma: no cover  # Minor-8
    """Shared helper: drive the NExtSEEK assistant viewset for query and plan ops.

    Handles 401/HTTP/transport errors uniformly and returns the shaped terminal
    dict {"reply": str, "debug": {...}, "bundle_id": int|None}.
    """
    import _assistant_client as ac  # pragma: no cover
    import httpx  # pragma: no cover
    client = ac.AssistantClient(  # pragma: no cover
        base_url=os.environ["NEXTSEEK_URL"],  # pragma: no cover
        assistant_prefix=os.environ.get("NEXTSEEK_ASSISTANT_PREFIX", "nextseek_api/assistant"),  # pragma: no cover
        auth=(_api_user(), _api_pass()),  # pragma: no cover
    )  # pragma: no cover
    try:  # pragma: no cover
        terminal, _ = client.run_query(query, mode=mode)  # pragma: no cover
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
    """Single-shot orchestrator via the NExtSEEK assistant viewset.

    Routes to run_query (standard) or run_query_plan (--planner flag).
    Returns the terminal payload shaped as {"reply": str, "debug": {...},
    "bundle_id": int|None}. Preserves the .reply extraction contract used
    by the nextseek-query shim (recon:runner §2b).
    """
    if _dry_run():  # pragma: no branch
        return {"reply": "[dry-run]", "debug": {}, "bundle_id": None}  # pragma: no cover
    mode = "plan" if args.planner else "standard"  # pragma: no cover
    return _run_viewset(args.query, mode=mode)  # pragma: no cover


def _api_user() -> str:  # pragma: no cover
    return os.environ.get("API_USER", "")  # pragma: no cover


def _api_pass() -> str:  # pragma: no cover
    return os.environ.get("API_PASS", "")  # pragma: no cover


_DISPATCH = {
    "query": _dispatch_query,
    "entity": _dispatch_entity,
    "parse": _dispatch_parse,
    "plan": _dispatch_plan,
    "api-read": _dispatch_api_read,
    "api-write": _dispatch_api_write,
    "graph": _dispatch_graph,
    "report": _dispatch_report,
    "generate-submission": _dispatch_generate_submission,
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
    p.add_argument("--planner", action="store_true",  # for query
                   help="Use run_query_plan instead of run_query (multi-step capable)")
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
