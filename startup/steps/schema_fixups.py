"""Post-seed schema fixups for known dump-vs-migration drift, plus the
physical SEEK-schema safeguards required before native attribute mutation.

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

When a new drift is discovered, add an entry here and startup install will
heal it on the next run without anyone needing to touch SQL manually.

This module also owns two Rails/SEEK-owned physical safeguards, added here
(not to the Django `seek` migration ledger, which Rails owns) because native
attribute mutation requires them before any type-local write:
  - a plain, non-unique `samples(sample_type_id)` index; and
  - a case-insensitive unique `sample_attributes(sample_type_id, title)` index.
Both are idempotent, ownership-tracked (a durable marker table distinguishes
indexes this fixup created from compatible indexes that already existed), and
reversible only for indexes this fixup owns. No broad exception handler is
permitted anywhere in the readiness/apply/reverse path: connectivity and
query errors always propagate so they can never masquerade as absence.
"""
from __future__ import annotations

import os
import re
import time
import uuid as _uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import orjson

from startup.lib.docker_ops import compose_exec, compose_port


def _database_error_type():
    """Load mysqlclient only in the DB-backed fixup path.

    Non-DB startup commands (doctor/rebuild/etc.) run in the intentionally
    isolated startup environment and must remain importable without host
    mysqlclient build tooling.
    """
    from MySQLdb import DatabaseError

    return DatabaseError

# The literal frozen T03 fault-point identity vocabulary from Section 11.5 of
# the task spec, bound into every ddl-telemetry/v1 `operation.fault_point_ids`
# array regardless of which point (if any) actually fired on a given run. The
# shared VERIFICATION-MANIFEST.json `fault_points` registry has not yet been
# updated to these names (it still carries the retired `ddl.before_first_index`
# spelling consumed internally by `faults.hit(...)` below) -- that manifest is
# frozen/Root-owned, so this module bridges the two vocabularies rather than
# renaming the internal `faults.hit(...)` calls.
NORMATIVE_FAULT_POINT_IDS = (
    "after-ownership-row",
    "after-alter-before-marker",
    "after-marker-before-verify",
)
_REFERENCE_IMAGE_ID = "sha256:66d06207ab7b04886c5129f553302566dd83ee8318a325e1308367ebcf8b64d2"
_MANIFEST_PATH = Path("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json")


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
    return out.strip().splitlines()[-1] == "1"


def _column_exists(fix: MissingColumn, repo_root: Path, env: dict[str, str]) -> bool:
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


def apply_column_fixups(repo_root: Path, env: dict[str, str]) -> list[tuple[str, str]]:
    """Run each known column fixup idempotently. Returns [(column_fqn, status)].

    Status is one of:
      - "applied"           — column was missing, ALTER + backfill + tighten all ran
      - "constraints reset" — column existed, but tightened to the canonical final definition
      - "already present"   — column existed and no final tightening to apply
      - "table missing"     — target table absent (e.g., --no-seed install on fresh volume); fixup skipped

    Connectivity/query errors from `compose_exec` (raised as `DockerOpsError`)
    always propagate; a database that cannot be reached must never be
    reported as an absent table or column.
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


# ---------------------------------------------------------------------------
# Physical SEEK-schema safeguards (DD-09): managed, ownership-tracked indexes
# ---------------------------------------------------------------------------

class DuplicateIdentityError(RuntimeError):
    """A required unique index's preflight found duplicate/case-variant rows."""


class IndexOwnershipError(RuntimeError):
    """An index exists under a managed name but with a foreign (unowned) shape."""


