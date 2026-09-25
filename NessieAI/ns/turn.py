"""The NS chat turn, and the helpers of a turn that both engines share.

``_select_chat_config`` picks the ChatConfig for a request (the admin-only
``use_prod`` switch), and ``_auto_title_if_unset`` titles a chat from its first
query; the NS endpoints in ``nextseek_api/services/assistant.py`` and the
Container-CC turn both call them. ``run_sse_pipeline`` and ``run_async_pipeline``
are the pipeline bodies of the ``query`` (SSE) and ``query/async`` endpoints: they
run the chat_nextseek orchestrator for the request's mode and then save the turn
through ``_save_session_or_report``. ``make_sse_send_event`` builds the SSE
endpoint's event callback. ``_granular_args`` projects a granular-op request into
the args ``NessieAI.ns.granular.run_op`` takes.

Moved verbatim from ``nextseek_api/services/assistant.py`` (Phase B of the
NessieAI consolidation); the two pipeline bodies and the event callback were
closures inside the ViewSet actions and are lifted to module functions that
take what they closed over as keyword arguments. The ViewSet keeps every HTTP
and host seam and hands them in: session resolution, the ``QueryTask`` row,
``make_db_event_callback``, the ``DictSessionAdapter``, credential resolution
and the prod swap, the thread start and the SSE stream. Patch the orchestrator
entry points here (``NessieAI.ns.turn.run_query``), where they are looked up.

Nothing here imports ``nextseek_api.services``. The one host edge is
``nextseek_api.assistant.session_adapter`` (``SessionSaveError``, which a failed
save raises), allowed for this module by
``NessieAI/tests/api/test_nessie_boundaries.py``. ``ChatSession`` appears only
in annotations, which ``from __future__ import annotations`` keeps strings.
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from nextseek_api.assistant.session_adapter import SessionSaveError

from chat_nextseek.config import ChatConfig
from chat_nextseek.failure_replies import fatal_query_error
from chat_nextseek.llm_clients import LLMFatalError
from chat_nextseek.orchestrator import run_query, run_query_plan, run_pipeline_launch
from chat_nextseek import turn_spend

logger = logging.getLogger(__name__)


def _auto_title_if_unset(chat_session: ChatSession, fallback_query: str = "") -> None:
    """Populate ChatSession.title from the first user query if currently NULL.

    Titles from the first ``user_query`` in ``results_history`` (the NS path).
    Container-CC and out-of-scope turns persist to ``extra_state`` / the
    transcript rather than ``results_history``, so they carry no ``user_query``
    here — for those, fall back to ``fallback_query`` (this turn's query) so
    their chats title too instead of being stuck on "New chat".

    Idempotent: subsequent calls on a session with a title set are a no-op.
    A manually-set title is therefore never overwritten — frontend rename
    always wins.
    """
    if chat_session.title:
        return
    history = chat_session.results_history or []
    first_user_query = ""
    for bundle in history:
        uq = (bundle or {}).get("user_query")
        if uq:
            first_user_query = uq
            break
    if not first_user_query:
        first_user_query = (fallback_query or "").strip()
    if not first_user_query:
        return
    title = " ".join(first_user_query.split())[:60]
    if not title:
        return
    chat_session.title = title
    chat_session.save(update_fields=["title", "updated_at"])


def _select_chat_config(request, req) -> ChatConfig:
    """Pick the ChatConfig instance for this request.

    Returns ``settings.NEXTSEEK_CHAT_CONFIG_PROD`` when the request asked for
    ``use_prod=True`` AND the caller is admin AND a prod config was actually
    built in ``local_settings.py``. Falls back to the default
    ``NEXTSEEK_CHAT_CONFIG`` in every other case.
    """
    if not getattr(req, "use_prod", False):
        return settings.NEXTSEEK_CHAT_CONFIG
    user = getattr(request, "user", None)
    # is_superuser ALONE. dmac/views.py:80,97 sets is_staff = 1 on every SEEK
    # user at registration and at every login, so `or is_staff` admitted every
    # authenticated account. Same predicate as seek/views.py verifySuperUser and
    # AdminSampleViewSet (#74).
    #
    # This gate matters more than the others: the PROD ChatConfig authenticates
    # to the API as a superuser service account, so admitting staff here handed
    # any authenticated user a superuser-scoped session and bypassed the
    # project scoping on advanced_search entirely.
    is_admin = bool(getattr(user, "is_superuser", False))
    if not is_admin:
        return settings.NEXTSEEK_CHAT_CONFIG
    prod_config = getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None)
    if prod_config is None:
        return settings.NEXTSEEK_CHAT_CONFIG
    return prod_config


def _granular_args(op: str, req) -> dict:
    """Project a validated request model into the op's chat_nextseek arg dict."""
    if op in ("entity", "parse", "graph"):
        return {"query": req.query}
    if op == "graph-schema":
        return {"types": req.types, "query": req.query}
    if op == "aggregate":
        return {"query": req.query, "parts": req.parts}
    if op == "api-read":
        return {"parser_plan": req.parser_plan}
    if op == "api-write":
        return {"parser_plan": req.parser_plan, "confirmed_write": req.confirmed_write,
                "query": req.query}
    if op == "report":
        return {"mode": req.mode, "project": req.project}
    if op == "generate-submission":
        return {"type": req.type, "uids": req.uids, "query": req.query}
    if op == "run-ls":
        return {"run_dir": req.run_dir}
    if op == "build-upload-xlsx":
        return {"rows": req.rows, "existing_parent_uids": req.existing_parent_uids}
    return {}


def _save_session_or_report(adapter, chat_session, send_event, session_id) -> None:
    """Persist the turn, and TELL THE USER if it could not be persisted.

    This used to be a bare ``adapter.save()`` in a ``finally:`` outside the
    caller's own ``try/except``. When the write failed the exception killed the
    background thread, ``_auto_title_if_unset`` never ran, and the user was left
    with a chat that had streamed a correct answer and then emptied itself on
    reload -- with no error anywhere they could see. A turn that cannot be saved
    is a failed turn and has to look like one.
    """
    try:
        adapter.save()
    except SessionSaveError as exc:
        logger.error("session %s: turn completed but was not saved", session_id)
        if send_event:
            send_event("query_error", {
                "error": (
                    "This answer was not saved to the conversation and will be "
                    "gone if you reload. The result was too large to store."
                ),
                "agent": "session",
                "session_id": session_id,
            })
        return
    except Exception:
        logger.exception("session %s: unexpected failure saving the turn", session_id)
        return
    _auto_title_if_unset(chat_session)


def make_sse_send_event(event_queue, resolved_session_id):
    """The ``query`` (SSE) endpoint's event callback: stamp the session id on
    the terminal events and queue each event for the SSE stream."""
    def send_event(event_type: str, data: dict[str, Any]) -> None:
        if event_type in ("query_complete", "query_error"):
            data.setdefault("session_id", resolved_session_id)
        event_queue.put((event_type, data))

    return send_event


def _error_tracking_send_event(send_event):
    """Wrap ``send_event`` so the caller can tell whether the orchestrator already
    reported the real failure.

    ``run_query`` emits a ``query_error`` carrying the provider's own message and then
    re-raises, and both pipeline bodies below caught that re-raise and emitted a SECOND
    ``query_error`` reading "Internal pipeline error". The generic one arrives last, so
    that is what the user saw: production turns 463/464 lost
    ``503 UNAVAILABLE ... experiencing high demand`` this way, and the review filed it as
    "the user got no answer" with no visible cause. Returns the wrapper and a state dict
    whose ``sent`` flag is True once any ``query_error`` has gone out.
    """
    state = {"sent": False}

    def wrapped(event_type: str, data: dict[str, Any]) -> None:
        if event_type == "query_error":
            state["sent"] = True
        send_event(event_type, data)

    return wrapped, state


def _report_fatal(fatal: LLMFatalError, send_event, error_state, session_id) -> None:
    """End a turn that an ``LLMFatalError`` escaped from with the orchestrator's own query_error.

    ``LLMFatalError`` is a ``BaseException``, so the bodies' ``except Exception`` never saw
    it. ``run_query`` and ``run_query_plan`` answer it themselves, but ``run_pipeline_launch``
    calls the pipeline agent unguarded, and a pipeline tool loop whose models both fail
    raises it: the thread died with no terminal event and the ``QueryTask`` stayed
    running. The event is the one the orchestrator's fatal handlers send
    (``chat_nextseek.failure_replies.fatal_query_error``): the plain text with
    ``reason: model_unavailable`` for unavailability, the raw message otherwise.
    """
    logger.error("Pipeline ended by a fatal model error: %s", fatal)
    if error_state["sent"]:
        return
    _, data = fatal_query_error(fatal, agent=getattr(fatal, "agent", None) or "unknown")
    # What the turn spent before it failed, carried out on the fatal by the entry point
    # (turn_spend.collects_turn): this event is the turn's last, so it holds the cost.
    send_event("query_error", {**data, **turn_spend.cost_fields(fatal), "session_id": session_id})


def _scope_kwargs(graph_scope) -> dict:
    """The orchestrator's ``graph_scope`` keyword, when the ViewSet resolved one.

    ``graph_scope`` is the caller's scope as plain data (``nextseek_api/graph_search/scope.py::plain_scope``,
    resolved in the ViewSet: this package never imports that module). ``None`` means it could not be resolved; the
    keyword is then left out and the turn runs on the request's config, which never carries a scope (the Django
    singletons carry none, a test pins it), so every graph query refuses. Either way ``None`` refuses.
    """
    return {} if graph_scope is None else {"graph_scope": graph_scope}


def run_sse_pipeline(*, adapter, chat_config, req, send_event, api_user, api_pass,
                     chat_session, resolved_session_id, event_queue, graph_scope=None) -> None:
    """Pipeline body of the ``query`` (SSE) endpoint, run on its daemon thread.

    Runs the orchestrator for ``req.mode`` (``plan`` or standard), turns an
    unhandled error into a ``query_error`` event, saves the turn, and always
    ends the stream with the ``None`` sentinel on ``event_queue``.
    ``graph_scope`` is the caller's project scope (``_scope_kwargs``).
    """
    tracked_send_event, error_state = _error_tracking_send_event(send_event)
    scope_kw = _scope_kwargs(graph_scope)
    try:
        match getattr(req, "mode", "standard"):
            case "plan":
                run_query_plan(adapter, chat_config, req.query, tracked_send_event, credentials={"api_user": api_user, "api_pass": api_pass}, **scope_kw)
            case _:
                run_query(adapter, chat_config, req.query, tracked_send_event, credentials={"api_user": api_user, "api_pass": api_pass}, **scope_kw)
    except LLMFatalError as fatal:
        _report_fatal(fatal, send_event, error_state, resolved_session_id)
    except Exception as exc:
        logger.exception("Unhandled pipeline error")
        if not error_state["sent"]:
            send_event("query_error", {
                "error": "Internal pipeline error",
                "agent": "unknown",
                # What the turn spent, taken out on the exception (turn_spend.collects_turn).
                **turn_spend.cost_fields(exc),
                "session_id": resolved_session_id,
            })
    finally:
        _save_session_or_report(
            adapter, chat_session, send_event, resolved_session_id)
        event_queue.put(None)  # sentinel


def run_async_pipeline(*, adapter, chat_config, req, send_event, api_user, api_pass,
                       chat_session, resolved_session_id, graph_scope=None) -> None:
    """Pipeline body of the ``query/async`` endpoint, run on its daemon thread.

    Runs the orchestrator for ``req.mode`` (``plan``, ``pipeline`` or
    standard), turns an unhandled error into a ``query_error`` event, and
    saves the turn. Progress reaches the client only through ``send_event``.
    ``graph_scope`` is the caller's project scope (``_scope_kwargs``).
    """
    tracked_send_event, error_state = _error_tracking_send_event(send_event)
    scope_kw = _scope_kwargs(graph_scope)
    try:
        match getattr(req, "mode", "standard"):
            case "plan":
                run_query_plan(adapter, chat_config, req.query, tracked_send_event, credentials={"api_user": api_user, "api_pass": api_pass}, **scope_kw)
            case "pipeline":
                run_pipeline_launch(adapter, chat_config, req.query, tracked_send_event, credentials={"api_user": api_user, "api_pass": api_pass}, **scope_kw)
            case _:
                run_query(adapter, chat_config, req.query, tracked_send_event, credentials={"api_user": api_user, "api_pass": api_pass}, **scope_kw)
    except LLMFatalError as fatal:
        _report_fatal(fatal, send_event, error_state, resolved_session_id)
    except Exception as exc:
        logger.exception("Unhandled pipeline error (async)")
        if not error_state["sent"]:
            send_event("query_error", {
                "error": "Internal pipeline error",
                "agent": "unknown",
                # What the turn spent, taken out on the exception (turn_spend.collects_turn).
                **turn_spend.cost_fields(exc),
                "session_id": resolved_session_id,
            })
    finally:
        _save_session_or_report(
            adapter, chat_session, send_event, resolved_session_id)
