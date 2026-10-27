"""A member who has left a project loses read scope for it.

SEEK records leaving on the ``group_memberships`` row: ``time_left_at`` (stored in UTC) is when the person leaves, and
SEEK's leaving job sets ``has_left`` once that time has passed; ``GroupMembership#has_left`` reads either. A membership
counts while neither says the person has left, so one whose ``time_left_at`` is still ahead counts until then.

Two sources compute a caller's project scope, and both apply the rule: the MySQL read behind ``resolve_scope``,
``plain_scope`` and ``caller_block`` (``nextseek_api.graph_search.scope``), and ``SeekDB.getCurrentUser``, whose
``relationships.projects`` (SEEK's REST ``Person#projects``, which keeps former projects) every REST-based resolver
reads. These tests run the real membership statement on SQLite.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import connections
from django.test import TestCase, override_settings

from nextseek_api.graph_search.scope import caller_block, plain_scope, resolve_scope

PERSON = 5
CURRENT, LEFT_FLAGGED, LEFT_NOT_YET_FLAGGED, LEAVING_LATER, FLAG_NULL = 2, 3, 4, 6, 7
STILL_IN = [CURRENT, LEAVING_LATER, FLAG_NULL]


def _utc(delta):
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%d %H:%M:%S")


def _rest_answer(project_ids):
    """SEEK's /people/current: every project the person ever joined."""
    return {"data": {"id": str(PERSON), "type": "people",
                     "relationships": {"projects": {"data": [{"id": str(p), "type": "projects"}
                                                             for p in project_ids]}}}}


ALL_EVER = [CURRENT, LEFT_FLAGGED, LEFT_NOT_YET_FLAGGED, LEAVING_LATER, FLAG_NULL]


