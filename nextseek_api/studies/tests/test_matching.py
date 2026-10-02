"""The shared matching (tool spec 4.3, 4.4)."""
from nextseek_api.studies.sources import matching as m
from nextseek_api.studies.tests.conftest import FakeReader, StudyRow, uid


def _raw(**extra):
    base = dict(title="Paper One", investigation_title="Alpha Investigation",
                uids=[(uid(2, kind="D.SEQ"), "row 2"), (uid(3, kind="D.SEQ"), "row 3")])
    base.update(extra)
    return m.RawTarget(**base)


def test_uids_resolve_and_the_key_names_the_investigation(alpha):
    targets, unmatched = m.match_targets([_raw()], FakeReader(alpha))
    assert unmatched == []
    [t] = targets
    assert (t.key, t.investigation_id, t.seek_study_id, t.sample_ids) == ("sheet:7:paper one", 7, None, [2, 3])
    assert t.provenance == {"2": ["row 2"], "3": ["row 3"]}


def test_investigation_titles_match_trimmed_and_case_insensitive(alpha):
    targets, _ = m.match_targets([_raw(investigation_title="  alpha INVESTIGATION ")], FakeReader(alpha))
    assert targets[0].investigation_id == 7


def test_an_unknown_investigation_makes_every_sample_unmatched_and_creates_nothing(alpha):
    targets, unmatched = m.match_targets([_raw(investigation_title="Nowhere")], FakeReader(alpha))
    assert targets == []
    assert {(u.reason, u.submitted) for u in unmatched} == {
        (m.INVESTIGATION_UNKNOWN, uid(2, kind="D.SEQ")), (m.INVESTIGATION_UNKNOWN, uid(3, kind="D.SEQ"))}


def test_a_uid_on_two_rows_is_not_unique_and_a_missing_one_is_not_found(alpha):
    alpha.samples[99] = {"uuid": uid(2, kind="D.SEQ"), "meta": {}}
    targets, unmatched = m.match_targets([_raw(uids=[(uid(2, kind="D.SEQ"), "row 2"), (uid(3, kind="D.SEQ"), "row 3"),
                                                     ("TIS-260101ZZZ-9", "row 4")])], FakeReader(alpha))
    assert targets[0].sample_ids == [3]
    assert sorted((u.reason, u.submitted) for u in unmatched) == [
        (m.SAMPLE_UID_NOT_FOUND, "TIS-260101ZZZ-9"), (m.SAMPLE_UID_NOT_UNIQUE, uid(2, kind="D.SEQ"))]


def test_a_title_the_investigation_holds_once_names_that_study(alpha):
    targets, _ = m.match_targets([_raw(title="alpha paper existing")], FakeReader(alpha))
    assert targets[0].seek_study_id == 21


def test_a_title_held_only_in_another_investigation_is_refused(alpha):
    alpha.studies.append(StudyRow(31, 8, "Beta Paper", None))
    _, unmatched = m.match_targets([_raw(title="Beta Paper")], FakeReader(alpha))
    assert {u.reason for u in unmatched} == {m.STUDY_TITLE_IN_OTHER_INVESTIGATION}


def test_a_title_two_studies_of_the_investigation_hold_is_ambiguous(alpha):
    alpha.studies.append(StudyRow(22, 7, "Alpha Paper Existing", None))
    _, unmatched = m.match_targets([_raw(title="Alpha Paper Existing")], FakeReader(alpha))
    assert {u.reason for u in unmatched} == {m.STUDY_TITLE_AMBIGUOUS}


def test_the_bucket_is_never_a_target(alpha):
    _, unmatched = m.match_targets([_raw(title="Alpha Unpublished")], FakeReader(alpha))
    assert {u.reason for u in unmatched} == {m.TARGET_IS_BUCKET}
    _, unmatched = m.match_targets([_raw(title="New Unpublished")], FakeReader(alpha))
    assert {u.reason for u in unmatched} == {m.TARGET_IS_BUCKET}


def test_a_seek_study_id_must_exist_sit_in_the_investigation_and_carry_the_title(alpha):
    reader = FakeReader(alpha)
    assert m.match_targets([_raw(title="Alpha Paper Existing", seek_study_id=21)], reader)[0][0].seek_study_id == 21
    for seek_id, title, reason in ((404, "Paper One", m.SEEK_STUDY_NOT_FOUND),
                                   (30, "Beta Unpublished", m.STUDY_NOT_IN_INVESTIGATION),
                                   (21, "Another Title", m.SEEK_STUDY_TITLE_DIFFERS)):
        _, unmatched = m.match_targets([_raw(title=title, seek_study_id=seek_id)], reader)
        assert {u.reason for u in unmatched} == {reason}, seek_id


def test_sample_ids_are_checked_against_seek(alpha):
    targets, unmatched = m.match_targets(
        [m.RawTarget(title="Paper One", investigation_id=7, key="graph_only:90",
                     sample_ids=[(2, "paper 90"), (777, "paper 90")])], FakeReader(alpha))
    assert targets[0].sample_ids == [2] and targets[0].key == "graph_only:90"
    assert [(u.reason, u.submitted) for u in unmatched] == [(m.SAMPLE_ID_NOT_FOUND, "777")]


def test_blank_description_doi_and_pmid_are_none_and_the_submitted_uid_is_reported_as_given(alpha):
    raw = _raw(description="  ", doi=" ", pmid="", uids=[("TIS-260101ZZZ-9", "dev uid TIS-260101ZZZ-9-PUB")],
               submitted={"TIS-260101ZZZ-9": "TIS-260101ZZZ-9-PUB"})
    _, unmatched = m.match_targets([raw], FakeReader(alpha))
    assert unmatched[0].submitted == "TIS-260101ZZZ-9-PUB"
    assert m.clean("  ") is None and m.clean(1111) == "1111" and m.clean(" 10.0000/x ") == "10.0000/x"
