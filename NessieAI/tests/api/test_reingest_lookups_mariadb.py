"""The raw SQL in nextseek_api/services/reingest_lookups.py, run on a real MariaDB/MySQL.

test_reingest_lookups.py checks what each query is sent and what the code makes of the rows a
stub hands back; nothing there proves the SQL itself runs. This module builds two throwaway
schemas holding the minimal tables those queries read, the way a box lays them out: SEEK's
tables in one schema (the `seek` alias), NExtSEEK's assay tables in another (`default`), so the
cross-schema joins and the alias each query picks are exercised as well. No SEEK instance is
needed. Skipped unless SPIKE_DB_HOST/USER/PASSWORD are set (the MariaDB lane of ci-pytest.yml
sets them); it never falls back to a configured database.
"""
from __future__ import annotations

import json
import os
import uuid
from unittest.mock import patch

import pytest

pymysql = pytest.importorskip("pymysql")

from nextseek_api.services import reingest_lookups  # noqa: E402
from NessieAI.tests.api.test_reingest_lookups import (  # noqa: E402
    LAB_CODES,
    LAB_FIXTURE,
    _old_lab_for_code,
)

_HOST = os.environ.get("SPIKE_DB_HOST")
_USER = os.environ.get("SPIKE_DB_USER")
_PASS = os.environ.get("SPIKE_DB_PASSWORD")
_PORT = int(os.environ.get("SPIKE_DB_PORT") or "3306")

pytestmark = pytest.mark.skipif(
    not (_HOST and _USER and _PASS), reason="needs SPIKE_DB_HOST/USER/PASSWORD (MariaDB)"
)

_TABLE = "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"

# SEEK's tables, with the columns these queries read (types as SEEK declares them).
_SEEK_DDL = [
    f"""CREATE TABLE samples (
        id INT AUTO_INCREMENT PRIMARY KEY, uuid VARCHAR(255), sample_type_id INT,
        json_metadata TEXT) {_TABLE}""",
    f"CREATE TABLE projects_samples (project_id INT, sample_id INT) {_TABLE}",
    f"""CREATE TABLE assay_assets (
        id INT AUTO_INCREMENT PRIMARY KEY, assay_id INT, asset_id INT, asset_type VARCHAR(255)) {_TABLE}""",
    f"CREATE TABLE assays (id INT PRIMARY KEY, study_id INT, title VARCHAR(255)) {_TABLE}",
    f"CREATE TABLE studies (id INT PRIMARY KEY, title VARCHAR(255)) {_TABLE}",
]

# NExtSEEK's (dmac) tables on `default`.
_DMAC_DDL = [
    f"CREATE TABLE internal_assays (id INT PRIMARY KEY, internal_assay_title VARCHAR(255)) {_TABLE}",
    f"CREATE TABLE assays_internal_assays (assay_id INT, internal_assay_id INT) {_TABLE}",
    f"CREATE TABLE assay_context (assay_name VARCHAR(255), Alternative_Assay_Names TEXT) {_TABLE}",
]


def _connect(database=None):
    return pymysql.connect(host=_HOST, user=_USER, password=_PASS, port=_PORT, database=database,
                           charset="utf8mb4", autocommit=True)


class _Connections:
    """Stands in for django.db.connections: each alias is a real connection to its schema."""

    def __init__(self, schemas):
        self.schemas = schemas
        self.used = []

    def __getitem__(self, alias):
        self.used.append(alias)
        return _connect(self.schemas[alias])