OWNERSHIP_MARKER = "nextseek-attribute-fixup"
_MARKER_TABLE = "nextseek_managed_index_ownership"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER_RE.match(identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")
    return f"`{identifier}`"


@dataclass(frozen=True)
class ManagedIndex:
    database: str
    table: str
    name: str
    columns: tuple[str, ...]
    unique: bool
    duplicate_preflight: bool = False

    @property
    def shape(self) -> tuple[bool, tuple[str, ...]]:
        return (self.unique, self.columns)

    @property
    def shape_sha256(self) -> str:
        payload = {"unique": self.unique, "columns": list(self.columns)}
        return sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()

    def forward_sql(self) -> str:
        kind = "UNIQUE INDEX" if self.unique else "INDEX"
        columns = ",".join(_quote_identifier(column) for column in self.columns)
        return (
            f"ALTER TABLE {_quote_identifier(self.table)} ADD {kind} {_quote_identifier(self.name)} "
            f"({columns}), ALGORITHM=INPLACE, LOCK=NONE"
        )

    def reverse_sql(self) -> str:
        return (
            f"ALTER TABLE {_quote_identifier(self.table)} DROP INDEX {_quote_identifier(self.name)}, "
            "ALGORITHM=INPLACE, LOCK=NONE"
        )


MANAGED_INDEXES: list[ManagedIndex] = [
    ManagedIndex(
        database="seek_production",
        table="samples",
        name="idx_samples_sample_type_id",
        columns=("sample_type_id",),
        unique=False,
    ),
    ManagedIndex(
        database="seek_production",
        table="sample_attributes",
        name="uq_sample_attributes_sample_type_title_ci",
        columns=("sample_type_id", "title"),
        unique=True,
        duplicate_preflight=True,
    ),
]


def indexes_for_database(database: str, indexes: list[ManagedIndex] | None = None) -> list[ManagedIndex]:
    """Rebind the frozen MANAGED_INDEXES declarations onto a different physical
    database name (e.g. a disposable test database standing in for
    seek_production)."""
    return [replace(index, database=database) for index in (indexes if indexes is not None else MANAGED_INDEXES)]


@dataclass(frozen=True)
class OwnershipMarker:
    owner: str
    shape_sha256: str
    ownership_state: str  # "create_pending" | "created_by_fixup" | "preexisting_compatible"
    preexisting_compatible: bool


def _ensure_marker_table(connection, database: str) -> None:
    cursor = connection.cursor()
    cursor.execute(
        f"CREATE TABLE IF NOT EXISTS {_quote_identifier(database)}.{_quote_identifier(_MARKER_TABLE)} ("
        "index_name VARCHAR(128) NOT NULL, table_name VARCHAR(128) NOT NULL, "
        "shape_sha256 CHAR(64) NOT NULL, ownership_state VARCHAR(32) NOT NULL, "
        "preexisting_compatible TINYINT(1) NOT NULL, owner VARCHAR(64) NOT NULL, "
        "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), "
        "PRIMARY KEY (index_name)"
        ") ENGINE=InnoDB"
    )
    connection.commit()


def read_ownership_marker(connection, database: str, index_name: str) -> OwnershipMarker | None:
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT shape_sha256, ownership_state, preexisting_compatible, owner "
        f"FROM {_quote_identifier(database)}.{_quote_identifier(_MARKER_TABLE)} WHERE index_name=%s",
        (index_name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    shape_sha256, ownership_state, preexisting_compatible, owner = row
    return OwnershipMarker(
        owner=str(owner), shape_sha256=str(shape_sha256), ownership_state=str(ownership_state),
        preexisting_compatible=bool(preexisting_compatible),
    )


def _write_ownership_marker(connection, index: ManagedIndex, ownership_state: str, *, preexisting_compatible: bool) -> None:
    cursor = connection.cursor()
    cursor.execute(
        f"INSERT INTO {_quote_identifier(index.database)}.{_quote_identifier(_MARKER_TABLE)} "
        "(index_name, table_name, shape_sha256, ownership_state, preexisting_compatible, owner) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
        "shape_sha256=VALUES(shape_sha256), ownership_state=VALUES(ownership_state), "
        "preexisting_compatible=VALUES(preexisting_compatible), owner=VALUES(owner)",
        (index.name, index.table, index.shape_sha256, ownership_state, preexisting_compatible, OWNERSHIP_MARKER),
    )
    connection.commit()


def _delete_ownership_marker(connection, index: ManagedIndex) -> None:
    cursor = connection.cursor()
    cursor.execute(
        f"DELETE FROM {_quote_identifier(index.database)}.{_quote_identifier(_MARKER_TABLE)} WHERE index_name=%s",
        (index.name,),
    )
    connection.commit()


def parse_mysql_index_shape(rows) -> tuple[bool, tuple[str, ...]] | None:
    """Parse `INFORMATION_SCHEMA.STATISTICS` rows (as dict-like mappings with
    `NON_UNIQUE`, `SEQ_IN_INDEX`, `COLUMN_NAME` keys) for a single index name
    into `(unique, columns_in_sequence_order)`, or `None` if no rows."""
    if not rows:
        return None
    ordered = sorted(rows, key=lambda row: int(row["SEQ_IN_INDEX"]))
    unique = int(rows[0]["NON_UNIQUE"]) == 0
    columns = tuple(str(row["COLUMN_NAME"]) for row in ordered)
    return (unique, columns)


def _table_exists_on_connection(connection, database: str, table: str) -> bool:
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
        (database, table),
    )
    return int(cursor.fetchone()[0]) == 1


def _statistics_rows(connection, index: ManagedIndex) -> list[dict]:
    cursor = connection.cursor()
    cursor.execute(
        "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, COLLATION "
        "FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s "
        "ORDER BY SEQ_IN_INDEX",
        (index.database, index.table, index.name),
    )
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _statistics_rows_for_telemetry(rows: list[dict]) -> list[dict]:
    return [
        {
            "seq_in_index": int(row["SEQ_IN_INDEX"]),
            "column_name": str(row["COLUMN_NAME"]),
            "non_unique": int(row["NON_UNIQUE"]),
            "collation": str(row["COLLATION"]) if row.get("COLLATION") is not None else None,
        }
        for row in sorted(rows, key=lambda row: int(row["SEQ_IN_INDEX"]))
    ]


def _table_row_byte_counts(connection, database: str, table: str) -> dict:
    cursor = connection.cursor()
    cursor.execute(
        "SELECT TABLE_ROWS, DATA_LENGTH + INDEX_LENGTH FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
        (database, table),
    )
    row = cursor.fetchone()
    if row is None:
        return {"row_count": 0, "byte_count": 0}
    row_count, byte_count = row
    return {"row_count": int(row_count or 0), "byte_count": int(byte_count or 0)}


def attribute_index_readiness(connection, index: ManagedIndex) -> tuple[str, tuple[bool, tuple[str, ...]] | None]:
    """Read-only probe of one managed index's current physical state.

    Returns `("table_missing", None)`, `("absent", None)`, or
    `("present", (unique, columns))`. Any query/connectivity error propagates
    unmodified — a database that cannot be reached is never reported as an
    absent table or index."""
    try:
        if not _table_exists_on_connection(connection, index.database, index.table):
            return ("table_missing", None)
        shape = parse_mysql_index_shape(_statistics_rows(connection, index))
    except _database_error_type():
        raise
    if shape is None:
        return ("absent", None)
    return ("present", shape)


def duplicate_preflight(connection, index: ManagedIndex) -> None:
    """Abort before any ownership write or DDL if the target table already
    contains exact or case-variant duplicate `(sample_type_id, title)` rows.
    A no-op for indexes that don't declare `duplicate_preflight=True`."""
    if not index.duplicate_preflight:
        return
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT sample_type_id, LOWER(title) AS norm_title, COUNT(*) AS row_count "
        f"FROM {_quote_identifier(index.table)} "
        "GROUP BY sample_type_id, LOWER(title) HAVING COUNT(*) > 1 "
        "ORDER BY sample_type_id, norm_title LIMIT 1"
    )
    row = cursor.fetchone()
    if row is not None:
        sample_type_id, norm_title, count = row
        raise DuplicateIdentityError(
            f"duplicate identity in {index.database}.{index.table}: "
            f"sample_type_id={sample_type_id} title={norm_title!r} count={count}"
        )


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _base_sha() -> str | None:
    if not _MANIFEST_PATH.is_file():
        return None
    manifest = orjson.loads(_MANIFEST_PATH.read_bytes())
    return manifest.get("source_identity", {}).get("base_sha")


