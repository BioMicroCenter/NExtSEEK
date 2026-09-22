from __future__ import annotations

import copy
import json
import logging
import re
import shutil
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from .artifacts import (
    ArtifactStore,
    build_metadata_bundle,
    build_saved_report_file_manifest,
    load_api_result_full,
)
from .chat_memory import append_turn, build_tool_summary_for_mode, resolve_bundle_for_recall
from .pipeline import agent as pipeline_agent
from .agents.followup import resolve_followup_outcome, run_followup
from .agents import (
    chatter_agent_answer,
    chatter_agent_plan,
    entity_agent,
    graph_agent,
    memory_agent_answer,
    multi_parser_agent,
    parser_agent,
    plan_evaluator_agent,
    planner_agent,
    report_writer_agent,
    reporter_agent,
    system_agent,
    _execute_single_plan_step,
    _materialize_intersection_result,
    _step_signature,
)
from .agents.reporter import report_coder_agent
from .config import ChatConfig
from .graph_scope import SCOPE_ATTR, GraphScope
from .prompt_variants import variant_record
from .llm_clients import LLMFatalError
from .helpers import (
    _extract_required_paths,
    _retry_advanced_search_if_empty,
    api_row_count,
    build_api_result_meta,
    fix_sample_endpoint,
    generate_report_outputs,
    log_api_call,
    reporter_reply_footer,
    run_reporter_summary,
    shortlist_catalog,
    slim_api_result_for_llm,
    tool_nextseek_api_request,
    matched_nothing,
    tool_neo4j_query,
)
from .graph_retry import RETRY_CHANGED_ANSWER_NOTE, zero_row_retry_context
from .helpers.lab_code import clamp_lab_codes
from .helpers.tools.neo4j import is_scope_refusal
from .helpers.uid_check import check_uids, uid_notes, uids_in
from .schemas import APIRequestPlan, EntityAgentOutput, ParserPlan, PlannerOutput, ReportWriterOutput
from .session import SessionState
from .tee import Tee

SendEvent = Callable[[str, dict[str, Any]], None]

_LOG = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Turn identity
# --------------------------------------------------------------------------
#
# Every entry point below (run_query, run_query_plan, run_pipeline_launch)
# used to carry the same four-line seam: override config.API_USER/API_PASS
# only `if credentials:` and only for the truthy halves. With absent session
# credentials the turn proceeded as whatever account ChatConfig was built
# with -- `demo`/`demopassword` in the shipped template -- silently answering
# with a different identity's permissions than the asking user's.
#
# `credentials is None` means something DIFFERENT from an empty mapping, and
# the distinction is what makes fail-closed safe to default:
#
#   * Every request-scoped caller (nextseek_api/services/assistant.py,
#     cc_assistant.py, evaluator.py) passes a MAPPING even when it could not
#     resolve the caller -- the values are simply None. That is the case this
#     gate exists for: a real asking user exists and we failed to bind to them.
#   * `credentials is None` comes from the single-operator surfaces (cli.py,
#     app.py, mcp_server.py, e2e/runner.py) where the ChatConfig credentials
#     ARE the operator's own identity and there is nobody to impersonate.
#     Those warn but are never refused.
#
# Two known consequences of that split, both verified, neither an oversight:
#
#   * DRF TOKEN callers are now REFUSED. AssistantViewSet (and CCAssistantViewSet)
#     list TokenAuthentication in authentication_classes and _check_auth resolves
#     ["BASIC","SESSION","TOKEN"], but credential resolution (assistant.py:728)
#     resolves only ["BASIC","SESSION"] and then falls back to
#     request.session.get(...), which is empty for a token request. So a token
#     caller arrives here as {"api_user": None, "api_pass": None} and is refused.
#     That is this gate working as intended -- a token caller previously ran
#     silently as the service account, which IS the hole -- but it is an
#     undocumented break of a supported auth mode. The real repair belongs in
#     services/assistant.py (resolve TOKEN into credentials, or 401 at the front
#     door); it is filed as a follow-up, not fixable from here.
#   * One single-operator surface passes a MAPPING and so CAN be refused:
#     evaluator/runner.py::_build_retry_credentials returns None when the config
#     has neither half and a complete dict when it has both -- but a PARTIAL
#     mapping when only API_USER or only API_PASS is set. A half-configured
#     batch-evaluator CLI therefore refuses. Only reachable on a misconfigured
#     ChatConfig; the clean repair (return None unless both halves are present)
#     is filed as a follow-up. Do not read the bullet above as absolute.
#
# Default is OFF (fail closed). The nessie_tests harness authenticates over
# HTTP Basic (nessie_tests/http_driver.py) which assistant.py resolves via
# resolve_seek_auth into a complete pair, so it never takes this path.
_ALLOW_SERVICE_ACCOUNT_FALLBACK_DEFAULT = False

_IDENTITY_LOG_MARK = "[SECURITY][IDENTITY]"

_IDENTITY_REFUSAL_REPLY = (
    "**This request was not run.**\n\n"
    "The assistant could not establish which NExtSEEK account this turn belongs to. "
    "Running it anyway would answer using a shared service account's permissions "
    "rather than yours, so the turn was refused instead. Sign in again (or supply "
    "Basic-auth credentials) and retry.\n\n"
    "_Operators: set `NEXTSEEK_ALLOW_SERVICE_ACCOUNT_FALLBACK` on the ChatConfig "
    "to re-enable the service-account fallback._"
)


# --------------------------------------------------------------------------
# Turn graph scope
# --------------------------------------------------------------------------
#
# Every graph statement is held to the caller's project scope by the Neo4j tool, which
# reads a GraphScope off the config (graph_scope.py; spec
# docs/superpowers/specs/2026-09-18-graph-cypher-scope.md). The request-scoped callers
# resolve it on the server and hand it to the entry points below as `graph_scope`
# (plain data, {"is_admin", "project_ids"}); the gate puts it on the per-request copy.
# A caller that leaves the keyword out keeps whatever its own config carries: that is
# how the single-operator surfaces (CLI, MCP, evaluator) set theirs.


class _Unset:
    def __repr__(self) -> str:
        return "_UNSET"


_UNSET: Any = _Unset()

#: The project-scoped sample search a refused graph question falls back to.
GRAPH_SEARCH_ENDPOINT = "/nextseek_api/samples/graph_search/"

#: Told to the chatter when a graph question was answered by the fallback.
SCOPE_FALLBACK_NOTE = (
    "The graph query written for this question could not be confirmed to stay within the user's projects, so it "
    "was not run. This answer comes from the project-scoped sample search instead. Say so, and say which "
    "conditions of the question that search could not apply."
)

#: Appended to the reply of a fallback turn, so the disclosure never depends on the model.
SCOPE_FALLBACK_FOOTER = (
    "Note: this answer comes from the project-scoped sample search, because the graph query for it could not be "
    "confirmed to stay within your projects. That search cannot express every condition a graph query can."
)


@dataclass(frozen=True)
class GraphScopeFallback:
    """A graph turn whose final query was refused for its scope: answer it through graph_search instead."""

    codes: tuple[str, ...]
    reasons: tuple[str, ...]
    submitted_cypher: str | None
    attempts: tuple[dict[str, Any], ...]


def _coerce_graph_scope(graph_scope: Any, *, entry_point: str) -> GraphScope | None:
    """A GraphScope as it is, a mapping through GraphScope.from_plain, anything else None (which refuses)."""
    if isinstance(graph_scope, GraphScope):
        return graph_scope
    if isinstance(graph_scope, Mapping):
        try:
            return GraphScope.from_plain(graph_scope)
        except ValueError as exc:
            _LOG.warning("%s: malformed graph scope, no graph query will run: %s", entry_point, exc)
            return None
    if graph_scope is not None:
        _LOG.warning("%s: graph scope of type %s ignored, no graph query will run", entry_point,
                     type(graph_scope).__name__)
    return None


def _coerce_setting_bool(value: Any, *, default: bool) -> bool:
    """Mirror ChatConfig._coerce_bool so a string 'false' from a config_map stays false."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _emit_identity_warning(message: str) -> None:
    """Warn on both surfaces an operator actually reads.

    Both land in CONTAINER stdout/stderr -- `docker logs nextseek` -- and NOT in
    the per-turn outputs/<ts>_<user>/console.txt trace. The identity gate runs
    before _ensure_query_log_dir, which is what installs the Tee onto
    sys.stdout/sys.stderr, so by design there is no per-turn trace yet (on the
    refuse branch there never will be: no run directory is created for a turn
    that did not run). Look in `docker logs`, not in outputs/, when triaging
    "why did this turn answer as someone else?".

    logging.warning carries the severity a log aggregator can filter on; with no
    chat_nextseek logger and no root handler configured in dmac/settings.py it
    reaches stderr via Python's lastResort handler. The print matches this
    module's diagnostic convention and keeps the line adjacent to the rest of
    the turn's output.
    """
    _LOG.warning(message)
    print(message)


def _credentials_are_complete(credentials: dict[str, str] | None) -> bool:
    """True only when BOTH halves of a per-request identity are present.

    A half-supplied pair is worse than none: the old code applied the supplied
    half and left the other on the service account, producing a mixed identity
    (user A's name, the service account's password). Partial counts as missing.
    """
    if not isinstance(credentials, dict):
        return False
    return bool(credentials.get("api_user")) and bool(credentials.get("api_pass"))


def _service_account_fallback_allowed(config: ChatConfig) -> bool:
    """Read the fallback setting off the config object (threaded from settings, never os.getenv)."""
    return _coerce_setting_bool(
        getattr(config, "NEXTSEEK_ALLOW_SERVICE_ACCOUNT_FALLBACK", None),
        default=_ALLOW_SERVICE_ACCOUNT_FALLBACK_DEFAULT,
    )


def _identity_gate(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    credentials: dict[str, str] | None,
    send_event: SendEvent | None,
    *,
    entry_point: str,
    graph_scope: Any = _UNSET,
) -> tuple[ChatConfig, dict[str, Any] | None]:
    """Bind the turn to the caller's identity and graph scope, or refuse to impersonate.

    The identity half is ``_bind_identity``. When ``graph_scope`` is given (anything but
    ``_UNSET``), the turn runs on a per-request copy whose ``GRAPH_SCOPE`` is that scope:
    a ``GraphScope`` as it is, a mapping through ``GraphScope.from_plain``, anything else
    (``None``, a malformed mapping) as ``None``, which refuses every graph query. The shared
    config is never mutated. ``_UNSET`` leaves the config's own scope in place.
    """
    bound, refusal = _bind_identity(session, config, credentials, send_event, entry_point=entry_point)
    if refusal is not None or graph_scope is _UNSET:
        return bound, refusal
    if bound is config:
        bound = copy.copy(config)
    setattr(bound, SCOPE_ATTR, _coerce_graph_scope(graph_scope, entry_point=entry_point))
    return bound, None


def _bind_identity(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    credentials: dict[str, str] | None,
    send_event: SendEvent | None,
    *,
    entry_point: str,
) -> tuple[ChatConfig, dict[str, Any] | None]:
    """Bind the turn to the caller's identity, or refuse to impersonate.

    Returns ``(config, refusal)``. When ``refusal`` is not None the entry point
    must return it unchanged: it is an already-emitted ``query_complete``
    payload carrying a user-facing explanation, chosen over raising so the
    caller renders a diagnosable refusal instead of a 500 (a bare raise escapes
    run_query through its re-raising ``except Exception`` and becomes a task
    crash reported as "Internal pipeline error").

    On the happy path a SHALLOW copy of config is made so the shared singleton
    is never mutated; LLM clients, catalogs, and prompts stay shared by reference.
    """
    if _credentials_are_complete(credentials):
        config = copy.copy(config)
        config.API_USER = credentials["api_user"]
        config.API_PASS = credentials["api_pass"]
        return config, None

    # Name the account only. NEVER the password -- not the value, not a mask,
    # not a length hint.
    account = getattr(config, "API_USER", None) or "<unset>"
    request_scoped = credentials is not None
    supplied = [
        key for key in ("api_user", "api_pass")
        if isinstance(credentials, dict) and credentials.get(key)
    ]
    if supplied:
        detail = f"incomplete per-request credentials (only {', '.join(supplied)} supplied)"
    elif request_scoped:
        detail = "no per-request credentials"
    else:
        detail = "no per-request identity supplied (single-operator surface)"

    if request_scoped and not _service_account_fallback_allowed(config):
        message = (
            f"{_IDENTITY_LOG_MARK} {entry_point}: refusing this turn -- {detail}; "
            f"falling back to the configured account {account!r} is disabled "
            f"(NEXTSEEK_ALLOW_SERVICE_ACCOUNT_FALLBACK)."
        )
        _emit_identity_warning(message)
        debug_payload: dict[str, Any] = {
            "identity_refused": True,
            "reason": detail,
            "fallback_account": str(account),
            "entry_point": entry_point,
        }
        try:
            session["last_debug"] = debug_payload
        except Exception:  # pragma: no cover - exotic session proxies
            pass
        return config, _emit_query_complete(
            send_event, _IDENTITY_REFUSAL_REPLY, debug_payload, None,
        )

    _emit_identity_warning(
        f"{_IDENTITY_LOG_MARK} {entry_point}: {detail}; this turn runs as the "
        f"configured account {account!r}, NOT as the asking user."
    )
    return config, None


def _artifacts_for(bundle: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Tables and downloadable files for a finished turn, or None.

    Every route registers its outputs in ``bundle["files"]`` but only the
    reporter branch ever passed artifacts to the UI, so a search's "Full API
    result JSON" was written to disk and never offered. The import is lazy and
    guarded because ``nextseek_api`` is the host application: this package is
    vendored and also runs standalone, where a missing artifact list is a
    degraded turn rather than a failed one.
    """
    if not bundle:
        return None
    try:
        from nextseek_api.assistant.excel_export import build_artifacts

        return build_artifacts(bundle) or None
    except Exception:
        return None


