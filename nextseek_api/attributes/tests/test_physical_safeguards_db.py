"""T03-owned migration-router / default-vs-SEEK boundary tests.

Distinct from the frozen `startup/tests/test_schema_fixups.py` DD-09 nodes
(Section 11.5): this module proves `AttributeMutationJob`/`AttributeMutationPartition`
are Django-`default`-database models discoverable via `nextseek_api.models`,
routed there by `seek.dbrouters.CustomRouter` (never SEEK/Rails), and that the
`0010_attribute_mutation_job` migration lives on the `nextseek_api` leaf
(`0009_normalize_chat_log_turn_ids`) rather than the Rails-owned `seek` ledger.
It does not duplicate or rename any DD-09 node from `test_schema_fixups.py`,
and does not re-exercise the full `AttributeMutationAuditStore.create_job`
identity contract already covered by `nextseek_api/attributes/tests/test_job_storage.py`.
"""
from __future__ import annotations

import pytest
from django.apps import apps
from django.db import connections
from django.db.migrations.loader import MigrationLoader

from nextseek_api.attributes.models_db import AttributeMutationJob, AttributeMutationPartition
from seek.dbrouters import CustomRouter


def test_job_and_partition_models_have_no_seek_database_override():
    """Omitting `_DATABASE` is what keeps a model on `default` per
    `CustomRouter` (Section 9); asserting its absence here pins that intent."""
    assert not hasattr(AttributeMutationJob, "_DATABASE")
    assert not hasattr(AttributeMutationPartition, "_DATABASE")


def test_router_routes_job_and_partition_reads_and_writes_to_default():
    router = CustomRouter()
    for model in (AttributeMutationJob, AttributeMutationPartition):
        assert router.db_for_read(model) == "default"
        assert router.db_for_write(model) == "default"


def test_job_and_partition_are_registered_under_nextseek_api_app():
    assert AttributeMutationJob._meta.app_label == "nextseek_api"
    assert AttributeMutationPartition._meta.app_label == "nextseek_api"
    assert apps.get_model("nextseek_api", "AttributeMutationJob") is AttributeMutationJob
    assert apps.get_model("nextseek_api", "AttributeMutationPartition") is AttributeMutationPartition


def test_models_module_reexports_job_and_partition_for_discovery():
    from nextseek_api import models as nextseek_models

    assert nextseek_models.AttributeMutationJob is AttributeMutationJob
    assert nextseek_models.AttributeMutationPartition is AttributeMutationPartition


@pytest.mark.django_db
def test_migration_0010_depends_on_leaf_0009_and_lives_under_nextseek_api():
    loader = MigrationLoader(connections["default"], ignore_no_migrations=True)
    key = ("nextseek_api", "0010_attribute_mutation_job")
    assert key in loader.disk_migrations
    migration = loader.disk_migrations[key]
    assert ("nextseek_api", "0009_normalize_chat_log_turn_ids") in migration.dependencies

    created = {
        operation.name for operation in migration.operations
        if operation.__class__.__name__ == "CreateModel"
    }
    assert created == {"AttributeMutationJob", "AttributeMutationPartition"}

    # Rails/SEEK owns its own migration ledger; the physical index safeguards
    # in startup/steps/schema_fixups.py are deliberately NOT a `seek` Django
    # migration (Section 9), and this job/partition migration must not appear
    # under the `seek` app either.
    assert not any(app_label == "seek" and "attribute_mutation_job" in name for app_label, name in loader.disk_migrations)


@pytest.mark.django_db
def test_job_and_partition_querysets_target_default_alias_explicitly():
    assert AttributeMutationJob.objects.db == "default"
    assert AttributeMutationPartition.objects.db == "default"
