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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

if TYPE_CHECKING:
    from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from .artifacts import (
    ArtifactStore,
    build_metadata_bundle,
    build_saved_report_file_manifest,
    load_api_result_full,
)
from .chat_memory import (
    CHAT_LOG_KEY,
    append_turn,
    build_tool_summary_for_mode,
    next_turn_id,
    resolve_bundle_for_recall,
)
from .pipeline import agent as pipeline_agent
from .agents.followup import (
    _stored_rows,
    describe_stored_result,
    preview_rows,
    resolve_followup_outcome,
    run_followup,
    stored_query_rebuildable,
)
from .agents.followup_compute import compute_over_rows
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
from .graph_review import (FOLLOWUP_SEEDED_SKIP, FOLLOWUP_TIER1_SKIP, GraphReview, ReviewInput, as_debug,
                           check_binding, check_premise, review_compute, review_tier1, with_checks)
from .graph_review_counts import SKIP_AFTER_MS, live_values, run_tier2
from .graph_scope import RESERVED_PREFIX, SCOPE_ATTR, GraphScope
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
from .helpers.lab_code import clamp_lab_codes, lab_near_miss_notes
from .helpers.suggestions import accept, pending_for, suggestions_from_review
from .helpers.tools.neo4j import is_scope_refusal
from .helpers.uid_check import check_uids, uid_notes, uids_in
from .schemas import APIRequestPlan, EntityAgentOutput, ParserPlan, PlannerOutput, ReportWriterOutput
from .session import SessionState
from .tee import Tee
from .uid_links import link_sample_uids

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


#: The refine context of a follow-up query rebuilt from the stored query (``seed_mode``
#: "stored_query"). The stored Cypher follows it on the next line.
STORED_QUERY_REFINE_LEAD = "Start from this earlier query and add the new condition; keep every filter it has:\n"


def _stored_query_refine(stored_query: dict) -> str:
    """The graph agent's refine context for a set rebuilt from the query that produced it.

    The stored parameters come with the Cypher, because a filter written as ``$type`` is
    no filter without its value. So does one sentence on LIMIT: a capped result is capped
    because its query hit a LIMIT, and a count that kept it would count the capped rows
    again, which is the partial answer this path exists to replace.
    """
    text = STORED_QUERY_REFINE_LEAD + stored_query["cypher"]
    parameters = stored_query.get("parameters") or {}
    if parameters:
        text += "\nIts parameters, which the new query needs too: " + json.dumps(
            parameters, default=str, sort_keys=True)
    return text + ("\nIts LIMIT, if it has one, capped only the rows that were kept, not the set: "
                   "a count or a breakdown must not keep it.")


def _count_text(value: Any) -> str | None:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else None


def _followup_scope_note(seed_mode: str, *, uids_available: int, uids_applied: int | None,
                         total: Any, partial: bool, scoped: bool) -> str | None:
    """What the loop's model must know about how a query was scoped; None when nothing.

    Silent for a complete seed that was bound (the set is exactly the earlier one) and for
    a fresh question (``scoped`` false: not about the earlier result). A scoped question
    with nothing to scope by (no UIDs, and no query to rebuild from) is not silent: it ran
    over every matching sample, and read as "those" that number is wrong.
    """
    of_total = _count_text(total)
    if seed_mode == "none":
        if not scoped:
            return None
        return ("This query could not be scoped to the earlier result, because that result kept "
                "no sample UIDs and has no query this one can be rebuilt from. It covers every "
                "matching sample, not only the earlier ones: say so in caveats, and do not "
                "present it as a number about those records.")
    if seed_mode == "stored_query":
        held = ("the stored copy kept no sample UIDs" if not uids_available else
                f"the stored copy holds only {uids_available:,} of its {of_total} records" if of_total else
                f"the stored copy holds only {uids_available:,} of its records")
        return ("This query was rebuilt from the earlier query's filters, because " + held + ", so no "
                "UIDs were bound and it covers the whole earlier set, not the stored rows. If it did "
                "not keep every one of those filters, it is a different set: say so.")
    if seed_mode == "uids":
        if not uids_applied:
            return ("This query did not filter on $uids, so it is not scoped to the previous result. "
                    "Say so, or run it again scoped.")
        if partial:
            part = (f"{uids_available:,} of the {of_total} records" if of_total else
                    "only part of the records")
            return (f"This query is scoped to the {uids_available:,} UIDs the stored copy holds, which "
                    f"are {part} in the earlier result, so it answers for those {uids_available:,}, not "
                    "the whole set. Say so in caveats.")
    return None


