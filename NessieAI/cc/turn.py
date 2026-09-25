"""The Container-CC chat turn: route it, then run it on the chosen engine.

``start_task`` is the body of ``CCAssistantViewSet._start_task``. It wraps the
event callback with the terminal tracker, picks the ChatConfig (with the prod
credential swap), and starts a daemon thread whose ``_run`` closure decides the
route (:mod:`NessieAI.router.policy`), records the ledger row, and then runs the
NS orchestrator, a Container-CC turn, or the out-of-scope reply. The helpers
above it persist the CC session id, the chat_log entry and transcript row of a
finished CC turn, and the cross-session memory summaries; ``cc_sweep`` reuses
``_session_metas`` and ``_persist_summary_standalone``.

Moved verbatim from ``nextseek_api/services/cc_assistant.py`` (Phase B of the
NessieAI consolidation). The ViewSet keeps every HTTP and host seam: session
resolution and its 404, the ``QueryTask`` row, ``make_db_event_callback``, the
``DictSessionAdapter``, credential resolution and the 202 response. Nothing
here imports ``nextseek_api.services``; the one host edge is
``nextseek_api.assistant.models_db`` (``ChatSession`` reads and writes, the
``CCSessionTranscript`` upsert), allowed for this module by
``NessieAI/tests/api/test_nessie_boundaries.py``.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path

from nextseek_api.assistant.models_db import CCSessionTranscript, ChatSession

from chat_nextseek.agents.parser import FORCE_MODES as PARSER_FORCE_MODES
from chat_nextseek import prompt_variants
from chat_nextseek.prompt_variants import VARIANT_NAMES as PROMPT_VARIANT_NAMES
from chat_nextseek.chat_memory import next_turn_id
from chat_nextseek.orchestrator import run_query, run_query_plan

from NessieAI.router import router as cc_router
from NessieAI.router import router_context
from NessieAI.router.policy import (
    _decide_route,
    _fallback_when_cc_unavailable,
    _record_ledger_row,
)
from NessieAI.ns.turn import _auto_title_if_unset, _select_chat_config
from NessieAI.cc import cc_engine
from NessieAI.cc import cc_config
from NessieAI.cc import cc_session
from NessieAI.cc import cc_summary
from NessieAI.cc import cc_memory
from NessieAI.cc import cc_memory_io
from NessieAI.cc import ns_digest
from NessieAI.cc import ns_turn_context
from NessieAI.cc import cc_turn_context
from NessieAI.cc import prior_turns
from NessieAI.cc.cc_turn_complete import (
    TurnCompletePayload,
    apply_turn_to_extra_state,
)
from NessieAI.cc import cc_turn_complete
from NessieAI.cc import cc_transcript_store

logger = logging.getLogger(__name__)

MAX_CC_CHAT_LOG_TURNS = 50  # match chat_nextseek/chat_memory.py MAX_TURNS


def _save_before_complete(send_event, adapter):
    """The NS turn's session is written BEFORE query_complete is sent.

    The task row turns `completed` on query_complete and the client sends the next question at once; the save used
    to run later, in `_run`'s finally, so a follow-up within a second read a session without the turn it followed
    (HeLa, 2026-09-23: 0.97 s). Saving twice is harmless: save() merges history by bundle id.
    """
    def wrapped(event, data):
        if event == "query_complete":
            try:
                adapter.save()
            except Exception:  # noqa: BLE001 - the finally save still runs
                logger.warning("pre-complete session save failed", exc_info=True)
        return send_event(event, data)
    return wrapped

# Evaluation only (the graph_search Nessie POC): the process flag that lets a
# superuser's QueryRequest.force_parser_mode reach the NS parser, and its
# QueryRequest.prompt_variant reach the NS agents. The venue sets it; no compose
# file or env template does.
EVAL_PARSER_FORCE_ENV = "NEXTSEEK_EVAL_PARSER_FORCE"


def _merge_extra_state(session, **updates) -> None:
    """Merge single keys onto the LATEST extra_state, never clobbering siblings.

    Every write here rewrites the whole ``extra_state`` JSON column, so a
    read-modify-write against a stale in-memory copy silently drops keys another
    writer added in the meantime. The ChatSession object held during a CC turn is
    loaded at turn start, while a nested query/async request on the SAME session
    (a different ORM object) can seed keys mid-turn. Reload the column first so
    this write merges onto it instead of overwriting it.

    The ``or {}`` is belt-and-braces only: the column is ``JSONField(default=dict)``
    with no ``null=True``, so a row loaded from the DB always has a dict here.
    """
    session.refresh_from_db(fields=["extra_state"])
    es = dict(session.extra_state or {})
    es.update(updates)
    session.extra_state = es
    session.save(update_fields=["extra_state", "updated_at"])


def _append_cc_turn_complete(payload: TurnCompletePayload) -> None:
    session = payload.chat_session
    # Same staleness hazard as _merge_extra_state: this is a whole-column
    # read-modify-write on an object loaded at CC-turn start, so reload
    # extra_state first or a concurrently-seeded key (e.g. pipeline_agent, which
    # the router gate reads next turn) is lost when the chat_log is appended.
    session.refresh_from_db(fields=["extra_state"])
    session.extra_state = apply_turn_to_extra_state(
        session.extra_state, payload, cap=MAX_CC_CHAT_LOG_TURNS)
    session.save(update_fields=["extra_state", "updated_at"])
    CCSessionTranscript.objects.update_or_create(
        chat_session=session,
        cc_session_id=payload.cc_session_id or "",
        turn_id=payload.turn_id,
        defaults={
            "blob": cc_transcript_store.compress(payload.raw_jsonl),
            "uncompressed_size": len(payload.raw_jsonl),
        },
    )


def _persist_summary_standalone(user, session_id, summary_dict, fp):
    """Single-key read-modify-write on extra_state; never clobber sibling keys."""
    try:
        sess = ChatSession.objects.get(session_id=session_id, user=user)
        es = dict(sess.extra_state or {})
        es["summary"] = summary_dict
        es["summary_fingerprint"] = fp
        sess.extra_state = es
        sess.save(update_fields=["extra_state", "updated_at"])
    except Exception:
        logger.exception("cc-1c: failed to persist summary for %s", session_id)


def _session_metas(user, current_id, paths, mem_cfg, project_dirname=None):
    """Build cc_memory.SessionMeta for the user's sessions (own sessions only)."""
    from pathlib import Path
    from NessieAI.cc.cc_provision import build_user_dirs

    metas = []
    # #40/#82: results_history AND last_debug are both multi-MB JSON after a
    # large NS turn; either one in the ORDER BY filesort trips MySQL "Out of
    # sort memory" (errno 1038). This helper reads exactly three fields off
    # each row -- session_id, extra_state, updated_at -- so select only those.
    # NOT .defer("extra_state"): it is read on every row below, so deferring it
    # would trade a sort column for one extra query PER ROW.
    # NOT a LIMIT either: rows come back -updated_at DESC, and one of the two
    # consumers is cc_sweep.select_sweep_targets, which wants the sessions that
    # are IDLE (the OLDEST updated_at). A LIMIT N would hand it the N LEAST
    # idle sessions and silently stop sweeping everything else.
    # 2026-09-07: the fix above was not enough. `extra_state` is ITSELF a JSON
    # column -- it holds chat_log -- so keeping it in the SELECT still put it in
    # the filesort, and it outgrew the buffer: measured on production,
    # sort_buffer_size=262,144 against a largest extra_state of 288,291 bytes.
    # One row was bigger than the whole buffer, so every Container-CC turn for
    # that account failed with 1038, surfacing as "Internal pipeline error".
    #
    # Order over small columns ONLY, then read the JSON back by id with no
    # ORDER BY, which needs no filesort at all. Same two-step shape as
    # assistant._most_recent_session. The ordering itself is still load-bearing
    # (cc_sweep.select_sweep_targets wants the most idle sessions), so it is
    # preserved exactly -- it just no longer drags the payload through the sort.
    ordered_ids = list(
        ChatSession.objects.filter(user=user)
        .order_by("-updated_at")
        .values_list("session_id", flat=True)
    )
    by_id = (
        ChatSession.objects.filter(session_id__in=ordered_ids)
        # .order_by() with no arguments is load-bearing: the model carries a
        # default Meta.ordering, which in_bulk inherits, and that would put the
        # filesort straight back on the query that selects extra_state.
        .order_by()
        .only("session_id", "extra_state", "updated_at")
        .in_bulk(field_name="session_id")
    )
    for _sid in ordered_ids:
        s = by_id.get(_sid)
        if s is None:  # deleted between the two queries
            continue
        sid = str(s.session_id)
        es = s.extra_state or {}
        session_project = es.get("cc_project_dirname") or project_dirname
        if not session_project:
            continue
        dirs = build_user_dirs(paths, session_project, user.username, session_id=sid)
        store = Path(dirs.cc_state_mnt) / "projects"
        jsonls = sorted(store.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime,
                        reverse=True) if store.is_dir() else []
        # G7-10: transcript_path is the nextseek-container MOUNT path (under
        # user_root_mount, inside the dmac-cc-users volume) — no host bind exists
        # any more, so cc_sweep / the sync summarizer read it directly with no
        # mount->host translation.
        transcript_mount_path = str(jsonls[0]) if jsonls else None
        prev_fp = es.get("summary_fingerprint")
        changed = False
        if transcript_mount_path:
            try:
                raw = Path(transcript_mount_path).read_bytes()
                changed = cc_summary.is_changed(prev_fp, cc_summary.fingerprint(raw))
            except OSError:
                changed = False
        metas.append(cc_memory.SessionMeta(
            session_id=sid, updated_at=s.updated_at.timestamp(),
            fingerprint=prev_fp, summary=es.get("summary"),
            transcript_path=transcript_mount_path, changed=changed))
    return metas