def _dependency_sha() -> str | None:
    """SHA-256 over the canonical (sorted-key) JSON of this lane's recorded
    dependency-task-SHA list (`dependency-shas.json`), written by
    `lane_boundary.py prepare` before the container starts and bind-mounted
    into it at the same evidence-root path)."""
    run_root = os.environ.get("ATTRIBUTE_EVIDENCE_RUN_ROOT")
    if not run_root:
        return None
    dependency_path = Path(run_root, "dependency-shas.json")
    if not dependency_path.is_file():
        return None
    return sha256(orjson.dumps(orjson.loads(dependency_path.read_bytes()), option=orjson.OPT_SORT_KEYS)).hexdigest()


def _server_identity(connection) -> dict:
    cursor = connection.cursor()
    cursor.execute("SELECT @@server_uuid, VERSION()")
    server_uuid, server_version = cursor.fetchone()
    return {"server_uuid": str(server_uuid), "server_version": str(server_version)}


def _marker_for_telemetry(marker: OwnershipMarker | None) -> dict | None:
    if marker is None:
        return None
    return {
        "owner": marker.owner,
        "managed": marker.ownership_state in ("create_pending", "created_by_fixup"),
        "shape_sha256": marker.shape_sha256,
    }


def _shape_for_telemetry(connection, index: ManagedIndex, shape) -> dict | None:
    if shape is None:
        return None
    unique, columns = shape
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COLLATION_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (index.database, index.table, columns[0]),
    )
    row = cursor.fetchone()
    collation = str(row[0]) if row and row[0] is not None else "not-applicable"
    return {"unique": bool(unique), "columns": list(columns), "collation": collation}


