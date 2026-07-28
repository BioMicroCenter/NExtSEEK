from pathlib import Path

import pytest

from nextseek_api.attributes.tests.real_boundary import (
    AttributeFaultController,
    DisposableAttributeBroker,
    DisposableAttributeDatabase,
    InjectedAttributeFault,
)


def test_disposable_database_interface_is_frozen():
    required = {"django_alias", "server_identity", "database_uuid", "database_name", "attach_from_identity", "owner_from_identity", "connect", "fresh_connection", "seed_seek_fixture", "create_seed_template", "export_seed_template_descriptor", "adopt_precreated_seed_template", "clone_shard", "reset_shard", "install_django_alias", "drop_shard", "subprocess_environment", "assert_no_owned_shards", "rails_database_url", "query", "execute_sql", "checksum", "checksum_query", "assert_torn_down"}
    assert required <= (set(DisposableAttributeDatabase.__dataclass_fields__) | set(dir(DisposableAttributeDatabase)))


def test_assert_torn_down_is_only_valid_after_teardown(monkeypatch):
    database = object.__new__(DisposableAttributeDatabase)
    database._torn_down = False
    with pytest.raises(AssertionError, match="only after teardown"):
        database.assert_torn_down()


def test_fault_controller_only_accepts_frozen_points():
    controller = AttributeFaultController({"executor.before_definition_write"})
    controller.arm("executor.before_definition_write")
    with pytest.raises(InjectedAttributeFault):
        controller.hit("executor.before_definition_write")
    assert controller.observed("executor.before_definition_write") == 1
    with pytest.raises(ValueError, match="unknown frozen fault point"):
        controller.arm("friendly.unreviewed.point")
    controller.clear()
    assert controller.observed("executor.before_definition_write") == 0


def test_disposable_broker_interface_is_frozen():
    required = {"broker_url", "queue_name", "route_sender", "start_worker", "kill_worker", "restart_worker", "published", "consumed", "teardown", "assert_torn_down"}
    assert required <= (set(DisposableAttributeBroker.__dataclass_fields__) | set(dir(DisposableAttributeBroker)))


def test_fixture_module_exports_exact_canonical_names():
    text = (Path(__file__).parent / "attribute_fixtures.py").read_text()
    assert "def disposable_attribute_db(" in text
    assert "def attribute_faults(" in text
    assert "def attribute_broker_lane(" in text
    assert "def rails_like_workload(" in text
    assert "def sql_telemetry(" in text


def test_disposable_database_rejects_denylisted_name(monkeypatch):
    monkeypatch.setenv("ATTRIBUTE_TEST_DATABASE_NAME", "seek_production")
    with pytest.raises(RuntimeError, match="denylisted"):
        DisposableAttributeDatabase.from_environment()


def test_attach_from_identity_is_nonowning_and_cannot_drop_base(disposable_attribute_db):
    database = disposable_attribute_db
    identity = {"server_identity": database.server_identity,
                "database_uuid": database.database_uuid,
                "database_name": database.database_name}
    attached = DisposableAttributeDatabase.attach_from_identity(identity)
    assert attached._owns_base is False
    assert attached.query("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s",
                          (database.database_name,))[0][0] == 1
    attached.teardown()
    attached.assert_torn_down()
    assert database.query("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s",
                          (database.database_name,))[0][0] == 1


def test_logical_seed_clone_reset_alias_and_django_telemetry(disposable_attribute_db, sql_telemetry, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    database.seed_seek_fixture({
        "sample_type_id": 991, "sample_titles": ["UID", "Value"],
        "samples": [{"id": 991001, "json_metadata": {"UID": "seed", "Value": "original"}}],
    })
    sql_telemetry.snapshot()  # discard setup-only server observations
    template = database.create_seed_template()
    assert len(template.checksum) == len(template.logical_seed_sha256) == len(template.semantic_state_sha256) == 64
    shard = database.clone_shard("worker-000")
    assert shard.database_name.startswith("attribute_test_")
    assert shard.database_name not in {"dmac", "seek_production", "test_dmac"}
    alias = database.install_django_alias(shard)
    try:
        with sql_telemetry.wrap_django_connection(alias) as django_connection:
            with django_connection.cursor() as cursor:
                cursor.execute("SELECT json_metadata FROM samples WHERE id=%s", [991001])
                assert "original" in cursor.fetchone()[0]
                cursor.execute("UPDATE samples SET json_metadata=%s WHERE id=%s", ['{"UID":"changed"}', 991001])
        first = sql_telemetry.snapshot()
        assert first.sql_count == 2 and first.maximum_packet_bytes > 0
        with pytest.raises(RuntimeError, match="template checksum"):
            database.reset_shard(shard, "0" * 64)
        database.reset_shard(shard, template.checksum)
        with sql_telemetry.wrap_django_connection(alias) as django_connection:
            with django_connection.cursor() as cursor:
                cursor.execute("SELECT json_metadata FROM samples WHERE id=%s", [991001])
                assert "original" in cursor.fetchone()[0]
        assert sql_telemetry.snapshot().sql_count == 1
    finally:
        database.drop_shard(shard)
    with pytest.raises(RuntimeError, match="not owned"):
        database.reset_shard(shard, template.checksum)


def test_broker_rejects_running_nextseek_namespace(monkeypatch):
    monkeypatch.setenv("ATTRIBUTE_TEST_BROKER_URL", "sqla+sqlite:////tmp/attribute-refusal.sqlite3")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "nextseek")
    with pytest.raises(RuntimeError, match="running nextseek"):
        DisposableAttributeBroker.from_environment()