def _emit_query_complete(
    send_event: SendEvent | None,
    reply: str,
    debug: dict[str, Any],
    bundle_id: int | None,
    *,
    artifacts: list[dict[str, Any]] | None = None,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the final query payload and emit a `query_complete` event when requested."""
    payload: dict[str, Any] = {
        "reply": reply,
        "debug": debug,
        "bundle_id": bundle_id,
    }
    if artifacts:
        payload["artifacts"] = artifacts
    if files:
        payload["files"] = files
    if send_event:
        send_event("query_complete", payload)
    return payload


def run_pipeline_launch(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    send_event: SendEvent | None = None,
    *,
    credentials: dict[str, str] | None = None,
    graph_scope: Any = _UNSET,
) -> dict[str, Any]:
    """Deterministic CC → pipeline_agent bridge entry (query/async mode='pipeline').

    Starts the pipeline wizard directly from a CC-composed summary message — no
    parser/reporter classification. Runs on the async task path, so pipeline_agent's
    real first reply is surfaced (no canned turn) and there is no 30 s bin ReadTimeout.
    Follow-up turns continue via the F9 router gate → _handle_pipeline_agent_turn.

    credentials — see _identity_gate. An incomplete per-request identity refuses
    the turn rather than launching a pipeline as the service account.
    """
    config, identity_refusal = _identity_gate(
        session, config, credentials, send_event, entry_point="run_pipeline_launch", graph_scope=graph_scope,
    )
    if identity_refusal is not None:
        return identity_refusal

    log_dir = _ensure_query_log_dir(session, config)
    if send_event:
        send_event("agent_started", {"agent": "pipeline_agent", "mode": "pipeline"})

    pa_start = pipeline_agent.start(session, config, user_query=user_text, log_dir=log_dir)
    reply = pa_start.get("reply") or ""
    snapshot = pipeline_agent.snapshot_for_chat_log(session)
    debug_payload = {"pipeline_agent": snapshot}
    session["last_debug"] = debug_payload
    append_turn(
        session,
        user_query=user_text,
        mode="pipeline_agent",
        intent_summary="pipeline_agent launched (cc bridge)",
        tool_summary={"pipeline_key": snapshot.get("pipeline_key"),
                      "cohorts": snapshot.get("cohort_count")},
        assistant_reply=reply,
        wizard_state=snapshot,
    )
    return _emit_query_complete(send_event, reply, debug_payload, None)


def _sanitize_output_component(value: str | None, default: str = "unknown") -> str:
    """Return a filesystem-safe path component for per-run output directories."""
    text = (value or "").strip()
    if not text:
        return default
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return sanitized or default


def _ensure_query_log_dir(session: SessionState | SessionStateProxy, config: ChatConfig) -> str:
    """
    Ensure the session has a per-run output directory.
    Layout:
      <OUTPUTS_DIR>/<YYMMDD_HHMMSS>_<API_USER>/files
    """
    existing = session.get("log_dir")
    if existing:
        return str(existing)

    ts = datetime.now().strftime("%y%m%d_%H%M%S")
    api_user = _sanitize_output_component(getattr(config, "API_USER", None))
    run_root = Path(config.OUTPUTS_DIR) / f"{ts}_{api_user}"
    files_dir = run_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    session["run_root_dir"] = str(run_root)
    session["log_dir"] = str(files_dir)
    session["console_log_path"] = str(run_root / "console.txt")
    session["chat_log_path"] = str(run_root / "chat.txt")
    session["api_log_path"] = str(run_root / "api_requests.json")
    session["prompts_log_path"] = str(run_root / "prompts.json")

    sys.stdout = Tee(sys.stdout, session["console_log_path"])
    sys.stderr = Tee(sys.stderr, session["console_log_path"])

    if not session.get("config_snapshot_logged"):
        try:
            config_snapshot = config.get_config_snapshot()
            print("[CONFIG] Snapshot:\n" + json.dumps(config_snapshot, indent=2))
        except Exception as e:
            print("[CONFIG] Failed to log config snapshot:", repr(e))
        session["config_snapshot_logged"] = True

    return str(files_dir)


def _write_graph_debug(log_dir: str, ts: str, payload: dict) -> str | None:
    """Write the graph agent debug payload to a timestamped JSON file in log_dir."""
    try:
        store = ArtifactStore(log_dir)
        entry = store.write_json(
            key=f"graph_debug_{ts}",
            label="Graph query debug JSON",
            filename=f"graph_debug_{ts}.json",
            payload=payload,
            kind="graph",
        )
        if entry:
            print(f"[GRAPH TEST] Debug written to {entry['path']}")
            return entry["path"]
        return None
    except Exception as e:
        print(f"[GRAPH TEST] Failed to write debug file: {e!r}")
        return None


def _build_graph_refine_context(last_bundle: dict) -> str:
    """Prior context for a refine the graph will run.

    Mirrors the REST refine block in api_agent_build_request (prior user query + prior plan).
    When the previous turn was REST there is no Cypher to carry, so the filters it actually
    sent are carried instead: without them a re-routed refine loses the scope the user set in
    the turn before and silently widens the question (F13).
    """
    prior_query = last_bundle.get("user_query") or ""
    graph_plan = last_bundle.get("graph_plan") or {}
    prior_cypher = graph_plan.get("cypher") or ""
    if prior_cypher:
        return (
            "Previous graph query context (you are refining it):\n"
            f"Prior user query: {prior_query or '[none]'}\n"
            f"Prior Cypher:\n{prior_cypher}"
        )

    parser_plan = last_bundle.get("parser_plan") or {}
    filters = {k: v for k, v in (parser_plan.get("filters") or {}).items() if v}
    api_plan = last_bundle.get("api_plan") or {}
    body = {k: v for k, v in (api_plan.get("requestBody") or {}).items() if v}
    return (
        "Previous REST search context (you are refining it, and it is moving to the graph):\n"
        f"Prior user query: {prior_query or '[none]'}\n"
        f"Filters it resolved: {json.dumps(filters, default=str) if filters else '[none]'}\n"
        f"What it sent: {json.dumps(body, default=str) if body else '[none]'}\n"
        "Keep every constraint above that the user has not changed in this turn."
    )


def _run_followup_agent(config, *, session, user_text: str, bundle: dict, log_dir) -> dict | None:
    """Run the follow-up tool loop, and never let it be the reason a turn fails.

    Its ``run_new_query`` seam re-uses the graph agent the ordinary graph turn uses, so
    a follow-up runs the same engine as a fresh question; the difference is only that
    the previous result's UIDs are handed to it. Returns None on any failure, which
    sends the caller to the pre-existing stored-result path.
    """
    def _run_query(*, question: str, seed_uids: list[str]) -> dict:
        refine = None
        if seed_uids:
            # The UIDs used to be pasted into the prompt, capped at 200, and the graph
            # agent copied that truncated list into `$uids` verbatim: on 2026-09-22 turn
            # 1147 three queries bound 28, 200 and 200 of 1,549 while the payload below
            # reported `seeded_uid_count` 1,549, so a 200-mouse answer would have been
            # reported as the whole set. Bind them as a parameter instead and show only a
            # handful for orientation.
            shown = ", ".join(seed_uids[:10])
            refine = (
                "Scope this query to the sample UIDs of the result the user is asking a "
                "follow-up about. They are ALREADY BOUND as the query parameter $uids: "
                "filter with `WHERE s.uuid IN $uids` and never paste UIDs into the query "
                "text. Do not widen to every sample of the same type.\n"
                f"$uids holds {len(seed_uids)} UIDs. A few of them, so you can see their "
                f"shape: {shown}"
            )
        graph_plan = graph_agent(
            config, question, EntityAgentOutput(),
            ParserPlan(mode="graph_query", intent_summary=question),
            refine_context=refine,
        )
        if not graph_plan.cypher:
            return {"ok": False, "error": "no query could be generated for that question"}
        parameters = dict(graph_plan.parameters or {})
        applied = None
        if seed_uids and "$uids" in graph_plan.cypher:
            parameters["uids"] = list(seed_uids)  # every one of them, not the ten shown
            applied = len(seed_uids)
        result = tool_neo4j_query(config, graph_plan.cypher, parameters)
        rows = result.get("data") or []
        # Counts and a few examples. Never rows: this goes back into a conversation
        # that is re-sent in full on every later iteration of the loop.
        return {
            "ok": bool(result.get("ok")),
            "count": result.get("total") if result.get("total") is not None else result.get("count"),
            "rows_returned": len(rows),
            "truncated": bool(result.get("truncated")),
            "examples": _followup_examples(rows),
            "error": result.get("error"),
            "uids_available": len(seed_uids),
            # None when the query did not filter on $uids: it then covers whatever it
            # matched, which is not the same set, and the answer has to say so.
            "uids_applied": applied,
            "scope_note": (None if applied or not seed_uids else
                           "This query did not filter on $uids, so it is not scoped to "
                           "the previous result. Say so, or run it again scoped."),
        }

    try:
        return run_followup(
            config, user_text=user_text, bundle=bundle, run_query=_run_query, log_dir=log_dir,
        )
    except Exception as exc:
        print(f"[DEBUG][FOLLOWUP] agent failed, falling back to the stored result: {exc!r}")
        return None


def _followup_examples(rows: list, limit: int = 3) -> list[str]:
    out: list[str] = []
    for row in rows[: limit * 3]:
        if not isinstance(row, dict):
            continue
        for key in ("uid", "UID", "uuid", "UUID", "id", "name"):
            value = row.get(key)
            if isinstance(value, str) and value:
                out.append(value)
                break
        if len(out) >= limit:
            break
    return out


def _clamp_lab_codes_to_entity(plan, entity_result):
    """``plan`` with every lab code the entity agent did not match removed (OD4).

    The entity agent emits ``lab_codes`` only from SEEK lab records it matched, so its list
    is empty exactly when nothing matched. The parser LLM writes its own ``filters.lab_codes``
    (per candidate in plan mode) and echoes the entity result into ``resolved``, and its
    prompt still teaches a surname-to-code rule: a scientist, or a lab whose SEEK title did
    not parse, could come back as a guessed code. Everything downstream (the API and graph
    agents, the empty-result retry ladder, the reply's scope note) reads the plan, so the
    clamp runs once, straight after the parser. Nothing but the codes changes.
    """
    if isinstance(entity_result, dict):
        matched = entity_result.get("lab_codes")
    else:
        matched = getattr(entity_result, "lab_codes", None)
    dropped: list[str] = []

    def _clamp(codes):
        kept = clamp_lab_codes(codes, matched)
        dropped.extend(c for c in (codes or []) if isinstance(c, str)
                       and c.strip().upper() not in kept and c not in dropped)
        return kept

    def _clamp_filters(filters):
        return filters.model_copy(update={"lab_codes": _clamp(filters.lab_codes)})

    updates: dict[str, Any] = {
        "resolved": plan.resolved.model_copy(update={"lab_codes": _clamp(plan.resolved.lab_codes)}),
    }
    if hasattr(plan, "candidates"):
        updates["candidates"] = [
            c.model_copy(update={"filters": _clamp_filters(c.filters)}) for c in plan.candidates
        ]
    else:
        updates["filters"] = _clamp_filters(plan.filters)
    if dropped:
        print(f"[DEBUG][PARSER] dropped lab codes no matched lab record gave: {dropped} "
              f"(entity lab_codes={list(matched or [])})")
    return plan.model_copy(update=updates)


#: How many times the graph turn may generate-execute-read before it settles.
#: Bounded on purpose: each try is a model call plus a Neo4j round trip on the user's
#: latency budget, and the measured graph stage already runs at a p90 of 18.9 s.
GRAPH_MAX_TRIES = 3


def _graph_attempt(cypher: str | None, result: dict, reason: str) -> dict[str, Any]:
    """One generate-execute round for debug.graph_attempts: what was written, what ran, and the decision."""
    scope = result.get("scope")
    return {
        "cypher": cypher, "ok": result.get("ok"),
        "count": result.get("count"), "error": result.get("error"),
        "reason": reason,
        "executed_cypher": result.get("cypher"),
        "scope_decision": scope.get("decision") if isinstance(scope, dict) else None,
    }


def _graph_scope_fallback(graph_plan, graph_result: dict, attempts: list, debug_payload: dict,
                          send_event) -> GraphScopeFallback:
    """Record a refused graph turn and hand it back to run_query, which answers through graph_search.

    No bundle is stored and the chatter is not called: the refused query produced nothing to
    remember or narrate, and a graph bundle would make the next refine re-run the graph path.
    """
    scope = graph_result.get("scope") if isinstance(graph_result.get("scope"), dict) else {}
    submitted = graph_result.get("submitted_cypher", graph_plan.cypher)
    fallback = GraphScopeFallback(
        codes=tuple(scope.get("codes") or ()),
        reasons=tuple(scope.get("reasons") or ()),
        submitted_cypher=submitted,
        attempts=tuple(attempts),
    )
    debug_payload["graph_plan"] = graph_plan.model_dump()
    debug_payload["graph_result"] = {k: v for k, v in graph_result.items() if k != "data"}
    debug_payload["graph_scope_fallback"] = {
        "endpoint": GRAPH_SEARCH_ENDPOINT,
        "codes": list(fallback.codes),
        "reasons": list(fallback.reasons),
        "submitted_cypher": submitted,
    }
    print(f"[GRAPH] Query refused for its project scope {list(fallback.codes)}; falling back to "
          f"{GRAPH_SEARCH_ENDPOINT}")
    send_event("search_complete", {"source": "neo4j", "ok": False, "count": None, "scope": "refused"})
    return fallback


def _schema_fallback_line(fallback) -> str | None:
    """The debug panel's line for a graph turn whose schema was the committed file (``GraphAgentPlan.context_fallback``),
    or None on a turn that read the live catalog."""
    if not isinstance(fallback, dict):
        return None
    return (f"committed graph schema (captured {fallback.get('fallback_fetched_at') or 'on an unknown date'}), "
            f"not the live catalog: {fallback.get('unavailable_reason')}")


def _fall_back_to_graph_search(plan: ParserPlan) -> tuple[ParserPlan, str, list[str]]:
    """The plan, mode and chatter notes that send a refused graph question through the REST branch."""
    plan = plan.model_copy(update={"mode": "new_search", "target_endpoint": GRAPH_SEARCH_ENDPOINT})
    return plan, "new_search", [SCOPE_FALLBACK_NOTE]


def _execute_graph_turn(
    *,
    config: ChatConfig,
    session,
    user_text: str,
    entity_result,
    plan,
    log_dir,
    artifact_store,
    send_event,
    debug_payload: dict,
    t_total_start: float,
    refine_context: str | None = None,
    note_agent: Callable[[str], None] | None = None,
):
    """``note_agent`` lets the caller follow which agent this turn is on.

    run_query's error handlers report ``current_agent``, a local of the caller. The
    graph turn runs in this function, so that local stayed "graph" for the whole turn:
    production turns 463 and 464 emitted a provider failure labelled as the graph
    agent when the graph query had already succeeded and the chatter was what failed.
    The REST path updates its own local in place and never had the problem.
    """
    def _on(agent: str) -> None:
        if note_agent is not None:
            note_agent(agent)

    _on("graph")
    send_event("agent_started", {"agent": "graph", "mode": "graph_query"})
    _t0 = time.perf_counter()

    # B2 (Pilot A v2, 2026-09-18): a UID the user names is looked up before the agent
    # writes a query, with and without a -PUB suffix. Two turns asked about -PUB UIDs the
    # graph stores without the suffix, matched nothing, and one reply said "0 samples are
    # directly derived" about a sample the query never found.
    turn_uids = uids_in(user_text, getattr(getattr(plan, "filters", None), "uids", None))
    uid_checks = check_uids(config, turn_uids, run=tool_neo4j_query) if turn_uids else []
    uid_agent_note, uid_reply_notes = uid_notes(uid_checks)
    if uid_agent_note:
        debug_payload["uid_checks"] = [{"asked": c.asked, "stored": c.stored} for c in uid_checks or []]
    agent_context = "\n\n".join(part for part in (refine_context, uid_agent_note) if part) or None

    print("\n[GRAPH] Running graph agent...")
    graph_plan = graph_agent(config, user_text, entity_result, plan, refine_context=agent_context)
    debug_payload["graph_context"] = graph_plan.context_mode
    # A fallback's reason and capture date: the harness reads the payload, the debug panel the summary line.
    schema_fallback = _schema_fallback_line(graph_plan.context_fallback)
    if schema_fallback:
        debug_payload["graph_context_fallback"] = graph_plan.context_fallback
    print(f"[DEBUG][GRAPH] Explanation: {graph_plan.explanation}")
    print(f"[DEBUG][GRAPH] Cypher:\n{graph_plan.cypher}")

    if not graph_plan.cypher:
        reply = f"Graph agent could not generate a query.\n\nReason: {graph_plan.explanation}"
        session["last_debug"] = debug_payload
        send_event("agent_complete", {"agent": "graph",
                                      "summary": {"schema_fallback": schema_fallback} if schema_fallback else None})
        print(f"[TIMING][GRAPH] {time.perf_counter() - _t0:.2f}s")
        print(f"[TIMING][TOTAL] {time.perf_counter() - t_total_start:.2f}s")
        return _emit_query_complete(send_event, reply, debug_payload, None)

    summary = {"cypher": graph_plan.cypher, "explanation": graph_plan.explanation}
    if schema_fallback:
        summary["schema_fallback"] = schema_fallback
    send_event("agent_complete", {"agent": "graph", "summary": summary})

    send_event("search_started", {"source": "neo4j", "cypher": graph_plan.cypher})
    graph_result = tool_neo4j_query(config, graph_plan.cypher, graph_plan.parameters)

    # Generate -> execute -> read the outcome -> regenerate, up to GRAPH_MAX_TRIES.
    # This was one retry and only on a Cypher error, so a query that ran perfectly well
    # and matched nothing was final. That is B11 (juanita, a guessed assay name returned
    # zero and the zero was reported as the answer). A zero-row result now gets exactly
    # one more go, and if the second query also finds nothing the FIRST result stands:
    # reporting a different query's number would be worse than reporting zero.
    attempts: list[dict[str, Any]] = [_graph_attempt(graph_plan.cypher, graph_result, "initial")]
    first_ok_empty = matched_nothing(graph_result)
    zero_row_retry_used = False

    for _ in range(GRAPH_MAX_TRIES - 1):
        if is_scope_refusal(graph_result):
            # Final for the turn: another model call can only write another query the
            # prover cannot prove. The turn falls back to graph_search below.
            break
        if not graph_result.get("ok"):
            neo4j_error = graph_result.get("error", "Unknown error")
            print(f"[GRAPH] Cypher failed, retrying: {neo4j_error}")
            retry_ctx = (
                f"Your previous Cypher query failed with this error:\n{neo4j_error}\n\n"
                "Revisit the schema carefully - check property types, relationship directions, "
                "and graph_topology - then generate a corrected query."
            )
            reason = "cypher_error"
        elif matched_nothing(graph_result) and not zero_row_retry_used:
            zero_row_retry_used = True
            print("[GRAPH] Query ran but matched nothing, retrying once with that context")
            # The wording, and why it no longer says "use the closest value", is in
            # graph_retry.py: the CC aggregate op retries a zero part in the same words.
            retry_ctx = zero_row_retry_context(graph_plan.cypher)
            reason = "zero_rows"
        else:
            break

        graph_plan_retry = graph_agent(
            config, user_text, entity_result, plan,
            retry_context=retry_ctx, refine_context=agent_context,
        )
        if not graph_plan_retry.cypher:
            break
        retry_result = tool_neo4j_query(config, graph_plan_retry.cypher, graph_plan_retry.parameters)
        attempts.append(_graph_attempt(graph_plan_retry.cypher, retry_result, reason))
        # Keep the retry only when it is an improvement. A retry that errors, or that
        # also finds nothing after a zero-row first attempt, leaves the original alone.
        if not retry_result.get("ok"):
            if graph_result.get("ok"):
                break
        elif reason == "zero_rows" and matched_nothing(retry_result):
            break
        graph_plan = graph_plan_retry
        graph_result = retry_result

    debug_payload["graph_attempts"] = attempts
    debug_payload["graph_scope"] = graph_result.get("scope")
    if is_scope_refusal(graph_result):
        return _graph_scope_fallback(graph_plan, graph_result, attempts, debug_payload, send_event)
    # The user must not be told a number without being told the first query found
    # nothing and the filter was changed to get it. Recording it in debug_payload was
    # not enough: nothing read the flag, so the reply never carried the caveat. It now
    # goes to the chatter as a query note as well.
    query_notes: list[str] = list(uid_reply_notes)
    if first_ok_empty and not matched_nothing(graph_result):
        debug_payload["graph_retry_changed_answer"] = True
        query_notes.append(RETRY_CHANGED_ANSWER_NOTE)

    send_event(
        "search_complete",
        {"source": "neo4j", "ok": graph_result.get("ok"), "count": graph_result.get("count")},
    )

    history = session.get("results_history", [])
    bundle_id = _next_bundle_id(session)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    graph_debug_path = _write_graph_debug(
        log_dir, ts,
        {
            "timestamp": ts,
            "user_query": user_text,
            "model": config.MODEL_MODE,
            "entity_output": entity_result.model_dump(),
            "parser_output": plan.model_dump(),
            "graph_output": graph_plan.model_dump(),
            "neo4j_output": {
                "ok": graph_result.get("ok"),
                "count": graph_result.get("count"),
                "error": graph_result.get("error"),
                "counters": graph_result.get("counters"),
                "cypher": graph_result.get("cypher"),
                "scope": graph_result.get("scope"),
                "data_preview": (graph_result.get("data") or [])[:20],
            },
        },
    )
    print(f"[TIMING][GRAPH] {time.perf_counter() - _t0:.2f}s")

    result_files: list[dict[str, Any]] = []
    entry = artifact_store.register_path(
        key="graph_debug", label="Graph query debug JSON", path=graph_debug_path,
        kind="graph", bundle_id=bundle_id,
    )
    if entry:
        result_files.append(entry)

    # F7: the rows themselves, as a file the user can have. The debug JSON above holds a
    # 20-row slice and is kind="graph", which the export layer treats as internal, so a
    # graph turn's payload carried no artifacts at all: a query returned hundreds of rows
    # across six columns and the researcher got none of them, reachable only by accident
    # through the debug panel's JSON button on the newest turn. The REST path has written
    # its rows whole since it was built; this is the graph counterpart, with a kind the
    # export layer does not exclude.
    graph_rows = (graph_result or {}).get("data") or []
    if graph_rows:
        try:
            rows_entry = artifact_store.write_json(
                key="graph_result",
                label="Graph query result rows",
                filename=f"graph_result_bundle_{bundle_id}.json",
                payload={
                    "cypher": graph_plan.cypher,
                    "parameters": graph_plan.parameters,
                    "count": graph_result.get("count"),
                    "total": graph_result.get("total"),
                    "truncated": bool(graph_result.get("truncated")),
                    "rows": graph_rows,
                },
                kind="graph_result",
                bundle_id=bundle_id,
            )
            if rows_entry:
                result_files.append(rows_entry)
        except Exception as e:  # never lose a finished answer to the file that describes it
            print("[DEBUG][GRAPH] Failed to write the graph result rows file:", repr(e))
    bundle = build_metadata_bundle(
        bundle_id=bundle_id, mode="graph_query", user_query=user_text,
        parser_plan=plan.model_dump(), graph_plan=graph_plan.model_dump(),
        graph_result=graph_result, terminal_reply=None,
        search_context={"endpoint": "neo4j"}, files=result_files,
        paths={"graph_debug_path": graph_debug_path},
    )
    history.append(bundle)
    session["results_history"] = history

    debug_payload["graph_plan"] = graph_plan.model_dump()
    debug_payload["graph_result"] = {k: v for k, v in graph_result.items() if k != "data"}

    _on("chatter")
    send_event("agent_started", {"agent": "chatter", "mode": "graph_query"})
    _t1 = time.perf_counter()
    reply = chatter_agent_answer(
        config, user_text, entity_result.model_dump(), plan.model_dump(),
        graph_plan=graph_plan.model_dump(), graph_result=graph_result,
        log_dir=log_dir, session=session, query_notes=query_notes,
    )
    print(f"[TIMING][CHATTER] {time.perf_counter() - _t1:.2f}s")
    send_event("agent_complete", {"agent": "chatter", "summary": None})
    bundle["terminal_reply"] = reply
    bundle["reply"] = reply
    bundle.setdefault("model_outputs", {})["terminal_reply"] = reply
    session["last_debug"] = debug_payload

    session["last_files"] = result_files
    append_turn(
        session, user_query=user_text, mode="graph_query",
        intent_summary=plan.intent_summary, entity_result=entity_result,
        tool_summary=build_tool_summary_for_mode("graph_query", graph_plan=graph_plan.model_dump()),
        result_payload=graph_result, assistant_reply=reply, bundle_id=bundle_id,
    )
    print(f"[TIMING][TOTAL] {time.perf_counter() - t_total_start:.2f}s")
    return _emit_query_complete(
        send_event, reply, debug_payload, bundle_id,
        artifacts=_artifacts_for(bundle), files=result_files or None,
    )


BUNDLE_SEQ_KEY = "bundle_seq"


def _next_bundle_id(session) -> int:
    """Allocate a monotonic bundle id for this session.

    These ids used to be ``len(results_history) + 1``. That silently collides
    whenever an append does not survive (concurrent writers reading a stale
    snapshot of the JSON column, a trim, a failed save): two different searches
    get the same id, and a later "what were those results?" resolves to whichever
    bundle answers to that id — which may be a different question entirely.

    A counter that only ever moves forward cannot collide, and it survives a lost
    append because it is stored separately from the history it indexes.
    """
    history = session.get("results_history") or []
    highest_seen = max((b.get("id") or 0) for b in history) if history else 0
    try:
        stored = int(session.get(BUNDLE_SEQ_KEY) or 0)
    except (TypeError, ValueError):
        stored = 0
    nxt = max(stored, highest_seen, len(history)) + 1
    session[BUNDLE_SEQ_KEY] = nxt
    return nxt


def _handle_pipeline_agent_turn(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    log_dir: str,
    send_event: SendEvent,
    artifact_store: ArtifactStore,
) -> dict[str, Any] | None:
    """If a pipeline_agent session is active, advance it. Returns the
    orchestrator payload, or None if the agent requested passthrough
    (caller should run normal parser).

    This gate runs before the normal parser path so pipeline_agent always
    wins for in-progress NFCORE flows.
    """
    if not pipeline_agent.is_active(session):
        return None
    result = pipeline_agent.handle_turn(session, config, user_text, log_dir=log_dir)
    action = result.get("action")
    if action == "passthrough":
        pipeline_agent.clear(session)
        return None
    if action == "cancel":
        reply = result.get("reply") or ""
        debug_payload = {"pipeline_agent": {"cancelled": True}}
        session["last_debug"] = debug_payload
        append_turn(
            session,
            user_query=user_text,
            mode="pipeline_agent",
            intent_summary="pipeline_agent cancelled",
            assistant_reply=reply,
        )
        return _emit_query_complete(send_event, reply, debug_payload, None)
    # All non-passthrough/cancel actions (ask, build, submit, etc.) get the
    # same chat-log treatment: record the turn with the agent's reply and a
    # snapshot of its state for the debug panel.
    reply = result.get("reply") or ""
    snapshot = pipeline_agent.snapshot_for_chat_log(session)
    debug_payload = {"pipeline_agent": snapshot}
    session["last_debug"] = debug_payload
    append_turn(
        session,
        user_query=user_text,
        mode="pipeline_agent",
        intent_summary="pipeline_agent turn",
        tool_summary={"pipeline_key": snapshot.get("pipeline_key"), "cohorts": snapshot.get("cohort_count")},
        assistant_reply=reply,
        wizard_state=snapshot,
    )
    return _emit_query_complete(send_event, reply, debug_payload, None)


def run_query(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    send_event: SendEvent | None = None,
    *,
    credentials: dict[str, str] | None = None,
    graph_scope: Any = _UNSET,
) -> dict[str, Any]:
    """
    Shared query orchestrator for Streamlit, CLI, and async/SSE consumers.
    Runs the full agent pipeline, updates session state, and emits optional progress events.

    credentials — optional dict with keys 'api_user' and 'api_pass'.  When BOTH are
    present a shallow copy of config is made so the shared singleton is never mutated;
    all LLM clients, catalogs, and prompts remain shared by reference.  Anything less
    than a complete pair is an unresolved identity: see _identity_gate, which warns and
    (by default) refuses the turn rather than running it as the service account.

    graph_scope — the caller's project scope for graph queries, as plain data
    ({"is_admin", "project_ids"}) or a GraphScope; see _identity_gate. Left out, the
    config's own scope stands (single-operator surfaces).
    """
    config, identity_refusal = _identity_gate(
        session, config, credentials, send_event, entry_point="run_query", graph_scope=graph_scope,
    )
    if identity_refusal is not None:
        return identity_refusal

    log_dir = _ensure_query_log_dir(session, config)
    artifact_store = ArtifactStore(log_dir)
    current_agent = "catalog"

    def _note_agent(agent: str) -> None:
        """Follow the agent through a turn that runs in another function.

        The error handlers at the bottom report current_agent; _execute_graph_turn is a
        separate function, so without this the whole graph turn -- the chatter included
        -- is reported as the graph agent.
        """
        nonlocal current_agent
        current_agent = agent

    _t_total_start = time.perf_counter()
    session["last_files"] = []

    _raw_send_event = send_event

    def send_event(event_name: str, payload: dict) -> None:
        if _raw_send_event:
            _raw_send_event(event_name, payload)

    try:
        # An in-progress samplesheet build always advances through the
        # pipeline_agent before the normal parser path.
        pipeline_payload = _handle_pipeline_agent_turn(
            session, config, user_text, log_dir, send_event, artifact_store,
        )
        if pipeline_payload is not None:
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return pipeline_payload

        send_event("agent_started", {"agent": "catalog", "mode": ""})
        sampletypes_short, assays_short, shortlist_diag = shortlist_catalog(
            user_text,
            config.MIN_SAMPLETYPES or [],
            config.MIN_ASSAYS or [],
            k_st=50,
            k_a=75,
            sampletype_index=getattr(config, "SAMPLETYPE_INDEX", None),
            assay_index=getattr(config, "ASSAY_INDEX", None),
            ratio=getattr(config, "SEMANTIC_RATIO", 0.7),
            min_k=getattr(config, "SEMANTIC_MIN_K", 10),
            max_k=getattr(config, "SEMANTIC_MAX_K", 80),
        )
        if not sampletypes_short:
            sampletypes_short = config.MIN_SAMPLETYPES or []
        if not assays_short:
            assays_short = config.MIN_ASSAYS or []
        send_event("agent_complete", {"agent": "catalog", "summary": None})

        current_agent = "entity"
        send_event("agent_started", {"agent": "entity", "mode": ""})
        _t0 = time.perf_counter()
        entity_result = entity_agent(config, user_text, sampletypes_short, assays_short)
        print(f"[TIMING][ENTITY] {time.perf_counter() - _t0:.2f}s")
        send_event("agent_complete", {"agent": "entity", "summary": entity_result.model_dump()})

        current_agent = "parser"
        send_event("agent_started", {"agent": "parser", "mode": ""})
        _t0 = time.perf_counter()
        plan = parser_agent(session, config, user_text, entity_result)
        print(f"[TIMING][PARSER] {time.perf_counter() - _t0:.2f}s")
        plan = ParserPlan.model_validate(fix_sample_endpoint(plan.model_dump()))
        plan = _clamp_lab_codes_to_entity(plan, entity_result)
        mode = plan.mode
        send_event(
            "agent_complete",
            {"agent": "parser", "summary": {"mode": mode, "endpoint": plan.target_endpoint}},
        )

        debug_payload: dict[str, Any] = {
            "entity_result": entity_result.model_dump(),
            "parser_plan": plan.model_dump(),
            "shortlist_sampletype_codes": shortlist_diag.get("sampletype_codes", []),
            "shortlist_assay_codes": shortlist_diag.get("assay_codes", []),
            "shortlist_diagnostics": shortlist_diag,
            "api_plan": None,
            "reporter_plan": None,
            "reporter_result": None,
            "report_writer_output": None,
            "reporter_metadata": None,
            "api_result_meta": None,
            "api_result_slim": None,
            "api_result_full": None,
            "raw_json_path": None,
            "error_context": None,
            **variant_record(config),  # prompt_variant + prompt_variant_files; parser_plan.mode is the route
        }

        if mode == "unsupported":
            notes = plan.notes or "No additional notes."
            # "We could not run this" and "this request is not supported" are different
            # answers and only one of them is worth retrying. Do not report an
            # infrastructure fault as a limitation of the user's question.
            if (plan.metadata or {}).get("failure"):
                reply = (
                    "Something went wrong on our side while planning that query, "
                    "so I haven't run it.\n\n"
                    f"{notes}"
                )
            else:
                reply = (
                    "I can't turn that request into a valid NExtSEEK operation yet.\n\n"
                    f"Reason from parser: {notes}"
                )
            session["last_debug"] = debug_payload
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                assistant_reply=reply,
            )
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(send_event, reply, debug_payload, None)

        if mode == "ask_about_last_results":
            current_agent = "memory"
            send_event("agent_started", {"agent": "memory", "mode": mode})
            history = session.get("results_history", [])
            if not history:
                reply = (
                    "You asked a follow-up question about previous data, but there are no stored results "
                    "in this session yet. Please run a search first."
                )
                session["last_debug"] = debug_payload
                send_event("agent_complete", {"agent": "memory", "summary": None})
                print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                return _emit_query_complete(send_event, reply, debug_payload, None)

            target_id = plan.target_result_id
            if target_id is None:
                # Parser punted on bundle selection — usually because the
                # relevant bundle is outside the recent_results_summary window.
                # Score every bundle in results_history by keyword overlap with
                # the user's current message instead of silently grabbing the
                # latest. (Fixes the case where "how many NDMA mice did the
                # first search return?" defaulted to history[-1].)
                bundle = resolve_bundle_for_recall(session, user_text)
                if bundle is None:
                    reply = (
                        "You asked a follow-up question about previous data, but there are no stored "
                        "results in this session yet. Please run a search first."
                    )
                    session["last_debug"] = debug_payload
                    send_event("agent_complete", {"agent": "memory", "summary": None})
                    print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                    return _emit_query_complete(send_event, reply, debug_payload, None)
                print(
                    f"[DEBUG][MEMORY] parser set target_result_id=None; "
                    f"resolved by keyword overlap → bundle id={bundle.get('id')}, "
                    f"query={bundle.get('user_query')!r}"
                )
            else:
                bundle = next((b for b in history if b.get("id") == target_id), None)
                if bundle is None:
                    reply = (
                        f"You referred to previous results with id={target_id}, but I couldn't find that "
                        "in this session. Please run a new search."
                    )
                    session["last_debug"] = debug_payload
                    send_event("agent_complete", {"agent": "memory", "summary": None})
                    print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                    return _emit_query_complete(send_event, reply, debug_payload, None)

            _t0 = time.perf_counter()
            # The follow-up agent first. This branch used to end here: it read one
            # stored bundle and answered from it, with no path back to the graph, so a
            # question the stored result could not answer was answered from it anyway
            # (wesselr 440 was told "No other data types are available" about a result
            # that could not have held them). The agent can look at what the bundle
            # holds and run a new query seeded with its UIDs. When the profile has no
            # tool-capable model, or the agent produces nothing, the old path still
            # runs: worse, but never worse than before.
            followup_outcome = _run_followup_agent(
                config, session=session, user_text=user_text, bundle=bundle, log_dir=log_dir,
            )
            # Written whatever happened: on turn 1147 the loop ran six times, queried the
            # graph three times and produced no reply, and because this block sat inside
            # `if answer:` the turn's debug carried no `followup` key at all, which is why
            # the failure read as "the memory agent is wrong" for a day.
            if followup_outcome is not None:
                debug_payload["followup"] = {
                    "tool_calls": followup_outcome.get("tool_calls"),
                    "queries": [
                        {"question": q.get("question"), "seeded": q.get("seeded"),
                         "count": (q.get("result") or {}).get("count"),
                         "uids_applied": (q.get("result") or {}).get("uids_applied")}
                        for q in followup_outcome.get("queries") or []
                    ],
                    "caveats": followup_outcome.get("caveats"),
                    "exhausted": bool(followup_outcome.get("exhausted")),
                    "unsupported": bool(followup_outcome.get("unsupported")),
                }
            # `if reply:` could not tell an exhausted loop from a profile with no tool
            # surface, so a lineage question was answered from a five-column bundle which
            # then reported the absence of what the loop had already found.
            answer, may_use_stored = resolve_followup_outcome(followup_outcome)
            if may_use_stored:
                answer = memory_agent_answer(config, user_text, bundle, log_dir=log_dir)
            elif not answer:
                answer = ("I could not finish this follow-up. Ask it as a fresh question and "
                          "I will run it properly.")
            print(f"[TIMING][MEMORY] {time.perf_counter() - _t0:.2f}s")
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary={"target_bundle": bundle.get("id")},
                assistant_reply=answer,
                bundle_id=bundle.get("id"),
            )
            debug_payload["api_plan"] = bundle.get("api_plan")
            api_full = load_api_result_full(bundle)
            # NOT debug_payload["api_result_full"]: last_debug is the other JSON
            # column session_adapter.save() writes in the same UPDATE, so putting
            # the payload here doubled the row again. raw_json_path + the slim
            # copy + api_result_meta are what the panel actually renders.
            debug_payload["raw_json_path"] = bundle.get("raw_result_path") or bundle.get("graph_debug_path")
            debug_payload["api_result_meta"] = {
                "ok": api_full.get("ok") if isinstance(api_full, dict) else None,
                "status_code": api_full.get("status_code") if isinstance(api_full, dict) else None,
                "url": api_full.get("url") if isinstance(api_full, dict) else None,
                "bundle_id": bundle.get("id"),
                "source_mode": bundle.get("mode"),
            }
            debug_payload["api_result_slim"] = bundle.get("api_result_slim")
            debug_payload["memory_coder_artifact"] = bundle.get("memory_coder_artifact")
            session["last_debug"] = debug_payload
            send_event("agent_complete", {"agent": "memory", "summary": None})
            session["last_files"] = bundle.get("files") or []
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(
                send_event,
                answer,
                debug_payload,
                bundle.get("id"),
                files=(bundle.get("files") or None),
            )

        if mode == "reporter":
            current_agent = "reporter"
            send_event("agent_started", {"agent": "reporter", "mode": mode})
            _t0 = time.perf_counter()
            reporter_plan = reporter_agent(config, user_text, plan)
            print(f"[TIMING][REPORTER] {time.perf_counter() - _t0:.2f}s")
            debug_payload["reporter_plan"] = reporter_plan.model_dump()
            reporter_mode = reporter_plan.reporter_mode or plan.report_mode or "summary"
            if reporter_mode == "summary_sql":  # legacy alias
                reporter_mode = "summary"
            print("[DEBUG][REPORTER] Mode selected:", reporter_mode, "Report type:", reporter_plan.report_type)
            send_event("agent_complete", {"agent": "reporter", "summary": {"reporter_mode": reporter_mode}})

            reporter_result = None
            report_writer_output = None
            saved_files: dict[str, str] = {}
            per_sample_reports = bool((reporter_plan.reporter_context or {}).get("per_sample_reports", False))

            if reporter_mode == "report_generation":
                report_type_for_branch = (reporter_plan.report_type or plan.report_type or "").upper()
                if report_type_for_branch.startswith("NFCORE"):
                    pa_start = pipeline_agent.start(
                        session,
                        config,
                        user_query=user_text,
                        parser_plan=plan,
                        reporter_plan=reporter_plan,
                        log_dir=log_dir,
                    )
                    reply = pa_start.get("reply") or ""
                    snapshot = pipeline_agent.snapshot_for_chat_log(session)
                    debug_payload["pipeline_agent"] = snapshot
                    session["last_debug"] = debug_payload
                    append_turn(
                        session,
                        user_query=user_text,
                        mode="pipeline_agent",
                        intent_summary="pipeline_agent launched",
                        entity_result=entity_result,
                        tool_summary={"pipeline_key": snapshot.get("pipeline_key"), "cohorts": snapshot.get("cohort_count")},
                        assistant_reply=reply,
                        wizard_state=snapshot,
                    )
                    print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                    return _emit_query_complete(send_event, reply, debug_payload, None)

                current_agent = "report_writer"
                send_event("agent_started", {"agent": "report_writer", "mode": reporter_mode})
                uids = reporter_plan.uids or []
                try:
                    parser_uids = plan.filters.uids if hasattr(plan, "filters") else []
                except Exception:
                    parser_uids = []
                if parser_uids:
                    existing = set(uids)
                    uids.extend([u for u in parser_uids if u not in existing])

                print("[DEBUG][REPORTER] Report generation UIDs:", uids)
                reporter_result, report_writer_output, saved_files, reply = generate_report_outputs(
                    config=config,
                    user_query=user_text,
                    parser_plan=plan,
                    reporter_plan=reporter_plan,
                    uids=uids,
                    log_dir=log_dir,
                    report_writer_fn=report_writer_agent,
                    report_coder_fn=report_coder_agent,
                    per_sample_reports=per_sample_reports,
                )
                send_event("agent_complete", {"agent": "report_writer", "summary": None})
            else:
                summary_mode = reporter_plan.summary_mode or "samples"
                project = reporter_plan.project
                if isinstance(project, str) and not project.strip():
                    project = None

                current_agent = "search"
                send_event("search_started", {"source": "reporter", "project": project, "summary_mode": summary_mode})
                # "an annual progress report for the Kamm project" resolves Kamm as a
                # LAB, so reporter_plan.project stays null and the report would run
                # across every project while describing itself as Kamm's. Hand the
                # resolved lab codes down so the summary can scope itself instead. An
                # empty list is the entity agent's answer (no lab record matched), and
                # run_reporter_summary does not replace it with the plan's codes.
                _lab_codes = list(getattr(entity_result, "lab_codes", None) or [])
                reporter_result, saved_files, reporter_summary = run_reporter_summary(
                    config, reporter_plan, log_dir, lab_codes=_lab_codes)
                send_event(
                    "search_complete",
                    {
                        "source": "reporter",
                        "ok": reporter_result.get("ok"),
                        "rows_returned": reporter_result.get("rows_returned"),
                        "summary_mode": summary_mode,
                    },
                )

                if not reporter_result.get("ok"):
                    reply = (
                        "The reporter agent could not run the project report.\n\n"
                        f"Error: {reporter_result.get('error', 'unknown error')}"
                    )
                    debug_payload["reporter_result"] = reporter_result
                    session["last_debug"] = debug_payload
                    print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                    return _emit_query_complete(send_event, reply, debug_payload, None)

                current_agent = "chatter"
                send_event("agent_started", {"agent": "chatter", "mode": "reporter"})
                try:
                    narrative = chatter_agent_answer(
                        config,
                        user_text,
                        entity_result.model_dump(),
                        plan.model_dump(),
                        reporter_summary=reporter_summary,
                        log_dir=log_dir,
                        session=session,
                    )
                except Exception as e:
                    narrative = (
                        "Project report completed.\n"
                        f"(Summary generation failed: {repr(e)})"
                    )
                send_event("agent_complete", {"agent": "chatter", "summary": None})

                reply_lines = [
                    narrative.strip(),
                    "",
                    *reporter_reply_footer(
                        config, reporter_result, saved_files, summary_mode
                    ),
                ]
                # Surface a clear hint when the connected DB has no data for the
                # requested project (e.g. local dev DB aliased to MYSQL_HOST_PROD).
                diag = reporter_result.get("db_diagnostic") or {}
                if isinstance(diag, dict) and diag.get("likely_missing_data"):
                    if not diag.get("project_exists_in_db"):
                        reply_lines.append(
                            f"- **Note:** project id `{reporter_result.get('project_id')}` "
                            "doesn't exist in the connected database. The reporter may be "
                            "pointed at a local/dev DB without prod data loaded — check "
                            "`MYSQL_HOST_PROD` in `nextseek.env`."
                        )
                    else:
                        reply_lines.append(
                            f"- **Note:** the connected database has zero rows for "
                            f"project id `{reporter_result.get('project_id')}` across "
                            "all time. Likely a local/dev DB without prod data loaded — "
                            "check `MYSQL_HOST_PROD` in `nextseek.env`."
                        )
                reply_lines.extend([
                    "",
                    "_Detailed tables and downloads are available in the **Reporter result** panel._",
                ])
                reply = "\n".join(reply_lines)

            debug_payload["reporter_result"] = reporter_result
            debug_payload["report_writer_output"] = (
                report_writer_output.model_dump()
                if isinstance(report_writer_output, ReportWriterOutput)
                else report_writer_output
            )
            debug_payload["reporter_metadata"] = None
            if isinstance(reporter_result, dict):
                if "metadata" in reporter_result:
                    debug_payload["reporter_metadata"] = reporter_result.get("metadata")
                elif isinstance(reporter_result.get("reports"), list) and reporter_result["reports"]:
                    debug_payload["reporter_metadata"] = {
                        entry.get("uid") or f"item_{idx}": entry.get("metadata")
                        for idx, entry in enumerate(reporter_result["reports"])
                    }
                if saved_files:
                    debug_payload["report_saved_files"] = saved_files

            history = session.get("results_history", [])
            bundle_id = _next_bundle_id(session)
            result_files = build_saved_report_file_manifest(saved_files)
            report_writer_output_payload = (
                report_writer_output.model_dump()
                if isinstance(report_writer_output, ReportWriterOutput)
                else report_writer_output
            )
            bundle = build_metadata_bundle(
                bundle_id=bundle_id,
                mode=mode,
                user_query=user_text,
                parser_plan=plan.model_dump(),
                reporter_plan=reporter_plan.model_dump(),
                reporter_result=reporter_result,
                report_writer_output=report_writer_output_payload,
                report_saved_files=saved_files,
                terminal_reply=reply,
                files=result_files,
            )
            history.append(bundle)
            session["results_history"] = history
            session["last_debug"] = debug_payload

            artifacts: list[dict[str, Any]] | None = None
            try:
                from nextseek_api.assistant.excel_export import build_artifacts

                artifacts = build_artifacts(bundle)
            except Exception:
                artifacts = None

            session["last_files"] = result_files
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary=build_tool_summary_for_mode("reporter", reporter_plan=reporter_plan.model_dump()),
                assistant_reply=reply,
                bundle_id=bundle_id,
            )
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(
                send_event,
                reply,
                debug_payload,
                bundle_id,
                artifacts=artifacts,
                files=result_files or None,
            )

        if mode == "system_question":
            current_agent = "system"
            send_event("agent_started", {"agent": "system", "mode": mode})
            _t0 = time.perf_counter()
            sys_output = system_agent(config, user_text, entity_result, plan)
            print(f"[TIMING][SYSTEM] {time.perf_counter() - _t0:.2f}s")
            print(f"[DEBUG][SYSTEM] mode={sys_output.mode}")
            print(f"[DEBUG][SYSTEM] entities_consulted={sys_output.entities_consulted}")
            print(f"[DEBUG][SYSTEM] notes={sys_output.notes!r}")
            print(f"[DEBUG][SYSTEM] narrative:\n{sys_output.narrative}")
            send_event("agent_complete", {"agent": "system", "summary": {"mode": sys_output.mode}})
            debug_payload["system_mode"] = sys_output.mode
            debug_payload["debug_info"] = {
                "entity": {
                    "sampletypes": entity_result.model_dump().get("sampletypes", []),
                    "assays": entity_result.model_dump().get("assays", []),
                },
                "parser": {
                    "mode": plan.mode,
                    "intent_summary": plan.intent_summary,
                },
                "system": {
                    "mode": sys_output.mode,
                    "entities_consulted": sys_output.entities_consulted,
                    "notes": sys_output.notes,
                },
            }
            reply = sys_output.narrative
            session["last_debug"] = debug_payload
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary={"system_mode": sys_output.mode},
                assistant_reply=reply,
            )
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(send_event, reply, debug_payload, None)

        # A graph query refused for its project scope is answered by graph_search through
        # the REST branch below; these notes go to its chatter and set the reply's footer.
        scope_notes: list[str] = []
        if mode == "graph_query":
            current_agent = "graph"
            outcome = _execute_graph_turn(
                config=config, session=session, user_text=user_text,
                entity_result=entity_result, plan=plan, log_dir=log_dir,
                artifact_store=artifact_store, send_event=send_event,
                debug_payload=debug_payload, t_total_start=_t_total_start,
                note_agent=_note_agent,
            )
            if not isinstance(outcome, GraphScopeFallback):
                return outcome
            plan, mode, scope_notes = _fall_back_to_graph_search(plan)

        if mode in ("new_search", "refine_last_search"):
            # Graph-origin refines re-run the graph path (with prior Cypher as context);
            # everything below this is REST refine prep.
            if mode == "refine_last_search":
                # The stored result the parser named in target_result_id, else the newest.
                from .chat_memory import select_refine_bundle

                _history = session.get("results_history", []) or []
                _prior, debug_payload["refine_target"] = select_refine_bundle(_history, plan.target_result_id)
                # F13: the engine used to come from the PREVIOUS bundle's mode alone, so a
                # REST search could never be refined into the graph however clearly the new
                # turn needed it. The parser now marks a refine it would have routed to the
                # graph as a fresh question, and that mark counts as well as the prior mode.
                _graph_refine = bool(_prior) and (
                    _prior.get("mode") == "graph_query"
                    or getattr(plan, "refine_engine", None) == "graph"
                )
                if _graph_refine:
                    current_agent = "graph"
                    outcome = _execute_graph_turn(
                        config=config, session=session, user_text=user_text,
                        entity_result=entity_result, plan=plan,
                        log_dir=log_dir, artifact_store=artifact_store, send_event=send_event,
                        debug_payload=debug_payload, t_total_start=_t_total_start,
                        refine_context=_build_graph_refine_context(_prior),
                        note_agent=_note_agent,
                    )
                    if not isinstance(outcome, GraphScopeFallback):
                        return outcome
                    plan, mode, scope_notes = _fall_back_to_graph_search(plan)
            if mode == "refine_last_search":
                plan_data = plan.model_dump()
                history = session.get("results_history", [])
                if history:
                    last_bundle = _prior  # chosen above: mode is still a refine only if that block ran
                    prev_plan = last_bundle.get("parser_plan", {}) or {}
                    previous_api_plan = last_bundle.get("api_plan")
                    previous_user_query = last_bundle.get("user_query")
                    previous_search_context = last_bundle.get("search_context", {}) or {}

                    if not plan_data.get("target_endpoint"):
                        plan_data["target_endpoint"] = (
                            prev_plan.get("target_endpoint")
                            if isinstance(prev_plan, dict)
                            else None
                        ) or (
                            previous_search_context.get("endpoint")
                            if isinstance(previous_search_context, dict)
                            else None
                        )
                    if not plan_data.get("resolved"):
                        plan_data["resolved"] = prev_plan.get("resolved", {}) if isinstance(prev_plan, dict) else {}
                    if not plan_data.get("filters"):
                        plan_data["filters"] = prev_plan.get("filters", {}) if isinstance(prev_plan, dict) else {}

                    filters = plan_data.get("filters", {}) or {}
                    prev_filters = prev_plan.get("filters", {}) or {}
                    if not prev_filters and isinstance(previous_search_context, dict):
                        prev_filters = previous_search_context.get("filters", {}) or {}
                    for key in ("sampletype_code", "assay_codes", "keywords", "uids"):
                        val = filters.get(key) if isinstance(filters, dict) else None
                        if val in (None, [], "") and isinstance(prev_filters, dict):
                            if key not in filters and isinstance(filters, dict):
                                filters = dict(filters)
                            filters[key] = prev_filters.get(key)
                    plan_data["filters"] = filters

                    match = re.search(r"project id\s*=*\s*(\d+)", user_text, re.IGNORECASE)
                    if match:
                        kw = filters.get("keywords") if isinstance(filters, dict) else []
                        if not isinstance(kw, list):
                            kw = []
                        kw.append(f"project id {match.group(1)}")
                        filters["keywords"] = kw
                        plan_data["filters"] = filters
                    if previous_api_plan:
                        plan_data["previous_api_plan"] = previous_api_plan
                    if previous_user_query:
                        plan_data["previous_user_query"] = previous_user_query

                plan = ParserPlan.model_validate(plan_data)
                debug_payload["parser_plan"] = plan.model_dump()

            endpoint = plan.target_endpoint
            if not endpoint:
                reply = (
                    "The parser recognized this as a database search, but did not specify an endpoint.\n\n"
                    f"Notes: {plan.notes or 'No additional notes.'}"
                )
                session["last_debug"] = debug_payload
                print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                return _emit_query_complete(send_event, reply, debug_payload, None)

            from .agents import api_agent_build_request

            current_agent = "api"
            send_event("agent_started", {"agent": "api", "mode": mode})
            _t0 = time.perf_counter()
            api_plan = api_agent_build_request(config, plan)
            print(f"[TIMING][API_AGENT] {time.perf_counter() - _t0:.2f}s")
            debug_payload["api_plan"] = api_plan.model_dump()
            send_event(
                "agent_complete",
                {"agent": "api", "summary": {"endpoint": api_plan.endpoint, "method": api_plan.method}},
            )

            if not api_plan.endpoint:
                reply = "The API Agent could not construct a valid request for this query."
                session["last_debug"] = debug_payload
                print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
                return _emit_query_complete(send_event, reply, debug_payload, None)

            current_agent = "search"
            send_event("search_started", {"source": "api", "endpoint": api_plan.endpoint, "method": api_plan.method})
            api_result_full = tool_nextseek_api_request(
                config=config,
                endpoint=api_plan.endpoint,
                method=api_plan.method,
                requestBody=api_plan.requestBody or {},
                queryParameters=api_plan.queryParameters or {},
            )
            api_plan_dict, api_result_full = _retry_advanced_search_if_empty(
                config, plan.model_dump(), api_plan.model_dump(), api_result_full
            )
            api_plan = APIRequestPlan.model_validate(api_plan_dict)
            debug_payload["api_plan"] = api_plan.model_dump()
            send_event(
                "search_complete",
                {
                    "source": "api",
                    "ok": api_result_full.get("ok"),
                    "status_code": api_result_full.get("status_code"),
                },
            )

            # api_plan_dict, not api_plan: APIRequestPlan has extra="ignore", so
            # re-validating drops retry_substituted_search recorded by the retry ladder.
            api_result_slim = slim_api_result_for_llm(api_result_full, api_plan=api_plan_dict)
            history = session.get("results_history", [])
            bundle_id = _next_bundle_id(session)
            raw_json_path = None
            try:
                entry = artifact_store.write_json(
                    key="api_result",
                    label="Full API result JSON",
                    filename=f"api_result_bundle_{bundle_id}.json",
                    payload=api_result_full,
                    kind="api",
                    bundle_id=bundle_id,
                )
                if entry:
                    raw_json_path = entry["path"]
            except Exception as e:
                print("[DEBUG][API_LOG] Failed to write raw API result file:", repr(e))

            # api_plan_dict for the same reason slim_api_result_for_llm takes it:
            # APIRequestPlan has extra="ignore", so the re-validated object drops
            # queryParameters/retry_substituted_search that the disclosure reads.
            debug_payload["api_result_meta"] = build_api_result_meta(
                api_result_full, api_plan_dict, bundle_id=bundle_id)
            debug_payload["api_result_slim"] = api_result_slim
            debug_payload["raw_json_path"] = raw_json_path
            debug_payload["error_context"] = None
            if not api_result_full.get("ok"):
                schema = config.get_schema_for_endpoint(api_plan.endpoint or "")
                req_schema = None
                if isinstance(schema, dict):
                    req_schema = (schema.get("request_schemas") or {}).get(api_plan.method)
                debug_payload["error_context"] = {
                    "ok": api_result_full.get("ok"),
                    "status_code": api_result_full.get("status_code"),
                    "error": api_result_full.get("error"),
                    "url": api_result_full.get("url"),
                    "method": api_result_full.get("method"),
                    "request_body": api_plan.requestBody,
                    "request_query": api_plan.queryParameters,
                    "response_preview": api_result_full.get("data"),
                    "schema_required_paths": _extract_required_paths(req_schema) if req_schema else [],
                }

            result_files: list[dict[str, Any]] = []
            entry = artifact_store.register_path(
                key="api_result",
                label="Full API result JSON",
                path=raw_json_path,
                kind="api",
                bundle_id=bundle_id,
            )
            if entry:
                result_files.append(entry)
            search_context = {
                "endpoint": api_plan.endpoint,
                "method": api_plan.method,
                "request_body": api_plan.requestBody or {},
                "query_params": api_plan.queryParameters or {},
                "filters": plan.model_dump().get("filters"),
            }
            memory_payload = {
                "data": (api_result_full.get("data") if isinstance(api_result_full, dict) else None),
                "api_plan": api_plan.model_dump(),
                "endpoint": api_plan.endpoint,
                "tool": mode,
            }
            bundle = build_metadata_bundle(
                bundle_id=bundle_id,
                mode=mode,
                user_query=user_text,
                parser_plan=plan.model_dump(),
                api_plan=api_plan.model_dump(),
                api_result_full=api_result_full,
                api_result_slim=api_result_slim,
                memory_payload=memory_payload,
                search_context=search_context,
                files=result_files,
                paths={"raw_result_path": raw_json_path},
            )
            history.append(bundle)
            session["results_history"] = history

            log_api_call(
                session,
                user_query=user_text,
                parser_plan=plan.model_dump(),
                api_plan=api_plan.model_dump(),
                api_result_full=api_result_full,
                bundle_id=bundle_id,
            )

            current_agent = "chatter"
            send_event("agent_started", {"agent": "chatter", "mode": "search"})
            _t0 = time.perf_counter()
            answer = chatter_agent_answer(
                config,
                user_text,
                entity_result.model_dump(),
                plan.model_dump(),
                api_plan.model_dump(),
                api_result_slim,
                api_result_full,
                debug_payload["error_context"],
                log_dir=log_dir,
                session=session,
                query_notes=scope_notes or None,
            )
            if scope_notes:
                answer = f"{answer}\n\n{SCOPE_FALLBACK_FOOTER}"
            print(f"[TIMING][CHATTER] {time.perf_counter() - _t0:.2f}s")
            send_event("agent_complete", {"agent": "chatter", "summary": None})
            bundle["terminal_reply"] = answer
            bundle["reply"] = answer
            bundle.setdefault("model_outputs", {})["terminal_reply"] = answer
            session["last_debug"] = debug_payload

            session["last_files"] = result_files
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary=build_tool_summary_for_mode(
                    mode,
                    api_plan=api_plan.model_dump(),
                    parser_plan=plan.model_dump(),
                ),
                result_payload=api_result_full,
                assistant_reply=answer,
                bundle_id=bundle_id,
            )
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            return _emit_query_complete(
                send_event, answer, debug_payload, bundle_id,
                artifacts=_artifacts_for(bundle), files=result_files or None,
            )

        reply = (
            f"The parser returned an unexpected mode={mode!r}. "
            "I don't yet know how to handle this case."
        )
        session["last_debug"] = debug_payload
        print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
        return _emit_query_complete(send_event, reply, debug_payload, None)

    except LLMFatalError as fatal:
        agent = getattr(fatal, "agent", None) or current_agent
        msg = str(fatal)
        print(f"[FATAL][{(agent or 'unknown').upper()}] Run killed: {msg}")
        if send_event:
            send_event("query_error", {"error": msg, "agent": agent, "fatal": True})
        reply = f"**The request could not be completed.**\n\n{msg}"
        # Persist a chat_log turn so subsequent parser/chatter turns know this
        # query was attempted and failed (otherwise the next turn sees a "hole"
        # in conversational history). Mark mode='error_<agent>' for grep-ability.
        try:
            append_turn(
                session,
                user_query=user_text,
                mode=f"error_{agent or 'unknown'}",
                intent_summary=f"Fatal LLM error in {agent or 'unknown'} agent.",
                tool_summary={"fatal": True, "agent": agent, "error": msg[:240]},
                assistant_reply=reply,
                status="error",
                error=msg,
            )
        except Exception as log_err:  # pragma: no cover
            print(f"[FATAL] failed to log chat_log turn: {log_err!r}")
        return _emit_query_complete(send_event, reply, {"fatal_error": msg, "agent": agent}, None)

    except Exception as exc:
        if send_event:
            send_event("query_error", {"error": str(exc), "agent": current_agent})
        raise


def handle_query(session: SessionState | SessionStateProxy, config: ChatConfig, user_text: str) -> str:
    """Convenience wrapper that runs the standard pipeline and returns only the reply text."""
    return run_query(session, config, user_text)["reply"]


def run_query_plan(
    session: SessionState | SessionStateProxy,
    config: ChatConfig,
    user_text: str,
    send_event: SendEvent | None = None,
    *,
    credentials: dict[str, str] | None = None,
    graph_scope: Any = _UNSET,
) -> dict[str, Any]:
    """
    Planner-based orchestrator: entity -> parser -> planner -> executor -> chatter -> evaluator.
    Parallel structure to `run_query`, using the same result contract.

    credentials, graph_scope — same shallow-copy and identity-gate semantics as run_query.
    """
    config, identity_refusal = _identity_gate(
        session, config, credentials, send_event, entry_point="run_query_plan", graph_scope=graph_scope,
    )
    if identity_refusal is not None:
        return identity_refusal

    log_dir = _ensure_query_log_dir(session, config)
    artifact_store = ArtifactStore(log_dir)
    _t_total_start = time.perf_counter()
    session["last_files"] = []

    _raw_send_event = send_event

    def send_event(event_name: str, payload: dict) -> None:
        print(f"[DEBUG][EVENT][PLAN] {event_name}: {list(payload.keys())}")
        if _raw_send_event:
            _raw_send_event(event_name, payload)

    try:
        send_event("agent_started", {"agent": "catalog", "mode": "plan"})
        sampletypes_short, assays_short, shortlist_diag = shortlist_catalog(
            user_text,
            config.MIN_SAMPLETYPES or [],
            config.MIN_ASSAYS or [],
            k_st=50,
            k_a=75,
            sampletype_index=getattr(config, "SAMPLETYPE_INDEX", None),
            assay_index=getattr(config, "ASSAY_INDEX", None),
            ratio=getattr(config, "SEMANTIC_RATIO", 0.7),
            min_k=getattr(config, "SEMANTIC_MIN_K", 10),
            max_k=getattr(config, "SEMANTIC_MAX_K", 80),
        )
        sampletypes_short = sampletypes_short or config.MIN_SAMPLETYPES or []
        assays_short = assays_short or config.MIN_ASSAYS or []
        send_event("agent_complete", {"agent": "catalog", "summary": None})

        send_event("agent_started", {"agent": "entity", "mode": "plan"})
        _t0 = time.perf_counter()
        entity_result = entity_agent(config, user_text, sampletypes_short, assays_short)
        print(f"[TIMING][ENTITY] {time.perf_counter() - _t0:.2f}s")
        send_event("agent_complete", {"agent": "entity", "summary": entity_result.model_dump()})

        send_event("agent_started", {"agent": "parser", "mode": "plan"})
        _t0 = time.perf_counter()
        multi_parser_plan = multi_parser_agent(session, config, user_text, entity_result)
        multi_parser_plan = _clamp_lab_codes_to_entity(multi_parser_plan, entity_result)
        print(f"[TIMING][MULTI_PARSER] {time.perf_counter() - _t0:.2f}s")
        send_event(
            "agent_complete",
            {"agent": "parser", "summary": {"candidates": len(multi_parser_plan.candidates), "intent": multi_parser_plan.intent_summary}},
        )
        print(f"\n[MULTI_PARSER]\n{json.dumps(multi_parser_plan.model_dump(), indent=2)}\n")

        debug_payload: dict[str, Any] = {
            "mode": "plan",
            "parser": multi_parser_plan.model_dump(),
            "planner": None,
            "planner_iterations": [],
            "entity": entity_result.model_dump(),
            "shortlist_sampletype_codes": shortlist_diag.get("sampletype_codes", []),
            "shortlist_assay_codes": shortlist_diag.get("assay_codes", []),
            "shortlist_diagnostics": shortlist_diag,
            "provisional_reply": None,
            "evaluator": None,
            "replan_attempted": False,
            "replan_reason": None,
            "termination_reason": None,
            "step_budget": {"max_steps": 5, "used_steps": 0},
            **variant_record(config),  # prompt_variant + prompt_variant_files
        }

        def _step_summary(sr: dict) -> dict:
            base = {k: v for k, v in sr.items() if k != "output"}
            base["count"] = (sr.get("output") or {}).get("count")
            # Surface api_plan for search steps so test criteria can inspect requestBody
            if sr.get("tool") in {"new_search", "refine_last_search"}:
                api_plan = (sr.get("output") or {}).get("api_plan")
                if api_plan:
                    base["api_plan"] = api_plan
            return base

        max_plan_steps = 5
        executed_steps = []
        step_results: dict[int, dict] = {}
        step_summary: dict[Any, dict] = {}
        enriched_context = {}
        intersection_uids = None
        seen_step_signatures: set[str] = set()
        planner_notes: list[str] = []
        stop_reason: str | None = None
        termination_reason: str | None = None

        for iteration in range(1, max_plan_steps + 1):
            send_event("agent_started", {"agent": "planner", "mode": "plan"})
            _t0 = time.perf_counter()
            planner_decision = planner_agent(
                session,
                config,
                user_text,
                entity_result,
                parser_plan=multi_parser_plan,
                prior_steps=executed_steps,
                step_results=step_results,
                max_steps=max_plan_steps,
            )
            print(f"[TIMING][PLANNER] {time.perf_counter() - _t0:.2f}s")
            debug_payload["planner_iterations"].append(planner_decision.model_dump())
            send_event(
                "agent_complete",
                {
                    "agent": "planner",
                    "summary": {
                        "iteration": iteration,
                        "action": planner_decision.action,
                        "intent": planner_decision.intent_summary,
                        "tool": planner_decision.step.tool if planner_decision.step else None,
                        "termination_reason": planner_decision.termination_reason,
                    },
                },
            )
            print(f"\n[PLANNER_DECISION {iteration}]\n{json.dumps(planner_decision.model_dump(), indent=2)}\n")
            if planner_decision.notes:
                planner_notes.append(planner_decision.notes)

            if planner_decision.action == "halt" or planner_decision.step is None:
                termination_reason = planner_decision.termination_reason or "answered"
                stop_reason = planner_decision.rationale or planner_decision.notes or termination_reason
                break

            next_step = planner_decision.step
            signature = _step_signature(next_step)
            if signature in seen_step_signatures:
                termination_reason = "repeated_strategy"
                stop_reason = "Planner proposed a step equivalent to one already executed."
                debug_payload["repeated_step"] = next_step.model_dump()
                break
            seen_step_signatures.add(signature)

            tool_output, debug_fragment_local, enriched_context, intersection_uids, step_stop_reason = _execute_single_plan_step(
                config,
                session,
                next_step,
                entity_result,
                log_dir,
                send_event,
                parser_plan=multi_parser_plan,
                step_results=step_results,
                enriched_context=enriched_context,
                intersection_uids=intersection_uids,
            )
            step_results[next_step.step_id] = tool_output
            executed_steps.append(next_step)
            for key, value in debug_fragment_local.items():
                if isinstance(value, dict):
                    debug_payload.setdefault(key, {}).update(value)
                else:
                    debug_payload[key] = value
            step_summary[next_step.step_id] = _step_summary(tool_output)
            debug_payload["step_budget"]["used_steps"] = len(executed_steps)

            if step_stop_reason:
                stop_reason = step_stop_reason
                if "missing required inputs" in step_stop_reason.lower():
                    termination_reason = "missing_required_inputs"
                else:
                    termination_reason = "hard_step_failure"
                break
        else:
            termination_reason = "step_budget_exhausted"
            stop_reason = "Planner reached the maximum step budget before halting."

        plan = PlannerOutput(
            intent_summary=multi_parser_plan.intent_summary or user_text,
            steps=executed_steps,
            notes=" | ".join(note for note in planner_notes if note),
        )
        _materialize_intersection_result(executed_steps, step_results, debug_payload, intersection_uids)
        if "intersection" in step_results:
            step_summary["intersection"] = _step_summary(step_results["intersection"])

        debug_payload["planner"] = plan.model_dump()
        debug_payload["step_results"] = step_summary
        debug_payload["termination_reason"] = termination_reason

        send_event("agent_started", {"agent": "chatter", "mode": "plan"})
        _t0 = time.perf_counter()
        terminal_reply = None
        if plan.steps:
            last_step = plan.steps[-1]
            last_output = (step_results.get(last_step.step_id) or {}).get("output") or {}
            if (
                last_step.tool in {"system_question", "ask_about_last_results", "memory_lookup", "unsupported", "report_generation"}
                and isinstance(last_output.get("reply"), str)
                and last_output.get("reply", "").strip()
            ):
                terminal_reply = last_output["reply"].strip()
        provisional_reply = terminal_reply or chatter_agent_plan(config, user_text, plan, step_results, log_dir, session=session)
        print(f"[TIMING][PLAN_CHATTER] {time.perf_counter() - _t0:.2f}s")
        print(f"[DEBUG][PLAN_CHATTER] Reply:\n{provisional_reply}")
        send_event("agent_complete", {"agent": "chatter", "summary": None})
        debug_payload["provisional_reply"] = provisional_reply

        send_event("agent_started", {"agent": "evaluator", "mode": "plan"})
        _t0 = time.perf_counter()
        evaluator_output = plan_evaluator_agent(
            config,
            user_text,
            entity_result,
            multi_parser_plan,
            plan,
            step_results,
            provisional_reply,
            stop_reason=stop_reason,
            log_dir=log_dir,
        )
        print(f"[TIMING][EVALUATOR] {time.perf_counter() - _t0:.2f}s")
        send_event(
            "agent_complete",
            {"agent": "evaluator", "summary": {"status": evaluator_output.overall_status, "answered": evaluator_output.answered_query}},
        )
        debug_payload["evaluator"] = evaluator_output.model_dump()

        evaluator_lines = [
            f"Status: {evaluator_output.overall_status}",
            f"Answered Query: {'yes' if evaluator_output.answered_query else 'no'}",
            f"Execution Consistent: {'yes' if evaluator_output.execution_consistent else 'no'}",
            "Steps Followed: " + " -> ".join(step.get("tool", "?") for step in (plan.model_dump().get("steps") or [])),
            f"Termination Reason: {termination_reason or 'answered'}",
        ]
        if evaluator_output.zero_results_assessment != "not_applicable":
            evaluator_lines.append(f"Zero Results Assessment: {evaluator_output.zero_results_assessment}")
        if evaluator_output.user_safe_summary:
            evaluator_lines.append(f"Summary: {evaluator_output.user_safe_summary}")
        elif evaluator_output.reason:
            evaluator_lines.append(f"Summary: {evaluator_output.reason}")

        reply = (
            "**Evaluator**\n\n"
            + "\n".join(f"- {line}" for line in evaluator_lines)
            + "\n\n"
            + provisional_reply
        )
        if stop_reason and termination_reason not in {None, "answered"} and not evaluator_output.user_safe_summary:
            reply = f"Plan halted: {stop_reason}\n\n{reply}"

        history = session.get("results_history", [])
        bundle_id = _next_bundle_id(session)
        result_files: list[dict[str, Any]] = []
        raw_result_paths: dict[int, str] = {}
        graph_debug_paths: dict[int, str] = {}
        plan_debug_path: str | None = None

        try:
            full_debug = {**debug_payload, "step_results_full": step_results}
            entry = artifact_store.write_json(
                key="plan_debug",
                label="Plan debug JSON",
                filename=f"plan_debug_{bundle_id}.json",
                payload=full_debug,
                kind="plan",
                bundle_id=bundle_id,
            )
            if entry:
                plan_debug_path = entry["path"]
                print(f"[PLAN] Debug written to {entry['path']}")
                result_files.append(entry)
        except Exception as e:
            print(f"[PLAN] Failed to write plan debug: {e!r}")

        for step_id, sr in step_results.items():
            if sr.get("tool") in {"new_search", "refine_last_search", "coding_filter"} and sr.get("ok") and sr.get("output"):
                try:
                    entry = artifact_store.write_json(
                        key=f"api_result_step_{step_id}",
                        label=f"Step result (step {step_id})",
                        filename=f"api_result_bundle_{bundle_id}_step_{step_id}.json",
                        payload=sr["output"],
                        kind="api",
                        bundle_id=bundle_id,
                        step_id=step_id,
                    )
                    if entry:
                        raw_result_paths[step_id] = entry["path"]
                        result_files.append(entry)
                except Exception as e:
                    print(f"[PLAN] Failed to write step {step_id} API result: {e!r}")
            if sr.get("tool") == "graph_query" and sr.get("output"):
                try:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_step_{step_id}"
                    graph_debug_path = _write_graph_debug(
                        log_dir,
                        ts,
                        {
                            "graph_plan": sr["output"].get("graph_plan"),
                            "result": {"data": sr["output"].get("data"), "count": sr["output"].get("count")},
                            "error": sr.get("error"),
                        },
                    )
                    if graph_debug_path:
                        graph_debug_paths[step_id] = graph_debug_path
                    entry = artifact_store.register_path(
                        key=f"graph_debug_step_{step_id}",
                        label=f"Graph debug (step {step_id})",
                        path=graph_debug_path,
                        kind="graph",
                        bundle_id=bundle_id,
                        step_id=step_id,
                    )
                    if entry:
                        result_files.append(entry)
                except Exception as e:
                    print(f"[PLAN] Failed to write step {step_id} graph debug: {e!r}")
            if sr.get("tool") in {"reporter", "report_generation"} and sr.get("ok") and sr.get("output"):
                try:
                    result_files.extend(
                        build_saved_report_file_manifest(
                            (sr.get("output") or {}).get("saved_files"),
                            key_prefix=f"step_{step_id}_",
                        )
                    )
                except Exception as e:
                    print(f"[PLAN] Failed to collect step {step_id} report files: {e!r}")

        canonical_api_plan = None
        canonical_api_result = None
        canonical_graph_plan = None
        canonical_graph_result = None
        canonical_reporter_plan = None
        canonical_reporter_result = None
        canonical_report_saved_files = None
        canonical_memory_payload = None
        canonical_search_context = None
        for sr in step_results.values():
            if not isinstance(sr, dict) or not sr.get("ok"):
                continue
            output = sr.get("output") or {}
            if not isinstance(output, dict):
                continue
            tool = sr.get("tool")
            if tool in {"new_search", "refine_last_search"} and canonical_api_plan is None:
                canonical_api_plan = output.get("api_plan")
                rows = output.get("data") if isinstance(output.get("data"), list) else []
                canonical_api_result = {
                    "ok": sr.get("ok"),
                    "data": {"rows": rows, "total": output.get("count", len(rows))},
                    "error": sr.get("error"),
                }
                canonical_memory_payload = {
                    "data": {"rows": rows, "total": output.get("count", len(rows))},
                    "api_plan": canonical_api_plan,
                    "endpoint": output.get("endpoint"),
                    "tool": tool,
                }
                canonical_search_context = {
                    "endpoint": output.get("endpoint"),
                    "method": (canonical_api_plan or {}).get("method") if isinstance(canonical_api_plan, dict) else None,
                    "request_body": (canonical_api_plan or {}).get("requestBody") if isinstance(canonical_api_plan, dict) else {},
                    "query_params": (canonical_api_plan or {}).get("queryParameters") if isinstance(canonical_api_plan, dict) else {},
                }
            elif tool == "coding_filter":
                rows = output.get("data") if isinstance(output.get("data"), list) else []
                canonical_memory_payload = {
                    "data": {"rows": rows, "total": output.get("count", len(rows))},
                    "source_output": output,
                    "tool": tool,
                }
            elif tool == "graph_query" and canonical_graph_plan is None:
                canonical_graph_plan = output.get("graph_plan")
                canonical_graph_result = {
                    "ok": sr.get("ok"),
                    "data": output.get("data") or [],
                    "count": output.get("count", 0),
                    "error": sr.get("error"),
                }
                if canonical_memory_payload is None:
                    canonical_memory_payload = canonical_graph_result
            elif tool in {"reporter", "report_generation"}:
                canonical_reporter_plan = canonical_reporter_plan or output.get("reporter_plan")
                canonical_reporter_result = canonical_reporter_result or output.get("reporter_result")
                canonical_report_saved_files = canonical_report_saved_files or output.get("saved_files")
                if canonical_memory_payload is None:
                    canonical_memory_payload = output

        bundle = build_metadata_bundle(
            bundle_id=bundle_id,
            mode="plan",
            user_query=user_text,
            parser_plan=None,
            api_plan=canonical_api_plan,
            api_result_full=canonical_api_result,
            graph_plan=canonical_graph_plan,
            graph_result=canonical_graph_result,
            reporter_plan=canonical_reporter_plan,
            reporter_result=canonical_reporter_result,
            report_saved_files=canonical_report_saved_files,
            planner_output=plan.model_dump(),
            multi_parser_plan=multi_parser_plan.model_dump(),
            step_results=step_results,
            terminal_reply=reply,
            provisional_reply=provisional_reply,
            memory_payload=canonical_memory_payload,
            search_context=canonical_search_context,
            files=result_files,
            paths={
                "raw_result_path": next(iter(raw_result_paths.values()), None),
                "graph_debug_path": next(iter(graph_debug_paths.values()), None),
                "plan_debug_path": plan_debug_path,
                "raw_result_paths": raw_result_paths,
                "graph_debug_paths": graph_debug_paths,
            },
        )
        history.append(bundle)
        session["results_history"] = history
        session["last_debug"] = debug_payload
        session["last_files"] = result_files

        last_step_tool = plan.steps[-1].tool if plan.steps else None
        plan_tool_summary: dict[str, Any] = {
            "steps": [s.tool for s in plan.steps],
            "termination_reason": termination_reason,
        }
        plan_result_payload: dict | None = None
        if canonical_api_result is not None:
            plan_result_payload = canonical_api_result
        elif canonical_graph_result is not None:
            plan_result_payload = canonical_graph_result
        append_turn(
            session,
            user_query=user_text,
            mode=f"plan:{last_step_tool}" if last_step_tool else "plan",
            intent_summary=multi_parser_plan.intent_summary,
            entity_result=entity_result,
            tool_summary=plan_tool_summary,
            result_payload=plan_result_payload,
            assistant_reply=reply,
            bundle_id=bundle_id,
        )

        print(f"[TIMING][TOTAL][PLAN] {time.perf_counter() - _t_total_start:.2f}s")
        return _emit_query_complete(
            send_event, reply, debug_payload, bundle_id,
            artifacts=_artifacts_for(bundle), files=result_files or None,
        )

    except LLMFatalError as fatal:
        agent = getattr(fatal, "agent", None) or "unknown"
        msg = str(fatal)
        print(f"[FATAL][PLAN][{agent.upper()}] Run killed: {msg}")
        if send_event:
            send_event("query_error", {"error": msg, "agent": agent, "fatal": True})
        reply = f"**The planner pipeline was stopped.**\n\n{msg}"
        try:
            append_turn(
                session,
                user_query=user_text,
                mode=f"error_plan_{agent}",
                intent_summary=f"Fatal LLM error in planner pipeline (agent={agent}).",
                tool_summary={"fatal": True, "agent": agent, "error": msg[:240]},
                assistant_reply=reply,
                status="error",
                error=msg,
            )
        except Exception as log_err:
            print(f"[FATAL][PLAN] failed to log chat_log turn: {log_err!r}")
        return _emit_query_complete(send_event, reply, {"fatal_error": msg, "agent": agent}, None)

    except Exception as e:
        import traceback

        print(f"[ERROR][PLAN] run_query_plan unhandled exception: {e!r}")
        traceback.print_exc()
        reply = f"An unexpected error occurred in the planner pipeline: {e}"
        try:
            append_turn(session, user_query=user_text, mode="error_plan_pipeline",
                        assistant_reply=reply, status="error", error=repr(e))
        except Exception:
            print("[FATAL] failed to log chat_log turn for plan pipeline error")
        return _emit_query_complete(send_event, reply, {"error": repr(e)}, None)


def handle_query_plan(session: SessionState | SessionStateProxy, config: ChatConfig, user_text: str) -> str:
    """Thin wrapper that runs the planner pipeline and returns only the reply text."""
    return run_query_plan(session, config, user_text)["reply"]