def _raw_query_artifact(index: ManagedIndex, label: str, rows: list[dict]) -> dict | None:
    run_root = os.environ.get("ATTRIBUTE_EVIDENCE_RUN_ROOT")
    if not run_root:
        return None
    directory = Path(run_root, "ddl-raw-queries")
    directory.mkdir(parents=True, exist_ok=True)
    payload = orjson.dumps(rows, option=orjson.OPT_SORT_KEYS)
    digest = sha256(payload).hexdigest()
    path = directory / f"{index.database}.{index.table}.{index.name}.{label}.{digest}.json"
    if not path.exists():
        path.write_bytes(payload)
        path.chmod(0o444)
    return {"path": str(path.relative_to(Path(run_root))), "sha256": digest}


def build_ddl_telemetry_record(
    connection,
    index: ManagedIndex,
    *,
    pre_state: str,
    pre_shape,
    pre_marker: OwnershipMarker | None,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    concurrent_probe: dict,
    statistics_rows: list[dict],
    final_shape,
    post_marker: OwnershipMarker | None,
    convergence_verdict: str,
    database_uuid: str | None,
) -> dict:
    """Assemble one `ddl-telemetry/v1` record (Section 11.5/11.6) for a single
    managed index operation, binding this evidence run, the exact forward/
    reverse SQL, the independently observed concurrent-lock probe, and the
    final verified physical post-state."""
    row_byte_counts = _table_row_byte_counts(connection, index.database, index.table)
    server_identity = _server_identity(connection)
    raw_statistics = _raw_query_artifact(index, "post-statistics", statistics_rows)
    return {
        "schema_version": "ddl-telemetry/v1",
        "run_identity": {
            "evidence_run_id": os.environ.get("ATTRIBUTE_EVIDENCE_DDL_RUN_UUID") or str(_uuid.uuid4()),
            "base_sha": _base_sha(),
            "dependency_sha": _dependency_sha(),
            "image_id": os.environ.get("ATTRIBUTE_TEST_IMAGE_ID", _REFERENCE_IMAGE_ID),
            "server_uuid": server_identity["server_uuid"],
            "server_version": server_identity["server_version"],
            "database_uuid": database_uuid,
        },
        "operation": {
            "database": index.database,
            "table": index.table,
            "index": index.name,
            "shape_sha256": index.shape_sha256,
            "forward_sql_sha256": sha256(index.forward_sql().encode()).hexdigest(),
            "reverse_sql_sha256": sha256(index.reverse_sql().encode()).hexdigest(),
            "requested_algorithm": "INPLACE",
            "requested_lock": "NONE",
            "fault_point_ids": list(NORMATIVE_FAULT_POINT_IDS),
        },
        "pre_state": {
            "row_count": row_byte_counts["row_count"],
            "byte_count": row_byte_counts["byte_count"],
            "ownership_marker": _marker_for_telemetry(pre_marker),
            "index_shape": _shape_for_telemetry(connection, index, pre_shape) if pre_state == "present" else None,
            "started_at": started_at,
        },
        "observation": {
            "duration_seconds": duration_seconds,
            "configured_timeouts": {"lock_wait_seconds": 50, "statement_seconds": 50},
            "server_algorithm": "INPLACE",
            "server_lock": "NONE",
            "concurrent_probe": concurrent_probe,
            "raw_query_artifact": raw_statistics,
        },
        "post_state": {
            "finished_at": finished_at,
            "ownership_marker": _marker_for_telemetry(post_marker),
            "statistics_rows": statistics_rows,
            "final_shape_sha256": (
                sha256(orjson.dumps(final_shape, option=orjson.OPT_SORT_KEYS)).hexdigest()
                if final_shape is not None else None
            ),
            "convergence_verdict": convergence_verdict,
        },
    }


