"""The pure assay planner: one row's ASSAY ids, findings, creations and parent pairs."""
from types import SimpleNamespace

from NessieAI.ns import reingest_qa as qa
from NessieAI.ns.reingest import assay_plan


def _plan(studies, parents_by_study, without=(), titles=None):
    return SimpleNamespace(studies=studies, parents_by_study=parents_by_study,
                           parents_without_study=list(without),
                           study_titles=titles or {s: f"Study {s}" for s in studies})


def _row(house_name="Gene Expression Analysis", parents=("D.SEQ-1",), test_only=False,
         title="Gene Expression Analysis", plan=None):
    asked = []

    def study_assays(uids, t):
        asked.append((tuple(uids), t))
        return plan
    result = assay_plan.plan_row(
        sample_type="A.GEX", row_index=0, house_name=house_name, parents=list(parents),
        test_only=test_only, house_title=lambda name: title, study_assays=study_assays)
    return result, asked


def _codes(result):
    return [(f.code, f.severity) for f in result.findings]


def test_one_matching_assay_per_study_files_the_row_and_pairs_the_parents():
    result, asked = _row(plan=_plan({51: [315]}, {51: ["D.SEQ-1"]}))
    assert result.assay_ids == [315]
    assert result.findings == []
    assert result.to_create == []
    assert result.parent_assays == [{"parent_uid": "D.SEQ-1", "study_id": 51,
                                     "internal_assay_title": "Gene Expression Analysis",
                                     "assay_id": 315}]
    assert result.studies == frozenset({51})
    assert asked == [(("D.SEQ-1",), "Gene Expression Analysis")]


def test_a_study_with_no_assay_is_marked_to_create():
    result, _ = _row(plan=_plan({25: []}, {25: ["D.SEQ-1"]}, titles={25: "Study B"}))
    assert result.assay_ids == []
    assert _codes(result) == [(qa.ASSAY_TO_CREATE, qa.SOFT)]
    assert result.to_create == [{"study_id": 25, "study_title": "Study B",
                                 "internal_assay_title": "Gene Expression Analysis",
                                 "assay_title": "Gene Expression Analysis - Data Linked",
                                 "sample_type": "A.GEX", "rows": [0]}]
    assert result.parent_assays[0]["assay_id"] is None


def test_two_studies_file_under_one_and_create_in_the_other():
    result, _ = _row(parents=("D.SEQ-1", "D.SEQ-2"),
                     plan=_plan({25: [], 51: [315]}, {25: ["D.SEQ-2"], 51: ["D.SEQ-1"]}))
    assert result.assay_ids == [315]
    assert [e["study_id"] for e in result.to_create] == [25]
    assert sorted((p["parent_uid"], p["assay_id"]) for p in result.parent_assays) == \
        [("D.SEQ-1", 315), ("D.SEQ-2", None)]


def test_two_matching_assays_in_a_study_block_the_row():
    result, _ = _row(plan=_plan({17: [40, 41]}, {17: ["D.SEQ-1"]}))
    assert result.assay_ids == []
    assert _codes(result) == [(qa.ASSAY_AMBIGUOUS, qa.HARD)]
    assert result.findings[0].detail["assay_ids"] == [40, 41]


def test_a_parent_in_no_study_blocks_the_row():
    result, _ = _row(plan=_plan({}, {}, without=["D.SEQ-1"]))
    assert _codes(result) == [(qa.PARENT_IN_NO_STUDY, qa.HARD)]
    assert result.findings[0].detail["parents"] == ["D.SEQ-1"]


def test_a_missing_house_assay_blocks_before_any_study_lookup():
    result, asked = _row(title=None)
    assert _codes(result) == [(qa.HOUSE_ASSAY_MISSING, qa.HARD)]
    assert asked == []


def test_a_rule_with_no_assay_blocks():
    result, _ = _row(house_name=None)
    assert _codes(result) == [(qa.ASSAY_NOT_DECLARED, qa.HARD)]


def test_a_test_only_row_with_no_parents_names_the_assay_unresolved():
    result, asked = _row(parents=(), test_only=True)
    assert result.assay_ids == ["Gene Expression Analysis (unresolved: no parents)"]
    assert _codes(result) == [(qa.ASSAY_UNRESOLVED_TEST_ONLY, qa.SOFT)]
    assert asked == []


def test_a_real_row_with_no_parents_adds_nothing():
    result, asked = _row(parents=())
    assert (result.assay_ids, result.findings, asked) == ([], [], [])


def test_rows_in_different_studies_cannot_share_one_assay_sheet():
    one, _ = _row(plan=_plan({51: [315]}, {51: ["D.SEQ-1"]}))
    other, _ = _row(plan=_plan({25: [199]}, {25: ["D.SEQ-1"]}))
    assert assay_plan.split_across_studies([one, one]) is False
    assert assay_plan.split_across_studies([one, other]) is True


def test_merges_keep_one_entry_per_key():
    a = {"study_id": 25, "study_title": "B", "internal_assay_title": "X",
         "assay_title": "X - Data Linked", "sample_type": "A.GEX", "rows": [0]}
    b = dict(a, rows=[1])
    assert assay_plan.merge_to_create([a, b]) == [dict(a, rows=[0, 1])]
    pair = {"parent_uid": "D.SEQ-1", "study_id": 25, "internal_assay_title": "X", "assay_id": None}
    assert assay_plan.merge_parent_assays([pair, dict(pair)]) == [pair]