def _run_followup_agent(config, *, session, user_text: str, bundle: dict, log_dir) -> dict | None:
    """Run the follow-up tool loop, and never let it be the reason a turn fails.

    Its ``run_new_query`` seam re-uses the graph agent the ordinary graph turn uses, so
    a follow-up runs the same engine as a fresh question; the difference is only that
    it is scoped to the previous result. Returns None on any failure, which sends the
    caller to the pre-existing stored-result path.

    How a query is scoped is its ``seed_mode``, on every payload:

    * ``"uids"``: the stored copy holds every UID of the result, or there is no query to
      rebuild from (a REST result, or a follow-up's own ``$uids`` query:
      ``stored_query_rebuildable``); every UID it holds is bound as ``$uids``.
    * ``"stored_query"``: the stored copy is capped (fewer UIDs than the result's total,
      or cut at its LIMIT) or kept no UIDs, and the result came from a graph query. The
      graph agent starts from that query's Cypher and parameters; no ``$uids`` is bound.
    * ``"none"``: nothing to scope by (a fresh question, or a result with neither UIDs nor
      a query to rebuild from). ``scoped``, which ``run_followup`` passes explicitly, tells
      the two apart: only a scoped one gets a ``scope_note``.

    ``scope_note`` says what the mode means for the answer when it needs saying
    (``_followup_scope_note``). Every query still runs through ``tool_neo4j_query``, with
    its write check and scope prover.

    Every query that ran and returned rows is also kept, in full, on the outcome as
    ``graph_runs`` (plan, result): the turn attaches the last one's rows as a file the way
    a graph turn does. They stay out of the conversation, which sees ``preview_rows``.

    Each query runs through the graph turn's own retries (``_run_graph_with_retries``: a
    Cypher error, a zero-row result) and its payload carries ``review``
    (``_review_followup_query``), read against one catalog provider for the whole loop turn.
    A zero-row retry that found something adds ``retry_note``, the graph turn's note for a
    number found by a changed filter.

    Its ``compute`` seam (``_compute``) runs ``compute_over_rows`` over the stored rows or over
    every row of the loop's last query that returned rows, adds the payload's ``source`` and
    ``review`` (``review_compute``), keeps the call and its payload as an artifact, and records
    each call on the outcome as ``compute_runs``. A computation makes no bundle of its own.
    """
    graph_runs: list[dict] = []
    compute_runs: list[dict] = []
    extent: dict[str, Any] = {}
    provider: dict[str, Any] = {}
    newest_bundle_id = _newest_bundle_id(session)

    def _catalog():
        """The loop turn's one catalog provider (``live_values``, default cold budget), built on first use, so
        its cap on uncached value reads covers every query of the turn. A failure to build it is kept and
        raised to each review, which records it."""
        if "value" not in provider:
            try:
                provider["value"] = live_values(config)
            except Exception as exc:
                provider["value"] = exc
        if isinstance(provider["value"], Exception):
            raise provider["value"]
        return provider["value"]

    def _stored_extent() -> tuple[Any, bool]:
        """The previous result's total and whether its stored copy was capped, read once; its size as a
        set (``_stored_set_size``) is kept beside them as ``extent["set_size"]``."""
        if not extent:
            try:
                described = describe_stored_result(bundle)
            except Exception as exc:  # unknown extent: treat the seed as complete, as before
                print(f"[DEBUG][FOLLOWUP] could not describe the stored result: {exc!r}")
                described = {}
            extent.update(total=described.get("total"), capped=bool(described.get("capped")),
                          set_size=_stored_set_size(described), described=described)
        return extent["total"], extent["capped"]

    def _run_query(*, question: str, seed_uids: list[str], stored_query: dict | None = None,
                   scoped: bool = False) -> dict:
        seed_uids = list(seed_uids or [])
        total, capped = _stored_extent() if (seed_uids or stored_query) else (None, False)
        of_total = _count_text(total)
        partial = bool(seed_uids) and (capped or (of_total is not None and len(seed_uids) < total))
        rebuild = (stored_query if (partial or not seed_uids) and stored_query_rebuildable(stored_query)
                   else None)
        refine = None
        if rebuild is not None:
            seed_mode = "stored_query"
            refine = _stored_query_refine(rebuild)
        elif seed_uids:
            seed_mode = "uids"
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
        else:
            seed_mode = "none"
        entity = EntityAgentOutput()
        parser_plan = ParserPlan(mode="graph_query", intent_summary=question)
        graph_plan = graph_agent(config, question, entity, parser_plan, refine_context=refine)
        if not graph_plan.cypher:
            return {"ok": False, "error": "no query could be generated for that question",
                    "seed_mode": seed_mode}
        # Only a "uids" seed is bound: a rebuilt query holds the whole set through the
        # stored filters, and binding the capped UIDs would cut it back to them. It is bound
        # into every attempt that names $uids, every one of them, not the ten shown.
        seed = {"uids": list(seed_uids)} if seed_mode == "uids" else None
        # The graph turn's safeguards (loop gap L4): one more try on a Cypher error, one on a
        # result that matched nothing, and the first result stands when a retry is no better.
        run = _run_graph_with_retries(config, question, entity, parser_plan, refine, seed,
                                      graph_plan=graph_plan)
        graph_plan, result = run.graph_plan, run.graph_result
        parameters = dict(run.parameters or {})
        applied = len(seed_uids) if seed is not None and "$uids" in (graph_plan.cypher or "") else None
        rows = result.get("data") or []
        scope_note = _followup_scope_note(seed_mode, uids_available=len(seed_uids), uids_applied=applied,
                                          total=total, partial=partial, scoped=scoped)
        if result.get("ok") and rows:
            # How the query was scoped travels with its rows, so a computation over them says the same thing:
            # a query bound to the UIDs of a capped copy covers only part of the earlier set.
            graph_runs.append({"graph_plan": graph_plan, "parameters": parameters,
                               "result": result, "uids_applied": applied, "question": question,
                               "seed_mode": seed_mode, "scope_note": scope_note,
                               "part_of_set": seed_mode == "uids" and partial and applied is not None})
        # The head of the rows, bounded: this goes back into a conversation that is
        # re-sent in full on every later iteration of the loop. It used to be counts and
        # three examples harvested from uid/id/name columns only, so a breakdown row such
        # as {"type": "TIS", "n": 25936} reached the model as "count: 23" and nothing it
        # could name (production acceptance run 2026-09-22, task 0006a373).
        shown = preview_rows(rows)
        # The reviewer reads the loop's question (the one the statement answers) for Tier 1,
        # and the user's own words against the stored result for premise and binding.
        _stored_extent()
        review = _review_followup_query(
            _catalog, question=question, user_text=user_text, graph_plan=graph_plan, graph_result=result,
            elapsed_ms=run.elapsed_ms, stored_total=extent["set_size"], seeded=applied is not None,
            target_bundle_id=bundle.get("id"), newest_bundle_id=newest_bundle_id,
        )
        payload = {
            "ok": bool(result.get("ok")),
            "count": result.get("total") if result.get("total") is not None else result.get("count"),
            "rows_returned": len(rows),
            "rows_shown": len(shown),
            "rows": shown,
            "truncated": bool(result.get("truncated")),
            "examples": _followup_examples(rows),
            "error": result.get("error"),
            "uids_available": len(seed_uids),
            # None when the query did not filter on $uids: it then covers whatever it
            # matched, which is not the same set, and the answer has to say so. Also None
            # on a rebuilt query, which binds no UIDs by design; scope_note says which.
            "uids_applied": applied,
            "seed_mode": seed_mode,
            "scope_note": scope_note,
            "review": as_debug(review),
        }
        if run.changed_answer:
            payload["retry_note"] = RETRY_CHANGED_ANSWER_NOTE
        return payload

    def _compute_artifact(payload: dict, review: GraphReview | None, *, where, group_by, code):
        """The call, its payload and its whole review on disk, like the memory coder's artifact. None on failure."""
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            bundle_id = bundle.get("id")
            return ArtifactStore(log_dir or config.LOG_DIR).write_json(
                key=f"followup_compute_{ts}", label="Follow-up computation",
                filename=f"followup_compute_bundle_{bundle_id}_{ts}.json",
                payload={"bundle_id": bundle_id, "question": user_text, "source": payload.get("source"),
                         "where": where, "group_by": group_by, "code": code, "payload": payload,
                         "review": as_debug(review) if review is not None else None},
                kind="memory", bundle_id=bundle_id,
            )
        except Exception as exc:  # never lose a computation to the file that records it
            print(f"[DEBUG][FOLLOWUP] could not write the computation's artifact: {exc!r}")
            return None

    def _keep_compute(source, where, group_by, code, payload: dict, review: GraphReview | None) -> dict:
        artifact = (_compute_artifact(payload, review, where=where, group_by=group_by, code=code)
                    if review is not None else None)
        compute_runs.append({
            "source": source, "where": where, "group_by": group_by,
            "code": code[:2000] if isinstance(code, str) else code,
            "ok": payload.get("ok"), "count": payload.get("count"), "error": payload.get("error"),
            "review_verdict": review.verdict if review is not None else None, "artifact": artifact,
        })
        return payload

    def _compute(*, source: str = "stored", where=None, group_by=None, code=None) -> dict:
        """``compute_over_rows`` over the stored rows (``source`` "stored") or every row of the loop's last query
        that returned rows ("last_query"), with the payload's ``source`` and ``review`` added."""
        if source not in ("stored", "last_query"):
            return _keep_compute(source, where, group_by, code, {
                "ok": False, "error": f"unknown source {source!r}: use 'stored' or 'last_query'"}, None)
        _stored_extent()
        described = extent.get("described") or {}
        if source == "last_query":
            if not graph_runs:
                return _keep_compute(source, where, group_by, code, {
                    "ok": False, "error": ("no query has run on this turn yet; run run_new_query first, "
                                           "or use source 'stored'")}, None)
            run = graph_runs[-1]
            result = run["result"]
            rows = list(result.get("data") or [])
            total = result.get("total") if result.get("total") is not None else result.get("count")
            # Every row of that query is in hand: the computation runs over all of them. It covers the whole earlier
            # set only when the query did; the query's own scope_note says what it covers, and comes with it.
            all_rows = (not result.get("truncated") and isinstance(total, int) and not isinstance(total, bool)
                        and total <= len(rows))
            origin = {"kind": "last_query", "question": run.get("question"), "seed_mode": run.get("seed_mode"),
                      "rows_in": len(rows), "total": total,
                      "complete": all_rows and not run.get("part_of_set")}
            payload = compute_over_rows(rows=rows, total=total, complete=all_rows, where=where,
                                        group_by=group_by, code=code)
            if run.get("scope_note"):
                payload["scope_note"] = " ".join(n for n in (run["scope_note"], payload.get("scope_note")) if n)
        else:
            rows = _stored_rows(bundle)
            total = described.get("total")
            complete = bool(rows) and not described.get("capped")
            origin = {"kind": "stored", "bundle_id": bundle.get("id"), "rows_in": len(rows), "total": total,
                      "complete": complete}
            if described.get("aggregate_values"):
                # One row holding what an aggregate computed: counting it would count that row.
                payload = {"ok": False, "needs_query": True, "columns": sorted(described["aggregate_values"]),
                           "error": ("The earlier result is an aggregate: read_stored_result's aggregate_values "
                                     "holds what it computed. Answer from that, or use run_new_query for anything "
                                     "about individual samples.")}
            else:
                payload = compute_over_rows(rows=rows, total=total, complete=complete, where=where,
                                            group_by=group_by, code=code)
            if payload.get("needs_query"):
                # Task 11's flag, the one read_stored_result and run_new_query use: never claim a rebuild the seam
                # would not do. It rebuilds only a copy that is capped or kept no rows.
                rebuildable = bool(described.get("stored_query_rebuildable"))
                payload["stored_query_available"] = rebuildable
                if not complete:
                    payload["error"] += (
                        " With seed_uids true, run_new_query rebuilds the whole set from the stored query."
                        if rebuildable else
                        " No stored query can be rebuilt for this result, so a new query cannot cover the whole "
                        "earlier set: its scope_note says what it covers.")
        payload["source"] = origin
        review = review_compute(question=user_text, source_kind=source, source_total=extent.get("set_size"),
                                target_bundle_id=bundle.get("id"),
                                newest_bundle_id=newest_bundle_id if newest_bundle_id is not None else bundle.get("id"),
                                payload=payload)
        payload["review"] = {"verdict": review.verdict, "fired": [c.name for c in review.checks if c.fired],
                             "disclosure": review.disclosure}
        return _keep_compute(source, where, group_by, code, payload, review)

    try:
        outcome = run_followup(
            config, user_text=user_text, bundle=bundle, run_query=_run_query, compute=_compute, log_dir=log_dir,
        )
        if isinstance(outcome, dict):
            outcome["graph_runs"] = graph_runs
            outcome["compute_runs"] = compute_runs
        return outcome
    except Exception as exc:
        print(f"[DEBUG][FOLLOWUP] agent failed, falling back to the stored result: {exc!r}")
        return None


