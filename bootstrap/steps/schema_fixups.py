"""Post-seed schema fixups for known dump-vs-migration drift.

Some Django migrations have been rewritten as `SeparateDatabaseAndState`
state-only no-ops after the actual DDL was applied to dev by an earlier
revision of that same migration file. If a seed dump is taken at the
intermediate state (column missing on disk, migration marked applied in
django_migrations), a fresh install's `manage.py migrate` step skips the
migration, the column never gets created, and the app crashes on any code
path that touches the column.

Each entry in KNOWN_FIXUPS is an idempotent ALTER with two safety gates:
- the target table must exist (skip silently if not — handles --no-seed paths
  where the schema hasn't been created yet)
- the column must not exist (skip if already present — handles re-runs)

When a new drift is discovered, add an entry here and bootstrap install will
heal it on the next run without anyone needing to touch SQL manually.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bootstrap.lib.docker_ops import DockerOpsError, compose_exec


@dataclass(frozen=True)
class MissingColumn:
    database: str
    table: str
    column: str
    column_definition: str               # initial ADD COLUMN form (loose enough to allow backfill — usually nullable)
    backfill_expression: str | None = None  # SQL expression to populate existing NULLs (e.g. "JSON_OBJECT()")
    final_column_definition: str | None = None  # post-backfill MODIFY COLUMN form (tight constraints to match the canonical schema)


KNOWN_FIXUPS: list[MissingColumn] = [
    # Django migration nextseek_api/0004_chatsession_extra_state_state_only is a
    # state-only no-op; the column was added by an earlier-revision DDL that's
    # absent from current seed dumps. Restore the missing column to match what
    # Django's ORM expects (JSONField(default=dict) → NOT NULL with default '{}').
    MissingColumn(
        database="dmac",
        table="assistant_chat_session",
        column="extra_state",
        column_definition="JSON NULL",                    # added nullable so the backfill UPDATE can target existing rows
        backfill_expression="JSON_OBJECT()",
        final_column_definition="JSON NOT NULL DEFAULT (JSON_OBJECT())",  # then tightened to match production
    ),
]


def _root_password(env: dict[str, str]) -> str:
    return env.get("MYSQL_ROOT_PASSWORD", "seek_root")


def _table_exists(fix: MissingColumn, repo_root: Path, env: dict[str, str]) -> bool:
    try:
        out = compose_exec(
            service="db",
            command=[
                "mysql", "-uroot", f"-p{_root_password(env)}", "-N",
                "-e",
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                f"WHERE TABLE_SCHEMA='{fix.database}' AND TABLE_NAME='{fix.table}';",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    return out.strip().splitlines()[-1] == "1"


def _column_exists(fix: MissingColumn, repo_root: Path, env: dict[str, str]) -> bool:
    try:
        out = compose_exec(
            service="db",
            command=[
                "mysql", "-uroot", f"-p{_root_password(env)}", "-N",
                "-e",
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                f"WHERE TABLE_SCHEMA='{fix.database}' "
                f"AND TABLE_NAME='{fix.table}' "
                f"AND COLUMN_NAME='{fix.column}';",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    return out.strip().splitlines()[-1] == "1"


def _add_and_backfill(fix: MissingColumn, repo_root: Path, env: dict[str, str]) -> None:
    statements = [f"ALTER TABLE {fix.table} ADD COLUMN {fix.column} {fix.column_definition};"]
    if fix.backfill_expression:
        statements.append(
            f"UPDATE {fix.table} SET {fix.column} = {fix.backfill_expression} "
            f"WHERE {fix.column} IS NULL;"
        )
    compose_exec(
        service="db",
        command=[
            "mysql", "-uroot", f"-p{_root_password(env)}", fix.database,
            "-e", " ".join(statements),
        ],
        project_dir=repo_root,
        env=env,
    )


def _tighten_constraints(fix: MissingColumn, repo_root: Path, env: dict[str, str]) -> None:
    """MODIFY COLUMN to the canonical final definition. Idempotent — MySQL is a
    no-op if the column already matches; it does the change otherwise."""
    if not fix.final_column_definition:
        return
    compose_exec(
        service="db",
        command=[
            "mysql", "-uroot", f"-p{_root_password(env)}", fix.database,
            "-e",
            f"ALTER TABLE {fix.table} MODIFY COLUMN {fix.column} {fix.final_column_definition};",
        ],
        project_dir=repo_root,
        env=env,
    )


def apply_all(repo_root: Path, env: dict[str, str]) -> list[tuple[str, str]]:
    """Run each known fixup idempotently. Returns [(column_fqn, status)] for the caller to render.

    Status is one of:
      - "applied"           — column was missing, ALTER + backfill + tighten all ran
      - "constraints reset" — column existed, but tightened to the canonical final definition
      - "already present"   — column existed and no final tightening to apply
      - "table missing"     — target table absent (e.g., --no-seed install on fresh volume); fixup skipped
    """
    results: list[tuple[str, str]] = []
    for fix in KNOWN_FIXUPS:
        fqn = f"{fix.database}.{fix.table}.{fix.column}"
        if not _table_exists(fix, repo_root, env):
            results.append((fqn, "table missing"))
            continue
        column_existed = _column_exists(fix, repo_root, env)
        if not column_existed:
            _add_and_backfill(fix, repo_root, env)
        _tighten_constraints(fix, repo_root, env)
        if column_existed:
            results.append(
                (fqn, "constraints reset" if fix.final_column_definition else "already present")
            )
        else:
            results.append((fqn, "applied"))
    return results
