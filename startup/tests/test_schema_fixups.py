"""Section 11.5 pinned real-boundary contract for the T03 physical SEEK-schema
safeguards: managed, ownership-tracked indexes on `samples(sample_type_id)`
and `sample_attributes(sample_type_id, title)`.

These tests exercise `startup.steps.schema_fixups` against a real, disposable
MySQL/MariaDB database (never `seek_production`) provisioned by the
`disposable_attribute_db`/`attribute_faults` fixtures owned by
`nextseek_api/attributes/tests/conftest.py`/`attribute_fixtures.py`. Every
node name and parametrize ID below is pinned exactly by task-03 Section
11.5/11.7 and must not be renamed or collapsed.

The three `test_attribute_partial_ddl_recovery` parameter IDs
(`after-ownership-row`, `after-alter-before-marker`, `after-marker-before-verify`)
are the normative Section 11.5 identity for this test; the *values* passed to
`attribute_faults.arm(...)` are the frozen `ddl.before_first_index` /
`ddl.after_first_index` / `ddl.after_second_index` points from the shared
`VERIFICATION-MANIFEST.json` `fault_points` registry, which has not yet been
renamed to the Section 11.5 vocabulary (see `schema_fixups.NORMATIVE_FAULT_POINT_IDS`
docstring). `pytest.param(..., id=...)` binds the two vocabularies without
touching the frozen manifest or the shared `AttributeFaultController`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path

import pytest
from MySQLdb import DatabaseError

from nextseek_api.attributes.tests.real_boundary import InjectedAttributeFault
from startup.steps import schema_fixups as sf


def test_attribute_duplicate_preflight_aborts_without_rewrite(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_case_duplicate")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    indexes = sf.indexes_for_database(database)
    before_checksum = disposable_attribute_db.checksum("sample_attributes")

    with pytest.raises(sf.DuplicateIdentityError):
        sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)

    assert disposable_attribute_db.checksum("sample_attributes") == before_checksum
    assert sf._table_exists_on_connection(connection, database, sf._MARKER_TABLE) is False
    for index in indexes:
        state, _ = sf.attribute_index_readiness(connection, index)
        assert state == "absent"


def test_attribute_preexisting_compatible_index_is_adopted_not_recreated(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    cursor = connection.cursor()
    cursor.execute("ALTER TABLE samples ADD INDEX idx_samples_sample_type_id (sample_type_id)")
    cursor.execute(
        "ALTER TABLE sample_attributes ADD UNIQUE INDEX "
        "uq_sample_attributes_sample_type_title_ci (sample_type_id, title)"
    )
    connection.commit()
    indexes = sf.indexes_for_database(database)

    records = sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)
    assert [record["post_state"]["convergence_verdict"] for record in records] == [
        "already-converged", "already-converged",
    ]
    for index in indexes:
        marker = sf.read_ownership_marker(connection, database, index.name)
        assert marker.ownership_state == "preexisting_compatible"
        assert marker.preexisting_compatible is True

    # A rerun stays "already-converged": a real ALTER ADD INDEX for either
    # name would raise "Duplicate key name" against MySQL, so this proves no
    # attempt is made to drop-and-recreate an adopted preexisting index.
    records_again = sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)
    assert [record["post_state"]["convergence_verdict"] for record in records_again] == [
        "already-converged", "already-converged",
    ]


def test_attribute_incompatible_same_name_index_stops(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    connection.cursor().execute("ALTER TABLE samples ADD INDEX idx_samples_sample_type_id (sample_type_id, id)")
    connection.commit()
    indexes = sf.indexes_for_database(database)

    with pytest.raises(sf.IndexOwnershipError):
        sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)

    assert sf.read_ownership_marker(connection, database, "idx_samples_sample_type_id") is None
    state, shape = sf.attribute_index_readiness(connection, indexes[0])
    assert state == "present"
    assert shape == (False, ("sample_type_id", "id"))
    # The second (never-reached) managed index is completely untouched.
    assert sf.read_ownership_marker(connection, database, "uq_sample_attributes_sample_type_title_ci") is None
    second_state, _ = sf.attribute_index_readiness(connection, indexes[1])
    assert second_state == "absent"


def test_attribute_readiness_query_error_propagates_without_ddl(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    indexes = sf.indexes_for_database(database)

    process_id = connection.thread_id()
    killer = disposable_attribute_db.fresh_connection()
    try:
        killer.cursor().execute("KILL CONNECTION %s", (process_id,))
    finally:
        killer.close()

    with pytest.raises(DatabaseError):
        sf.attribute_index_readiness(connection, indexes[0])

    # No DDL/ownership write occurred: a fresh connection observes the exact
    # untouched pre-fault state.
    fresh = disposable_attribute_db.connect()
    assert sf._table_exists_on_connection(fresh, database, sf._MARKER_TABLE) is False
    state, _ = sf.attribute_index_readiness(fresh, indexes[0])
    assert state == "absent"


def test_attribute_ddl_telemetry_binds_run_sql_lock_and_post_state(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    indexes = sf.indexes_for_database(database)

    records = sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)
    assert len(records) == len(indexes)

    run_root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
    for index, record in zip(indexes, records):
        assert record["schema_version"] == "ddl-telemetry/v1"

        run_identity = record["run_identity"]
        assert run_identity["server_uuid"] == disposable_attribute_db.server_identity["server_uuid"]
        assert run_identity["database_uuid"] == disposable_attribute_db.database_uuid
        assert run_identity["image_id"]

        operation = record["operation"]
        assert (operation["database"], operation["table"], operation["index"]) == (
            index.database, index.table, index.name,
        )
        assert operation["shape_sha256"] == index.shape_sha256
        assert operation["forward_sql_sha256"] == hashlib.sha256(index.forward_sql().encode()).hexdigest()
        assert operation["reverse_sql_sha256"] == hashlib.sha256(index.reverse_sql().encode()).hexdigest()
        assert operation["requested_algorithm"] == "INPLACE"
        assert operation["requested_lock"] == "NONE"
        assert set(operation["fault_point_ids"]) == set(sf.NORMATIVE_FAULT_POINT_IDS)

        pre_state = record["pre_state"]
        assert pre_state["started_at"]

        observation = record["observation"]
        assert observation["duration_seconds"] >= 0
        assert observation["concurrent_probe"]["attempted"] is True
        raw_artifact = observation["raw_query_artifact"]
        artifact_path = run_root / raw_artifact["path"]
        assert artifact_path.is_file()
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == raw_artifact["sha256"]

        post_state = record["post_state"]
        assert post_state["finished_at"]
        assert post_state["convergence_verdict"] in {"applied", "already-converged", "recovered"}
        assert post_state["statistics_rows"]
        assert post_state["final_shape_sha256"] is not None
        assert post_state["ownership_marker"]["owner"] == sf.OWNERSHIP_MARKER


@pytest.mark.parametrize(
    "fault_point",
    [
        pytest.param("ddl.before_first_index", id="after-ownership-row"),
        pytest.param("ddl.after_first_index", id="after-alter-before-marker"),
        pytest.param("ddl.after_second_index", id="after-marker-before-verify"),
    ],
)
def test_attribute_partial_ddl_recovery(disposable_attribute_db, attribute_faults, fault_point):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    indexes = sf.indexes_for_database(database)

    attribute_faults.arm(fault_point)
    with pytest.raises(InjectedAttributeFault, match=re.escape(fault_point)):
        sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)
    assert attribute_faults.observed(fault_point) == 1
    attribute_faults.clear()

    records = sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)
    for index, record in zip(indexes, records):
        assert record["post_state"]["convergence_verdict"] in {"applied", "already-converged", "recovered"}
        state, shape = sf.attribute_index_readiness(connection, index)
        assert state == "present"
        assert shape == index.shape
        marker = sf.read_ownership_marker(connection, database, index.name)
        assert marker.ownership_state == "created_by_fixup"

    # A further rerun stays converged (no double-apply, no data loss).
    idempotent = sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)
    assert all(record["post_state"]["convergence_verdict"] == "already-converged" for record in idempotent)


def test_attribute_owned_only_reverse_preserves_preexisting_index(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    indexes = sf.indexes_for_database(database)

    # Pre-create the non-unique samples index by hand so the fixup adopts it
    # as preexisting_compatible instead of owning it; the sample_attributes
    # unique index is left for the fixup to create and own.
    connection.cursor().execute("ALTER TABLE samples ADD INDEX idx_samples_sample_type_id (sample_type_id)")
    connection.commit()
    sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)

    samples_marker = sf.read_ownership_marker(connection, database, "idx_samples_sample_type_id")
    assert samples_marker.ownership_state == "preexisting_compatible"
    attributes_marker = sf.read_ownership_marker(connection, database, "uq_sample_attributes_sample_type_title_ci")
    assert attributes_marker.ownership_state == "created_by_fixup"

    # Correct NON_UNIQUE interpretation: the hand-created samples index is
    # non-unique (NON_UNIQUE=1 -> unique=False); the fixup-created
    # sample_attributes index is unique (NON_UNIQUE=0 -> unique=True).
    _, samples_shape = sf.attribute_index_readiness(connection, indexes[0])
    assert samples_shape == (False, ("sample_type_id",))
    _, attributes_shape = sf.attribute_index_readiness(connection, indexes[1])
    assert attributes_shape == (True, ("sample_type_id", "title"))

    records = sf.reverse_managed_indexes_on_connection(connection, indexes)
    by_index = {record["operation"]["index"]: record for record in records}

    preserved = by_index["idx_samples_sample_type_id"]
    assert preserved["post_state"]["convergence_verdict"] == "preserved preexisting"
    preserved_state, _ = sf.attribute_index_readiness(connection, indexes[0])
    assert preserved_state == "present"
    assert sf.read_ownership_marker(connection, database, "idx_samples_sample_type_id").ownership_state == \
        "preexisting_compatible"

    reversed_record = by_index["uq_sample_attributes_sample_type_title_ci"]
    assert reversed_record["post_state"]["convergence_verdict"] == "reversed"
    assert reversed_record["post_state"]["statistics_rows"] == []
    assert reversed_record["post_state"]["final_shape_sha256"] is None
    assert reversed_record["post_state"]["ownership_marker"] is None
    reversed_state, _ = sf.attribute_index_readiness(connection, indexes[1])
    assert reversed_state == "absent"
    assert sf.read_ownership_marker(connection, database, "uq_sample_attributes_sample_type_title_ci") is None


def test_attribute_concurrent_insert_enforces_unique_identity(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_empty")
    database = disposable_attribute_db.database_name
    sf.apply_managed_indexes_on_connection(
        disposable_attribute_db.connect(), sf.indexes_for_database(database), attribute_faults
    )
    first_finished = threading.Event()
    outcomes: list[str] = []

    def insert(row_id: int, title: str, *, wait_for_first: bool) -> None:
        if wait_for_first:
            assert first_finished.wait(timeout=5)
        connection = disposable_attribute_db.fresh_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO sample_attributes "
                "(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title) "
                "VALUES (%s,7,5,%s,0,1,0)",
                (row_id, title),
            )
            connection.commit()
            outcomes.append("inserted")
        except Exception:
            connection.rollback()
            outcomes.append("duplicate")
        finally:
            connection.close()
        if not wait_for_first:
            first_finished.set()

    threads = [
        threading.Thread(target=insert, args=(1, "RNA"), kwargs={"wait_for_first": False}),
        threading.Thread(target=insert, args=(2, "rna"), kwargs={"wait_for_first": True}),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert sorted(outcomes) == ["duplicate", "inserted"]
    assert disposable_attribute_db.query(
        "SELECT COUNT(*) FROM sample_attributes WHERE sample_type_id=%s", (7,)
    )[0][0] == 1


def test_attribute_json_explain_uses_owned_sample_index(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    sf.apply_managed_indexes_on_connection(connection, sf.indexes_for_database(database), attribute_faults)

    explain_json = disposable_attribute_db.query(
        "EXPLAIN FORMAT=JSON SELECT id FROM samples WHERE sample_type_id = %s", (7,)
    )[0][0]
    parsed = json.loads(explain_json)
    table_info = parsed["query_block"]["table"]
    assert "idx_samples_sample_type_id" in (table_info.get("possible_keys") or [])
    assert table_info.get("key") == "idx_samples_sample_type_id"
