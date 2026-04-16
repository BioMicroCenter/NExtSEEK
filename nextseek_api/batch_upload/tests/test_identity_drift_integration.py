"""Wave 3 end-to-end integration tests for identity drift handling.

Exercises the real orchestrator path against:
- a throwaway MariaDB database seeded via raw SQL
- a dedicated Neo4j test database on a real Neo4j server

The test intentionally covers both synchronous graph upload and the follow-up
orphan-resolution step so the always-on production path stays aligned.

Safety contract:
- the test never targets the ambient/default Neo4j database
- the operator must opt in explicitly before any graph cleanup or writes occur
"""
from __future__ import annotations

import csv
import json
import os
import time
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path

import pytest
import django
from django.conf import settings
from django.test.utils import override_settings
from sqlalchemy import create_engine, text

from nextseek_api.batch_upload.config import Neo4jConfig
from nextseek_api.batch_upload.db_engine import CAPABILITIES, dispose_engine
from nextseek_api.batch_upload.orchestrator import run_batch_upload_multi
from nextseek_api.batch_upload.orphan_resolution import discover_orphans, resolve_orphans
from nextseek_api.batch_upload.prefetch import clear_caches


pymysql = pytest.importorskip("pymysql")
pytest.importorskip("sqlalchemy")
pytest.importorskip("neo4j")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
django.setup()

try:
    _NEO4J_CFG = Neo4jConfig.from_django_settings()
except Exception:
    _NEO4J_CFG = None

_ALLOW_NEO4J_TEST_DB = os.environ.get("WAVE3_ALLOW_NEO4J_TEST_DB", "").strip() == "1"
_NEO4J_DB_READY_TIMEOUT_S = float(os.environ.get("WAVE3_NEO4J_DB_READY_TIMEOUT_S", "30"))
_NEO4J_AUTO_DB_PREFIX = "wave3-auto-"


def _mariadb_conn_info() -> tuple[str | None, str | None, str | None, int | None]:
    host = os.environ.get("SPIKE_DB_HOST")
    user = os.environ.get("SPIKE_DB_USER")
    password = os.environ.get("SPIKE_DB_PASSWORD")
    port = os.environ.get("SPIKE_DB_PORT")
    if host and user and password:
        return host, user, password, int(port or "3306")

    db = settings.DATABASES.get("seek") or {}
    engine = db.get("ENGINE", "")
    if "mysql" not in engine:
        return None, None, None, None

    host = db.get("HOST") or "127.0.0.1"
    user = db.get("USER") or None
    password = db.get("PASSWORD") or None
    port = int(str(db.get("PORT") or "3306"))
    if not user or password is None:
        return None, None, None, None
    return host, user, password, port


_HOST, _USER, _PASS, _PORT = _mariadb_conn_info()
_HAS_DB_FIXTURE = all([_HOST, _USER, _PASS, _PORT])

FIXTURES_DIR = Path(__file__).with_name("fixtures")
DEFAULT_FIXTURE = FIXTURES_DIR / "wave3_default_mode.xlsx"
UPDATE_FIXTURE = FIXTURES_DIR / "wave3_update_mode.xlsx"

PROJECT_ID = 1
CONTRIBUTOR_ID = 1
STUDY_ID = 701
STUDY_TITLE = "Wave 3 Integration Study"
NAME_SAMPLE_TYPE_ID = 501
FILE_SAMPLE_TYPE_ID = 502
NAME_SAMPLE_TYPE = "WTNAM_blood"
FILE_SAMPLE_TYPE = "D.WTFIL_files"

UID_DRIFT_A = "WTNAM-240301BMC-1"
UID_DRIFT_B = "WTNAM-240301BMC-2"
UID_DRIFT_C = "WTNAM-240301BMC-3"
UID_DRIFT_W = "WTNAM-240301BMC-4"
UID_RARE_UID_ONLY = "WTNAM-240301BMC-5"
UID_DUP_1 = "WTNAM-240301BMC-6"
UID_DUP_2 = "WTNAM-240301BMC-7"
UID_FILE_PARENT = "D.WTFIL-240301BMC-1"
UID_PREEXISTING_ORPHAN = "WTNAM-240301BMC-8"

