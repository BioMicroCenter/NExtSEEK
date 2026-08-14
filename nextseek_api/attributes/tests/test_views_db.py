import pytest
from django.test import override_settings

from nextseek_api.attributes.models_db import AttributeMutationJob
from nextseek_api.attributes.tests.test_planner_db import _seed_blood
from nextseek_api.attributes.tests.test_repository import _reset_seek_tables

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _clean_seek_boundary(disposable_attribute_db):
    yield
    _reset_seek_tables(disposable_attribute_db)


def _admin(seek_auth_boundary):
    return seek_auth_boundary.credential("token", "valid-admin")


def _create_body(*, dry_run=False):
    return {
        "targets": [{
            "sample_type": 1,
            "attributes": [{"title": "Future", "sample_attribute_type": 1}],
        }],
        "dry_run": dry_run,
    }


def test_real_product_dry_run_is_write_free(disposable_attribute_db, seek_auth_boundary):
    _seed_blood(disposable_attribute_db, population=1)
    before = seek_auth_boundary.write_checksums()
    response = seek_auth_boundary.dispatch_product_route(
        _admin(seek_auth_boundary), method="post",
        path="/nextseek_api/attributes/batch-create/", body=_create_body(dry_run=True),
    )
    assert response.status_code == 200
    assert response.data["mode"] == "dry_run"
    assert AttributeMutationJob.objects.count() == 0
    assert seek_auth_boundary.write_checksums() == before


@override_settings(
    ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD=5000,
    ATTRIBUTE_MUTATION_IN_JOB_PARALLELISM=1,
)
def test_real_product_synchronous_path_terminalizes_200(disposable_attribute_db, seek_auth_boundary):
    _seed_blood(disposable_attribute_db, population=1)
    response = seek_auth_boundary.dispatch_product_route(
        _admin(seek_auth_boundary), method="post",
        path="/nextseek_api/attributes/batch-create/", body=_create_body(),
    )
    assert response.status_code == 200
    assert response.data["mode"] == "synchronous"
    job = AttributeMutationJob.objects.get()
    assert job.state == "succeeded"
    assert job.terminal_result == response.data
    assert job.claim_owner is None
    assert job.lease_expires_at is None
    assert job.last_heartbeat_at is None
    assert job.claim_generation >= 1
    assert job.lease_version >= 1
    assert job.state_version >= 1


@override_settings(ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD=0)
def test_real_product_asynchronous_path_accepts_durable_202(disposable_attribute_db, seek_auth_boundary):
    _seed_blood(disposable_attribute_db, population=1)
    response = seek_auth_boundary.dispatch_product_route(
        _admin(seek_auth_boundary), method="post",
        path="/nextseek_api/attributes/batch-create/", body=_create_body(),
    )
    assert response.status_code == 202
    assert response.data["mode"] == "asynchronous"
    job = AttributeMutationJob.objects.get(job_id=response.data["job_id"])
    assert job.state == "accepted"
    assert job.execution_mode == "asynchronous"
    assert job.outbox_state == "pending"
    assert job.terminal_result is None
    assert job.partitions.count() == 1


@override_settings(
    ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD=5000,
    ATTRIBUTE_MUTATION_IN_JOB_PARALLELISM=1,
)
def test_real_product_mixed_sync_outcomes_return_207_without_live_lease(disposable_attribute_db, seek_auth_boundary):
    _seed_blood(disposable_attribute_db, population=0)
    body = {
        "targets": [
            {"sample_type": 1, "attributes": [{"attribute": 10, "changes": {"required": False}}]},
            {"sample_type": 3, "attributes": [{"attribute": 21, "changes": {"description": "updated"}}]},
        ]
    }
    response = seek_auth_boundary.dispatch_product_route(
        _admin(seek_auth_boundary), method="patch",
        path="/nextseek_api/attributes/batch-patch/", body=body,
    )
    assert response.status_code == 207
    assert response.data["mode"] == "synchronous"
    assert {outcome["status"] for outcome in response.data["outcomes"]} == {"succeeded", "failed"}
    job = AttributeMutationJob.objects.get()
    assert job.state == "partial"
    assert job.claim_owner is None
    assert job.lease_expires_at is None
    assert job.last_heartbeat_at is None