def _summarize_sync_target(user, tgt, mem_cfg, scrub) -> bool:
    """cc-1c: summarize one OTHER session's transcript, in-request. #72.

    ``tgt`` comes from ``cc_memory.select_sync_target(metas, current_id=...)``,
    which deliberately skips the CURRENT session — so this transcript belongs to
    a chat that is not running, and may predate ``cc_engine.scrub_transcript_store``
    entirely (that scrub runs only in the ``finally`` of a turn the owning session
    itself ran, so a chat that never runs another turn is never cleaned).

    ``scrub`` therefore has to be applied HERE, at the read: the bytes go to a
    third-party summarizer model and the summary lands in ``extra_state`` and
    then in the merged ``CLAUDE.md`` mounted into later agent containers. It is
    the same ``bytes -> bytes`` scrubber the staged transcript copies get.

    The persisted FINGERPRINT is deliberately taken over the RAW bytes, not the
    scrubbed ones. It is a change-detection hash of the file as it lies on disk,
    and the two other producers of it -- ``_session_metas`` (which has no
    credentials for a scrub) and ``cc_sweep`` -- hash the file unmodified.
    Fingerprinting the scrubbed bytes here would mismatch theirs forever, so
    this session would look "changed" on every turn and be re-summarized (a paid
    LLM call) each time.

    Returns True when a summary was persisted; the caller then re-derives metas.
    Never raises: a summarizer failure must not fail the user's turn.
    """
    from django.utils import timezone

    try:
        # G7-10: transcript_path is already the mount path inside the volume —
        # read directly, no host translation.
        raw = Path(tgt.transcript_path).read_bytes()
        prov = cc_summary.SummaryProvenance(
            chat_session_id=tgt.session_id,
            claude_session_id=(tgt.summary or {}).get("claude_session_id"),
            transcript_path=tgt.transcript_path,
            chat_model=cc_router._resolve_cc_model_id() or "",
            generated_at=timezone.now().isoformat())
        summary = cc_summary.summarize_transcript(
            scrub(raw) if scrub is not None else raw, prov, mem_cfg)
        _persist_summary_standalone(
            user, tgt.session_id, summary.model_dump(), cc_summary.fingerprint(raw))
        return True
    except Exception:
        logger.exception("cc-1c: sync summarize failed; continuing")
        return False