SEEDED_COUNTS = {
    "drift-a": 1,
    "drift-b": 1,
    "drift-c": 1,
    "drift-w": 1,
    "dup": 2,
    "drift-d.fastq": 1,
}

CREATE_TABLES_SQL = [
    """
    CREATE TABLE sample_types (
      id INT PRIMARY KEY,
      title VARCHAR(255) NOT NULL UNIQUE
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE studies (
      id INT PRIMARY KEY,
      title VARCHAR(255),
      description TEXT,
      investigation_id INT DEFAULT NULL
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE investigations (
      id INT PRIMARY KEY,
      title VARCHAR(255),
      description TEXT
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE assays (
      id INT PRIMARY KEY,
      title VARCHAR(255),
      study_id INT DEFAULT NULL
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE policies (
      id INT AUTO_INCREMENT PRIMARY KEY,
      name VARCHAR(255),
      access_type INT,
      created_at DATETIME,
      updated_at DATETIME
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE permissions (
      id INT AUTO_INCREMENT PRIMARY KEY,
      contributor_type VARCHAR(255),
      contributor_id INT,
      policy_id INT,
      access_type INT,
      created_at DATETIME,
      updated_at DATETIME
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE projects_sample_types (
      project_id INT,
      sample_type_id INT,
      UNIQUE KEY uq_projects_sample_types (project_id, sample_type_id)
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE projects_samples (
      project_id INT,
      sample_id INT,
      UNIQUE KEY uq_projects_samples (project_id, sample_id)
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE assay_assets (
      assay_id INT,
      asset_id INT,
      version INT,
      created_at DATETIME,
      updated_at DATETIME,
      relationship_type_id INT DEFAULT NULL,
      asset_type VARCHAR(32),
      direction INT DEFAULT 1
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE sops (
      id INT PRIMARY KEY,
      title VARCHAR(255)
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE internal_assays (
      id INT PRIMARY KEY,
      internal_assay_title VARCHAR(255)
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE assays_internal_assays (
      assay_id INT,
      internal_assay_id INT
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE samples (
      id INT AUTO_INCREMENT PRIMARY KEY,
      title VARCHAR(255),
      sample_type_id INT,
      json_metadata LONGTEXT,
      uuid VARCHAR(255) UNIQUE,
      contributor_id INT DEFAULT NULL,
      policy_id INT DEFAULT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      first_letter VARCHAR(1),
      other_creators TEXT,
      originating_data_file_id INT DEFAULT NULL,
      deleted_contributor VARCHAR(255)
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def _load_migration_sql():
    mod = import_module("seek.migrations.0002_samples_name_identity")
    return mod.FORWARD_SQL


def _neo4j_skip_reason() -> str | None:
    if not _NEO4J_CFG:
        return "Wave 3 integration requires Django Neo4j settings"
    if not _NEO4J_CFG.URI or not _NEO4J_CFG.NEO4J_USER or not _NEO4J_CFG.PASSWORD:
        return "Wave 3 integration requires configured Neo4j URI and auth"
    if not _ALLOW_NEO4J_TEST_DB:
        return "Set WAVE3_ALLOW_NEO4J_TEST_DB=1 to opt into Neo4j test DB writes"
    return None


def _live_test_skip_reason() -> str | None:
    if not _HAS_DB_FIXTURE:
        return "Wave 3 integration requires MariaDB access via DATABASES['seek'] or SPIKE_DB_* overrides"
    return _neo4j_skip_reason()


def _generate_neo4j_db_name() -> str:
    return f"{_NEO4J_AUTO_DB_PREFIX}{int(time.time() * 1000)}"


def _assert_safe_auto_db_name(neo4j_db: str) -> None:
    if not neo4j_db.startswith(_NEO4J_AUTO_DB_PREFIX):
        raise RuntimeError(f"Refusing to manage non-auto Neo4j DB name: {neo4j_db!r}")
    if neo4j_db in {"neo4j", "system"}:
        raise RuntimeError(f"Refusing unsafe Neo4j DB name: {neo4j_db!r}")
    if _NEO4J_CFG and _NEO4J_CFG.NEO4J_DB and neo4j_db == _NEO4J_CFG.NEO4J_DB:
        raise RuntimeError(
            f"Refusing to operate on ambient configured Neo4j DB: {neo4j_db!r}"
        )


def _neo4j_test_settings(neo4j_db: str) -> dict:
    reason = _neo4j_skip_reason()
    if reason:
        raise RuntimeError(reason)
    _assert_safe_auto_db_name(neo4j_db)
    return {
        "NAME": neo4j_db,
        "URI": _NEO4J_CFG.URI,
        "AUTH": (_NEO4J_CFG.NEO4J_USER, _NEO4J_CFG.PASSWORD),
    }


@contextmanager
def _neo4j_driver():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        _NEO4J_CFG.URI,
        auth=(_NEO4J_CFG.NEO4J_USER, _NEO4J_CFG.PASSWORD),
    )
    try:
        yield driver
    finally:
        driver.close()


def _wait_for_neo4j_db_online(driver, neo4j_db: str, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    last_status = None
    while time.time() < deadline:
        result = driver.execute_query(
            "SHOW DATABASE $db YIELD name, currentStatus RETURN currentStatus",
            {"db": neo4j_db},
            database_="system",
        )
        if result.records:
            last_status = result.records[0]["currentStatus"]
            if last_status == "online":
                return
        time.sleep(0.5)
    raise RuntimeError(
        f"Neo4j database {neo4j_db!r} did not reach 'online' within {timeout_s}s "
        f"(last status: {last_status!r})"
    )


def _create_neo4j_db(driver, neo4j_db: str) -> None:
    _assert_safe_auto_db_name(neo4j_db)
    driver.execute_query(
        f"CREATE DATABASE `{neo4j_db}` IF NOT EXISTS", database_="system"
    )
    _wait_for_neo4j_db_online(driver, neo4j_db, _NEO4J_DB_READY_TIMEOUT_S)


def _drop_neo4j_db(driver, neo4j_db: str) -> None:
    _assert_safe_auto_db_name(neo4j_db)
    driver.execute_query(
        f"DROP DATABASE `{neo4j_db}` IF EXISTS", database_="system"
    )


def _seed_sample(cur, *, sample_id: int, title: str, uuid: str, json_meta: str, sample_type_id: int) -> None:
    cur.execute(
        """
        INSERT INTO samples
        (id, title, sample_type_id, json_metadata, uuid, contributor_id, policy_id, first_letter, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """,
        (sample_id, title, sample_type_id, json_meta, uuid, CONTRIBUTOR_ID, sample_id, uuid[0].lower()),
    )


@pytest.fixture
def integration_env():
    reason = _live_test_skip_reason()
    if reason:
        pytest.skip(reason)

    neo4j_db = _generate_neo4j_db_name()
    neo4j_settings = _neo4j_test_settings(neo4j_db)
    db_name = f"test_w3_{int(time.time() * 1000)}"
    raw = pymysql.connect(host=_HOST, user=_USER, password=_PASS, port=_PORT, autocommit=True)
    with _neo4j_driver() as driver:
        _create_neo4j_db(driver, neo4j_db)
        try:
            with raw.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                cur.execute(f"USE `{db_name}`")
                for sql in CREATE_TABLES_SQL:
                    cur.execute(sql)
                cur.execute(_load_migration_sql())

                cur.execute(
                    "INSERT INTO sample_types (id, title) VALUES (%s, %s), (%s, %s)",
                    (NAME_SAMPLE_TYPE_ID, NAME_SAMPLE_TYPE, FILE_SAMPLE_TYPE_ID, FILE_SAMPLE_TYPE),
                )
                cur.execute(
                    "INSERT INTO projects_sample_types (project_id, sample_type_id) VALUES (%s, %s), (%s, %s)",
                    (PROJECT_ID, NAME_SAMPLE_TYPE_ID, PROJECT_ID, FILE_SAMPLE_TYPE_ID),
                )
                cur.execute("INSERT INTO studies (id, title, description) VALUES (%s, %s, %s)", (STUDY_ID, STUDY_TITLE, ""))
                cur.execute("INSERT INTO assays (id, title, study_id) VALUES (401, 'Wave 3 Assay', %s)", (STUDY_ID,))

                for policy_id in range(1001, 1010):
                    cur.execute(
                        "INSERT INTO policies (id, name, access_type, created_at, updated_at) VALUES (%s, %s, 0, NOW(), NOW())",
                        (policy_id, f"policy-{policy_id}"),
                    )

                _seed_sample(
                    cur,
                    sample_id=1001,
                    title=UID_DRIFT_A,
                    uuid=UID_DRIFT_A,
                    json_meta=json.dumps({"Name": "drift-a", "UID": UID_DRIFT_A}),
                    sample_type_id=NAME_SAMPLE_TYPE_ID,
                )
                _seed_sample(
                    cur,
                    sample_id=1002,
                    title="Undefined",
                    uuid=UID_DRIFT_B,
                    json_meta=json.dumps({"Name": "drift-b", "UID": UID_DRIFT_B}),
                    sample_type_id=NAME_SAMPLE_TYPE_ID,
                )
                _seed_sample(
                    cur,
                    sample_id=1003,
                    title="drift-c",
                    uuid=UID_DRIFT_C,
                    json_meta=json.dumps({"Name": "drift-c", "UID": UID_DRIFT_C}),
                    sample_type_id=NAME_SAMPLE_TYPE_ID,
                )
                _seed_sample(
                    cur,
                    sample_id=1004,
                    title=UID_DRIFT_W,
                    uuid=UID_DRIFT_W,
                    json_meta=json.dumps({"Name": "drift-w", "UID": UID_DRIFT_W}),
                    sample_type_id=NAME_SAMPLE_TYPE_ID,
                )
                _seed_sample(
                    cur,
                    sample_id=1005,
                    title="legacy-v",
                    uuid=UID_RARE_UID_ONLY,
                    json_meta=json.dumps({"UID": UID_RARE_UID_ONLY}),
                    sample_type_id=NAME_SAMPLE_TYPE_ID,
                )
                _seed_sample(
                    cur,
                    sample_id=1006,
                    title=UID_DUP_1,
                    uuid=UID_DUP_1,
                    json_meta=json.dumps({"Name": "dup", "UID": UID_DUP_1}),
                    sample_type_id=NAME_SAMPLE_TYPE_ID,
                )
                _seed_sample(
                    cur,
                    sample_id=1007,
                    title=UID_DUP_2,
                    uuid=UID_DUP_2,
                    json_meta=json.dumps({"Name": "dup", "UID": UID_DUP_2}),
                    sample_type_id=NAME_SAMPLE_TYPE_ID,
                )
                _seed_sample(
                    cur,
                    sample_id=1008,
                    title="drift-d.fastq",
                    uuid=UID_FILE_PARENT,
                    json_meta=json.dumps({"File_PrimaryData": "drift-d.fastq", "UID": UID_FILE_PARENT}),
                    sample_type_id=FILE_SAMPLE_TYPE_ID,
                )
                _seed_sample(
                    cur,
                    sample_id=1009,
                    title="orphan-needs-resolution",
                    uuid=UID_PREEXISTING_ORPHAN,
                    json_meta=json.dumps({"Name": "orphan-needs-resolution", "Parent": "future-parent"}),
                    sample_type_id=NAME_SAMPLE_TYPE_ID,
                )

            driver.execute_query(
                """
                UNWIND $rows AS row
                MERGE (s:Sample {uuid: row.uuid})
                SET s.id = row.id, s.type = row.sample_type, s.parent_titles = row.parent_titles
                """,
                {"rows": [
                    {"uuid": UID_DRIFT_A, "id": 1001, "sample_type": NAME_SAMPLE_TYPE, "parent_titles": []},
                    {"uuid": UID_FILE_PARENT, "id": 1008, "sample_type": FILE_SAMPLE_TYPE, "parent_titles": []},
                    {"uuid": UID_PREEXISTING_ORPHAN, "id": 1009, "sample_type": NAME_SAMPLE_TYPE, "parent_titles": ["future-parent"]},
                ]},
                database_=neo4j_db,
            )

            yield {"conn": raw, "db_name": db_name, "neo4j_settings": neo4j_settings}
        finally:
            try:
                with raw.cursor() as cur:
                    cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
            except Exception:
                pass
            raw.close()
            dispose_engine()
            CAPABILITIES["insert_returning"] = False
            clear_caches()
            _drop_neo4j_db(driver, neo4j_db)


def _db_settings(db_name: str) -> dict:
    db = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": db_name,
        "USER": _USER,
        "PASSWORD": _PASS,
        "HOST": _HOST,
        "PORT": str(_PORT),
    }
    return {"default": db, "seek": db}


def _count_by_identity(conn, identity: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM samples WHERE name_identity = %s", (identity,))
        return int(cur.fetchone()[0])


def _fetch_one(conn, sql: str, params: tuple):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _read_summary_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _find_summary_row(rows: list[dict], *, uid: str | None = None, status: str | None = None, reason_substr: str | None = None) -> dict:
    for row in rows:
        if uid is not None and row.get("uid") != uid:
            continue
        if status is not None and row.get("status") != status:
            continue
        if reason_substr is not None and reason_substr not in (row.get("reason") or ""):
            continue
        return row
    raise AssertionError(f"Summary row not found for uid={uid!r} status={status!r} reason_substr={reason_substr!r}")


def _query_graph(driver, neo4j_db: str, cypher: str, params: dict | None = None):
    return driver.execute_query(cypher, params or {}, database_=neo4j_db)


def _run_batch(db_name: str, neo4j_db: str, fixture_path: Path, *, update_existing: bool, output_dir: str) -> dict:
    dispose_engine()
    CAPABILITIES["insert_returning"] = False
    clear_caches()
    Neo4jConfig.from_django_settings.cache_clear()
    with override_settings(
        DATABASES=_db_settings(db_name),
        NEXTSEEK_DATABASE="seek",
        NEO4J_DATABASE=_neo4j_test_settings(neo4j_db),
    ):
        return run_batch_upload_multi(
            xlsx_paths=[str(fixture_path)],
            project_id=PROJECT_ID,
            contributor_id=CONTRIBUTOR_ID,
            lababbv="BMC",
            config=import_module("nextseek_api.batch_upload.config").BatchUploadConfig(
                update_existing=update_existing,
                enable_auto_permissions=False,
            ),
            output_dir=output_dir,
        )


class TestIdentityDriftIntegration:
    def test_default_mode_end_to_end(self, integration_env, tmp_path):
        conn = integration_env["conn"]
        db_name = integration_env["db_name"]
        neo4j_db = integration_env["neo4j_settings"]["NAME"]
        pre_counts = {identity: _count_by_identity(conn, identity) for identity in SEEDED_COUNTS}

        result = _run_batch(db_name, neo4j_db,DEFAULT_FIXTURE, update_existing=False, output_dir=str(tmp_path))
        rows = _read_summary_rows(result["summary_path"])

        assert result["totals"]["skipped"] >= 5
        assert result["totals"]["failed"] >= 1
        ambiguous_row = _find_summary_row(rows, status="failed", reason_substr="ambiguous identity match")
        assert "[1006, 1007]" in ambiguous_row["reason"]
        assert _find_summary_row(rows, uid=UID_DRIFT_A, status="skipped")["reason"] == "name_already_exists"
        assert _find_summary_row(rows, uid=UID_RARE_UID_ONLY, status="skipped")["reason"] == "duplicate"

        for identity, expected in SEEDED_COUNTS.items():
            assert _count_by_identity(conn, identity) == expected

        orphan_child = _fetch_one(
            conn,
            "SELECT uuid, json_metadata FROM samples WHERE name_identity = %s",
            ("orphan-child",),
        )
        assert orphan_child is not None
        orphan_meta = json.loads(orphan_child[1])
        assert orphan_meta["Parent"] == "missing-parent"

        child_by_name = _fetch_one(
            conn,
            "SELECT uuid, json_metadata FROM samples WHERE name_identity = %s",
            ("child-by-name",),
        )
        assert child_by_name is not None
        child_by_name_uid, child_by_name_meta_raw = child_by_name
        child_by_name_meta = json.loads(child_by_name_meta_raw)
        assert child_by_name_meta["Parent"] == UID_DRIFT_A

        child_by_file = _fetch_one(
            conn,
            "SELECT uuid, json_metadata FROM samples WHERE name_identity = %s",
            ("child-by-file",),
        )
        assert child_by_file is not None
        child_by_file_uid, child_by_file_meta_raw = child_by_file
        child_by_file_meta = json.loads(child_by_file_meta_raw)
        assert child_by_file_meta["Parent"] == UID_FILE_PARENT

        with _neo4j_driver() as driver:
            graph_child = _query_graph(
                driver,
                neo4j_db,
                "MATCH (s:Sample {uuid: $uuid}) RETURN s.parent_titles AS parent_titles",
                {"uuid": orphan_child[0]},
            )
            assert graph_child.records[0]["parent_titles"] == ["missing-parent"]

            rel_by_name = _query_graph(
                driver,
                neo4j_db,
                """
                MATCH (:Sample {uuid: $child})-[:DERIVED_FROM]->(:Sample {uuid: $parent})
                RETURN count(*) AS c
                """,
                {"child": child_by_name_uid, "parent": UID_DRIFT_A},
            )
            assert rel_by_name.records[0]["c"] == 1

            rel_by_file = _query_graph(
                driver,
                neo4j_db,
                """
                MATCH (:Sample {uuid: $child})-[:DERIVED_FROM]->(:Sample {uuid: $parent})
                RETURN count(*) AS c
                """,
                {"child": child_by_file_uid, "parent": UID_FILE_PARENT},
            )
            assert rel_by_file.records[0]["c"] == 1

            future_parent_uid = result["_identity_map"]["future-parent"]
            discover = discover_orphans(driver, neo4j_db, result["_identity_map"])
            assert any(orphan["uuid"] == UID_PREEXISTING_ORPHAN for orphan in discover)

            engine = create_engine(f"mysql+pymysql://{_USER}:{_PASS}@{_HOST}:{_PORT}/{db_name}")
            try:
                with engine.begin() as sql_conn:
                    stats = resolve_orphans(
                        orphans=discover,
                        parent_info=result["_parent_info"],
                        sql_conn=sql_conn,
                        neo4j_driver=driver,
                        neo4j_database=neo4j_db,
                    )
            finally:
                engine.dispose()

            assert stats["resolved"] >= 1
            orphan_row = _fetch_one(
                conn,
                "SELECT json_metadata FROM samples WHERE uuid = %s",
                (UID_PREEXISTING_ORPHAN,),
            )
            orphan_meta = json.loads(orphan_row[0])
            assert orphan_meta["Parent"] == future_parent_uid

            orphan_rel = _query_graph(
                driver,
                neo4j_db,
                """
                MATCH (:Sample {uuid: $child})-[:DERIVED_FROM]->(:Sample {uuid: $parent})
                RETURN count(*) AS c
                """,
                {"child": UID_PREEXISTING_ORPHAN, "parent": future_parent_uid},
            )
            assert orphan_rel.records[0]["c"] == 1

    def test_update_existing_mode_end_to_end(self, integration_env, tmp_path):
        conn = integration_env["conn"]
        db_name = integration_env["db_name"]
        neo4j_db = integration_env["neo4j_settings"]["NAME"]
        pre_counts = {identity: _count_by_identity(conn, identity) for identity in SEEDED_COUNTS}

        result = _run_batch(db_name, neo4j_db,UPDATE_FIXTURE, update_existing=True, output_dir=str(tmp_path))
        rows = _read_summary_rows(result["summary_path"])

        assert result["totals"]["updated"] >= 2
        assert result["totals"]["failed"] >= 1
        ambiguous_row = _find_summary_row(rows, status="failed", reason_substr="ambiguous identity match")
        assert "[1006, 1007]" in ambiguous_row["reason"]

        for identity, expected in pre_counts.items():
            assert _count_by_identity(conn, identity) == expected

        file_row = _fetch_one(
            conn,
            "SELECT json_metadata, title FROM samples WHERE uuid = %s",
            (UID_FILE_PARENT,),
        )
        assert file_row is not None
        file_meta = json.loads(file_row[0])
        assert file_meta["File_PrimaryData"] == "drift-d.fastq"
        assert "File_PrimartyData" not in file_meta

        uid_only_row = _fetch_one(
            conn,
            "SELECT title, json_metadata FROM samples WHERE uuid = %s",
            (UID_RARE_UID_ONLY,),
        )
        assert uid_only_row is not None
        assert uid_only_row[0] == UID_RARE_UID_ONLY
        uid_only_meta = json.loads(uid_only_row[1])
        assert uid_only_meta["UID"] == UID_RARE_UID_ONLY

        drift_a_row = _fetch_one(
            conn,
            "SELECT json_metadata FROM samples WHERE uuid = %s",
            (UID_DRIFT_A,),
        )
        drift_a_meta = json.loads(drift_a_row[0])
        assert drift_a_meta["Scientist"] == "Updated Scientist"

        with _neo4j_driver() as driver:
            sample_props = _query_graph(
                driver,
                neo4j_db,
                "MATCH (s:Sample {uuid: $uuid}) RETURN s.Scientist AS scientist",
                {"uuid": UID_DRIFT_A},
            )
            assert sample_props.records[0]["scientist"] == "Updated Scientist"

            future_parent_uid = result["_identity_map"]["future-parent"]
            discover = discover_orphans(driver, neo4j_db, result["_identity_map"])
            engine = create_engine(f"mysql+pymysql://{_USER}:{_PASS}@{_HOST}:{_PORT}/{db_name}")
            try:
                with engine.begin() as sql_conn:
                    stats = resolve_orphans(
                        orphans=discover,
                        parent_info=result["_parent_info"],
                        sql_conn=sql_conn,
                        neo4j_driver=driver,
                        neo4j_database=neo4j_db,
                    )
            finally:
                engine.dispose()

            assert stats["resolved"] >= 1
            orphan_rel = _query_graph(
                driver,
                neo4j_db,
                """
                MATCH (:Sample {uuid: $child})-[:DERIVED_FROM]->(:Sample {uuid: $parent})
                RETURN count(*) AS c
                """,
                {"child": UID_PREEXISTING_ORPHAN, "parent": future_parent_uid},
            )
            assert orphan_rel.records[0]["c"] == 1
