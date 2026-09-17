"""The superuser review surface for the reingest attribute approval queue.

Covers the endpoint from ``.superpowers/sdd/p2-task-6-brief.md`` plus the
resolution-1 guard: ``approve`` must never hand the mapper a rule for an
attribute that is not defined on its target sample type. ``attribute_exists``
is patched at its import site in ``nextseek_api.services.reingest_proposals``
rather than exercised against a real sample-type catalog -- same rule
``NessieAI/tests/api/test_reingest_lookups.py`` follows for the loader
underneath it.
"""
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ReingestAttributeProposal as Proposal

pytestmark = pytest.mark.django_db
BASE = "/nextseek_api/reingest-proposals/"


def _proposal(**overrides):
    fields = dict(
        pipeline="nf-core/rnaseq", raw_key="Kraken2_bracken_fraction",
        proposed_target="D.SEQ", proposed_attribute="ContamPercent",
        datatype="number", example_value="3.2")
    fields.update(overrides)
    return Proposal.objects.create(**fields)


def _client(*, superuser):
    user = get_user_model().objects.create(
        username="su" if superuser else "plain",
        is_staff=True, is_superuser=superuser)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def test_a_staff_non_superuser_is_refused():
    _proposal()
    client, _ = _client(superuser=False)
    assert client.get(BASE).status_code in (401, 403)


def test_an_anonymous_caller_is_refused():
    assert APIClient().get(BASE).status_code in (401, 403)


def test_a_superuser_lists_pending_proposals():
    _proposal()
    client, _ = _client(superuser=True)
    response = client.get(BASE, {"status": "pending"})
    assert response.status_code == 200
    assert response.json()["results"][0]["proposed_attribute"] == "ContamPercent"


@patch("nextseek_api.services.reingest_proposals.attribute_exists", return_value=True)
def test_approve_stamps_the_reviewer_and_time(mock_exists):
    # A pending row's attribute exists by construction (the model docstring's
    # own invariant), but the guard runs unconditionally -- see resolution 1 --
    # so this still has to tell the mock the attribute is defined.
    row = _proposal()
    client, user = _client(superuser=True)
    assert client.post(f"{BASE}{row.pk}/approve/").status_code == 200
    row.refresh_from_db()
    assert row.status == Proposal.STATUS_APPROVED
    assert row.reviewed_by_id == user.id
    assert row.reviewed_at is not None
    mock_exists.assert_called_once_with(row.proposed_target, row.proposed_attribute)


def test_reject_stamps_the_reviewer_too():
    row = _proposal()
    client, user = _client(superuser=True)
    assert client.post(f"{BASE}{row.pk}/reject/").status_code == 200
    row.refresh_from_db()
    assert row.status == Proposal.STATUS_REJECTED
    assert row.reviewed_by_id == user.id


# --- Resolution 1: approve must not invent a sample attribute -------------

@patch("nextseek_api.services.reingest_proposals.attribute_exists", return_value=False)
def test_approve_is_refused_for_a_needs_definition_row_whose_attribute_is_undefined(mock_exists):
    row = _proposal(status=Proposal.STATUS_NEEDS_DEFINITION)
    client, _ = _client(superuser=True)

    response = client.post(f"{BASE}{row.pk}/approve/")

    assert response.status_code == 409
    row.refresh_from_db()
    # The row's ruling is untouched: no reviewer, no timestamp, still needs_definition.
    assert row.status == Proposal.STATUS_NEEDS_DEFINITION
    assert row.reviewed_by_id is None
    assert row.reviewed_at is None
    mock_exists.assert_called_once_with(row.proposed_target, row.proposed_attribute)


@patch("nextseek_api.services.reingest_proposals.attribute_exists", return_value=True)
def test_approve_succeeds_once_the_attribute_is_defined(mock_exists):
    """Proves the guard is data-dependent, not a blanket refusal of needs_definition rows."""
    row = _proposal(status=Proposal.STATUS_NEEDS_DEFINITION)
    client, user = _client(superuser=True)

    response = client.post(f"{BASE}{row.pk}/approve/")

    assert response.status_code == 200
    row.refresh_from_db()
    assert row.status == Proposal.STATUS_APPROVED
    assert row.reviewed_by_id == user.id
    assert row.reviewed_at is not None


def test_reject_works_on_a_needs_definition_row():
    """Rejecting an attribute that does not exist is always legitimate -- no guard here."""
    row = _proposal(status=Proposal.STATUS_NEEDS_DEFINITION)
    client, user = _client(superuser=True)

    response = client.post(f"{BASE}{row.pk}/reject/")

    assert response.status_code == 200
    row.refresh_from_db()
    assert row.status == Proposal.STATUS_REJECTED
    assert row.reviewed_by_id == user.id


@patch("nextseek_api.services.reingest_proposals.attribute_exists")
def test_approve_refusal_is_a_server_error_when_the_catalog_is_unreachable(mock_exists):
    """A RuntimeError from attribute_exists (outage) must not be reported as a refusal."""
    mock_exists.side_effect = RuntimeError("sample type catalog came back empty")
    row = _proposal(status=Proposal.STATUS_NEEDS_DEFINITION)
    client, _ = _client(superuser=True)

    response = client.post(f"{BASE}{row.pk}/approve/")

    assert response.status_code == 503
    row.refresh_from_db()
    assert row.status == Proposal.STATUS_NEEDS_DEFINITION
    assert row.reviewed_by_id is None


# --- Resolution 5: the ?pipeline= filter must actually filter --------------

def test_pipeline_filter_actually_filters():
    _proposal(pipeline="nf-core/rnaseq", raw_key="rnaseq-key")
    _proposal(pipeline="nf-core/sarek", raw_key="sarek-key")
    client, _ = _client(superuser=True)

    response = client.get(BASE, {"pipeline": "nf-core/sarek"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["pipeline"] == "nf-core/sarek"
    assert results[0]["raw_key"] == "sarek-key"
