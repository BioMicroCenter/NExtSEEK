from importlib import import_module

from django.apps import apps


def test_attribute_merge_migration_joins_both_deployed_leaves():
    migration = import_module(
        "nextseek_api.migrations.0019_merge_attribute_async_turn_ledger"
    ).Migration

    assert set(migration.dependencies) == {
        ("nextseek_api", "0011_attribute_async_orchestration"),
        ("nextseek_api", "0018_turn_ledger_attempted_provenance"),
    }
    assert migration.operations == []


def test_attribute_heartbeat_model_is_registered_with_the_app():
    assert apps.get_model(
        "nextseek_api", "AttributeOutboxDispatcherHeartbeat"
    )._meta.db_table == "attributes_outbox_dispatcher_heartbeat"
