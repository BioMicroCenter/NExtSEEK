import pytest

from nextseek_api.services import reingest_lookups

pytestmark = pytest.mark.django_db


def test_known_sample_types_includes_the_reingest_targets():
    known = reingest_lookups.known_sample_types()
    assert {"D.SEQ", "A.GEX", "A.ALN", "A.SCXP"} <= known


def test_attributes_for_returns_titles_and_required_flags():
    attrs = reingest_lookups.attributes_for("A.GEX")
    assert attrs, "A.GEX should have attributes"
    assert {"title", "required"} <= set(attrs[0])
    assert "Checksum_PrimaryData" in {a["title"] for a in attrs}


def test_checksum_primary_data_is_required_on_the_analysis_types():
    for sample_type in ("A.GEX", "A.ALN", "A.SCXP"):
        required = {a["title"] for a in reingest_lookups.attributes_for(sample_type)
                    if a["required"]}
        assert "Checksum_PrimaryData" in required, sample_type


def test_attributes_for_an_unknown_type_is_empty_not_an_error():
    assert reingest_lookups.attributes_for("A.NOPE") == []


def test_uids_by_primary_data_returns_a_list_for_an_unmatched_path():
    assert reingest_lookups.uids_by_primary_data(
        "/net/cluster/fastq/not-a-real-file.fastq.gz") == []


def test_notes_for_uids_omits_a_uid_it_could_not_fetch():
    # Omission is load-bearing: QA turns "absent" into a hard reject rather than
    # letting a blind Notes write destroy text nobody read.
    assert reingest_lookups.notes_for_uids(["D.SEQ-NOT-A-REAL-UID"]) == {}


def test_notes_for_uids_of_an_empty_list_is_empty():
    assert reingest_lookups.notes_for_uids([]) == {}