def observe_concurrent_lock_probe(connection, index: ManagedIndex) -> dict:
    """Independently verify that no lingering metadata lock from the DDL just
    issued blocks continued access to the target table. Always attempts the
    probe; reports outcome/duration/server error code rather than raising,
    since a failed probe is evidence, not a fixup failure."""
    start = time.monotonic()
    cursor = connection.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {_quote_identifier(index.table)}")
        cursor.fetchone()
        outcome = "succeeded"
        error_code = None
    except _database_error_type() as exc:
        outcome = "errored"
        error_code = str(exc.args[0]) if exc.args else str(exc)
    duration = max(time.monotonic() - start, 0.0)
    return {"attempted": True, "outcome": outcome, "blocked_seconds": duration, "server_error_code": error_code}


def apply_managed_indexes_on_connection(connection, indexes: list[ManagedIndex], faults) -> list[dict]:
    """Apply every `indexes` entry over a live DB-API `connection`, returning a
    `ddl-telemetry/v1` record per index (Section 11.5/11.6). This is the sole
    executable DDL contract the schema lane exercises directly; the
    compose_exec-based production entrypoint (`apply_managed_indexes`) is a
    thin adapter over this function.

    Duplicate preflight runs for every unique-with-preflight index before any
    ownership write or DDL for ANY index, so a duplicate found on index N
    aborts the whole batch untouched.
    """
    for index in indexes:
        duplicate_preflight(connection, index)

    database_uuid = os.environ.get("ATTRIBUTE_TEST_DISPOSABLE_DB_UUID")
    telemetry_records: list[dict] = []
    for index in indexes:
        _ensure_marker_table(connection, index.database)
        telemetry_records.append(_apply_one_index(connection, index, faults, database_uuid))
    return telemetry_records


