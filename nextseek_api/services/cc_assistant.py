"""DRF ViewSet for the additive dmac_assistant integration (router + Container-CC).

NEW endpoints, fully additive — the existing ``AssistantViewSet`` (chat_nextseek
wrapper) is untouched and reused only via import:

  POST /nextseek_api/cc-assistant/query/async/        router-dispatched (NS or CC)
  POST /nextseek_api/cc-assistant/cc/query/async/     force the Container-CC route
  GET  /nextseek_api/cc-assistant/tasks/{id}/progress/  poll fallback (same shape)

All three create/read the SAME ``QueryTask`` model the existing assistant uses,
and drive ``make_db_event_callback`` — so the EXISTING ``TaskProgressConsumer``
websocket (``ws/assistant/progress/{task_id}/``) streams them to the unchanged
chat_frontend with no new consumer or routing entry.

The router (``cc_assistant.router.decide``) is dmac_assistant's BAML RouteQuery
(with a heuristic fallback). The NS route reuses the in-process
``chat_nextseek.run_query`` exactly as ``AssistantViewSet`` does; the CC route
runs a sandboxed ``claude`` container via ``cc_assistant.cc_engine``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from django.http import Http404, StreamingHttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import (
    BasicAuthentication,
    SessionAuthentication,
    TokenAuthentication,
)
from drf_spectacular.utils import extend_schema, OpenApiExample
from pydantic import ValidationError

from nextseek_api.assistant.models_api import AsyncQueryResponse, QueryRequest, TaskProgressResponse
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.assistant.pipeline_adapter import make_db_event_callback
from nextseek_api.helpers import resolve_seek_auth

# Reuse the existing assistant's helpers (do NOT redefine its behavior).
from nextseek_api.services.assistant import (
    CsrfExemptSessionAuthentication,
    _auto_title_if_unset,
    _error_response,
    _select_chat_config,
)

from chat_nextseek.orchestrator import run_query, run_query_plan

from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant import router_context
from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant import cc_config
from nextseek_api.cc_assistant import cc_session
from nextseek_api.cc_assistant import cc_summary
from nextseek_api.cc_assistant import cc_memory
from nextseek_api.cc_assistant import cc_memory_io
from nextseek_api.cc_assistant import ns_digest
from nextseek_api.cc_assistant import ns_turn_context
from nextseek_api.cc_assistant import cc_turn_context
from nextseek_api.cc_assistant.cc_provision import ProjectResolutionError

from nextseek_api.cc_assistant.cc_turn_complete import (
    TurnCompletePayload,
    apply_turn_to_extra_state,
)
from chat_nextseek.pipeline import agent as pipeline_agent
from nextseek_api.cc_assistant import cc_turn_complete
from chat_nextseek.chat_memory import next_turn_id
from nextseek_api.cc_assistant import cc_transcript_store
from nextseek_api.assistant.models_db import CCSessionTranscript

MAX_CC_CHAT_LOG_TURNS = 50  # match chat_nextseek/chat_memory.py MAX_TURNS


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


logger = logging.getLogger(__name__)


def _iter_and_cleanup(path: Path):
    """Mirror content_blobs._iter_and_cleanup — unlink temp zip after stream."""
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                yield chunk
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _iter_file(path: Path):
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            yield chunk


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
    from nextseek_api.cc_assistant.cc_provision import build_user_dirs

    metas = []
    # results_history can be multi-MB JSON; including it in ORDER BY filesort
    # trips MySQL "Out of sort memory" (errno 1038) after large NS turns.
    # This helper only needs session_id / extra_state / updated_at.
    qs = (
        ChatSession.objects.filter(user=user)
        .defer("results_history")
        .order_by("-updated_at")
    )
    for s in qs:
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


def _prev_route_was_cc(history: list[router_context.HistoryTurn] | None) -> bool:
    """True when the most recent ENGINE-RUNNING turn in this chat ran CC and completed.

    ``unrelated`` turns are transparent. They never reach an engine, never produce
    a bundle and carry no routing state worth inheriting, so scanning past them is
    what keeps one off-topic aside from silently ending stickiness: with a plain
    ``history[-1]`` test, "cluster these samples" (CC) -> "what's the weather"
    (unrelated) -> "now group those by genotype" dropped back to NS and then failed
    for want of an NS bundle to refine, which is the exact breakage sticky CC
    exists to prevent.

    The scan stops at the FIRST engine-running turn it finds, whatever its status.
    That is deliberate: a failed CC turn must NOT trap the chat on a route that
    just broke, so we must not keep looking past it for an older healthy CC turn.

    Bounded by ``router_context.MAX_HISTORY_TURNS`` (5), so three consecutive
    ``unrelated`` turns push the CC turn out of the window and stickiness is lost
    regardless. Accepted: the window is the router's own context limit.
    """
    for turn in reversed(history or []):
        if turn.router_choice == cc_router.ROUTE_UNRELATED:
            continue
        return (turn.router_choice == cc_router.ROUTE_CC
                and turn.status == "completed")
    return False


def _decide_route(user, req, *, force_cc: bool, session=None, history: list[router_context.HistoryTurn] | None = None) -> cc_router.RouteDecision:
    """Pick the route for a query, honoring the admin-only ``force_route`` override.

    Precedence: ``force_route`` > ``pipeline_agent`` > sticky CC > the router.
    An explicit force (``force_cc`` or an admin's ``force_route``) wins first,
    THEN the BAML router (:func:`cc_router.decide`) decides, and two guards may
    still redirect an NS-bound turn: an active ``pipeline_agent`` wizard keeps
    it on NExtSEEK, and a chat whose previous turn completed on CC keeps it on
    CC (A1 "sticky CC"). A non-admin's
    ``force_route`` is ignored and falls back to the router (mirrors
    ``use_prod``'s server-side admin gate). Forced decisions are
    ``ROUTE_NS``/``ROUTE_CC`` (never ``ROUTE_UNRELATED``), so a forced query
    always runs on the chosen path instead of hitting the out-of-scope canned
    reply.
    """
    forced = getattr(req, "force_route", None)
    if forced in ("ns", "cc"):
        is_admin = bool(
            getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
        )
        if not is_admin:
            forced = None  # non-admins can never force a route

    if force_cc or forced == "cc":
        # CC always runs Opus (the only proxy-allowlisted model); hardcoding
        # sonnet here would 403 at the Bedrock proxy.
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_CC, model_class="opus",
            model_id=cc_router._resolve_cc_model_id(),
            reasoning="forced", source="forced",
        )
    if forced == "ns":
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_NS, model_class=None,
            model_id=None, reasoning="forced", source="forced",
        )
    decision = cc_router.decide(req.query, history=history)
    if (
        session is not None
        and pipeline_agent.is_active(session)
        and decision.route == cc_router.ROUTE_NS
    ):
        # A pipeline wizard is mid-flow: keep confirm/tweak/launch turns on the NS
        # route so they reach pipeline_agent.handle_turn.
        #
        # This used to short-circuit BEFORE consulting the router, which let an
        # open build capture every following turn — a plain sample search got
        # answered by the samplesheet builder ("searching the database isn't
        # something I can do") because the model never saw the query. Now the
        # router decides first and only an NS-bound turn is handed to the wizard;
        # the wizard itself calls `handoff` when the turn is not about the build,
        # which the orchestrator turns into a passthrough.
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_NS, model_class=None, model_id=None,
            reasoning=f"pipeline_active; router said ns ({decision.reasoning})",
            source="pipeline",
        )
    # A1 (sticky CC): the router classifies each turn independently, so a
    # conversation that starts on CC gets yanked back to NS mid-thread and the
    # follow-up fails for want of an NS bundle to refine ("Find samples from a
    # 4 week study." -> CC, "Just the 4 week ones." -> NS -> broken). Once a CC
    # turn completes, keep the chat on CC.
    #
    # ORDER IS LOAD-BEARING, for the same reason spelled out on the pipeline
    # gate above: a guard that short-circuits BEFORE the router captures every
    # following turn without the model ever seeing the query. Both guards run
    # AFTER cc_router.decide and only ever redirect an NS-bound turn. Do not
    # "simplify" this to the top of the function.
    #
    # ROUTE_UNRELATED is deliberately excluded -- converting it would spin up an
    # Opus container for an out-of-scope question instead of returning the
    # canned refusal. Only NS -> CC.
    try:
        sticky = decision.route == cc_router.ROUTE_NS and _prev_route_was_cc(history)
    except Exception:  # noqa: BLE001 - routing must never crash on bad history
        logger.warning("CC router: sticky-CC history inspection failed", exc_info=True)
        sticky = False
    if sticky:
        return cc_router.RouteDecision(
            route=cc_router.ROUTE_CC, model_class="opus",
            model_id=cc_router._resolve_cc_model_id(),
            reasoning=f"sticky_cc; router said ns ({decision.reasoning})",
            source="sticky",
        )
    return decision


class CCAssistantViewSet(viewsets.ViewSet):
    """Router + Container-Claude-Code assistant (additive to AssistantViewSet)."""

    authentication_classes = [TokenAuthentication, CsrfExemptSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    # ------------------------------------------------------------------ auth
    def _check_auth(self, request):
        basic_tuple, extra_headers = resolve_seek_auth(request, ["BASIC", "SESSION", "TOKEN"])
        if not basic_tuple and not extra_headers and not request.user.is_authenticated:
            return False, _error_response(
                "Authentication required",
                "Provide Basic, Session, or Token credentials.",
                status.HTTP_401_UNAUTHORIZED,
            )
        return True, None

    # ------------------------------------------------------------------ session
    def _resolve_session(self, request, req) -> ChatSession:
        if req.session_id:
            return ChatSession.objects.get(session_id=req.session_id, user=request.user)
        if getattr(req, "force_new", False):
            return ChatSession.objects.create(user=request.user)
        existing = (
            ChatSession.objects.filter(user=request.user).order_by("-updated_at").first()
        )
        return existing or ChatSession.objects.create(user=request.user)

    def _resolve_credentials(self, request):
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if basic_tuple and basic_tuple[0] and basic_tuple[1]:
            return basic_tuple
        return request.session.get("username"), request.session.get("password")

    # ------------------------------------------------------------------ dispatch
    def _start_task(self, request, req, *, force_cc: bool) -> Response:
        try:
            chat_session = self._resolve_session(request, req)
        except ChatSession.DoesNotExist:
            return _error_response(
                "Not found", "Session not found or you do not own it.", status.HTTP_404_NOT_FOUND
            )

        query_task = QueryTask.objects.create(
            session=chat_session, user=request.user, query=req.query, status="running",
        )
        resolved_session_id = str(chat_session.session_id)
        send_event = make_db_event_callback(str(query_task.task_id), resolved_session_id)
        terminal_seen = cc_turn_complete.new_terminal_tracker()
        send_event = cc_turn_complete.wrap_send_event(send_event, terminal_seen)
        adapter = DictSessionAdapter(chat_session)
        api_user, api_pass = self._resolve_credentials(request)
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
        _is_admin = bool(
            getattr(request.user, "is_staff", False)
            or getattr(request.user, "is_superuser", False)
        )
        _requested_timeout = getattr(req, "max_turn_length_s", None) if _is_admin else None
        resolved_turn_timeout = cc_engine.clamp_turn_timeout(_requested_timeout)

        def _run() -> None:
            ran_ns = False
            decision = None
            try:
                history = router_context.build_history(
                    (chat_session.extra_state or {}).get("chat_log") or []
                )
                decision = _decide_route(request.user, req, force_cc=force_cc, session=adapter, history=history)

                send_event("route_decided", {
                    "route": decision.route, "model_class": decision.model_class,
                    "source": decision.source, "reasoning": decision.reasoning,
                })

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
                    try:
                        if mode == "plan":
                            run_query_plan(adapter, chat_config, req.query, send_event, credentials=creds)
                        else:
                            run_query(adapter, chat_config, req.query, send_event, credentials=creds)
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
                    from nextseek_api.cc_assistant.cc_provision import (
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
                        from django.utils import timezone

                        metas = _session_metas(
                            request.user, cc_state_key, paths, mem_cfg, project_dirname)
                        tgt = cc_memory.select_sync_target(metas, current_id=cc_state_key)
                        if tgt is not None and tgt.transcript_path:
                            try:
                                # G7-10: transcript_path is already the mount path
                                # inside the volume — read directly, no host xlate.
                                raw = Path(tgt.transcript_path).read_bytes()
                                prov = cc_summary.SummaryProvenance(
                                    chat_session_id=tgt.session_id,
                                    claude_session_id=(tgt.summary or {}).get("claude_session_id"),
                                    transcript_path=tgt.transcript_path,
                                    chat_model=cc_router._resolve_cc_model_id() or "",
                                    generated_at=timezone.now().isoformat())
                                summary = cc_summary.summarize_transcript(raw, prov, mem_cfg)
                                _persist_summary_standalone(
                                    request.user, tgt.session_id,
                                    summary.model_dump(),
                                    cc_summary.fingerprint(raw))
                                metas = _session_metas(
                                    request.user, cc_state_key, paths, mem_cfg, project_dirname)
                            except Exception:
                                logger.exception("cc-1c: sync summarize failed; continuing")

                        window = cc_memory.select_window(
                            metas, current_id=cc_state_key, window_size=mem_cfg.window_size)
                        memory_md = cc_memory.render_memory(
                            window, fresh_session=False,
                            transcripts_mount=cc_engine._CONTAINER_MEMORY_TRANSCRIPTS)
                        staged = cc_memory_io.stage_transcripts(window, mem_root / "transcripts")
                        if staged:
                            transcripts_subpath = dirs.transcripts_subpath
                    within_chat_md = ns_digest.render_within_chat_digest(
                        ns_turn_context.build_contexts(
                            (chat_session.extra_state or {}).get("chat_log") or [],
                            chat_session.results_history or [],
                            session_id=str(chat_session.session_id)),
                        cc_turn_context.build_cc_contexts(
                            (chat_session.extra_state or {}).get("chat_log") or []))
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

        return Response(
            AsyncQueryResponse(
                task_id=query_task.task_id, session_id=chat_session.session_id,
            ).model_dump(mode="json"),
            status=status.HTTP_202_ACCEPTED,
        )

    # ------------------------------------------------------------------ routes
    @extend_schema(
        operation_id="CC Assistant: Query (Async, routed)",
        description="Router-dispatched async query. The dmac_assistant BAML router "
                    "decides between the deterministic NExtSEEK pipeline (chat_nextseek) "
                    "and the sandboxed Container-Claude-Code agent. Returns a task_id; "
                    "stream progress over the existing ws/assistant/progress/{task_id}/.",
        tags=["Assistant (CC)"],
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
        examples=[OpenApiExample(
            name="Routed query", value={"query": "Find me mice treated with NDMA"},
            request_only=True,
        )],
    )
    @action(detail=False, methods=["post"], url_path="query/async")
    def query_async(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        try:
            req = QueryRequest.model_validate(request.data)
        except ValidationError as e:
            return _error_response("Validation error", str(e), status.HTTP_422_UNPROCESSABLE_ENTITY)
        return self._start_task(request, req, force_cc=False)

    @extend_schema(
        operation_id="CC Assistant: Query (Async, force Container-CC)",
        description="Force the Container-Claude-Code route (bypass the router). "
                    "Runs a sandboxed claude container; streams progress over the "
                    "existing assistant websocket.",
        tags=["Assistant (CC)"],
        request=QueryRequest,
        responses={202: AsyncQueryResponse},
    )
    @action(detail=False, methods=["post"], url_path="cc/query/async")
    def cc_query_async(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        try:
            req = QueryRequest.model_validate(request.data)
        except ValidationError as e:
            return _error_response("Validation error", str(e), status.HTTP_422_UNPROCESSABLE_ENTITY)
        return self._start_task(request, req, force_cc=True)

    @extend_schema(
        operation_id="CC Assistant: Task Progress (poll fallback)",
        description="Poll a routed/CC task's progress (same shape as the existing "
                    "assistant). The websocket is the primary channel; this is the fallback.",
        tags=["Assistant (CC)"],
        responses={200: TaskProgressResponse},
    )
    @action(detail=False, methods=["get"], url_path=r"tasks/(?P<task_id>[0-9a-f-]+)/progress")
    def task_progress(self, request, task_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err
        try:
            query_task = QueryTask.objects.select_related("session").get(
                task_id=task_id, user=request.user,
            )
        except QueryTask.DoesNotExist:
            return _error_response("Not found", "Task not found or you do not own it.", status.HTTP_404_NOT_FOUND)
        return Response(
            TaskProgressResponse(
                task_id=query_task.task_id,
                session_id=query_task.session.session_id,
                status=query_task.status,
                progress=[{"event": p.get("event", ""), "data": p.get("data", {})}
                          for p in (query_task.progress or [])],
                result=query_task.result if query_task.status in ("completed", "error") else None,
            ).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        from django.conf import settings
        from rest_framework.response import Response
        from rest_framework import status as drf_status
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from nextseek_api.cc_assistant.cc_upload_tasks import run_cc_upload_task
        from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename

        uploaded = request.FILES.getlist("file")
        if not uploaded:
            return Response({"error": "no files"}, status=400)
        cap = getattr(settings, "BATCH_UPLOAD_MAX_TOTAL_BYTES", 200 * 1024 * 1024)
        if sum(f.size for f in uploaded) > cap:
            return Response({"error": "upload too large"}, status=413)

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)

        staged = []
        seen_names: set[str] = set()
        stage_root = os.path.join(getattr(settings, "MEDIA_ROOT", "/tmp"), "cc_upload_staging")
        os.makedirs(stage_root, exist_ok=True)
        for f in uploaded:
            safe = validate_upload_filename(getattr(f, "name", ""))
            if safe in seen_names:
                return Response({"error": f"duplicate filename in batch: {safe}"}, status=400)
            seen_names.add(safe)
            tmp = os.path.join(stage_root, f"{int(time.time() * 1000)}_{safe}")
            with open(tmp, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)
            staged.append({"name": safe, "tmp_path": tmp})

        from nextseek_api.batch_upload.job_index import register_job

        task = run_cc_upload_task.delay(input_mnt=dirs.input_mnt, files=staged)
        register_job(user_id=request.user.pk, job_id=task.id, project_id=int(project.id) if str(project.id).isdigit() else 0)
        return Response({"job_id": task.id, "status": "queued"},
                        status=drf_status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path=r"upload/status/(?P<job_id>[^/.]+)")
    def upload_status(self, request, job_id=None):
        from rest_framework.response import Response
        from celery.result import AsyncResult
        from nextseek_api.batch_upload.celery_app import app as celery_app
        from nextseek_api.batch_upload.job_index import user_owns_job

        if not user_owns_job(request.user.pk, job_id):
            return Response({"error": "not found"}, status=404)
        r = AsyncResult(job_id, app=celery_app)
        resp = {"job_id": job_id, "state": r.state, "meta": {}, "result": None}
        if r.state == "PROGRESS":
            resp["meta"] = r.info or {}
        elif r.state == "SUCCESS":
            resp["result"] = r.result
        elif r.state == "FAILURE":
            resp["meta"] = {"error": str(r.result)}
        return Response(resp)

    @action(detail=False, methods=["get"], url_path="upload/list")
    def upload_list(self, request):
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
        from nextseek_api.cc_assistant.cc_upload_list import list_input_files

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)
        return Response({"files": list_input_files(dirs.input_mnt)})

    @action(detail=False, methods=["get"], url_path=r"artifacts/(?P<session>[0-9a-f-]+)/download")
    def download_artifact(self, request, session=None):
        from nextseek_api.assistant.models_db import ChatSession
        from nextseek_api.cc_assistant.cc_config import CCPaths
        from nextseek_api.cc_assistant.cc_provision import resolve_user_project, build_user_dirs
        from nextseek_api.cc_assistant.cc_engine import _safe_relpath

        cs = ChatSession.objects.filter(user=request.user, session_id=session).first()
        if cs is None:
            raise Http404("no such session")
        key = request.query_params.get("key", "")
        if not key or (key != "all" and not _safe_relpath(key)):
            raise Http404("bad key")

        api_user, api_pass = self._resolve_credentials(request)
        try:
            project = resolve_user_project(api_user, api_pass)
        except ProjectResolutionError:
            return Response({"error": "could not resolve SEEK project"}, status=503)
        dirs = build_user_dirs(CCPaths.from_env(), project.dirname, request.user.username)
        from nextseek_api.cc_assistant.cc_endpoint_guards import resolve_artifact_path
        art_dir = Path(dirs.output_mnt) / "artifacts"
        if key == "all":
            turn_id = request.query_params.get("turn_id", "")
            if not turn_id or not _safe_relpath(turn_id):
                raise Http404("bad turn_id")
            art_dir = art_dir / turn_id
            import tempfile
            from nextseek_api.cc_assistant.cc_artifacts import build_artifact_zip
            # Exclude the per-turn artifacts.zip written by Task 6 into this same
            # art_dir, else key="all" nests the prior zip inside the new one.
            files = [p for p in art_dir.rglob("*") if p.is_file() and p.name != "artifacts.zip"]
            tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
            build_artifact_zip(files, tmp, arc_prefix=art_dir)
            resp = StreamingHttpResponse(_iter_and_cleanup(tmp), content_type="application/zip")
            resp["Content-Disposition"] = 'attachment; filename="artifacts.zip"'
            return resp

        target = resolve_artifact_path(str(art_dir), key)
        if not target.is_file():
            raise Http404("not found")
        resp = StreamingHttpResponse(_iter_file(target), content_type="application/octet-stream")
        resp["Content-Disposition"] = f'attachment; filename="{target.name}"'
        return resp

    @action(detail=False, methods=["get"], url_path=r"transcript/(?P<session>[0-9a-f-]+)/(?P<turn>[^/.]+)")
    def recover_transcript(self, request, session=None, turn=None):
        from django.conf import settings
        from django.http import HttpResponse
        from nextseek_api.assistant.models_db import ChatSession, CCSessionTranscript
        from nextseek_api.cc_assistant.cc_transcript_store import decompress

        cs = ChatSession.objects.filter(user=request.user, session_id=session).first()
        if cs is None:
            raise Http404("no such session")
        cc_sid = request.query_params.get("cc_session_id")
        qs = CCSessionTranscript.objects.filter(chat_session=cs, turn_id=turn)
        if cc_sid:
            qs = qs.filter(cc_session_id=cc_sid)
        elif qs.count() > 1:
            return Response({"error": "cc_session_id required"}, status=400)
        row = qs.order_by("-created_at").first()
        if row is None:
            raise Http404("no transcript")
        jsonl = decompress(bytes(row.blob), max_bytes=getattr(settings, "CC_TRANSCRIPT_MAX_BYTES", 256 * 1024 * 1024))
        resp = HttpResponse(jsonl, content_type="application/x-ndjson")
        resp["Content-Disposition"] = f'attachment; filename="transcript-{turn}.jsonl"'
        return resp
