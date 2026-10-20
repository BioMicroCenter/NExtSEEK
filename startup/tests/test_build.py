"""Tests for startup.steps.build orchestration."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from startup.steps import build


@patch("startup.steps.build.compose_up")
@patch("startup.steps.build.compose_build")
def test_start_cc_stack_builds_agent_and_starts_long_running_services(
    mock_build: MagicMock,
    mock_up: MagicMock,
) -> None:
    env = {"COMPOSE_PROJECT_NAME": "nextseek-test"}

    build.start_cc_stack(Path("/repo"), env)

    mock_build.assert_called_once_with(
        services=["cc-agent"], project_dir=Path("/repo"), env=env
    )
    assert mock_up.call_args_list[0].kwargs == {
        "services": ["bedrock-proxy", "nextseek-sidecar", "cc-spawner"],
        "project_dir": Path("/repo"),
        "env": env,
        "build": True,
        "force_recreate": True,
    }
    assert mock_up.call_args_list[1].kwargs == {
        "services": ["nextseek_nginx"],
        "project_dir": Path("/repo"),
        "env": env,
        "force_recreate": True,
    }


@patch("startup.steps.build.start_cc_stack")
@patch("startup.steps.build.build_and_start_nextseek")
@patch("startup.steps.build.start_seek_side")
@patch("startup.steps.build.start_databases")
def test_start_full_stack_includes_cc_phase(
    mock_db: MagicMock,
    mock_seek: MagicMock,
    mock_nextseek: MagicMock,
    mock_cc: MagicMock,
) -> None:
    env = {"COMPOSE_PROJECT_NAME": "nextseek-test"}

    build.start_full_stack(Path("/repo"), env)

    mock_db.assert_called_once_with(Path("/repo"), env)
    mock_seek.assert_called_once_with(Path("/repo"), env)
    mock_nextseek.assert_called_once_with(Path("/repo"), env)
    mock_cc.assert_called_once_with(Path("/repo"), env)


@patch("startup.steps.build.wait_for_neo4j")
@patch("startup.steps.build.wait_for_mysql")
@patch("startup.steps.build.compose_up")
def test_start_databases_waits_with_the_kept_passwords(
    mock_up: MagicMock, mock_mysql: MagicMock, mock_neo4j: MagicMock
) -> None:
    """#103: a box whose passwords are not the demo values still comes up."""
    env = {"COMPOSE_PROJECT_NAME": "nextseek-test", "MYSQL_ROOT_PASSWORD": "root-rotated"}

    build.start_databases(Path("/repo"), env, neo4j_password="neo-rotated")

    mock_mysql.assert_called_once_with(Path("/repo"), env, "root-rotated")
    mock_neo4j.assert_called_once_with(Path("/repo"), env, "neo-rotated")


@patch("startup.steps.build.wait_for_neo4j")
@patch("startup.steps.build.wait_for_mysql")
@patch("startup.steps.build.compose_up")
def test_start_databases_defaults_to_the_demo_passwords(
    mock_up: MagicMock, mock_mysql: MagicMock, mock_neo4j: MagicMock
) -> None:
    env = {"COMPOSE_PROJECT_NAME": "nextseek-test"}
    build.start_databases(Path("/repo"), env)
    mock_mysql.assert_called_once_with(Path("/repo"), env, "seek_root")
    mock_neo4j.assert_called_once_with(Path("/repo"), env, "demopassword")
