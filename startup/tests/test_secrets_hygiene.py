"""SEC-H07/H08/H21/H23: file modes, no DB password on argv, rotation fails closed, no login in docs."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from startup.steps import config, users
from startup.tests.test_config import _box

_ROOT = Path(__file__).resolve().parents[2]


def test_rendered_env_files_and_local_settings_are_private(tmp_path: Path) -> None:
    repo = _box(tmp_path)
    values = config.default_values(8000, 3000)
    old = os.umask(0o022)
    try:
        paths = [
            config.render_db_env(repo, values),
            config.render_nextseek_env(repo, values),
            config.render_local_settings(repo, values),
        ]
    finally:
        os.umask(old)
    for p in paths[:2]:
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p
    # the app runs as another uid and reads this one through the deploy account's group
    assert stat.S_IMODE(paths[2].stat().st_mode) == 0o640


def test_a_password_is_never_defaulted_for_an_existing_volume() -> None:
    with pytest.raises(ValueError, match="MYSQL_ROOT_PASSWORD"):
        config.default_values(8000, 3000, generate_passwords=False)


@patch("startup.steps.users.compose_exec")
def test_no_mysql_password_on_any_users_argv(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "1\n"
    users.user_exists("demo", Path("/repo"), {"MYSQL_ROOT_PASSWORD": "s3cretpw"})
    users.set_random_passwords(Path("/repo"), {"MYSQL_ROOT_PASSWORD": "s3cretpw"}, {})
    for c in mock_exec.call_args_list:
        argv = " ".join(c.kwargs["command"])
        assert "s3cretpw" not in argv and not re.search(r"(^|\s)-p\S", argv)


def test_rails_script_clears_the_login_token_columns() -> None:
    for col in ("remember_token", "remember_token_expires_at", "activation_code", "reset_password_code"):
        assert col in users._RAILS_SCRIPT


def test_dump_script_keeps_the_password_off_argv() -> None:
    text = (_ROOT / "startup/seed/regenerate/dump_mysql.sh").read_text()
    assert '-p"$MYSQL_DEV_PASSWORD"' not in text and "MYSQL_PWD" in text


def test_refresh_kit_clears_and_checks_the_token_columns() -> None:
    kill = (_ROOT / "startup/seed/regenerate/refresh/kill.py").read_text()
    verify = (_ROOT / "startup/seed/regenerate/refresh/verify.py").read_text()
    assert "remember_token" in kill and "remember_token" in verify


@pytest.mark.parametrize("doc", [
    "NExtSTEPS.md", "startup/seed/README.md",
    "NessieAI/docker/cc-runtime/docs/nextseek/15-installation.md",
])
def test_no_demo_login_in_the_docs(doc: str) -> None:
    assert not re.search(r"demopassword|userpassword", (_ROOT / doc).read_text())


def test_nessie_command_has_no_literal_default_password() -> None:
    assert "demopassword" not in (_ROOT / "nextseek_api/management/commands/nessie.py").read_text()


def test_no_step_builds_a_mysql_password_argv() -> None:
    """Every startup step goes through MYSQL_ROOT_CMD: no -p<password> and no 'seek_root' fallback in the source."""
    for f in sorted((_ROOT / "startup" / "steps").glob("*.py")):
        text = f.read_text()
        assert "seek_root" not in text, f.name
        assert not re.search(r'f"-p\{', text), f.name


def test_no_step_puts_a_neo4j_password_on_argv() -> None:
    for f in sorted((_ROOT / "startup" / "steps").glob("*.py")):
        text = f.read_text()
        assert not re.search(r'"cypher-shell"', text), f.name


def test_the_refresh_dump_passes_mysql_pwd_through_docker_exec() -> None:
    run = (_ROOT / "startup/seed/regenerate/refresh/run.sh").read_text()
    assert "docker exec -e MYSQL_PWD" in run


def test_verify_reads_the_token_columns_through_its_own_container() -> None:
    verify = (_ROOT / "startup/seed/regenerate/refresh/verify.py").read_text()
    assert 's.scalar("SELECT COUNT(*) FROM users' not in verify and 'fake_exec("db"' in verify


def test_root_env_gets_the_host_gid_once_and_keeps_an_existing_value(tmp_path: Path) -> None:
    from startup.lib.env import read_env
    config.render_root_env(tmp_path, {"COMPOSE_PROJECT_NAME": "x"})
    assert read_env(tmp_path / ".env")["NEXTSEEK_HOST_GID"] == str(os.getgid())
    (tmp_path / ".env").write_text("COMPOSE_PROJECT_NAME=x\nNEXTSEEK_HOST_GID=4242\n")
    config.render_root_env(tmp_path, {"COMPOSE_PROJECT_NAME": "x"})
    config.ensure_host_gid(tmp_path)
    assert read_env(tmp_path / ".env")["NEXTSEEK_HOST_GID"] == "4242"
    assert (tmp_path / ".env").read_text().count("NEXTSEEK_HOST_GID") == 1


def test_ensure_host_gid_adds_the_key_to_an_old_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("COMPOSE_PROJECT_NAME=x\n")
    config.ensure_host_gid(tmp_path)
    config.ensure_host_gid(tmp_path)
    from startup.lib.env import read_env
    text = (tmp_path / ".env").read_text()
    assert text.count("NEXTSEEK_HOST_GID") == 1 and "COMPOSE_PROJECT_NAME=x" in text
    assert read_env(tmp_path / ".env")["NEXTSEEK_HOST_GID"] == str(os.getgid())