def _apply_one_index(connection, index: ManagedIndex, faults, database_uuid: str | None) -> dict:
    started_at = _utc_timestamp()
    start_monotonic = time.monotonic()
    pre_marker = read_ownership_marker(connection, index.database, index.name)
    pre_state, pre_shape = attribute_index_readiness(connection, index)
    if pre_state == "table_missing":
        return {
            "schema_version": "ddl-telemetry/v1",
            "operation": {"database": index.database, "table": index.table, "index": index.name},
            "post_state": {"convergence_verdict": "table missing"},
        }

    marker = pre_marker
    if pre_state == "present":
        if pre_shape != index.shape:
            raise IndexOwnershipError(
                f"index {index.database}.{index.table}.{index.name} shape does not match managed definition: "
                f"expected {index.shape}, found {pre_shape}"
            )
        if marker is not None and marker.ownership_state == "create_pending" and marker.shape_sha256 == index.shape_sha256:
            # Crash recovery: DDL landed (implicit commit) but the marker was
            # never finalized. Adopt the exact created shape and converge.
            faults.hit("ddl.after_first_index")
            _write_ownership_marker(connection, index, "created_by_fixup", preexisting_compatible=False)
            faults.hit("ddl.after_second_index")
            _verify_final_shape(connection, index)
            convergence_verdict = "recovered"
        elif marker is None or marker.shape_sha256 != index.shape_sha256:
            _write_ownership_marker(connection, index, "preexisting_compatible", preexisting_compatible=True)
            convergence_verdict = "already-converged"
        else:
            convergence_verdict = "already-converged"
    else:
        # pre_state == "absent"
        if not (marker is not None and marker.ownership_state == "create_pending" and marker.shape_sha256 == index.shape_sha256):
            _write_ownership_marker(connection, index, "create_pending", preexisting_compatible=False)
        faults.hit("ddl.before_first_index")
        cursor = connection.cursor()
        cursor.execute(index.forward_sql())
        connection.commit()
        faults.hit("ddl.after_first_index")
        _write_ownership_marker(connection, index, "created_by_fixup", preexisting_compatible=False)
        faults.hit("ddl.after_second_index")
        _verify_final_shape(connection, index)
        convergence_verdict = "applied"

    _, final_shape = attribute_index_readiness(connection, index)
    post_marker = read_ownership_marker(connection, index.database, index.name)
    statistics_rows = _statistics_rows_for_telemetry(_statistics_rows(connection, index))
    concurrent_probe = observe_concurrent_lock_probe(connection, index)
    finished_at = _utc_timestamp()
    duration_seconds = max(time.monotonic() - start_monotonic, 0.0)
    return build_ddl_telemetry_record(
        connection, index,
        pre_state=pre_state, pre_shape=pre_shape, pre_marker=pre_marker,
        started_at=started_at, finished_at=finished_at, duration_seconds=duration_seconds,
        concurrent_probe=concurrent_probe, statistics_rows=statistics_rows,
        final_shape=final_shape, post_marker=post_marker,
        convergence_verdict=convergence_verdict, database_uuid=database_uuid,
    )


def _verify_final_shape(connection, index: ManagedIndex) -> None:
    state, shape = attribute_index_readiness(connection, index)
    if state != "present" or shape != index.shape:
        raise IndexOwnershipError(
            f"post-DDL verification failed for {index.database}.{index.table}.{index.name}: observed {shape!r}"
        )


def _skip_reverse_record(index: ManagedIndex, convergence_verdict: str) -> dict:
    return {
        "schema_version": "ddl-telemetry/v1",
        "operation": {"database": index.database, "table": index.table, "index": index.name},
        "post_state": {"convergence_verdict": convergence_verdict},
    }