def _stored_set_size(described: dict) -> Any:
    """How many records the stored result is a set of, for ``check_premise``; None when its total is not that.

    A record set (it holds UIDs) has its total, and a single-number aggregate has that number
    (``describe_stored_result`` makes it the total). Any other total is no set size: a breakdown's counts its
    groups (23 downstream types of 1,641 mice), and a one-row aggregate of two numbers has a total of 1."""
    if described.get("uid_count"):
        return described.get("total")
    aggregate = described.get("aggregate_values")
    if isinstance(aggregate, Mapping):
        numbers = [v for v in aggregate.values() if isinstance(v, (int, float))]
        if len(numbers) == 1:
            return described.get("total")
    return None


def _newest_bundle_id(session) -> int | None:
    """The id of the newest stored result in ``results_history``, or None when there is none."""
    try:
        ids = [b.get("id") for b in (session.get("results_history") or []) if isinstance(b, dict)]
    except Exception:
        return None
    ids = [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]
    return max(ids) if ids else None


def _review_verdict(result: Any) -> str | None:
    """A loop query payload's review verdict, for ``debug.followup``."""
    review = result.get("review") if isinstance(result, dict) else None
    return review.get("verdict") if isinstance(review, dict) else None


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


def _graph_attempt(cypher: str | None, result: dict, reason: str, *, elapsed_ms: int) -> dict[str, Any]:
    """One generate-execute round for debug.graph_attempts: what was written, what ran, the decision, and how long
    the tool_neo4j_query call took."""
    scope = result.get("scope")
    return {
        "cypher": cypher, "ok": result.get("ok"),
        "count": result.get("count"), "error": result.get("error"),
        "reason": reason,
        "executed_cypher": result.get("cypher"),
        "scope_decision": scope.get("decision") if isinstance(scope, dict) else None,
        "elapsed_ms": elapsed_ms,
    }