@pytest.fixture
def two_schemas(settings):
    tag = uuid.uuid4().hex[:10]
    seek_db, dmac_db = f"rl_seek_{tag}", f"rl_dmac_{tag}"
    admin = _connect()
    try:
        with admin.cursor() as c:
            for name in (seek_db, dmac_db):
                c.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        with _connect(seek_db).cursor() as c:
            for ddl in _SEEK_DDL:
                c.execute(ddl)
        with _connect(dmac_db).cursor() as c:
            for ddl in _DMAC_DDL:
                c.execute(ddl)
        settings.SEEK_DATABASE = "seek"
        settings.DATABASES = {**settings.DATABASES,
                              "seek": {**settings.DATABASES["default"], "NAME": seek_db}}
        connections = _Connections({"seek": seek_db, "default": dmac_db})
        with patch("django.db.connections", connections):
            yield connections, seek_db, dmac_db
    finally:
        with admin.cursor() as c:
            for name in (seek_db, dmac_db):
                c.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()


def _run(database, statements):
    with _connect(database).cursor() as c:
        for sql, params in statements:
            c.execute(sql, params)


def _seed_studies(seek_db, dmac_db):
    """Three parents: D.SEQ-1 and D.SEQ-2 in study 51 (which holds a Genome Alignment assay 315 and an
    untagged one), D.SEQ-3 in study 25 (no such assay), and D.SEQ-4 in no study; projects 14 and 3."""
    _run(seek_db, [
        ("INSERT INTO samples (id, uuid, sample_type_id, json_metadata) VALUES (%s, %s, 1, '{}')", [i, f"D.SEQ-{i}"])
        for i in (1, 2, 3, 4)
    ] + [
        ("INSERT INTO projects_samples (project_id, sample_id) VALUES (%s, %s)", pair)
        for pair in ([14, 1], [14, 2], [3, 3], [14, 3])
    ] + [
        ("INSERT INTO studies (id, title) VALUES (%s, %s)", [51, "Study A"]),
        ("INSERT INTO studies (id, title) VALUES (%s, %s)", [25, "Study B"]),
        ("INSERT INTO assays (id, study_id, title) VALUES (%s, %s, %s)", [100, 51, "Sequencing"]),
        ("INSERT INTO assays (id, study_id, title) VALUES (%s, %s, %s)", [101, 25, "Sequencing"]),
        ("INSERT INTO assays (id, study_id, title) VALUES (%s, %s, %s)", [315, 51, "Genome Alignment"]),
        ("INSERT INTO assays (id, study_id, title) VALUES (%s, %s, %s)", [470, 51, "Genome Alignment - Data Linked"]),
        ("INSERT INTO assays (id, study_id, title) VALUES (%s, %s, %s)", [471, 51, "genome alignment - data linked"]),
        ("INSERT INTO assays (id, study_id, title) VALUES (%s, %s, %s)", [472, 51, "Genome Alignment - Data Linked"]),
        ("INSERT INTO assay_assets (assay_id, asset_id, asset_type) VALUES (%s, %s, %s)", [100, 1, "Sample"]),
        ("INSERT INTO assay_assets (assay_id, asset_id, asset_type) VALUES (%s, %s, %s)", [100, 2, "Sample"]),
        ("INSERT INTO assay_assets (assay_id, asset_id, asset_type) VALUES (%s, %s, %s)", [101, 3, "Sample"]),
        ("INSERT INTO assay_assets (assay_id, asset_id, asset_type) VALUES (%s, %s, %s)", [101, 4, "DataFile"]),
    ])
    _run(dmac_db, [
        ("INSERT INTO internal_assays (id, internal_assay_title) VALUES (%s, %s)", [34, "Genome Alignment"]),
        ("INSERT INTO internal_assays (id, internal_assay_title) VALUES (%s, %s)", [35, "Variant Call Filtering"]),
        ("INSERT INTO assays_internal_assays (assay_id, internal_assay_id) VALUES (%s, %s)", [315, 34]),
        ("INSERT INTO assays_internal_assays (assay_id, internal_assay_id) VALUES (%s, %s)", [472, 35]),
        ("INSERT INTO assay_context (assay_name, Alternative_Assay_Names) VALUES (%s, %s)",
         ["Variant Call Filtering", "Variant Calling Analysis, Variant Calling"]),
        ("INSERT INTO assay_context (assay_name, Alternative_Assay_Names) VALUES (%s, %s)",
         ["Genome Alignment", None]),
    ])