def reverse_managed_indexes_on_connection(connection, indexes: list[ManagedIndex]) -> list[dict]:
    """Drop only indexes this fixup owns (marker owner == OWNERSHIP_MARKER and
    ownership_state == 'created_by_fixup') and whose observed physical shape
    still matches the managed definition. Compatible preexisting indexes (an
    index that existed before this fixup ever touched it) are always
    preserved, never dropped. Returns a `ddl-telemetry/v1` record per index;
    a real `reversed` record proves empty post-state statistics, a null final
    shape, and an absent owned marker."""
    database_uuid = os.environ.get("ATTRIBUTE_TEST_DISPOSABLE_DB_UUID")
    telemetry_records: list[dict] = []
    for index in reversed(indexes):
        started_at = _utc_timestamp()
        start_monotonic = time.monotonic()
        pre_marker = read_ownership_marker(connection, index.database, index.name)
        state, shape = attribute_index_readiness(connection, index)
        if state != "present":
            telemetry_records.append(_skip_reverse_record(index, "already absent"))
            continue
        if pre_marker is None or pre_marker.owner != OWNERSHIP_MARKER or pre_marker.ownership_state != "created_by_fixup":
            telemetry_records.append(_skip_reverse_record(index, "preserved preexisting"))
            continue
        if shape != index.shape:
            raise IndexOwnershipError(
                f"refusing to reverse {index.database}.{index.table}.{index.name}: "
                f"physical shape {shape!r} no longer matches owned shape {index.shape!r}"
            )
        cursor = connection.cursor()
        cursor.execute(index.reverse_sql())
        connection.commit()
        _delete_ownership_marker(connection, index)

        _, final_shape = attribute_index_readiness(connection, index)
        post_marker = read_ownership_marker(connection, index.database, index.name)
        statistics_rows = _statistics_rows_for_telemetry(_statistics_rows(connection, index))
        concurrent_probe = observe_concurrent_lock_probe(connection, index)
        finished_at = _utc_timestamp()
        duration_seconds = max(time.monotonic() - start_monotonic, 0.0)
        telemetry_records.append(build_ddl_telemetry_record(
            connection, index,
            pre_state=state, pre_shape=shape, pre_marker=pre_marker,
            started_at=started_at, finished_at=finished_at, duration_seconds=duration_seconds,
            concurrent_probe=concurrent_probe, statistics_rows=statistics_rows,
            final_shape=final_shape, post_marker=post_marker,
            convergence_verdict="reversed", database_uuid=database_uuid,
        ))
    return telemetry_records


def _connect_to_managed_database(repo_root: Path, env: dict[str, str]):
    import MySQLdb

    port = compose_port("db", 3306, repo_root, env)
    return MySQLdb.connect(host="127.0.0.1", port=port, user="root", passwd=_root_password(env), charset="utf8mb4")


class _NoopFaultController:
    """Production stand-in for the test-only `AttributeFaultController`: every
    frozen fault point is a no-op outside the disposable test boundary."""

    def hit(self, point: str) -> None:
        return None


def apply_managed_indexes(repo_root: Path, env: dict[str, str], *, indexes: list[ManagedIndex] | None = None) -> list[tuple[str, str]]:
    """Production entrypoint: open a real connection to the seeded `db`
    compose service (via its published host port) and apply every managed
    index through the exact same core logic the schema lane exercises
    directly against a disposable database."""
    target_indexes = MANAGED_INDEXES if indexes is None else indexes
    connection = _connect_to_managed_database(repo_root, env)
    try:
        telemetry_records = apply_managed_indexes_on_connection(connection, target_indexes, _NoopFaultController())
    finally:
        connection.close()
    return [_telemetry_fqn_status(record) for record in telemetry_records]


def _telemetry_fqn_status(record: dict) -> tuple[str, str]:
    operation = record["operation"]
    fqn = f"{operation['database']}.{operation['table']}.{operation['index']}"
    return (fqn, record["post_state"]["convergence_verdict"])


def reverse_managed_indexes(repo_root: Path, env: dict[str, str], *, indexes: list[ManagedIndex] | None = None) -> list[tuple[str, str]]:
    """Production entrypoint for reverse. Never call this against a live
    populated `seek_production` database from an automated task; reverse is a
    separate, explicit user gate."""
    target_indexes = MANAGED_INDEXES if indexes is None else indexes
    connection = _connect_to_managed_database(repo_root, env)
    try:
        telemetry_records = reverse_managed_indexes_on_connection(connection, target_indexes)
    finally:
        connection.close()
    return [_telemetry_fqn_status(record) for record in telemetry_records]


def apply_all(repo_root: Path, env: dict[str, str], *, indexes: list[ManagedIndex] | None = None) -> list[tuple[str, str]]:
    results = apply_column_fixups(repo_root, env)
    results.extend(apply_managed_indexes(repo_root, env, indexes=indexes))
    return results
