"""Tests for startup.steps.users."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from startup.lib.docker_ops import DockerOpsError
from startup.steps.users import (
    REQUIRED_USERS,
    set_random_passwords,
    user_exists,
    verify_users_present,
)


def test_required_users_includes_demo_and_user() -> None:
    logins = [u.login for u in REQUIRED_USERS]
    assert "demo" in logins
    assert "user" in logins


def test_required_users_has_two_entries() -> None:
    assert len(REQUIRED_USERS) == 2


@patch("startup.steps.users.compose_exec")
def test_user_exists_true_when_query_returns_one(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "1\n"
    assert user_exists("demo", repo_root=Path("/repo"), env={}) is True


@patch("startup.steps.users.compose_exec")
def test_user_exists_false_when_query_returns_zero(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "0\n"
    assert user_exists("ghost", repo_root=Path("/repo"), env={}) is False


@patch("startup.steps.users.user_exists")
def test_verify_users_present_returns_missing(mock_exists: MagicMock) -> None:
    mock_exists.side_effect = [True, False]
    missing = verify_users_present(repo_root=Path("/repo"), env={})
    assert missing == ["user"]


@patch("startup.steps.users.user_exists")
def test_verify_users_present_returns_empty_when_all_present(mock_exists: MagicMock) -> None:
    mock_exists.return_value = True
    missing = verify_users_present(repo_root=Path("/repo"), env={})
    assert missing == []


@patch("startup.steps.users.compose_exec")
def test_set_random_passwords_sends_them_on_stdin_only(mock_exec: MagicMock) -> None:
    mock_exec.return_value = ""
    out = set_random_passwords(Path("/repo"), {"MYSQL_ROOT_PASSWORD": "rootpw"}, {})
    assert set(out) == {"demo", "user"}
    assert all(len(p) >= 24 for p in out.values())
    calls = mock_exec.call_args_list
    seek = [c for c in calls if c.kwargs["service"] == "seek"]
    db = [c for c in calls if c.kwargs["service"] == "db"]
    assert len(seek) == 1 and len(db) == 1
    stdin = seek[0].kwargs["stdin"].decode()
    for login, pw in out.items():
        assert f"{login} {pw}" in stdin
        for c in calls:
            assert pw not in " ".join(c.kwargs["command"])
    sql = " ".join(db[0].kwargs["command"])
    assert "password='!'" in sql and "'demo'" in sql and "'user'" in sql
    assert "dmac.auth_user" in sql


@patch("startup.steps.users.compose_exec")
def test_set_random_passwords_fails_soft(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = DockerOpsError("boom")
    assert set_random_passwords(Path("/repo"), {"MYSQL_ROOT_PASSWORD": "rootpw"}, {}) is None


@patch("startup.steps.users.compose_exec")
def test_django_side_runs_first_and_a_failure_leaves_seek_untouched(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = DockerOpsError("sql failed")
    assert set_random_passwords(Path("/repo"), {"MYSQL_ROOT_PASSWORD": "rootpw"}, {}) is None
    assert [c.kwargs["service"] for c in mock_exec.call_args_list] == ["db"]


@patch("startup.steps.users.compose_exec")
def test_seek_changes_are_one_transaction_and_its_failure_returns_none(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = [None, DockerOpsError("rails failed")]
    assert set_random_passwords(Path("/repo"), {"MYSQL_ROOT_PASSWORD": "rootpw"}, {}) is None
    calls = mock_exec.call_args_list
    assert [c.kwargs["service"] for c in calls] == ["db", "seek"]
    assert "User.transaction" in " ".join(calls[1].kwargs["command"])
