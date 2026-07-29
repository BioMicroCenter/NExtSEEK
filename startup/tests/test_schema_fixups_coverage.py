"""Supplemental T03 coverage tests for `startup.steps.schema_fixups`.

These are *not* part of the Section 11.5 pinned real-boundary contract (that
list lives exactly in `test_schema_fixups.py`); this module exists solely to
exercise remaining branches -- the pre-existing legacy column-fixup helpers,
small telemetry guard clauses, and the compose-based production entrypoints
-- so the frozen `coverage` lane's 95% owned-module gate is met. No pinned
node name or parametrize ID is defined here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from startup.steps import schema_fixups as sf


# ---------------------------------------------------------------------------
# Legacy `apply_column_fixups` (pre-existing, unrelated to T03's index work,
# but part of the same coverage-gated module).
# ---------------------------------------------------------------------------

def test_table_missing_short_circuits_column_and_backfill_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(sf, "compose_exec", lambda **kwargs: (calls.append(kwargs), "0")[1])
    results = sf.apply_column_fixups(Path("/repo"), {})
    # Real KNOWN_FIXUPS entry: table-exists probe returns "0" -> table missing.
    assert results == [("dmac.assistant_chat_session.extra_state", "table missing")]
    assert len(calls) == 1


def test_column_missing_applies_backfill_and_tightens(monkeypatch):
    calls = []

    def recording_compose_exec(*, service, command, project_dir, env):
        calls.append(command[-1])
        if "INFORMATION_SCHEMA.TABLES" in command[-1]:
            return "1"
        if "INFORMATION_SCHEMA.COLUMNS" in command[-1]:
            return "0"
        return ""

    monkeypatch.setattr(sf, "compose_exec", recording_compose_exec)
    results = sf.apply_column_fixups(Path("/repo"), {})
    assert results == [("dmac.assistant_chat_session.extra_state", "applied")]
    joined = " ".join(calls)
    assert "ADD COLUMN extra_state" in joined
    assert "UPDATE assistant_chat_session" in joined
    assert "MODIFY COLUMN extra_state" in joined


def test_column_present_without_final_definition_is_already_present(monkeypatch):
    fixup = sf.MissingColumn(
        database="dmac", table="t", column="c", column_definition="INT NULL",
    )
    monkeypatch.setattr(sf, "KNOWN_FIXUPS", [fixup])

    def fake_compose_exec(*, service, command, project_dir, env):
        if "INFORMATION_SCHEMA.TABLES" in command[-1]:
            return "1"
        if "INFORMATION_SCHEMA.COLUMNS" in command[-1]:
            return "1"
        return ""

    monkeypatch.setattr(sf, "compose_exec", fake_compose_exec)
    results = sf.apply_column_fixups(Path("/repo"), {})
    assert results == [("dmac.t.c", "already present")]


def test_column_present_with_final_definition_resets_constraints(monkeypatch):
    fixup = sf.MissingColumn(
        database="dmac", table="t", column="c", column_definition="INT NULL",
        final_column_definition="INT NOT NULL",
    )
    monkeypatch.setattr(sf, "KNOWN_FIXUPS", [fixup])
    calls = []

    def fake_compose_exec(*, service, command, project_dir, env):
        calls.append(command[-1])
        if "INFORMATION_SCHEMA.TABLES" in command[-1]:
            return "1"
        if "INFORMATION_SCHEMA.COLUMNS" in command[-1]:
            return "1"
        return ""

    monkeypatch.setattr(sf, "compose_exec", fake_compose_exec)
    results = sf.apply_column_fixups(Path("/repo"), {})
    assert results == [("dmac.t.c", "constraints reset")]
    assert any("MODIFY COLUMN c INT NOT NULL" in call for call in calls)


def test_root_password_defaults_and_env_override():
    assert sf._root_password({}) == "seek_root"
    assert sf._root_password({"MYSQL_ROOT_PASSWORD": "x"}) == "x"


# ---------------------------------------------------------------------------
# Small telemetry/identity helper guard clauses that don't need a live DB.
# ---------------------------------------------------------------------------

def test_utc_timestamp_is_iso_like():
    stamp = sf._utc_timestamp()
    assert stamp.endswith("Z")
    assert "T" in stamp


def test_base_sha_missing_manifest_returns_none(monkeypatch):
    monkeypatch.setattr(sf, "_MANIFEST_PATH", Path("/nonexistent/manifest.json"))
    assert sf._base_sha() is None


def test_dependency_sha_missing_env_var_returns_none(monkeypatch):
    monkeypatch.delenv("ATTRIBUTE_EVIDENCE_RUN_ROOT", raising=False)
    assert sf._dependency_sha() is None


def test_dependency_sha_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ATTRIBUTE_EVIDENCE_RUN_ROOT", str(tmp_path))
    assert sf._dependency_sha() is None


def test_dependency_sha_present_file_hashes_canonical_bytes(monkeypatch, tmp_path):
    import orjson
    from hashlib import sha256

    monkeypatch.setenv("ATTRIBUTE_EVIDENCE_RUN_ROOT", str(tmp_path))
    payload = {"b": 2, "a": 1}
    (tmp_path / "dependency-shas.json").write_bytes(orjson.dumps(payload))
    expected = sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
    assert sf._dependency_sha() == expected


def test_shape_for_telemetry_none_shape_returns_none():
    assert sf._shape_for_telemetry(connection=None, index=None, shape=None) is None


def test_raw_query_artifact_missing_run_root_returns_none(monkeypatch):
    monkeypatch.delenv("ATTRIBUTE_EVIDENCE_RUN_ROOT", raising=False)
    index = sf.ManagedIndex(database="d", table="t", name="n", columns=("c",), unique=False)
    assert sf._raw_query_artifact(index, "label", []) is None


def test_noop_fault_controller_hit_is_a_no_op():
    assert sf._NoopFaultController().hit("anything") is None


def test_telemetry_fqn_status_extracts_operation_and_verdict():
    record = {
        "operation": {"database": "d", "table": "t", "index": "n"},
        "post_state": {"convergence_verdict": "applied"},
    }
    assert sf._telemetry_fqn_status(record) == ("d.t.n", "applied")


def test_table_row_byte_counts_missing_information_schema_row_defaults_zero():
    class _Cursor:
        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return None

    class _Connection:
        def cursor(self):
            return _Cursor()

    assert sf._table_row_byte_counts(_Connection(), "d", "t") == {"row_count": 0, "byte_count": 0}


# ---------------------------------------------------------------------------
# Production entrypoints (compose-exec/MySQLdb-connect based), exercised with
# a stubbed connection factory and empty index lists so they never touch a
# real socket.
# ---------------------------------------------------------------------------

class _StubConnection:
    closed = False

    def close(self):
        self.closed = True


def test_apply_managed_indexes_opens_and_closes_production_connection(monkeypatch):
    stub = _StubConnection()
    monkeypatch.setattr(sf, "_connect_to_managed_database", lambda repo_root, env: stub)
    results = sf.apply_managed_indexes(Path("/repo"), {}, indexes=[])
    assert results == []
    assert stub.closed is True


def test_reverse_managed_indexes_opens_and_closes_production_connection(monkeypatch):
    stub = _StubConnection()
    monkeypatch.setattr(sf, "_connect_to_managed_database", lambda repo_root, env: stub)
    results = sf.reverse_managed_indexes(Path("/repo"), {}, indexes=[])
    assert results == []
    assert stub.closed is True


def test_apply_managed_indexes_closes_connection_even_on_failure(monkeypatch):
    stub = _StubConnection()
    monkeypatch.setattr(sf, "_connect_to_managed_database", lambda repo_root, env: stub)

    def boom(connection, indexes, faults):
        raise RuntimeError("ddl exploded")

    monkeypatch.setattr(sf, "apply_managed_indexes_on_connection", boom)
    with pytest.raises(RuntimeError, match="ddl exploded"):
        sf.apply_managed_indexes(Path("/repo"), {}, indexes=[])
    assert stub.closed is True


def test_apply_all_chains_column_fixups_and_managed_indexes(monkeypatch):
    monkeypatch.setattr(sf, "apply_column_fixups", lambda repo_root, env: [("dmac.t.c", "applied")])
    stub = _StubConnection()
    monkeypatch.setattr(sf, "_connect_to_managed_database", lambda repo_root, env: stub)
    results = sf.apply_all(Path("/repo"), {}, indexes=[])
    assert results == [("dmac.t.c", "applied")]


def test_connect_to_managed_database_uses_compose_port(monkeypatch):
    captured = {}

    def fake_compose_port(service, port, repo_root, env):
        captured["args"] = (service, port, repo_root, env)
        return 33060

    class _FakeMySQLdb:
        @staticmethod
        def connect(**kwargs):
            captured["connect_kwargs"] = kwargs
            return _StubConnection()

    monkeypatch.setattr(sf, "compose_port", fake_compose_port)
    monkeypatch.setitem(__import__("sys").modules, "MySQLdb", _FakeMySQLdb)
    connection = sf._connect_to_managed_database(Path("/repo"), {})
    assert isinstance(connection, _StubConnection)
    assert captured["args"] == ("db", 3306, Path("/repo"), {})
    assert captured["connect_kwargs"]["port"] == 33060


# ---------------------------------------------------------------------------
# Real-boundary edge cases that need an actual disposable database: the
# table-missing convergence path, post-DDL verification failure, and the
# reverse "already absent" / shape-mismatch paths.
# ---------------------------------------------------------------------------

def test_apply_one_index_reports_table_missing_without_ddl(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_empty")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    connection.cursor().execute("DROP TABLE samples")
    connection.commit()
    index = sf.indexes_for_database(database)[0]
    sf._ensure_marker_table(connection, database)

    record = sf._apply_one_index(connection, index, attribute_faults, database_uuid=None)
    assert record["post_state"]["convergence_verdict"] == "table missing"
    assert record["operation"]["table"] == "samples"


def test_verify_final_shape_raises_on_shape_drift(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    index = sf.indexes_for_database(database)[0]
    connection.cursor().execute(
        "ALTER TABLE samples ADD INDEX idx_samples_sample_type_id (sample_type_id, id)"
    )
    connection.commit()

    with pytest.raises(sf.IndexOwnershipError):
        sf._verify_final_shape(connection, index)


def test_reverse_reports_already_absent_for_untouched_indexes(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_empty")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    indexes = sf.indexes_for_database(database)
    sf._ensure_marker_table(connection, database)

    records = sf.reverse_managed_indexes_on_connection(connection, indexes)
    assert all(record["post_state"]["convergence_verdict"] == "already absent" for record in records)


def test_reverse_refuses_to_drop_owned_index_whose_shape_has_drifted(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    indexes = sf.indexes_for_database(database)
    sf.apply_managed_indexes_on_connection(connection, indexes, attribute_faults)

    owned_index = indexes[0]
    connection.cursor().execute(
        f"ALTER TABLE samples DROP INDEX {owned_index.name}, "
        f"ADD INDEX {owned_index.name} (sample_type_id, id)"
    )
    connection.commit()

    with pytest.raises(sf.IndexOwnershipError):
        sf.reverse_managed_indexes_on_connection(connection, indexes)


def test_observe_concurrent_lock_probe_reports_errored_on_database_error(disposable_attribute_db, attribute_faults):
    disposable_attribute_db.seed_seek_fixture("attribute_schema_unique")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    index = sf.indexes_for_database(database)[0]

    process_id = connection.thread_id()
    killer = disposable_attribute_db.fresh_connection()
    try:
        killer.cursor().execute("KILL CONNECTION %s", (process_id,))
    finally:
        killer.close()

    result = sf.observe_concurrent_lock_probe(connection, index)
    assert result["attempted"] is True
    assert result["outcome"] == "errored"
    assert result["server_error_code"] is not None


def test_quote_identifier_rejects_unsafe_names():
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        sf._quote_identifier("bad-name")