def _emit_ns_run_root(send_event, session) -> None:
    """Publish the NS engine's output directory to the event stream.

    `run_root` is set into the chat_nextseek session dict by the orchestrator
    (orchestrator.py:339) and otherwise never leaves it, so a test harness or a
    support request cannot join a task_id to its console.txt, api_requests.json
    or files/.

    The directory belongs to the chat SESSION, not to the turn. `log_dir` is
    persisted into ChatSession.extra_state by DictSessionAdapter.save(), the next
    turn's adapter reloads it, and _ensure_query_log_dir (orchestrator.py:329-331)
    then returns early instead of making a new directory; Tee opens console.txt
    with mode="a", so turns 2..n append into turn 1's directory. Several task_ids
    can therefore legitimately map to the SAME run_root, and a consumer must
    de-duplicate rather than assume uniqueness. That sharing is chat_nextseek's
    design and that subpackage is vendored, so this is a fact to describe, not a
    behaviour to fix here.

    Deliberately total: this is instrumentation, and instrumentation must never be
    able to fail a real user's turn. A session object that raises, a session with
    no run_root, or a send_event that raises all emit nothing and return quietly.
    The send_event call is inside the guard because the caller invokes this from a
    `finally`, where an escaping exception would REPLACE the in-flight one and so
    destroy the real error the user needs to see.
    """
    try:
        run_root = session.get("run_root_dir") if session is not None else None
        if run_root:
            send_event("ns_run_root", {"run_root": str(run_root)})
    except Exception:
        return


