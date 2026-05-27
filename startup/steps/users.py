"""Idempotent test-user verification + creation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from startup.lib.docker_ops import compose_exec, DockerOpsError


@dataclass
class TestUser:
    login: str
    password: str
    admin: bool


REQUIRED_USERS: list[TestUser] = [
    TestUser(login="demo", password="demopassword", admin=True),
    TestUser(login="user", password="userpassword", admin=False),
]


def _is_safe_login(login: str) -> bool:
    """Whitelist for login names we'll inline into a SQL string.

    REQUIRED_USERS hardcodes 'demo' and 'user'; this is a belt-and-suspenders
    guard so a future caller can't sneak SQL injection through this path."""
    return login.isalnum() and 0 < len(login) <= 64


def user_exists(login: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Return True if a row with this login exists in seek_production.users."""
    if not _is_safe_login(login):
        raise ValueError(f"unsafe login name: {login!r}")
    try:
        out = compose_exec(
            service="db",
            command=[
                "mysql",
                "-uroot",
                f"-p{env.get('MYSQL_ROOT_PASSWORD', 'seek_root')}",
                "-N",
                "seek_production",
                "-e",
                f"SELECT COUNT(*) FROM users WHERE login = '{login}';",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    try:
        return int(out.strip().splitlines()[-1]) > 0
    except (ValueError, IndexError):
        return False


def verify_users_present(repo_root: Path, env: dict[str, str]) -> list[str]:
    """Return the list of REQUIRED_USERS logins that are NOT in the DB."""
    missing: list[str] = []
    for user in REQUIRED_USERS:
        if not user_exists(user.login, repo_root, env):
            missing.append(user.login)
    return missing
