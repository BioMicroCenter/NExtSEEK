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
    """A stub `approved_rules` that always returns `{}` would also pass a
    bare "pending is absent" check. Put a pending row and an approved row
    for the SAME pipeline side by side so this can only pass if the status
    filtering is real: the approved one must come back and the pending one
    must not."""
    _record()  # pending: ContamPercent / Kraken2_bracken_fraction
    approved_entry = dict(ENTRY, raw_key="Other_raw_key", proposed_attribute="OtherAttribute")
    _record(username="u2", entries=[approved_entry])
    Proposal.objects.filter(proposed_attribute="OtherAttribute").update(
        status=Proposal.STATUS_APPROVED)

    rules = proposals.approved_rules("nf-core/rnaseq")
    assert "OtherAttribute" in rules
    assert "ContamPercent" not in rules


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


def test_a_lost_create_race_folds_into_the_row_that_won_instead_of_raising():
    """Simulates the race record()'s IntegrityError catch exists for: another
    transaction's insert for the exact same (pipeline, raw_key,
    proposed_attribute) commits between this transaction's own "does a row
    already exist" read and its own create(). We can't reproduce that timing
    with real threads deterministically, so the "winner" row is created
    first -- as a plain statement outside any transaction record() opens, so
    none of record()'s internal rollbacks can touch it -- and record()'s own
    existence read is blinded to it, standing in for the read landing in the
    adversarial window a lock only narrows, never eliminates on every
    backend (see the module docstring's "Concurrency" section: sqlite's
    select_for_update() is a documented no-op). record() then reaches its
    own create() call for the exact same key, and sqlite's real
    unique_together constraint raises the same IntegrityError the actual
    race would -- nothing about the exception itself is mocked, only the
    read that would otherwise have found `winner` first."""
    winner = Proposal.objects.create(
        pipeline="nf-core/rnaseq", raw_key=ENTRY["raw_key"],
        proposed_target=ENTRY["proposed_target"],
        proposed_attribute=ENTRY["proposed_attribute"],
        datatype=ENTRY["datatype"], example_value=ENTRY["example_value"],
        source_file=ENTRY["source_file"], rationale=ENTRY["rationale"],
        first_seen_run="/net/cluster/runs/r0", last_seen_run="/net/cluster/runs/r0",
        manifest_digest="orig",
    )

    # Blind record()'s existence read only -- not every `.filter()` call --
    # so the post-collision fold's own `.filter(pk=...).update(...)` still
    # works normally. select_for_update() is unconditional because the real
    # code never passes it arguments.
    real_filter = Proposal.objects.filter

    def _blind_the_existence_check(*args, **kwargs):
        if set(kwargs) == {"pipeline", "raw_key"}:
            return Proposal.objects.none()
        return real_filter(*args, **kwargs)

    with patch.object(Proposal.objects, "filter", side_effect=_blind_the_existence_check), \
         patch.object(Proposal.objects, "select_for_update",
                       return_value=Proposal.objects.none()):
        _record()  # must fold into `winner`, not raise IntegrityError

    assert Proposal.objects.count() == 1
    winner.refresh_from_db()
    assert winner.times_proposed == 2
    assert winner.last_seen_run == "/net/cluster/runs/r1"
    assert winner.manifest_digest == "abc123"


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
    `attributes_for_strict` already returns [] for an unknown sample type."""
    with patch("nextseek_api.services.reingest_lookups.attributes_for_strict",
              side_effect=RuntimeError("catalog database unreachable")):
        with pytest.raises(RuntimeError):
            proposals.attribute_exists("D.FLOW", "UID")


@patch("nextseek_api.services.context_catalog._sample_type_rows")
def test_attribute_exists_raises_when_the_real_catalog_lookup_fails(rows):
    """The test above mocks `attributes_for_strict` directly, which bypasses
    the real chain and gives false confidence. This one goes through it:
    `attribute_exists` calls `reingest_lookups.attributes_for_strict`, which
    calls `context_catalog.load_sample_type_strict` /
    `load_sample_types_strict` -- the twin of the lenient loader that lets a
    failure from `_sample_type_rows` propagate instead of swallowing it into
    `[]` (see test_context_catalog.py's own use of this patch target). It
    must still raise here, not return a fabricated `False`."""
    rows.side_effect = RuntimeError("sample_types_context table unreachable")
    with pytest.raises(RuntimeError):
        proposals.attribute_exists("D.FLOW", "UID")
