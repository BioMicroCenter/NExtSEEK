"""The studies tool's models (tool spec 4.2 and 6.7)."""
import json

import pytest
from pydantic import ValidationError

from nextseek_api.studies import models


def _target(**extra):
    base = dict(key="sheet:7:paper one", investigation_id=7, title="Paper One", sample_ids=[3, 1, 3, 2])
    base.update(extra)
    return models.StudyTarget(**base)


def test_sample_ids_are_unique_and_sorted():
    assert _target().sample_ids == [1, 2, 3]


def test_extra_fields_are_refused():
    with pytest.raises(ValidationError):
        _target(colour="blue")


def test_a_blank_title_is_refused():
    with pytest.raises(ValidationError):
        _target(title="   ")


def test_an_association_set_round_trips_byte_for_byte(tmp_path):
    aset = models.AssociationSet(source="sheet", source_ref="studies.csv sha256:0", created_at="2026-01-01T00:00:00Z",
                                 targets=[_target(provenance={"1": ["row 2"]})],
                                 unmatched=[models.Unmatched(reason="sample_uid_not_found", target_key="sheet:7:x",
                                                             submitted="TIS-260101AAA-9")])
    path = tmp_path / "associations.json"
    path.write_text(aset.to_json(), encoding="utf-8")
    again = models.AssociationSet.from_file(path)
    assert again.to_json() == aset.to_json()
    assert again.sha256() == aset.sha256()
    assert aset.to_json().endswith("\n")


def test_core_leaves_out_keys_provenance_and_the_source():
    one = models.AssociationSet(source="sheet", source_ref="a", created_at="t1",
                                targets=[_target(provenance={"1": ["row 2"]})])
    two = models.AssociationSet(source="graph_only", source_ref="b", created_at="t2",
                                targets=[_target(key="graph_only:90", provenance={"1": ["paper 90"]})])
    assert one.core_json() == two.core_json()
    assert json.loads(one.core_json())["targets"][0]["sample_ids"] == [1, 2, 3]


def test_a_plan_round_trips_and_says_whether_it_creates_a_study(tmp_path):
    plan = models.StudyMovePlan(
        plan_version=models.PLAN_VERSION, created_at="t", code_sha="c", associations_sha256="a", run_id="r",
        buckets={7: 20}, seek_next_study_id=100, graph_max_study_id=60,
        targets=[models.TargetPlan(key="k", investigation_id=7, title="Paper One",
                                   study=models.StudyAction(action="create", payload={"data": {}}))],
        units=[], publications=[], graph=models.GraphPlan(sync_ids={1: [3, 4]}), skipped=[], no_change={},
        empty_bucket_assays=[], warnings=[], summary={})
    path = tmp_path / "plan.json"
    path.write_text(plan.to_json(), encoding="utf-8")
    again = models.StudyMovePlan.from_file(path)
    assert again == plan
    assert again.graph.sync_ids == {1: [3, 4]}
    assert again.creates_study()


def test_the_share_input_and_project_rows_refuse_extra_keys_and_hash_stably():
    inp = models.ShareInput(sample_uids=["TIS-260101AAA-2"], source_project_id=3, destination_project_id=5,
                            destination_study_id=40, created_at="t")
    assert inp.sha256() == models.ShareInput.model_validate_json(inp.to_json()).sha256()
    with pytest.raises(ValidationError):
        models.ProjectInsert(project_id=5, sample_id=1, role="parent", colour="blue")
    with pytest.raises(ValidationError):
        models.ProjectInsert(project_id=5, sample_id=1, role="child")


def test_a_move_plan_defaults_to_move_and_loads_without_the_share_fields():
    plan = models.StudyMovePlan(
        plan_version=models.PLAN_VERSION, created_at="t", code_sha="c", associations_sha256="a", run_id="r",
        buckets={}, seek_next_study_id=100, targets=[], units=[models.LinkUnit(
            unit=1, target_key="k", investigation_id=7, source_assay_ids=[101], digest="d", inserts=[], removals=[],
            sync_ids=[1])], publications=[], graph=models.GraphPlan(), skipped=[], no_change={},
        empty_bucket_assays=[], warnings=[], summary={})
    assert (plan.mode, plan.share, plan.units[0].project_inserts) == ("move", None, [])
    older = json.loads(plan.to_json())
    del older["mode"], older["share"], older["units"][0]["project_inserts"]
    assert models.StudyMovePlan.model_validate(older) == plan