def _ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


class GraphRun(NamedTuple):
    """What ``_run_graph_with_retries`` settled on."""
    graph_plan: Any       # the GraphAgentPlan whose result was kept
    graph_result: dict    # the kept result
    attempts: list        # every generate-execute round, for debug.graph_attempts
    elapsed_ms: int       # the Neo4j time of the kept result, never a discarded retry's
    parameters: Any       # what the kept statement ran with
    changed_answer: bool  # the first query matched nothing and a retry found something


def _bind_parameters(graph_plan, parameters_extra: dict | None):
    """The parameters ``graph_plan`` runs with: its own, plus each of ``parameters_extra`` that its Cypher names as
    ``$name`` (over any value the model wrote for it). Its own, untouched, when none of them applies."""
    named = {k: v for k, v in (parameters_extra or {}).items() if f"${k}" in (graph_plan.cypher or "")}
    return {**dict(graph_plan.parameters or {}), **named} if named else graph_plan.parameters


def _run_graph_with_retries(config, question: str, entity, plan, refine: str | None,
                            parameters_extra: dict | None = None, *, graph_plan) -> GraphRun:
    """Execute ``graph_plan``, read the outcome, and regenerate, up to ``GRAPH_MAX_TRIES`` statements in all.

    The one execution path of the graph turn (``_execute_graph_turn``) and of the follow-up loop's queries
    (``_run_followup_agent``), so the two retry the same way. ``graph_plan`` is the first statement, which the caller
    has already had the graph agent write; a retry asks the agent again with ``question``, ``entity``, ``plan`` and
    ``refine`` (the refine context) plus the failure. ``parameters_extra`` holds parameters the caller binds itself
    (the follow-up's ``$uids``), bound into every attempt whose Cypher names them.

    Each attempt is timed around ``tool_neo4j_query`` alone, and ``elapsed_ms`` is the kept attempt's, so the
    reviewer times the statement the caller keeps, never a retry it threw away. A scope refusal is final at once:
    another statement would be refused the same way, and the graph turn falls back to graph_search.
    """
    parameters = _bind_parameters(graph_plan, parameters_extra)
    t_query = time.perf_counter()
    graph_result = tool_neo4j_query(config, graph_plan.cypher, parameters)
    kept_ms = _ms_since(t_query)  # the Neo4j time of graph_result, the result the caller keeps

    # Generate -> execute -> read the outcome -> regenerate, up to GRAPH_MAX_TRIES.
    # This was one retry and only on a Cypher error, so a query that ran perfectly well
    # and matched nothing was final. That is case B11 (a guessed assay name returned
    # zero and the zero was reported as the answer). A zero-row result now gets exactly
    # one more go, and if the second query also finds nothing the FIRST result stands:
    # reporting a different query's number would be worse than reporting zero.
    attempts: list[dict[str, Any]] = [
        _graph_attempt(graph_plan.cypher, graph_result, "initial", elapsed_ms=kept_ms)]
    first_ok_empty = matched_nothing(graph_result)
    zero_row_retry_used = False

    for _ in range(GRAPH_MAX_TRIES - 1):
        if is_scope_refusal(graph_result):
            # Final: another model call can only write another query the prover cannot
            # prove. The graph turn falls back to graph_search.
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
            config, question, entity, plan,
            retry_context=retry_ctx, refine_context=refine,
        )
        if not graph_plan_retry.cypher:
            break
        retry_parameters = _bind_parameters(graph_plan_retry, parameters_extra)
        t_query = time.perf_counter()
        retry_result = tool_neo4j_query(config, graph_plan_retry.cypher, retry_parameters)
        retry_ms = _ms_since(t_query)
        attempts.append(_graph_attempt(graph_plan_retry.cypher, retry_result, reason, elapsed_ms=retry_ms))
        # Keep the retry only when it is an improvement. A retry that errors, or that
        # also finds nothing after a zero-row first attempt, leaves the original alone.
        if not retry_result.get("ok"):
            if graph_result.get("ok"):
                break
        elif reason == "zero_rows" and matched_nothing(retry_result):
            break
        graph_plan = graph_plan_retry
        graph_result = retry_result
        parameters = retry_parameters
        kept_ms = retry_ms

    return GraphRun(graph_plan=graph_plan, graph_result=graph_result, attempts=attempts, elapsed_ms=kept_ms,
                    parameters=parameters,
                    changed_answer=first_ok_empty and not matched_nothing(graph_result))


