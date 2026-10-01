"""Hermetic tests for graph_search's scope resolver.

The resolver reads SEEK membership from MySQL through Django's ``seek`` connection; every
test patches that connection, so no database is touched.
"""

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.graph_search.scope import Scope, ScopeUnavailable, resolve_scope


def _user(username="someone", *, is_superuser=False, is_staff=False):
    user = MagicMock()
    user.username = username
    user.is_superuser = is_superuser
    user.is_staff = is_staff
    return user


def _cursor(person_row, project_rows=()):
    cursor = MagicMock()
    cursor.fetchone.return_value = person_row
    cursor.fetchall.return_value = list(project_rows)
    return cursor


def _wire(mock_connections, cursor):
    mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor


def _sql_calls(cursor):
    return [(" ".join(c.args[0].split()), list(c.args[1])) for c in cursor.execute.call_args_list]


@patch("nextseek_api.graph_search.scope.connections")
def test_superuser_is_admin_and_runs_no_sql(mock_connections):
    scope = resolve_scope(_user("demo", is_superuser=True))

    assert scope == Scope(is_admin=True, person_id=None, project_ids=())
    mock_connections.__getitem__.assert_not_called()


@patch("nextseek_api.graph_search.scope.connections")
def test_is_staff_alone_is_not_admin(mock_connections):
    cursor = _cursor((144,), [(2,)])
    _wire(mock_connections, cursor)

    scope = resolve_scope(_user("user", is_staff=True))

    assert scope == Scope(is_admin=False, person_id=144, project_ids=(2,))
    assert cursor.execute.call_count == 2


@patch("nextseek_api.graph_search.scope.connections")
def test_known_login_returns_sorted_project_ids(mock_connections):
    cursor = _cursor((145,), [(16,), (2,), (7,)])
    _wire(mock_connections, cursor)

    scope = resolve_scope(_user("tcgamember"))

    assert scope == Scope(is_admin=False, person_id=145, project_ids=(2, 7, 16))
    assert isinstance(scope.project_ids, tuple)
    mock_connections.__getitem__.assert_called_with("seek")
    login_sql, project_sql = _sql_calls(cursor)
    assert login_sql == ("SELECT person_id FROM users WHERE login = %s", ["tcgamember"])
    assert project_sql == (
        "SELECT DISTINCT wg.project_id FROM group_memberships gm "
        "JOIN work_groups wg ON wg.id = gm.work_group_id "
        "WHERE gm.person_id = %s ORDER BY wg.project_id",
        [145],
    )


@patch("nextseek_api.graph_search.scope.connections")
def test_former_members_are_not_filtered_out(mock_connections):
    cursor = _cursor((145,), [(3,)])
    _wire(mock_connections, cursor)

    resolve_scope(_user("tcgamember"))

    _, (project_sql, _params) = _sql_calls(cursor)
    assert "has_left" not in project_sql
    assert "time_left_at" not in project_sql


@patch("nextseek_api.graph_search.scope.connections")
def test_unknown_login_raises(mock_connections):
    cursor = _cursor(None)
    _wire(mock_connections, cursor)

    with pytest.raises(ScopeUnavailable):
        resolve_scope(_user("nobody"))

    assert cursor.execute.call_count == 1


@patch("nextseek_api.graph_search.scope.connections")
def test_login_with_null_person_raises(mock_connections):
    cursor = _cursor((None,))
    _wire(mock_connections, cursor)

    with pytest.raises(ScopeUnavailable):
        resolve_scope(_user("orphan-login"))

    assert cursor.execute.call_count == 1


@patch("nextseek_api.graph_search.scope.connections")
def test_empty_username_raises_without_sql(mock_connections):
    with pytest.raises(ScopeUnavailable):
        resolve_scope(_user(""))

    mock_connections.__getitem__.assert_not_called()


@patch("nextseek_api.graph_search.scope.connections")
def test_person_with_no_membership_gets_an_empty_tuple(mock_connections):
    cursor = _cursor((144,), [])
    _wire(mock_connections, cursor)

    scope = resolve_scope(_user("user"))

    assert scope == Scope(is_admin=False, person_id=144, project_ids=())


def test_scope_is_frozen():
    scope = Scope(is_admin=False, person_id=1, project_ids=(2,))

    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.is_admin = True
