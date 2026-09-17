import pytest
from django.db.utils import IntegrityError

from nextseek_api.assistant.models_db import ReingestAttributeProposal as Proposal

pytestmark = pytest.mark.django_db


def _make(**kw):
    base = dict(pipeline="nf-core/rnaseq", raw_key="Kraken2_bracken_fraction",
                proposed_target="D.SEQ", proposed_attribute="ContamPercent",
                datatype="number", example_value="3.2",
                source_file="star_salmon/contaminants/kraken2/x.report.txt",
                rationale="contamination percentage from the Kraken2 report")
    base.update(kw)
    return Proposal.objects.create(**base)


def test_a_new_proposal_starts_pending():
    assert _make().status == Proposal.STATUS_PENDING


def test_the_same_key_and_attribute_is_unique():
    _make()
    with pytest.raises(IntegrityError):
        _make()


def test_statuses_cover_both_kinds_of_gap():
    # pending (attribute exists, source unconfirmed) and needs_definition
    # (attribute does not exist yet) are deliberately different statuses,
    # not just two arbitrary distinct strings.
    statuses = {choice for choice, _ in Proposal.STATUS_CHOICES}
    assert statuses == {
        Proposal.STATUS_PENDING,
        Proposal.STATUS_APPROVED,
        Proposal.STATUS_REJECTED,
        Proposal.STATUS_NEEDS_DEFINITION,
    }
    assert len(statuses) == 4

    row = _make(status=Proposal.STATUS_NEEDS_DEFINITION)
    row.refresh_from_db()
    assert row.status == "needs_definition"


def test_a_fresh_proposal_defaults_times_proposed_to_one_and_review_fields_null():
    row = _make()
    assert row.times_proposed == 1
    assert row.reviewed_by is None
    assert row.reviewed_at is None
