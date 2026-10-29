"""Idempotent test-user verification + creation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from startup.steps.config import generate_password
from startup.lib.docker_ops import compose_exec, DockerOpsError, MYSQL_ROOT_CMD


def _mysql_cmd(*args: str) -> list[str]:
    return [*MYSQL_ROOT_CMD, *args]


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
            command=_mysql_cmd(
                "-N",
                "seek_production",
                "-e",
                f"SELECT COUNT(*) FROM users WHERE login = '{login}';",
            ),
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


# Reads "login password" lines from stdin, so no password is ever in argv.
# One transaction: SEEK changes all of the logins or none. The same save clears the
# remember-me, activation and reset tokens the seed carries (SEC-H07): update_columns
# skips the callbacks and validations, which would re-issue or reject them.
_RAILS_SCRIPT = (
    "User.transaction { STDIN.each_line { |l| login, pw = l.split(' ', 2); "
    "pw = pw.to_s.strip; u = User.find_by!(login: login); u.password = pw; "
    "u.password_confirmation = pw; u.save!; "
    "u.update_columns(remember_token: nil, remember_token_expires_at: nil, "
    "activation_code: nil, reset_password_code: nil, reset_password_code_until: nil) } }"
)


def set_random_passwords(
    repo_root: Path, db_env: dict[str, str], compose_env: dict[str, str]
) -> dict[str, str] | None:
    """Give each REQUIRED_USERS login a random password; return {login: password}.

    Django's copy of the published hash is made unusable ('!') first (until the
    next SEEK login re-hashes it); then SEEK's own User model sets the SEEK
    password (passwords on stdin only), all logins in one transaction. Either
    step failing leaves SEEK with the published passwords, so the caller's
    warning stays true. Returns None on any failure.
    """
    logins = [u.login for u in REQUIRED_USERS]
    if not all(_is_safe_login(login) for login in logins):
        return None
    passwords = {login: generate_password(24) for login in logins}
    stdin = "".join(f"{login} {pw}\n" for login, pw in passwords.items()).encode()
    quoted = ",".join(f"'{login}'" for login in logins)
    try:
        compose_exec(
            service="db",
            command=_mysql_cmd(
                "-e",
                f"UPDATE dmac.auth_user SET password='!' WHERE username IN ({quoted});",
            ),
            project_dir=repo_root,
            env=db_env,
        )
        compose_exec(
            service="seek",
            command=["bundle", "exec", "rails", "runner", _RAILS_SCRIPT],
            project_dir=repo_root,
            env=compose_env,
            stdin=stdin,
        )
    except DockerOpsError:
        return None
    return passwords
