"""Post-seed data cleanup.

The shipped seed dumps capture the dev environment's data at a snapshot in
time. Most of it is useful as a fixture (the schema, sample types, projects),
but some rows are dev-user-specific artifacts that show up as confusing
clutter on a fresh install — most notably a wall of untitled "New chat"
sessions in the assistant sidebar.

This module deletes those clearly-stale rows after seed import. Idempotent:
safe to re-run on already-cleaned data.

Note this is intentionally separate from `schema_fixups.py`. That module is
about SCHEMA drift (missing columns, wrong types). This module is about DATA
drift (rows that exist but shouldn't, on a fresh install).
"""
from __future__ import annotations

from pathlib import Path

from bootstrap.lib.docker_ops import DockerOpsError, compose_exec


def _root_password(env: dict[str, str]) -> str:
    return env.get("MYSQL_ROOT_PASSWORD", "seek_root")


def _query_count(sql: str, repo_root: Path, env: dict[str, str]) -> int:
    """Run a SELECT COUNT(*) ... query and return the integer. Returns 0 on any failure."""
    try:
        out = compose_exec(
            service="db",
            command=["mysql", "-uroot", f"-p{_root_password(env)}", "-N", "dmac", "-e", sql],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return 0
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def _run_delete(sql: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Run a DELETE statement against dmac. Returns False on failure."""
    try:
        compose_exec(
            service="db",
            command=["mysql", "-uroot", f"-p{_root_password(env)}", "dmac", "-e", sql],
            project_dir=repo_root,
            env=env,
        )
        return True
    except DockerOpsError:
        return False


def clear_stale_chat_sessions(repo_root: Path, env: dict[str, str]) -> int:
    """Delete assistant_chat_session rows where title is NULL or empty.

    Returns the count actually deleted. Returns 0 if the table doesn't exist
    (e.g., a --no-seed install on a fresh volume) or the delete itself fails.
    """
    if _query_count(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA='dmac' AND TABLE_NAME='assistant_chat_session';",
        repo_root,
        env,
    ) == 0:
        return 0
    count = _query_count(
        "SELECT COUNT(*) FROM assistant_chat_session WHERE title IS NULL OR title = '';",
        repo_root,
        env,
    )
    if count == 0:
        return 0
    if not _run_delete(
        "DELETE FROM assistant_chat_session WHERE title IS NULL OR title = '';",
        repo_root,
        env,
    ):
        return 0
    return count