def _with_parser_force(chat_config, user, req):
    """Evaluation only: hand the NS engine a config copy that forces the parser's mode.

    Returns ``chat_config`` itself unless all three hold: the request carries a
    valid ``force_parser_mode`` ("graph" or "api"), the process sets
    NEXTSEEK_EVAL_PARSER_FORCE=1, and the caller is a superuser (``is_superuser``
    alone: the SEEK login sets ``is_staff`` on every account). Then it returns a
    shallow copy carrying ``FORCE_PARSER_MODE``, which ``parser_agent`` reads.
    The shared singleton is never mutated, so no other turn in this process sees
    the force, and the PROD identity check in ``start_task`` still compares the
    singleton.
    """
    mode = getattr(req, "force_parser_mode", None)
    if (mode not in PARSER_FORCE_MODES or os.environ.get(EVAL_PARSER_FORCE_ENV) != "1"
            or not bool(getattr(user, "is_superuser", False))):
        return chat_config
    forced = copy.copy(chat_config)
    forced.FORCE_PARSER_MODE = mode
    return forced


def _with_prompt_variant(chat_config, user, req):
    """Evaluation only: hand the NS engine a config copy running an alternative prompt set.

    The same gate as ``_with_parser_force``, and independent of it: returns ``chat_config`` itself unless the
    request carries a known ``prompt_variant`` (``chat_nextseek.prompt_variants.VARIANT_NAMES``), the process
    sets NEXTSEEK_EVAL_PARSER_FORCE=1, and the caller is a superuser (``is_superuser`` alone). Then it returns
    ``prompt_variants.apply_variant``'s shallow copy; the singleton is never mutated. A variant that cannot be
    loaded is logged and the defaults run: the turn then records ``prompt_variant: null``, which the harness
    preflight refuses, so a broken variant cannot pass for a measured one.
    """
    name = getattr(req, "prompt_variant", None)
    if (name not in PROMPT_VARIANT_NAMES or os.environ.get(EVAL_PARSER_FORCE_ENV) != "1"
            or not bool(getattr(user, "is_superuser", False))):
        return chat_config
    try:
        return prompt_variants.apply_variant(chat_config, name)
    except prompt_variants.VariantError as exc:
        logger.error("prompt_variant %r could not be applied; this turn runs the default prompts: %s", name, exc)
        return chat_config