#: The chatter's note for a graph result the reviewer flagged (graph_review.py): the review's facts, to be stated
#: without narrating how they were found.
REVIEW_NOTE = ("What the result matched: {facts} State this plainly in the first sentences. "
               "Do not mention a review or a second query.")
#: describe_query_scope cuts a note at 400 characters, which would drop the instruction at this one's end.
REVIEW_NOTE_MAX = 399
#: The reviewer's wall clock, both tiers together.
REVIEW_BUDGET_S = 8.0
#: A turn this old runs no count variant and reads only cached catalog values.
REVIEW_LATE_TURN_S = 45
#: The Tier 1 checks that have a Tier 2 count variant (graph_review_counts._BUILDERS).
REVIEW_VARIANT_CHECKS = frozenset({"stem_miss", "all_question_narrowed", "zero_unproven_base", "unapplied_value"})


def _review_note(disclosure: str) -> str:
    """``REVIEW_NOTE`` holding the review's facts, at most ``REVIEW_NOTE_MAX`` characters.

    A disclosure can hold 299 characters (graph_review.DISCLOSURE_MAX) and the template takes 111, so a full one
    would pass the chatter's 400-character cut. Whole facts are dropped from the end until the note fits; a single
    fact too long for the room is cut short."""
    facts = " ".join(str(disclosure).split())
    room = REVIEW_NOTE_MAX - len(REVIEW_NOTE.format(facts=""))
    if len(facts) > room:
        end = facts.rfind(". ", 0, room)
        facts = facts[:end + 1] if end > 0 else facts[:room - 1].rstrip() + "…"
    return REVIEW_NOTE.format(facts=facts)


def _review_input(question: str, graph_plan, graph_result: dict, elapsed_ms: int | None) -> ReviewInput:
    """The reviewer's input for one kept result: the question the statement answers, the statement as the model
    wrote it, its parameters, the rows and counts, and ``elapsed_ms`` (the kept statement's Neo4j time).

    The server's scope parameter is left out of the parameters: every count goes back through
    ``tool_neo4j_query``, whose prover refuses a reserved name on the way in, and Tier 1 has no use for it."""
    raw = graph_result.get("parameters")
    if not isinstance(raw, Mapping):
        raw = graph_plan.parameters or {}
    parameters = {k: v for k, v in raw.items()
                  if not (isinstance(k, str) and k.lower().startswith(RESERVED_PREFIX))}
    return ReviewInput(
        question=question,
        cypher=graph_plan.cypher,
        parameters=parameters,
        keyword_fields=dict(graph_plan.keyword_fields or {}),
        rows=list(graph_result.get("data") or []),
        count=graph_result.get("count"),
        total=graph_result.get("total"),
        ok=bool(graph_result.get("ok")),
        error=graph_result.get("error"),
        elapsed_ms=elapsed_ms,
    )


def _review_followup_query(get_catalog: Callable[[], Any], *, question: str, user_text: str, graph_plan,
                           graph_result: dict, elapsed_ms: int | None, stored_total, target_bundle_id,
                           newest_bundle_id, seeded: bool = False) -> GraphReview:
    """The graph reviewer over one follow-up loop query (loop gap L4), for the tool payload's ``review``.

    Tier 1 (``review_tier1``) reads the loop's own ``question``, the one the statement answers, without
    ``premise_count`` (``FOLLOWUP_TIER1_SKIP``), and without ``unapplied_value`` when the statement is bound to
    the earlier result's UIDs (``seeded``, ``FOLLOWUP_SEEDED_SKIP``); then ``check_premise`` and
    ``check_binding`` read the user's words against the stored result the loop is about (``with_checks``
    discloses them first). ``stored_total`` is that result's size as a set (``_stored_set_size``), or None.
    ``get_catalog`` returns the loop turn's one catalog provider, or raises why it could not be built. No Tier 2:
    the loop's model can run a relaxed query itself, and a count per loop query would stack the reviewer's time
    budget.

    Never raises. Anything escaping becomes an ``ok`` review that records the error, so the loop goes on as it
    would without a reviewer."""
    t0 = time.perf_counter()
    try:
        inp = _review_input(question, graph_plan, graph_result, elapsed_ms)
        skip = {**FOLLOWUP_TIER1_SKIP, **(FOLLOWUP_SEEDED_SKIP if seeded else {})}
        review = review_tier1(inp, get_catalog(), skip=skip)
        return with_checks(review, [
            check_premise(user_text, stored_total=stored_total),
            check_binding(target_bundle_id=target_bundle_id, newest_bundle_id=newest_bundle_id, user_text=user_text),
        ])
    except Exception as exc:  # a reviewer bug must never cost the user their answer
        return GraphReview("ok", [], None, None, [], int((time.perf_counter() - t0) * 1000), error=repr(exc))


