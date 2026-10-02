"""What a Container-CC turn pass may reach: one row per route name, its methods and its chat check.

TurnPassAuthentication calls ``allowed`` inside ``authenticate``, from ``request.resolver_match.view_name`` (the
namespaced route name, never a raw path) and the method, before any view runs. A route that is not listed is
refused, so a new endpoint stays closed to the pass until someone adds its row here; the URL-conf walk in
nextseek_api/tests/test_turn_pass_routes.py fails on a row that names no route. The turn is always
``request.auth``: nothing here takes a turn id from a body or a path.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from django.db.models import Q
from django.http import QueryDict

GET = frozenset({"GET"})
POST = frozenset({"POST"})

CHAT_CHECKS = (
    "none",                    # user-scoped reads and ops with no chat
    "path_session",            # the path's session_id is this turn's chat
    "task_or_child",           # the path's task_id is this turn's task or a nested turn it started
    "body_session_optional",   # a body session_id, when present, is this turn's chat
    "body_session_required",   # a body session_id is present and is this turn's chat
    "body_session_forbidden",  # no body session_id: these ops keep the parser's throwaway session
)


@dataclass(frozen=True)
class AllowRow:
    route_name: str          # request.resolver_match.view_name, namespaced ("nextseek_api:...")
    methods: frozenset[str]
    chat_check: str


_A = "nextseek_api:assistant-"

ALLOW_TABLE: tuple[AllowRow, ...] = (
    AllowRow("nextseek_api:projects-list", GET, "none"),
    AllowRow("nextseek_api:projects-detail", GET, "none"),
    AllowRow("nextseek_api:assays-list", GET, "none"),
    AllowRow("nextseek_api:assays-detail", GET, "none"),
    AllowRow("nextseek_api:studies-detail", GET, "none"),
    AllowRow("nextseek_api:sample_types-list", GET, "none"),
    AllowRow("nextseek_api:sample_types-detail", GET, "none"),
    AllowRow(_A + "get-session", GET, "path_session"),
    AllowRow(_A + "download-bundle", GET, "path_session"),
    AllowRow(_A + "download-artifact", GET, "path_session"),
    AllowRow(_A + "task-progress", GET, "task_or_child"),
    AllowRow(_A + "report", POST, "body_session_optional"),
    AllowRow(_A + "generate-submission", POST, "body_session_optional"),
    AllowRow(_A + "build-upload-xlsx", POST, "body_session_optional"),
    AllowRow(_A + "entity", POST, "body_session_forbidden"),
    AllowRow(_A + "parse", POST, "body_session_forbidden"),
    AllowRow(_A + "graph", POST, "body_session_forbidden"),
    AllowRow(_A + "aggregate", POST, "body_session_forbidden"),
    AllowRow(_A + "graph-schema", POST, "none"),
    AllowRow(_A + "api-read", POST, "none"),
    AllowRow(_A + "run-ls", POST, "none"),
    AllowRow(_A + "query-async", POST, "body_session_required"),
    AllowRow("nextseek_api:samples-advanced-search-list", POST, "none"),
    AllowRow("nextseek_api:batch-upload-validate", POST, "none"),
)

#: Settings a pass may never send (operator ruling 2026-09-28: a pass drops the admin-only switches).
REFUSED_SETTINGS = ("use_prod", "force_new", "force_route", "max_turn_length_s", "force_parser_mode", "prompt_variant")
#: The nested turns a pass may start: query, plan and pipeline.
QUERY_MODES = frozenset({"standard", "plan", "pipeline"})

# Refusal reasons, a closed list. REASON_SESSION_NOT_ACCEPTED is answered as VALIDATION (422); every other reason
# is PASS_NOT_ALLOWED (403).
REASON_ROUTE = "route_not_allowed"
REASON_METHOD = "method_not_allowed"
REASON_BODY = "body_not_an_object"
REASON_SETTING = "setting_not_allowed"
REASON_MODE = "mode_not_allowed"
REASON_OTHER_CHAT = "other_chat"
REASON_OTHER_TASK = "not_this_turns_task"
REASON_SESSION_REQUIRED = "session_required"
REASON_SESSION_NOT_ACCEPTED = "session_not_accepted"

_ROWS = {row.route_name: row for row in ALLOW_TABLE}
# Every assistant POST row reads the body: the op request models all carry use_prod, and query/async carries the
# rest of the admin-only settings.
_BODY_ROUTES = frozenset(row.route_name for row in ALLOW_TABLE if "POST" in row.methods)
# The assistant client only posts JSON: a form body is refused there. batch-upload-validate is multipart by design,
# and advanced-search is JSON-only through its own model, so neither gets the form refusal.
_JSON_ONLY = frozenset(name for name in _BODY_ROUTES if name.startswith(_A))
_UNSET = (None, False, "", "false", "False", "0")


def _is_set(value) -> bool:
    try:
        return value not in _UNSET
    except TypeError:  # an unhashable, uncomparable value is set by definition
        return True


def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _same_chat(value, turn) -> bool:
    parsed = _as_uuid(value) if value not in (None, "") else None
    return parsed is not None and parsed == turn.chat_id


def _task_of_turn(value, turn) -> bool:
    from nextseek_api.assistant.models_db import QueryTask

    parsed = _as_uuid(value) if value not in (None, "") else None
    if parsed is None:
        return False
    return QueryTask.objects.filter(task_id=parsed).filter(
        Q(pk=turn.task_id) | Q(parent_cc_turn_id=turn.pk)
    ).exists()


def allowed(request, turn) -> tuple[bool, str]:
    """(True, "") when ``turn``'s pass may make this request, else (False, one of the REASON_* values)."""
    match = getattr(request, "resolver_match", None)
    row = _ROWS.get(getattr(match, "view_name", None) or "")
    if row is None:
        return False, REASON_ROUTE
    if str(request.method or "").upper() not in row.methods:
        return False, REASON_METHOD
    body = None
    if row.route_name in _BODY_ROUTES:
        body = request.data
        if not isinstance(body, Mapping) or (row.route_name in _JSON_ONLY and isinstance(body, QueryDict)):
            return False, REASON_BODY
        if any(_is_set(body.get(key)) for key in REFUSED_SETTINGS):
            return False, REASON_SETTING
    kwargs = getattr(match, "kwargs", None) or {}
    check = row.chat_check
    if check == "none":
        return True, ""
    if check == "path_session":
        return (True, "") if _same_chat(kwargs.get("session_id"), turn) else (False, REASON_OTHER_CHAT)
    if check == "task_or_child":
        return (True, "") if _task_of_turn(kwargs.get("task_id"), turn) else (False, REASON_OTHER_TASK)
    session_id = body.get("session_id") if body is not None else None
    if check == "body_session_forbidden":
        return (False, REASON_SESSION_NOT_ACCEPTED) if _is_set(session_id) else (True, "")
    if check == "body_session_optional":
        if not _is_set(session_id):
            return True, ""
        return (True, "") if _same_chat(session_id, turn) else (False, REASON_OTHER_CHAT)
    if check == "body_session_required":
        if not _is_set(session_id):
            return False, REASON_SESSION_REQUIRED
        if not _same_chat(session_id, turn):
            return False, REASON_OTHER_CHAT
        if body.get("mode") not in QUERY_MODES:
            return False, REASON_MODE
        return True, ""
    return False, REASON_ROUTE  # a check this module does not know never allows
