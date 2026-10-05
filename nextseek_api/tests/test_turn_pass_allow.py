"""The allow table and its chat checks, called the way TurnPassAuthentication calls them (piece 1)."""
import uuid
from types import SimpleNamespace

import pytest
from django.http import QueryDict

from nextseek_api.assistant import turn_pass_allow as allow
from nextseek_api.assistant.models_db import QueryTask
from nextseek_api.tests.turn_pass_support import make_turn, make_user

pytestmark = pytest.mark.django_db

OPS = "nextseek_api:assistant-"


def _req(route, method="POST", data=None, **kwargs):
    return SimpleNamespace(
        resolver_match=SimpleNamespace(view_name=route, kwargs=kwargs),
        method=method,
        data={} if data is None else data,
    )


def test_the_table_lists_exactly_the_approved_routes():
    names = [row.route_name for row in allow.ALLOW_TABLE]
    assert len(names) == len(set(names)), "one row per route"
    assert set(names) == {
        "nextseek_api:projects-list", "nextseek_api:projects-detail",
        "nextseek_api:assays-list", "nextseek_api:assays-detail", "nextseek_api:studies-detail",
        "nextseek_api:sample_types-list", "nextseek_api:sample_types-detail",
        OPS + "get-session", OPS + "download-bundle", OPS + "download-artifact", OPS + "task-progress",
        OPS + "report", OPS + "generate-submission", OPS + "build-upload-xlsx",
        OPS + "entity", OPS + "parse", OPS + "graph", OPS + "aggregate",
        OPS + "graph-schema", OPS + "api-read", OPS + "run-ls",
        OPS + "query-async",
        "nextseek_api:samples-advanced-search-list", "nextseek_api:batch-upload-validate",
    }
    for refused in (OPS + "api-write", OPS + "query", OPS + "me", OPS + "list-sessions",
                    "nextseek_api:cc-assistant-cc-query-async", "nextseek_api:cc-assistant-query-async",
                    "nextseek_api:people-current", "nextseek_api:samples-graph-search-list",
                    "nextseek_api:studies-list"):
        assert refused not in names


def test_each_route_carries_its_ruled_chat_check_and_methods():
    rows = {row.route_name: row for row in allow.ALLOW_TABLE}
    assert {row.chat_check for row in allow.ALLOW_TABLE} <= set(allow.CHAT_CHECKS)
    for op in ("report", "generate-submission", "build-upload-xlsx"):
        assert rows[OPS + op].chat_check == "body_session_optional"
    for op in ("entity", "parse", "graph", "aggregate"):
        assert rows[OPS + op].chat_check == "body_session_forbidden"
    for op in ("graph-schema", "api-read", "run-ls"):
        assert rows[OPS + op].chat_check == "none"
    assert rows[OPS + "query-async"].chat_check == "body_session_required"
    assert rows[OPS + "task-progress"].chat_check == "task_or_child"
    for route in ("get-session", "download-bundle", "download-artifact"):
        assert rows[OPS + route].chat_check == "path_session"
    get_routes = {
        "nextseek_api:projects-list", "nextseek_api:projects-detail", "nextseek_api:assays-list",
        "nextseek_api:assays-detail", "nextseek_api:studies-detail", "nextseek_api:sample_types-list",
        "nextseek_api:sample_types-detail",
        OPS + "get-session", OPS + "download-bundle", OPS + "download-artifact", OPS + "task-progress",
    }
    for row in allow.ALLOW_TABLE:
        assert row.methods == (allow.GET if row.route_name in get_routes else allow.POST), row.route_name


def test_an_unlisted_route_or_a_missing_match_is_refused():
    turn, _ = make_turn()
    assert allow.allowed(_req(OPS + "api-write"), turn) == (False, allow.REASON_ROUTE)
    assert allow.allowed(SimpleNamespace(resolver_match=None, method="GET", data={}), turn) == (
        False, allow.REASON_ROUTE)


def test_a_listed_route_with_another_method_is_refused():
    turn, _ = make_turn()
    assert allow.allowed(_req(OPS + "report", "GET"), turn) == (False, allow.REASON_METHOD)
    assert allow.allowed(_req(OPS + "get-session", "PATCH", session_id=str(turn.chat_id)), turn) == (
        False, allow.REASON_METHOD)
    assert allow.allowed(_req(OPS + "get-session", "DELETE", session_id=str(turn.chat_id)), turn) == (
        False, allow.REASON_METHOD)


def test_the_path_session_must_be_this_turns_chat():
    turn, _ = make_turn()
    route = OPS + "get-session"
    assert allow.allowed(_req(route, "GET", session_id=str(turn.chat_id)), turn) == (True, "")
    assert allow.allowed(_req(route, "GET", session_id=str(turn.chat_id).upper()), turn) == (True, "")
    for other in (str(uuid.uuid4()), "not-a-uuid", "", None):
        assert allow.allowed(_req(route, "GET", session_id=other), turn) == (False, allow.REASON_OTHER_CHAT)


def test_the_progress_check_takes_this_turns_task_or_its_children_only():
    turn, _ = make_turn()
    child = QueryTask.objects.create(session=turn.chat, user=turn.user, query="n", parent_cc_turn=turn)
    same_user_other_turn, _ = make_turn(turn.user)
    stranger, _ = make_turn(make_user("stranger"))
    route = OPS + "task-progress"
    assert allow.allowed(_req(route, "GET", task_id=str(turn.task.task_id)), turn) == (True, "")
    assert allow.allowed(_req(route, "GET", task_id=str(child.task_id)), turn) == (True, "")
    for other in (str(same_user_other_turn.task.task_id), str(stranger.task.task_id), "zz", None):
        assert allow.allowed(_req(route, "GET", task_id=other), turn) == (False, allow.REASON_OTHER_TASK)