def _scoped_graph_query(chat_config, graph_scope):
    """The graph read ``prior_turns`` stages ``samples.csv`` with (CC-RERUN-FINDINGS fix 1).

    ``tool_neo4j_query`` with its default arguments, on a per-request copy of the NS config that
    carries this caller's scope, so the write check and the scope prover apply exactly as they
    do on the caller's own graph turns. ``graph_scope`` is the ViewSet's plain data; anything
    that is not a well-formed scope becomes None, which refuses every statement. The reader's
    ``cache_key`` names the scope and the graph, so a samples.csv read under one is never reused
    under another. None when the reader cannot be built: staging must never fail the turn.
    """
    try:
        import json as _json
        from collections.abc import Mapping

        from chat_nextseek import helpers
        from chat_nextseek.graph_scope import GraphScope, with_scope

        scope = graph_scope if isinstance(graph_scope, GraphScope) else None
        if scope is None and isinstance(graph_scope, Mapping):
            try:
                scope = GraphScope.from_plain(graph_scope)
            except ValueError:
                logger.warning("cc: malformed graph scope; the previous turns get no samples.csv")
        scoped = with_scope(chat_config, scope)

        def query(cypher: str, parameters: dict) -> dict:
            return helpers.tool_neo4j_query(scoped, cypher, parameters)
        query.cache_key = _json.dumps({
            "admin": None if scope is None else scope.is_admin,
            "projects": None if scope is None else list(scope.project_ids),
            "graph": str(getattr(chat_config, "NEO4J_URI", "") or ""),
        }, sort_keys=True)
        return query
    except Exception:  # noqa: BLE001 - the turn runs without samples.csv
        logger.warning("cc: could not build the samples.csv reader", exc_info=True)
        return None


def _eval_config(chat_config, user, req):
    """Both evaluation switches on one per-request copy: the parser force, then the prompt variant."""
    return _with_prompt_variant(_with_parser_force(chat_config, user, req), user, req)


