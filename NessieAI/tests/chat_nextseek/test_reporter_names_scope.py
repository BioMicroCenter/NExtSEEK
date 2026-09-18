"""
The reporter's own text names only projects and investigations the caller may see.

Two messages list names from SEEK: the error for a project name the reporter cannot resolve
(``helpers/dates.py::_normalize_project_id``) and the note under a lab-scoped report
(``reports/runners.py::reporter_reply_footer``). Both are read from tables that hold every project and investigation.
An admin sees the whole list as before; anyone else sees only their own projects' names in the error, and no
investigation title in the note, because the config does not record which investigations a caller's projects hold.
With no scope on the config, neither message names anything.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 8.
"""
from __future__ import annotations

import pytest

from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers.dates import _normalize_project_id
from chat_nextseek.reports.runners import reporter_reply_footer

PROJECTS = {"PROJECT ONE": 1, "PROJECT TWO": 2, "PUBLISHED DATA": 2, "PROJECT THREE": 3}
INVESTIGATIONS = {"INVESTIGATION ONE": 11, "INVESTIGATION TWO": 12, "INVESTIGATION THREE": 13}


class _Cfg:
    PROJECT_NAME_TO_ID = PROJECTS
    INVESTIGATION_NAME_TO_ID = INVESTIGATIONS

    def __init__(self, scope):
        if scope is not None:
            self.GRAPH_SCOPE = scope


def _unknown_project_message(scope) -> str:
    with pytest.raises(ValueError) as err:
        _normalize_project_id(_Cfg(scope), "Nonexistent programme")
    return str(err.value)


def _lab_footer(scope) -> str:
    result = {"ok": True, "rows_returned": 3, "scope": {"kind": "lab", "lab_codes": ["BBB"]}}
    return "\n".join(reporter_reply_footer(_Cfg(scope), result, {}, "samples"))


def test_an_admin_is_shown_every_project_name():
    message = _unknown_project_message(GraphScope.admin("test"))
    for name in PROJECTS:
        assert name in message


def test_a_member_is_shown_only_their_own_projects_names():
    message = _unknown_project_message(GraphScope.for_projects([2], source="test"))
    assert "PROJECT TWO" in message and "PUBLISHED DATA" in message
    assert "PROJECT ONE" not in message and "PROJECT THREE" not in message
    assert "Nonexistent programme" in message


@pytest.mark.parametrize("scope", [GraphScope.for_projects([], source="test"), None], ids=["no-projects", "no-scope"])
def test_a_caller_who_sees_no_project_is_shown_no_project_name(scope):
    message = _unknown_project_message(scope)
    for name in PROJECTS:
        assert name not in message
    assert "numeric project_id" in message


def test_the_lab_note_names_every_investigation_for_an_admin():
    footer = _lab_footer(GraphScope.admin("test"))
    assert "lab BBB, not a project" in footer
    for name in INVESTIGATIONS:
        assert name in footer


@pytest.mark.parametrize("scope", [GraphScope.for_projects([2], source="test"), GraphScope.for_projects([], source="test"),
                                   None], ids=["member", "no-projects", "no-scope"])
def test_the_lab_note_names_no_investigation_for_anyone_else(scope):
    footer = _lab_footer(scope)
    assert "lab BBB, not a project" in footer
    for name in INVESTIGATIONS:
        assert name not in footer