def test_the_throwaway_session_ops_refuse_a_body_session():
    turn, _ = make_turn()
    for op in ("entity", "parse", "graph", "aggregate"):
        assert allow.allowed(_req(OPS + op, data={"query": "q"}), turn) == (True, "")
        assert allow.allowed(_req(OPS + op, data={"query": "q", "session_id": None}), turn) == (True, "")
        assert allow.allowed(_req(OPS + op, data={"query": "q", "session_id": str(turn.chat_id)}), turn) == (
            False, allow.REASON_SESSION_NOT_ACCEPTED)


def test_the_artifact_ops_take_no_session_or_this_turns_chat():
    turn, _ = make_turn()
    for op in ("report", "generate-submission", "build-upload-xlsx"):
        assert allow.allowed(_req(OPS + op, data={}), turn) == (True, "")
        assert allow.allowed(_req(OPS + op, data={"session_id": str(turn.chat_id)}), turn) == (True, "")
        assert allow.allowed(_req(OPS + op, data={"session_id": str(uuid.uuid4())}), turn) == (
            False, allow.REASON_OTHER_CHAT)


def test_a_nested_turn_must_name_this_chat_and_a_known_mode():
    turn, _ = make_turn()
    route = OPS + "query-async"
    chat = str(turn.chat_id)
    for mode in ("standard", "plan", "pipeline"):
        assert allow.allowed(_req(route, data={"query": "q", "mode": mode, "session_id": chat}), turn) == (True, "")
    assert allow.allowed(_req(route, data={"query": "q", "mode": "standard"}), turn) == (
        False, allow.REASON_SESSION_REQUIRED)
    assert allow.allowed(_req(route, data={"query": "q", "mode": "standard", "session_id": str(uuid.uuid4())}),
                         turn) == (False, allow.REASON_OTHER_CHAT)
    assert allow.allowed(_req(route, data={"query": "q", "mode": "wizard", "session_id": chat}), turn) == (
        False, allow.REASON_MODE)
    assert allow.allowed(_req(route, data={"query": "q", "session_id": chat}), turn) == (False, allow.REASON_MODE)


@pytest.mark.parametrize("setting, value", [
    ("use_prod", True), ("force_new", True), ("force_route", "cc"), ("force_route", "auto"),
    ("max_turn_length_s", 600), ("force_parser_mode", "graph"), ("prompt_variant", "v2_apoc"),
])
def test_a_body_that_sets_an_admin_setting_is_refused_on_every_body_route(setting, value):
    turn, _ = make_turn()
    body_routes = [row.route_name for row in allow.ALLOW_TABLE
                   if row.route_name.startswith(OPS) and "POST" in row.methods]
    assert len(body_routes) == 11
    for route in body_routes:
        body = {"query": "q", "mode": "standard", "session_id": str(turn.chat_id), setting: value}
        assert allow.allowed(_req(route, data=body), turn) == (False, allow.REASON_SETTING), route


def test_an_unset_setting_is_not_a_refusal():
    turn, _ = make_turn()
    body = {"query": "q", "use_prod": False, "force_new": False, "force_route": None, "prompt_variant": None}
    assert allow.allowed(_req(OPS + "graph", data=body), turn) == (True, "")


def test_a_body_that_is_not_an_object_is_refused():
    turn, _ = make_turn()
    for body in (["query", "q"], "query=q", 7):
        assert allow.allowed(_req(OPS + "graph", data=body), turn) == (False, allow.REASON_BODY)


def test_a_form_body_is_refused_on_the_assistant_routes():
    turn, _ = make_turn()
    for body in (QueryDict("query=q&use_prod=true"), QueryDict("query=q&use_prod=false")):
        assert allow.allowed(_req(OPS + "graph", data=body), turn) == (False, allow.REASON_BODY)


def test_a_multipart_body_to_batch_upload_validate_is_allowed_but_still_setting_checked():
    turn, _ = make_turn()
    route = "nextseek_api:batch-upload-validate"
    assert allow.allowed(_req(route, data=QueryDict("project=1")), turn) == (True, "")
    assert allow.allowed(_req(route, data=QueryDict("use_prod=true")), turn) == (False, allow.REASON_SETTING)


@pytest.mark.parametrize("route", ["nextseek_api:samples-advanced-search-list", "nextseek_api:batch-upload-validate"])
@pytest.mark.parametrize("setting, value", [("use_prod", True), ("force_route", "cc"), ("prompt_variant", "v2")])
def test_the_non_assistant_post_rows_refuse_settings_too(route, setting, value):
    turn, _ = make_turn()
    assert allow.allowed(_req(route, data={setting: value}), turn) == (False, allow.REASON_SETTING)


def test_head_and_options_on_a_get_row_are_refused():
    turn, _ = make_turn()
    for method in ("HEAD", "OPTIONS"):
        assert allow.allowed(_req(OPS + "get-session", method, session_id=str(turn.chat_id)), turn) == (
            False, allow.REASON_METHOD)


def test_a_child_task_of_another_turn_is_refused_on_progress():
    turn, _ = make_turn()
    other, _ = make_turn(turn.user)
    child = QueryTask.objects.create(session=other.chat, user=other.user, query="n", parent_cc_turn=other)
    assert allow.allowed(_req(OPS + "task-progress", "GET", task_id=str(child.task_id)), turn) == (
        False, allow.REASON_OTHER_TASK)
