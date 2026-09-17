from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from NessieAI.ns.reingest import proposals
from nextseek_api.assistant.models_db import ReingestAttributeProposal as Proposal

pytestmark = pytest.mark.django_db

ENTRY = {"raw_key": "Kraken2_bracken_fraction", "proposed_target": "D.SEQ",
         "proposed_attribute": "ContamPercent", "datatype": "number",
         "example_value": "3.2", "source_file": "x.report.txt",
         "rationale": "contamination percentage"}

# A row a human already ruled on, plus a genuine sample-type row for the
# attribute_exists tests. Fields mirror nextseek_api/tests/test_context_catalog.py's
# ROW fixture, which is the committed pattern for exercising the real catalog
# loader (`context_catalog.load_sample_type`) without a seeded database.
SAMPLE_TYPE_ROW = {
    "sample_type": "D.FLOW", "sampletype_id": 13, "name": "Flow Cytometry Data",
    "description": "A flow cytometry file.",
    "clade": "Raw", "tags": "flow cytometry data",
    "required_metadata": "UID, File_PrimaryData, Parent",
    "standard_metadata": "Instrument, Protocol",
    "possible_metadata_fields": "Stain, QC_notes",
    "parent_sampletypes": "", "child_sampletypes": "",
    "associated_assay_parents": "", "associated_assay_children": "",
}


def _record(**kw):
    user = get_user_model().objects.create(username=kw.pop("username", "t"))
    entries = kw.pop("entries", [ENTRY])
    return proposals.record(entries, pipeline="nf-core/rnaseq",
                            run_dir="/net/cluster/runs/r1",
                            manifest_digest="abc123", user_id=user.id, **kw)


def test_recording_the_same_key_twice_increments_rather_than_duplicating():
    _record()
    _record(username="u2")
    assert Proposal.objects.count() == 1
    assert Proposal.objects.first().times_proposed == 2


def test_first_and_last_seen_run_are_both_tracked():
    _record()
    row = Proposal.objects.first()
    assert row.first_seen_run == "/net/cluster/runs/r1"
    assert row.last_seen_run == "/net/cluster/runs/r1"


def test_approved_rules_returns_only_approved_rows():
    _record()
    assert proposals.approved_rules("nf-core/rnaseq") == {}
    Proposal.objects.update(status=Proposal.STATUS_APPROVED)
    rules = proposals.approved_rules("nf-core/rnaseq")
    assert rules["ContamPercent"].from_key == "Kraken2_bracken_fraction"


def test_a_pending_row_is_never_returned_as_a_rule():
    _record()
    assert "ContamPercent" not in proposals.approved_rules("nf-core/rnaseq")


def test_recording_does_not_reset_a_rejected_row_to_pending():
    _record()
    Proposal.objects.update(status=Proposal.STATUS_REJECTED)
    _record(username="u3")
    row = Proposal.objects.first()
    assert row.status == Proposal.STATUS_REJECTED
    assert Proposal.objects.count() == 1
    assert row.times_proposed == 2


def test_recording_does_not_reset_an_approved_row_to_pending():
    """A human's ruling (approved) is not a veto target for repetition either."""
    _record()
    Proposal.objects.update(status=Proposal.STATUS_APPROVED)
    _record(username="u3")
    row = Proposal.objects.first()
    assert row.status == Proposal.STATUS_APPROVED
    assert Proposal.objects.count() == 1
    assert row.times_proposed == 2


def test_recording_does_not_change_status_of_a_needs_definition_row():
    """needs_definition is non-terminal (nobody has ruled yet), but a repeat
    sighting still must never touch status -- only a human review does."""
    _record()
    Proposal.objects.update(status=Proposal.STATUS_NEEDS_DEFINITION)
    _record(username="u3")
    row = Proposal.objects.first()
    assert row.status == Proposal.STATUS_NEEDS_DEFINITION
    assert Proposal.objects.count() == 1
    assert row.times_proposed == 2


def test_a_different_attribute_for_the_same_raw_key_bumps_the_open_row_instead_of_forking():
    """The agent proposing this run isn't deterministic: a second sighting of the
    same raw_key with a DIFFERENT candidate attribute must not fork a second
    row while the first is still open (pending/needs_definition) -- a reviewer
    should see one gap with competing suggestions, not two gaps."""
    _record()
    alt_entry = dict(ENTRY, proposed_attribute="ContamFraction",
                      rationale="alternate reading of the same field")
    _record(username="u2", entries=[alt_entry])

    assert Proposal.objects.count() == 1
    row = Proposal.objects.first()
    assert row.proposed_attribute == "ContamPercent"  # original candidate stands
    assert row.times_proposed == 2
    assert "ContamFraction" in row.rationale  # the alternative is visible


def test_a_terminal_row_for_a_different_attribute_lets_a_new_question_through():
    """Once every row for (pipeline, raw_key) is terminal, a sighting proposing
    a genuinely different attribute is a new question, not evidence for the
    old one -- so it creates a new row rather than being folded in or dropped."""
    _record()
    Proposal.objects.update(status=Proposal.STATUS_REJECTED)
    alt_entry = dict(ENTRY, proposed_attribute="ContamFraction")
    _record(username="u2", entries=[alt_entry])

    assert Proposal.objects.count() == 2
    rejected = Proposal.objects.get(proposed_attribute="ContamPercent")
    assert rejected.status == Proposal.STATUS_REJECTED
    assert rejected.times_proposed == 1
    fresh = Proposal.objects.get(proposed_attribute="ContamFraction")
    assert fresh.status == Proposal.STATUS_PENDING
    assert fresh.times_proposed == 1


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_attribute_exists_is_true_for_an_attribute_really_on_the_sample_type(rows):
    rows.return_value = [SAMPLE_TYPE_ROW]
    assert proposals.attribute_exists("D.FLOW", "UID") is True
    assert proposals.attribute_exists("D.FLOW", "Instrument") is True


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_attribute_exists_is_false_for_an_attribute_not_on_the_sample_type(rows):
    rows.return_value = [SAMPLE_TYPE_ROW]
    assert proposals.attribute_exists("D.FLOW", "NotARealAttribute") is False


def test_attribute_exists_propagates_a_lookup_failure_rather_than_returning_false():
    """A transient outage must not be reported as `False` (attribute does not
    exist): downstream, `False` parks the value in Notes and queues a
    needs_definition row for superusers -- a fabricated schema gap. The
    genuine "not defined" answer needs no exception handling at all, because
    `attributes_for` already returns [] for an unknown sample type."""
    with patch("nextseek_api.services.reingest_lookups.attributes_for",
              side_effect=RuntimeError("catalog database unreachable")):
        with pytest.raises(RuntimeError):
            proposals.attribute_exists("D.FLOW", "UID")