def _review_graph_turn(config, user_text: str, graph_plan, graph_result: dict, *, elapsed_ms: int | None,
                       t_turn_start: float) -> GraphReview:
    """The graph reviewer over the result the turn keeps, before the chatter writes the reply.

    Tier 1 (``review_tier1``) reads the question, the statement the model wrote with its parameters, and the rows
    and counts, against the stored values the caller can see: one ``live_values`` provider per turn, reading only
    cached values when the statement took over ``SKIP_AFTER_MS`` or the turn has already run
    ``REVIEW_LATE_TURN_S``. Tier 2 (``run_tier2``, bounded count variants) runs only when a check that has a variant
    fired and the turn is younger than ``REVIEW_LATE_TURN_S``, inside what Tier 1 left of ``REVIEW_BUDGET_S``.

    The server's scope parameter is left out of the parameters: every count goes back through
    ``tool_neo4j_query``, whose prover refuses a reserved name on the way in, and Tier 1 has no use for it.

    ``elapsed_ms`` is the Neo4j time of the statement under review, the one whose result the turn kept, never a
    retry the turn threw away: a count variant relaxes that statement, so its time decides whether one runs.

    Never raises. Anything escaping becomes an ``ok`` review that records the error, so the turn goes on as it
    would without a reviewer; a failure inside Tier 2 keeps Tier 1's verdict (``run_tier2``'s own contract).
    """
    t0 = time.perf_counter()
    try:
        inp = _review_input(user_text, graph_plan, graph_result, elapsed_ms)
        slow = isinstance(elapsed_ms, int) and elapsed_ms > SKIP_AFTER_MS
        late = t0 - t_turn_start > REVIEW_LATE_TURN_S
        catalog = live_values(config, max_cold=0) if slow or late else live_values(config)
        review = review_tier1(inp, catalog)
        fired = {check.name for check in review.checks if check.fired}
        if fired & REVIEW_VARIANT_CHECKS and time.perf_counter() - t_turn_start < REVIEW_LATE_TURN_S:
            spent = time.perf_counter() - t0
            review = run_tier2(config, inp, review, budget_s=max(0.0, REVIEW_BUDGET_S - spent))
        return review
    except Exception as exc:  # a reviewer bug must never cost the user their answer
        return GraphReview("ok", [], None, None, [], _ms_since(t0), error=repr(exc))


# --------------------------------------------------------------------------
# Suggestion chips (#128, helpers/suggestions.py)
# --------------------------------------------------------------------------
#
# A graph turn whose review suggests a next question offers it as a chip in debug.suggestions and remembers it
# for the turn it was offered on. The next NS turn asks accept() whether its text is that chip's query, before it
# is routed anywhere, and the offer is cleared either way: it lasts one turn, and any turn written to chat_log in
# between (a Container-CC turn included) cancels it. A click offers no chip of its own, so chips never chain.


def _last_turn_id(session) -> int:
    """The id of the newest turn in ``chat_log``, or 0 when there is none.

    Read the way ``chat_memory.next_turn_id`` numbers turns: the largest id, never the last entry's. Every writer
    (``append_turn`` here, the Container-CC and non-answer writers in ``NessieAI/cc``) gives a new entry
    ``next_turn_id(log)``, so right after a turn is written this is that turn's id, and anything written later,
    whoever writes it, is larger."""
    return next_turn_id(session.get(CHAT_LOG_KEY)) - 1


def _accepted_suggestion(session, user_text: str) -> dict[str, Any] | None:
    """The chip this message clicked, or None. Clears what the previous turn offered either way."""
    try:
        return accept(session, user_text, last_turn_id=_last_turn_id(session))
    except Exception as exc:  # a chip's bookkeeping must never cost the user their answer
        print(f"[DEBUG][SUGGEST] could not read the offered suggestion: {exc!r}")
        return None


def _suggestions_for(review: dict[str, Any] | None, bundle_id: int) -> list[dict[str, Any]]:
    """The chips for this turn's review (``debug_payload["graph_review"]``), or [] when there are none."""
    try:
        return suggestions_from_review(review or {}, bundle_id=bundle_id)
    except Exception as exc:  # a chip's bookkeeping must never cost the user their answer
        print(f"[DEBUG][SUGGEST] could not build the suggestion: {exc!r}")
        return []