@override_settings(SEEK_DATABASE="default")  # the SEEK tables below live in the test database
class ScopeEndsOnLeaving(TestCase):
    databases = {"default"}

    def setUp(self):
        past, future = _utc(-timedelta(days=1)), _utc(timedelta(days=1))
        rows = [  # (project, has_left, time_left_at)
            (CURRENT, 0, None),
            (LEFT_FLAGGED, 1, past),
            (LEFT_NOT_YET_FLAGGED, 0, past),
            (LEAVING_LATER, 0, future),
            (FLAG_NULL, None, None),
        ]
        with connections["default"].cursor() as c:
            for table in ("users", "group_memberships", "work_groups", "projects"):
                c.execute(f"DROP TABLE IF EXISTS {table}")
            c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, person_id INTEGER)")
            c.execute("CREATE TABLE work_groups (id INTEGER PRIMARY KEY, project_id INTEGER)")
            c.execute("CREATE TABLE group_memberships (id INTEGER PRIMARY KEY, person_id INTEGER, "
                      "work_group_id INTEGER, has_left INTEGER, time_left_at DATETIME)")
            c.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT)")
            c.execute("INSERT INTO users (login, person_id) VALUES (%s, %s)", ["leaver", PERSON])
            for wg, (project, has_left, left_at) in enumerate(rows, start=1):
                c.execute("INSERT INTO work_groups (id, project_id) VALUES (%s, %s)", [wg, project])
                c.execute("INSERT INTO group_memberships (person_id, work_group_id, has_left, time_left_at) "
                          "VALUES (%s, %s, %s, %s)", [PERSON, wg, has_left, left_at])
                c.execute("INSERT INTO projects (id, title) VALUES (%s, %s)", [project, f"P{project}"])

    def _user(self):
        return SimpleNamespace(username="leaver", is_superuser=False)

    def test_the_membership_read_drops_projects_the_person_has_left(self):
        self.assertEqual(resolve_scope(self._user()).project_ids, tuple(STILL_IN))
        self.assertEqual(plain_scope(self._user()), {"is_admin": False, "project_ids": STILL_IN})
        from nextseek_api.graph_search.scope import current_project_ids
        self.assertEqual(current_project_ids(PERSON), tuple(STILL_IN))

    def test_the_caller_block_lists_only_projects_still_held(self):
        block = caller_block(self._user())
        self.assertEqual([p["id"] for p in block["projects"]], STILL_IN)
        self.assertEqual(block["project_count"], len(STILL_IN))

    def test_seeks_current_person_answer_keeps_only_projects_still_held(self):
        from seek.seekdb import SeekDB
        db = SeekDB(None, None, None)
        db._SeekDB__seekapi = MagicMock(getCurrentUser=MagicMock(return_value=_rest_answer(ALL_EVER)))
        refs = db.getCurrentUser()["data"]["relationships"]["projects"]["data"]
        self.assertEqual([int(r["id"]) for r in refs], STILL_IN)

    def test_a_project_page_refuses_a_project_the_caller_has_left(self):
        from seek.seekdb import SeekDB
        from seek.views.projects import _may_see_project
        request = SimpleNamespace(seekdb=SeekDB(None, None, None))
        request.seekdb._SeekDB__seekapi = MagicMock(getCurrentUser=MagicMock(return_value=_rest_answer(ALL_EVER)))
        with patch("seek.views.projects.verifySuperUser", return_value=0):
            self.assertTrue(_may_see_project(request, CURRENT))
            self.assertFalse(_may_see_project(request, LEFT_FLAGGED))
            self.assertFalse(_may_see_project(request, LEFT_NOT_YET_FLAGGED))

    def test_the_home_page_lists_only_projects_still_held(self):
        from dmac.views import _home_projects
        projects = MagicMock()
        projects.objects.filter.return_value.order_by.return_value.values.return_value = []
        with patch("seek.decorators.verifySuperUser", return_value=0), \
                patch("seek.seekdb.SeekDB.getSeekLogin", return_value={"status": True}), \
                patch("seek.seekapi.SeekAPI.getCurrentUser", return_value=_rest_answer(ALL_EVER)), \
                patch("seek.models.Projects", projects):
            _home_projects(SimpleNamespace())
        self.assertEqual(projects.objects.filter.call_args.kwargs["id__in"], STILL_IN)

    def test_a_superuser_stays_unscoped(self):
        scope = resolve_scope(SimpleNamespace(username="leaver", is_superuser=True))
        self.assertTrue(scope.is_admin)

    def _gate(self, participating, is_superuser=False):
        """The assistant pilot gate's answer for a caller SEEK REST lists in every project they ever joined."""
        from django.core.cache import cache
        from nextseek_api.services.assistant import UserInParticipatingProject
        cache.clear()  # the gate caches a positive answer per user id
        user = SimpleNamespace(pk=PERSON, is_authenticated=True, is_superuser=is_superuser)
        client = MagicMock()
        client.get_current_person.return_value = (json.dumps(_rest_answer(ALL_EVER)),)
        with patch("nextseek_api.services.assistant.SeekAPIClient", return_value=client), \
                patch("nextseek_api.services.assistant.ASSISTANT_PARTICIPATING_PROJECTS", {str(p) for p in participating}):
            return UserInParticipatingProject().has_permission(SimpleNamespace(user=user), None)

    def test_the_assistant_pilot_gate_counts_only_projects_still_held(self):
        self.assertFalse(self._gate([LEFT_FLAGGED, LEFT_NOT_YET_FLAGGED]))
        self.assertTrue(self._gate([CURRENT]))
        self.assertTrue(self._gate([LEAVING_LATER]))
        self.assertTrue(self._gate([LEFT_FLAGGED, FLAG_NULL]))

    def test_the_assistant_pilot_gate_is_unchanged_for_a_superuser(self):
        self.assertTrue(self._gate([LEFT_FLAGGED], is_superuser=True))

    def test_the_assistant_pilot_gate_refuses_when_the_membership_read_fails(self):
        with patch("nextseek_api.services.assistant.current_project_ids", side_effect=RuntimeError("db down")):
            self.assertFalse(self._gate([CURRENT]))

    def _client_for(self, participating):
        """A signed-in caller SEEK REST lists in every project they ever joined, with ``participating`` as the pilot."""
        from django.contrib.auth.models import User
        from django.core.cache import cache
        from rest_framework.test import APIClient
        from nextseek_api.assistant.models_db import ChatSession
        cache.clear()
        user = User.objects.create_user("leaver", password="p")
        session = ChatSession.objects.create(user=user)
        seek = MagicMock()
        seek.get_current_person.return_value = (json.dumps(_rest_answer(ALL_EVER)),)
        for p in (patch("nextseek_api.services.assistant.SeekAPIClient", return_value=seek),
                  patch("nextseek_api.services.assistant.ASSISTANT_PARTICIPATING_PROJECTS",
                        {str(p) for p in participating}),
                  patch("nextseek_api.services.assistant.resolve_seek_auth", return_value=(("leaver", "p"), {}))):
            p.start()
            self.addCleanup(p.stop)
        client = APIClient()
        client.force_authenticate(user=user)
        return client, f"/nextseek_api/assistant/sessions/{session.session_id}/"

    def _answers(self, client, session_url):
        return {
            "list": client.get("/nextseek_api/assistant/sessions/").status_code,
            "search": client.get("/nextseek_api/assistant/sessions/search/?q=ndma").status_code,
            "open": client.get(session_url).status_code,
            "who am I": client.get("/nextseek_api/assistant/me/").status_code,
            "rename": client.patch(session_url, {"title": "x"}, format="json").status_code,
            "delete": client.delete(session_url).status_code,
            "new chat": client.post("/nextseek_api/assistant/sessions/", {}, format="json").status_code,
            "ask": client.post("/nextseek_api/assistant/query/", {"query": "hi"}, format="json").status_code,
        }

    def test_a_pilot_leaver_still_reads_their_own_chats_and_starts_nothing_new(self):
        answers = self._answers(*self._client_for([LEFT_FLAGGED, LEFT_NOT_YET_FLAGGED]))
        self.assertEqual(answers, {"list": 200, "search": 200, "open": 200, "who am I": 403, "rename": 403, "delete": 403, "new chat": 403,
                                   "ask": 403})

    def test_every_read_a_pilot_leaver_keeps_is_a_get_only_action(self):
        """A misspelt or renamed name here would refuse a leaver that read with every other test green."""
        from collections import defaultdict
        from nextseek_api.services.assistant import AssistantViewSet, UserInParticipatingProject
        methods = defaultdict(set)
        for action in AssistantViewSet.get_extra_actions():
            for method, name in action.mapping.items():
                methods[name].add(method)
        for name in UserInParticipatingProject._OWN_CHAT_READS:
            self.assertEqual(methods[name], {"get"}, name)

    def test_a_caller_never_in_a_pilot_project_is_refused_everything(self):
        answers = self._answers(*self._client_for([99]))
        self.assertEqual(set(answers.values()), {403}, answers)