def start_task(request, req, *, force_cc: bool, chat_session, query_task,
               send_event, adapter, api_user, api_pass,
               resolved_session_id: str, graph_scope=None) -> None:
    """Run one routed chat turn on a daemon thread; return at once.

    The body of ``CCAssistantViewSet._start_task``, which keeps every HTTP
    and host seam and hands them in: the resolved ``chat_session``, its new
    ``query_task`` row, the ``send_event`` callback bound to that row, the
    ``DictSessionAdapter`` over the session, the caller's resolved SEEK
    credentials, the caller's project scope for graph queries (plain data,
    resolved by the ViewSet) and the session id string. The turn's own outcome
    reaches the client only through ``send_event``; nothing is returned.

    ``graph_scope`` reaches the NS engine only. ``None`` leaves the keyword out,
    and the request configs carry no scope of their own, so every graph query
    refuses. The CC route's graph ops come back over HTTP to the granular view,
    which resolves the scope again for the same caller.
    """
    scope_kw = {} if graph_scope is None else {"graph_scope": graph_scope}
    terminal_seen = cc_turn_complete.new_terminal_tracker()
    send_event = cc_turn_complete.wrap_send_event(send_event, terminal_seen)
    user_api_user, user_api_pass = api_user, api_pass
    chat_config = _select_chat_config(request, req)

    # Prod-config credential swap (mirror AssistantViewSet).
    from django.conf import settings
    prod_config = getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)
    if prod_config is not None and chat_config is prod_config:
        if chat_config.API_USER and chat_config.API_PASS:
            api_user, api_pass = chat_config.API_USER, chat_config.API_PASS

    mode = getattr(req, "mode", "standard")
    # Capture identity for the CC route (scoped Dropbox mounts + output).
    cc_user_id = request.user.username
    cc_run_id = str(query_task.task_id)

    # Admin-only per-turn wall-clock override (Debug panel max-turn-length),
    # clamped to the env-bounded hard ceiling. Non-admins -> configured
    # default. Mirrors the force_route / use_prod server-side admin gate.
    # is_superuser ALONE. dmac/views.py:80,97 sets is_staff = 1 on every SEEK
    # user at registration and at every login, so `or is_staff` admitted every
    # authenticated account. Same predicate as seek/views.py verifySuperUser and
    # AdminSampleViewSet (#74).
    _is_admin = bool(getattr(request.user, "is_superuser", False))
    _requested_timeout = getattr(req, "max_turn_length_s", None) if _is_admin else None
    resolved_turn_timeout = cc_engine.clamp_turn_timeout(_requested_timeout)

    def _run() -> None:
        ran_ns = False
        decision = None
        try:
            # Fresh state per turn: the adapter was built in the request thread, possibly while the
            # previous turn's save was still in flight. reload() refreshes chat_session itself (the
            # adapter wraps the same object), so the chat_log read below is fresh too. Fakes without
            # reload() are tolerated; a failed reload must not fail the turn.
            _reload = getattr(adapter, "reload", None)
            if _reload is not None:
                try:
                    _reload()
                except Exception:  # noqa: BLE001 - run on the request-time snapshot
                    logger.warning("session reload at turn start failed", exc_info=True)
            chat_log = (chat_session.extra_state or {}).get("chat_log") or []
            history = router_context.build_history(chat_log)
            # The whole chat_log too: stickiness holds for the rest of the chat, not
            # only while a CC turn is inside the router's 5-turn window.
            decision = _decide_route(request.user, req, force_cc=force_cc, session=adapter,
                                     history=history, chat_log=chat_log)
            # A turn the policy moved to CC (sticky, follow-up) goes back to NExtSEEK
            # for this one turn when the CC runner is down, rather than erroring.
            decision = _fallback_when_cc_unavailable(decision, cc_engine.cc_runner_available)

            send_event("route_decided", {
                "route": decision.route, "model_class": decision.model_class,
                "source": decision.source, "reasoning": decision.reasoning,
                # Which router model answered, and whether it fell back (fix 5): None
                # when the keyword rules decided or the turn was forced.
                "router_model": getattr(decision, "router_model", None),
                "router_fallback": getattr(decision, "router_fallback", None),
                # What the router's model calls cost (fix 6a): router_cost_usd,
                # router_cost_partial and router_usage. Absent on a forced turn, which made
                # no router call; present on every routed one, unrelated included.
                **cc_router.router_cost_fields(decision),
            })
            _record_ledger_row(chat_session, decision, query_task=query_task)

            if decision.route == cc_router.ROUTE_UNRELATED:
                from django.utils import timezone
                chat_session.extra_state = cc_turn_complete.apply_non_answer_to_extra_state(
                    chat_session.extra_state, user_query=req.query,
                    router_choice=cc_router.ROUTE_UNRELATED, status="completed",
                    error=None, ts=timezone.now().isoformat(timespec="seconds"),
                    cap=MAX_CC_CHAT_LOG_TURNS)
                chat_session.save(update_fields=["extra_state", "updated_at"])
                send_event("query_complete", {
                    "reply": cc_router.UNRELATED_CANNED_TEXT,
                    "bundle_id": None,
                    "session_id": resolved_session_id,
                })
                return

            if decision.route == cc_router.ROUTE_NS:
                ran_ns = True
                creds = {"api_user": api_user, "api_pass": api_pass}
                ns_send = _save_before_complete(send_event, adapter)
                try:
                    if mode == "plan":
                        run_query_plan(adapter, _with_prompt_variant(chat_config, request.user, req),
                                       req.query, ns_send, credentials=creds, **scope_kw)
                    else:
                        # The evaluation switches: a per-request copy, made after the
                        # PROD identity check above has compared the singleton.
                        run_query(adapter, _eval_config(chat_config, request.user, req),
                                  req.query, ns_send, credentials=creds, **scope_kw)
                finally:
                    # In a `finally` deliberately. run_query resolves
                    # run_root_dir three statements in (orchestrator.py:620),
                    # long before anything can fail, and it re-raises anything
                    # that is not an LLMFatalError. So a turn that RAISED still
                    # left a populated outputs/<ts>_<user>/ behind -- and that is
                    # exactly the turn whose console.txt a collector or a support
                    # request most needs. The join key has to survive the raise.
                    # _emit_ns_run_root is total, so it cannot mask the in-flight
                    # exception on its way out.
                    #
                    # NOT one directory per turn: the path is reused by every
                    # turn of a multi-turn chat session (see the helper's
                    # docstring), so consumers must de-duplicate on run_root.
                    _emit_ns_run_root(send_event, adapter)
            else:
                ok, detail = cc_engine.cc_runner_available()
                if not ok:
                    send_event("query_error", {
                        "error": f"Container-CC route is not available: {detail}",
                        "agent": "container_cc", "session_id": resolved_session_id,
                    })
                    return
                cc_state_key = str(chat_session.session_id)
                prior_id = cc_session.resume_id_from_state(chat_session.extra_state)

                def _persist_cc_session(cc_sid: str) -> None:
                    # Single-key read-modify-write; never clobber other
                    # extra_state keys. Re-captured every turn (robust if the
                    # claude id rotates under -p --resume).
                    try:
                        _merge_extra_state(chat_session, cc_session_id=cc_sid)
                    except Exception:
                        logger.exception(
                            "cc: failed to persist cc_session_id=%r; resume unavailable this turn",
                            cc_sid,
                        )

                cc_send = cc_session.make_session_sniffer(send_event, _persist_cc_session)

                paths = cc_config.CCPaths.from_env()
                from NessieAI.cc.cc_provision import (
                    ProjectResolutionError,
                    build_user_dirs,
                    resolve_user_project,
                )
                try:
                    project = resolve_user_project(user_api_user, user_api_pass)
                except ProjectResolutionError as exc:
                    logger.warning("cc-step2: project resolution failed: %s", exc)
                    send_event("query_error", {
                        "error": (
                            "Could not resolve your SEEK project. "
                            "Please try again shortly."
                        ),
                        "agent": "container_cc",
                        "session_id": resolved_session_id,
                    })
                    return
                stored_project_dirname = (chat_session.extra_state or {}).get("cc_project_dirname")
                if stored_project_dirname and stored_project_dirname != project.dirname:
                    logger.warning(
                        "cc-step2: stored project dirname %r no longer matches resolved %r",
                        stored_project_dirname,
                        project.dirname,
                    )
                    send_event("query_error", {
                        "error": (
                            "Your SEEK project membership changed for this chat. "
                            "Please start a new chat."
                        ),
                        "agent": "container_cc",
                        "session_id": resolved_session_id,
                    })
                    return
                project_dirname = stored_project_dirname or project.dirname
                try:
                    if not (chat_session.extra_state or {}).get("cc_project_dirname"):
                        _merge_extra_state(chat_session, cc_project_dirname=project_dirname)
                except Exception:
                    logger.exception("cc-step2: failed to persist project dirname")

                mem_cfg = cc_config.CCMemoryConfig.from_env()
                fresh = bool(getattr(req, "fresh_session", False))
                memory_claude_md = None
                transcripts_subpath = None
                dirs = build_user_dirs(
                    paths, project_dirname, request.user.username,
                    session_id=cc_state_key)
                mem_root = Path(dirs.memory_mnt)
                memory_md = ""
                if not fresh:
                    # #72: ONE scrubber for every point in this turn that
                    # re-reads a transcript belonging to a session other than
                    # the current one — the sync summarizer just below, and
                    # the read-only staged copies further down. Both republish
                    # those bytes (to a third-party model / to a later agent
                    # container), and the engine's in-place source scrub
                    # covers neither for a session that never runs again.
                    transcript_scrub = cc_engine.transcript_scrubber({
                        "NEXTSEEK_USERNAME": user_api_user or "",
                        "NEXTSEEK_PASSWORD": user_api_pass or "",
                        "API_PASS": user_api_pass or "",
                    })
                    metas = _session_metas(
                        request.user, cc_state_key, paths, mem_cfg, project_dirname)
                    tgt = cc_memory.select_sync_target(metas, current_id=cc_state_key)
                    if tgt is not None and tgt.transcript_path:
                        if _summarize_sync_target(
                                request.user, tgt, mem_cfg, transcript_scrub):
                            metas = _session_metas(
                                request.user, cc_state_key, paths, mem_cfg, project_dirname)

                    window = cc_memory.select_window(
                        metas, current_id=cc_state_key, window_size=mem_cfg.window_size)
                    memory_md = cc_memory.render_memory(
                        window, fresh_session=False,
                        transcripts_mount=cc_engine._CONTAINER_MEMORY_TRANSCRIPTS)
                    # #72: this staging dir is mounted READ-ONLY into the next
                    # agent container, so scrub secrets on the way in. Covers
                    # transcripts written before the engine's in-place source
                    # scrub existed, and sessions that never run another turn.
                    staged = cc_memory_io.stage_transcripts(
                        window, mem_root / "transcripts", scrub=transcript_scrub,
                    )
                    if staged:
                        transcripts_subpath = dirs.transcripts_subpath
                within_chat_md = ns_digest.render_within_chat_digest(
                    ns_turn_context.build_contexts(
                        (chat_session.extra_state or {}).get("chat_log") or [],
                        chat_session.results_history or [],
                        session_id=str(chat_session.session_id)),
                    cc_turn_context.build_cc_contexts(
                        (chat_session.extra_state or {}).get("chat_log") or []))
                # 2026-09-23: every follow-up comes here, so the previous turns' Search
                # details, rows and downloads are staged for this turn to read, from this
                # chat only and through the download endpoint's own guard (prior_turns).
                # Within-chat, so a fresh_session turn gets them too, like the digest.
                # samples.csv: every stored property of the samples an NS turn returned, read
                # once through the caller's own scoped graph tool.
                staged_prior = prior_turns.stage_prior_turns(
                    chat_log=(chat_session.extra_state or {}).get("chat_log") or [],
                    results_history=chat_session.results_history or [],
                    dest_dir=Path(dirs.previous_turns_mnt),
                    cc_artifacts_root=Path(dirs.output_mnt) / "artifacts",
                    graph_query=_scoped_graph_query(chat_config, graph_scope),
                )
                within_chat_md = "\n\n".join(
                    p for p in (prior_turns.memory_pointer(staged_prior), within_chat_md) if p)
                combined = ns_digest.compose_turn_claude_md(within_chat_md, memory_md)
                written = cc_memory_io.write_memory_file(mem_root / "CLAUDE.md", combined)
                if written:
                    memory_claude_md = str(written)

                # Surface the CC turn's parameters in the Debug panel (#4):
                # model, resume session, budget cap, and the resolved
                # wall-clock — previously all backend-only.
                send_event("cc_turn_meta", {
                    "model_id": decision.model_id,
                    "cc_session_id": prior_id or None,
                    "budget_usd": cc_engine._DEFAULT_MAX_BUDGET_USD,
                    "turn_timeout_s": resolved_turn_timeout,
                })
                cc_engine.run_cc_turn(
                    query=req.query,
                    model_id=decision.model_id,
                    send_event=cc_send,
                    user_id=cc_user_id,
                    project_dirname=project_dirname,
                    run_id=cc_run_id,
                    paths=paths,
                    session_id=prior_id,
                    cc_state_key=cc_state_key,
                    memory_claude_md=memory_claude_md,
                    transcripts_subpath=transcripts_subpath,
                    previous_turns=staged_prior is not None,
                    api_user=user_api_user, api_pass=user_api_pass,
                    chat_session=chat_session,
                    user_query=req.query or "",
                    on_turn_complete=_append_cc_turn_complete,
                    turn_timeout=resolved_turn_timeout,
                    chat_session_id=cc_state_key,
                )
        except Exception:
            logger.exception("cc-assistant pipeline error")
            send_event("query_error", {
                "error": "Internal pipeline error", "agent": "unknown",
                "session_id": resolved_session_id,
            })
        finally:
            unrelated = decision is not None and decision.route == cc_router.ROUTE_UNRELATED
            if cc_turn_complete.should_append_non_answer(terminal_seen, unrelated=unrelated):
                from django.utils import timezone
                ts = timezone.now().isoformat(timespec="seconds")
                rc = decision.route if decision is not None else None
                err = terminal_seen["error"]
                if ran_ns:
                    log = list(adapter.get("chat_log") or [])
                    entry = cc_turn_complete.serialize_non_answer_entry(
                        user_query=req.query, router_choice=rc, status="error",
                        error=err, ts=ts, turn_id=next_turn_id(log))
                    adapter["chat_log"] = cc_turn_complete.append_capped(
                        log, entry, cap=MAX_CC_CHAT_LOG_TURNS)
                else:
                    chat_session.refresh_from_db(fields=["extra_state"])
                    chat_session.extra_state = cc_turn_complete.apply_non_answer_to_extra_state(
                        chat_session.extra_state, user_query=req.query,
                        router_choice=rc, status="error", error=err, ts=ts,
                        cap=MAX_CC_CHAT_LOG_TURNS)
                    chat_session.save(update_fields=["extra_state", "updated_at"])
            if ran_ns:
                adapter.save()
            # Title the chat for every route that reached here, not just NS
            # (#3: chats stuck on "New chat" because only the NS path titled).
            # CC / out-of-scope turns don't populate results_history, so pass
            # this turn's query as the fallback title source.
            _auto_title_if_unset(chat_session, fallback_query=req.query)

    threading.Thread(target=_run, daemon=True).start()