def _remember_suggestions(session, items: list[dict[str, Any]]) -> None:
    """Keep the chips for the newest turn in ``chat_log``: called right after ``append_turn`` has written this
    graph turn, so that is the id this turn is stored under, and the one the next turn's ``_last_turn_id`` reads
    unless another turn is written in between. No chips clears the entry."""
    try:
        pending_for(session, items, turn_id=_last_turn_id(session))
    except Exception as exc:  # a chip's bookkeeping must never cost the user their answer
        print(f"[DEBUG][SUGGEST] could not remember the offered suggestion: {exc!r}")


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
    offer_suggestions: bool = True,
):
    """``note_agent`` lets the caller follow which agent this turn is on.

    ``offer_suggestions`` is False on a turn that is itself a click on a chip: it offers no chip of its own.

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
        # The guard's reason names catalog properties and repair steps: kept for the debug panel
        # and the harness (`graph_refusal`), never shown (prod retest 2026-09-23, Q3/Q4: "Reason:
        # Graph agent could not produce valid Cypher; properties ['node.year', ...] are not in the
        # catalog").
        debug_payload["graph_refusal"] = graph_plan.explanation or "no query"
        reply = GRAPH_REFUSAL_REPLY
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
    # The retry loop (a Cypher error, a zero-row result) is shared with the follow-up loop's queries.
    run = _run_graph_with_retries(config, user_text, entity_result, plan, agent_context, None,
                                  graph_plan=graph_plan)
    graph_plan, graph_result, attempts = run.graph_plan, run.graph_result, run.attempts

    debug_payload["graph_attempts"] = attempts
    debug_payload["graph_scope"] = graph_result.get("scope")
    if is_scope_refusal(graph_result):
        return _graph_scope_fallback(graph_plan, graph_result, attempts, debug_payload, send_event)
    # Nothing used to inspect a graph result that ran, so confidently wrong numbers reached the reply (98
    # "converters" of which 57 were stored as Non-converter, 2026-09-23). The reviewer reads the result the turn
    # keeps; what it found goes to the debug panel, to the session (a later turn offers its suggestion), and on a
    # note or suggest to the chatter as one note.
    review = _review_graph_turn(config, user_text, graph_plan, graph_result, elapsed_ms=run.elapsed_ms,
                                t_turn_start=t_total_start)
    debug_payload["graph_review"] = as_debug(review)
    session["_graph_review"] = debug_payload["graph_review"]
    # A number found by a changed filter may not mean what the question asked, so the
    # chatter is told the filter changed (a query note; the debug flag alone reached no
    # one). The note asks it to qualify what the result covers when that differs from the
    # question, and never to narrate the retry itself (2026-09-23 ruling, graph_retry.py).
    query_notes: list[str] = list(uid_reply_notes)
    if run.changed_answer:
        debug_payload["graph_retry_changed_answer"] = True
        query_notes.append(RETRY_CHANGED_ANSWER_NOTE)
    # A lab the question misspells resolves to no code, by design, and used to leave the
    # turn with a keyword and a confident zero ("There are no samples associated with the
    # Engleward lab", 2026-09-22). The near record is a note, so the reply can ask.
    near_miss_notes = lab_near_miss_notes(getattr(entity_result, "lab_near_misses", None))
    if near_miss_notes:
        debug_payload["lab_near_misses"] = [m.model_dump() if hasattr(m, "model_dump") else m
                                            for m in entity_result.lab_near_misses]
        query_notes.extend(near_miss_notes)
    review_disclosure = review.disclosure if review.verdict in ("note", "suggest") else None
    if review_disclosure:
        query_notes.append(_review_note(review_disclosure))

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
    rows_entry = _write_graph_rows_file(
        artifact_store, bundle_id=bundle_id, cypher=graph_plan.cypher,
        parameters=graph_plan.parameters, graph_result=graph_result,
    )
    if rows_entry:
        result_files.append(rows_entry)
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
    # The reviewer's suggestion as a chip, from this turn's review only: session["_graph_review"] outlives the turn
    # that wrote it. Built before the chatter runs, so its reply can offer the same step.
    suggestions = _suggestions_for(debug_payload.get("graph_review"), bundle_id) if offer_suggestions else []
    if suggestions:
        debug_payload["suggestions"] = suggestions

    _on("chatter")
    send_event("agent_started", {"agent": "chatter", "mode": "graph_query"})
    _t1 = time.perf_counter()
    # The chatter states the review's facts first and offers the first chip's step last, backed by code when the
    # model's reply drops either. No chip (a click on one included) means no offer.
    reply = chatter_agent_answer(
        config, user_text, entity_result.model_dump(), plan.model_dump(),
        graph_plan=graph_plan.model_dump(), graph_result=graph_result,
        log_dir=log_dir, session=session, query_notes=query_notes,
        review_disclosure=review_disclosure,
        offered_step=suggestions[0].get("label") if suggestions else None,
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
    _remember_suggestions(session, suggestions)
    print(f"[TIMING][TOTAL] {time.perf_counter() - t_total_start:.2f}s")
    return _emit_query_complete(
        send_event, reply, debug_payload, bundle_id,
        artifacts=_artifacts_for(bundle), files=result_files or None,
    )


def _write_graph_rows_file(artifact_store, *, bundle_id: int, cypher, parameters,
                           graph_result: dict | None) -> dict[str, Any] | None:
    """The "Graph query result rows" file for a bundle, or None when there are no rows.

    One writer for the graph turn and the follow-up turn that ran graph queries, so the
    two attach the same file under the same key and kind.
    """
    graph_rows = (graph_result or {}).get("data") or []
    if not graph_rows:
        return None
    try:
        return artifact_store.write_json(
            key="graph_result",
            label="Graph query result rows",
            filename=f"graph_result_bundle_{bundle_id}.json",
            payload={
                "cypher": cypher,
                "parameters": parameters,
                "count": graph_result.get("count"),
                "total": graph_result.get("total"),
                "truncated": bool(graph_result.get("truncated")),
                "rows": graph_rows,
            },
            kind="graph_result",
            bundle_id=bundle_id,
        )
    except Exception as e:  # never lose a finished answer to the file that describes it
        print("[DEBUG][GRAPH] Failed to write the graph result rows file:", repr(e))
        return None


def _followup_result_bundle(session, artifact_store, *, outcome: dict | None, user_text: str,
                            parser_plan: dict, stored_bundle: dict, reply: str) -> dict | None:
    """A graph bundle for the rows a follow-up's own queries found, or None.

    A follow-up turn used to re-emit the STORED bundle's files: on the production
    acceptance run of 2026-09-22 (task 0006a373) the downstream-types follow-up attached
    turn 1's mouse list and none of the 23 type rows its answer was about. When the loop
    ran at least one query that returned rows, the LAST such query becomes a bundle of its
    own with the same rows file a graph turn writes. Last, not largest: the loop's final
    query is the one its answer rests on, and the largest is usually the broad UID dump a
    model runs for examples, not the breakdown the question asked for. A follow-up that
    only read the stored result makes nothing, and keeps the stored bundle's files.
    """
    runs = (outcome or {}).get("graph_runs") or []
    if not runs:
        return None
    run = runs[-1]
    graph_plan, graph_result = run["graph_plan"], run["result"]
    bundle_id = _next_bundle_id(session)
    parameters = dict(run.get("parameters") or {})
    files: list[dict[str, Any]] = []
    entry = _write_graph_rows_file(
        artifact_store, bundle_id=bundle_id, cypher=graph_plan.cypher,
        parameters=parameters, graph_result=graph_result,
    )
    if entry:
        files.append(entry)
    # The bound UIDs are on disk in the file above; the session row keeps only how many,
    # because results_history is a JSON column re-written on every save.
    if isinstance(parameters.get("uids"), list):
        parameters["uids"] = f"<{len(parameters['uids'])} UIDs of bundle {stored_bundle.get('id')}>"
    plan_dump = graph_plan.model_dump() if hasattr(graph_plan, "model_dump") else dict(graph_plan)
    plan_dump["parameters"] = parameters
    bundle = build_metadata_bundle(
        bundle_id=bundle_id, mode="graph_query", user_query=user_text,
        parser_plan=parser_plan, graph_plan=plan_dump, graph_result=graph_result,
        terminal_reply=reply,
        search_context={"endpoint": "neo4j", "followup_of_bundle": stored_bundle.get("id")},
        files=files,
    )
    bundle["reply"] = reply
    history = session.get("results_history", [])
    history.append(bundle)
    session["results_history"] = history
    return bundle


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



#: What a user is told when the graph agent (or its guard) produced no query. The reason is internal and
#: goes to the debug payload's `graph_refusal`.
GRAPH_REFUSAL_REPLY = (
    "I couldn't build a search for that question. Try naming the sample type, project or attribute "
    "you mean, or ask it a different way."
)

#: What a user is told when the parser routes a turn to "unsupported". The parser's own notes
#: are routing prose ("No downstream path supports ...", "not a data query ... system_question")
#: and stay in the debug payload; printing them after "Reason from parser:" showed the user the
#: machinery (local run 2026-09-22, bucket6.export_this_session).
UNSUPPORTED_REPLY = (
    "I can't do that one from here. I can search, count and compare sample metadata, follow "
    "samples through their lineage, build reports and submission workbooks, and explain what "
    "NExtSEEK holds. If you tell me what you're after in those terms, I'll try again."
)


def unsupported_reply(plan) -> str:
    """The reply for an unsupported plan: a planning fault says so, anything else says what is possible.

    "We could not run this" and "this request is not supported" are different answers and only
    one of them is worth retrying, so an infrastructure fault is never reported as a limitation
    of the user's question. Neither shows the parser's notes, which are internal.
    """
    if (plan.metadata or {}).get("failure"):
        return ("Something went wrong on our side while planning that query, so I haven't run it. "
                "Please try again in a moment.")
    return UNSUPPORTED_REPLY


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
    # Before the turn is routed anywhere, the wizard included: whatever this turn is, the previous turn's chip
    # offer ends here.
    accepted_suggestion = _accepted_suggestion(session, user_text)

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
        if accepted_suggestion is not None:
            debug_payload["suggestion_accepted"] = {k: accepted_suggestion.get(k) for k in ("id", "source", "kind")}

        if mode == "unsupported":
            reply = unsupported_reply(plan)
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
            # (in task 440 the user was told "No other data types are available" about
            # a result that could not have held them). The agent can look at what the bundle
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
                         "uids_applied": (q.get("result") or {}).get("uids_applied"),
                         "seed_mode": (q.get("result") or {}).get("seed_mode"),
                         "review_verdict": _review_verdict(q.get("result"))}
                        for q in followup_outcome.get("queries") or []
                    ],
                    "computes": followup_outcome.get("compute_runs") or [],
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
            answer = link_sample_uids(answer)
            print(f"[TIMING][MEMORY] {time.perf_counter() - _t0:.2f}s")
            own_bundle = None
            if not may_use_stored:
                try:
                    own_bundle = _followup_result_bundle(
                        session, artifact_store, outcome=followup_outcome, user_text=user_text,
                        parser_plan=plan.model_dump(), stored_bundle=bundle, reply=answer,
                    )
                except Exception as exc:  # never lose a finished answer to its attachment
                    print(f"[DEBUG][FOLLOWUP] could not attach the follow-up's rows: {exc!r}")
                if own_bundle is not None:
                    debug_payload.setdefault("followup", {})["attached_bundle"] = own_bundle["id"]
            append_turn(
                session,
                user_query=user_text,
                mode=mode,
                intent_summary=plan.intent_summary,
                entity_result=entity_result,
                tool_summary={"target_bundle": bundle.get("id")},
                assistant_reply=answer,
                bundle_id=(own_bundle or bundle).get("id"),
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
            print(f"[TIMING][TOTAL] {time.perf_counter() - _t_total_start:.2f}s")
            if own_bundle is not None:
                session["last_files"] = own_bundle.get("files") or []
                return _emit_query_complete(
                    send_event, answer, debug_payload, own_bundle["id"],
                    artifacts=_artifacts_for(own_bundle), files=(own_bundle.get("files") or None),
                )
            session["last_files"] = bundle.get("files") or []
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
            reply = link_sample_uids(sys_output.narrative)
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
                note_agent=_note_agent, offer_suggestions=accepted_suggestion is None,
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
                        note_agent=_note_agent, offer_suggestions=accepted_suggestion is None,
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


def _plan_graph_result(step_result: dict) -> dict:
    """A planner graph step's result as the plan bundle stores it.

    ``total`` and ``truncated`` are kept as the step has them from ``tool_neo4j_query``:
    ``count`` is only the number of rows returned, so without them a step that hit its
    LIMIT was stored as 1,000 of 1,000 and a follow-up read the capped rows as the set.
    """
    output = step_result.get("output") or {}
    return {
        "ok": step_result.get("ok"),
        "data": output.get("data") or [],
        "count": output.get("count", 0),
        "total": output.get("total"),
        "truncated": bool(output.get("truncated")),
        "error": step_result.get("error"),
    }


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
                canonical_graph_result = _plan_graph_result(sr)
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
