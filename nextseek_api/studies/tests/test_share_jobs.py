"""The share job store (tool spec 16.4, 16.5): compare-and-set claims, owner-scoped finishes, leases."""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from nextseek_api.studies import share_jobs as jobs
from nextseek_api.studies.models import ShareInput
from nextseek_api.studies.models_db import SampleShare


@pytest.fixture
def share(db):
    user = get_user_model().objects.create(username="operator", is_superuser=True)
    inp = ShareInput(sample_uids=["TIS-260101AAA-2"], source_project_id=3, destination_project_id=5,
                     destination_study_id=40, created_at="t")
    return jobs.create_share(inp, user)


def test_a_new_share_is_planning_with_its_request(share):
    assert (share.state, share.actor_login, share.request["destination_study_id"]) == ("planning", "operator", 40)
    assert jobs.next_claimable() == share


def test_two_claims_of_one_share_one_wins(share):
    other = SampleShare.objects.get(pk=share.pk)
    assert jobs.claim(share, "w1") and not jobs.claim(other, "w2")
    assert SampleShare.objects.get(pk=share.pk).claim_owner == "w1"


def test_a_stale_handle_loses(share):
    stale = SampleShare.objects.get(pk=share.pk)
    assert jobs.claim(share, "w1")
    assert jobs.finish_plan(share, "w1", state="planned", run_dir="r", plan_sha256="a" * 64, summary={})
    assert not jobs.to_applying(stale)


def test_heartbeat_and_finish_from_another_owner_do_nothing(share):
    jobs.claim(share, "w1")
    assert not jobs.heartbeat(share, "w2")
    assert not jobs.finish_plan(share, "w2", state="planned")
    assert SampleShare.objects.get(pk=share.pk).state == "planning"


def test_finish_refuses_an_unknown_state(share):
    jobs.claim(share, "w1")
    with pytest.raises(ValueError):
        jobs.finish_plan(share, "w1", state="applied")
    with pytest.raises(ValueError):
        jobs.finish_apply(share, "w1", state="planned")


def test_to_queued_from_planning_does_nothing(share):
    assert not jobs.to_queued(share) and share.state == "planning"


def test_the_apply_path_and_the_unit_pass(share):
    jobs.claim(share, "w1")
    jobs.finish_plan(share, "w1", state="planned", run_dir="r", plan_sha256="a" * 64, summary={"x": 1})
    share.refresh_from_db()
    assert jobs.to_applying(share) and jobs.to_applying(share) and share.state == "applying"
    assert jobs.to_queued(share) and jobs.next_claimable() == share
    assert jobs.claim(share, "w2") and share.state == "running"
    assert jobs.back_to_queued(share, "w2") and SampleShare.objects.get(pk=share.pk).state == "queued"
    share.refresh_from_db()
    assert jobs.claim(share, "w3")
    assert jobs.finish_apply(share, "w3", state="applied", receipt={"links": 2})
    assert SampleShare.objects.get(pk=share.pk).state == "applied" and jobs.next_claimable() is None


def test_an_expired_lease_is_claimable_again(share):
    jobs.claim(share, "w1")
    SampleShare.objects.filter(pk=share.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    share.refresh_from_db()
    assert jobs.next_claimable() == share and jobs.claim(share, "w2")
    assert SampleShare.objects.get(pk=share.pk).claim_owner == "w2"


def test_state_version_only_moves_by_f(share):
    first = SampleShare.objects.get(pk=share.pk).state_version
    a, b = SampleShare.objects.get(pk=share.pk), SampleShare.objects.get(pk=share.pk)
    assert jobs.claim(a, "w1")
    assert not jobs.claim(b, "w2")
    assert SampleShare.objects.get(pk=share.pk).state_version == first + 1


def test_to_apply_failed_moves_only_an_applicable_share(share):
    assert not jobs.to_apply_failed(share, {"code": "destination_changed"}) and share.state == "planning"
    jobs.claim(share, "w1")
    jobs.finish_plan(share, "w1", state="planned", run_dir="r", plan_sha256="a" * 64, summary={})
    share.refresh_from_db()
    assert jobs.to_apply_failed(share, {"code": "destination_changed"}) and share.state == "apply_failed"
    assert not jobs.applicable(share) and not jobs.to_applying(share)


def test_finish_apply_and_back_to_queued_from_another_owner_do_nothing(share):
    jobs.claim(share, "w1")
    jobs.finish_plan(share, "w1", state="planned", run_dir="r", plan_sha256="a" * 64, summary={})
    share.refresh_from_db()
    jobs.to_queued(share)
    jobs.claim(share, "w2")
    assert not jobs.finish_apply(share, "w3", state="applied") and not jobs.back_to_queued(share, "w3")
    assert SampleShare.objects.get(pk=share.pk).state == "running"


def test_a_rolled_back_share_is_ended_by_its_run_directory_and_never_claimed(share):
    jobs.claim(share, "w1")
    jobs.finish_plan(share, "w1", state="planned", run_dir="r", plan_sha256="a" * 64, summary={})
    assert jobs.end_rolled_back("r") == 1 and jobs.end_rolled_back("r") == 0 and jobs.end_rolled_back("") == 0
    share.refresh_from_db()
    assert share.state == "rolled_back" and not jobs.applicable(share) and jobs.next_claimable() is None
