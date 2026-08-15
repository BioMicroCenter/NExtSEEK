"""Tests for startup.steps.users."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from startup.steps.users import (
    REQUIRED_USERS,
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