def test_project_ids_for_uids(two_schemas):
    connections, seek_db, dmac_db = two_schemas
    _seed_studies(seek_db, dmac_db)
    assert reingest_lookups.project_ids_for_uids_strict(["D.SEQ-1", "D.SEQ-3", "D.SEQ-9"]) == [3, 14]
    assert connections.used == ["seek"]


def test_study_assays_for_parents(two_schemas):
    connections, seek_db, dmac_db = two_schemas
    _seed_studies(seek_db, dmac_db)
    plan = reingest_lookups.study_assays_for_parents_strict(
        ["D.SEQ-1", "D.SEQ-2", "D.SEQ-3", "D.SEQ-4"], "Genome Alignment")
    assert plan.studies == {25: [], 51: [315]}
    assert plan.parents_by_study == {25: ["D.SEQ-3"], 51: ["D.SEQ-1", "D.SEQ-2"]}
    assert plan.parents_without_study == ["D.SEQ-4"]   # its only link is a DataFile's
    assert plan.study_titles == {25: "Study B", 51: "Study A"}
    assert connections.used == ["default"]


def test_house_assay_title(two_schemas):
    _, seek_db, dmac_db = two_schemas
    _seed_studies(seek_db, dmac_db)
    assert reingest_lookups.house_assay_title_strict("Variant Calling Analysis") == "Variant Call Filtering"
    assert reingest_lookups.house_assay_title_strict("Genome Alignment") == "Genome Alignment"
    assert reingest_lookups.house_assay_title_strict("No Such Assay") is None


def test_assay_ids_in_study_and_the_house_assay_id(two_schemas):
    _, seek_db, dmac_db = two_schemas
    _seed_studies(seek_db, dmac_db)
    assert reingest_lookups.assay_ids_in_study_strict(51, "Genome Alignment") == [315]
    assert reingest_lookups.assay_ids_in_study_strict(25, "Genome Alignment") == []
    assert reingest_lookups.internal_assay_id_strict("Genome Alignment") == 34
    assert reingest_lookups.internal_assay_id_strict("No Such Assay") is None


def test_untagged_assays_in_study(two_schemas):
    # 471 matches only under the case-insensitive collation and is dropped in Python; 472 is tagged.
    _, seek_db, dmac_db = two_schemas
    _seed_studies(seek_db, dmac_db)
    assert reingest_lookups.untagged_assay_ids_in_study_strict(51, "Genome Alignment - Data Linked") == [470]


def test_lab_for_code_answers_as_the_whole_table_loop_did(two_schemas, monkeypatch):
    connections, seek_db, _ = two_schemas
    _run(seek_db, [("INSERT INTO samples (uuid, sample_type_id, json_metadata) VALUES (%s, 1, %s)",
                    [f"S-{i}", raw]) for i, raw in enumerate(LAB_FIXTURE)])
    for code in LAB_CODES:
        monkeypatch.setattr(reingest_lookups, "_lab_spellings_cache", None)
        assert reingest_lookups.lab_for_code_strict(code) == _old_lab_for_code(LAB_FIXTURE, code), code
    assert set(connections.used) == {"seek"}


def test_lab_spellings_skip_a_malformed_row_instead_of_failing(two_schemas, monkeypatch):
    _, seek_db, _ = two_schemas
    _run(seek_db, [("INSERT INTO samples (uuid, sample_type_id, json_metadata) VALUES (%s, 1, %s)", pair)
                   for pair in (["S-1", '{"Lab": "Abcdef Lab"'], ["S-2", json.dumps({"Lab": "Abcdef Lab"})])])
    monkeypatch.setattr(reingest_lookups, "_lab_spellings_cache", None)
    assert reingest_lookups._read_lab_spellings_strict() == {"Abcdef Lab": 1}
