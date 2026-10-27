import pytest
from pydantic import ValidationError

from NessieAI.ns.paper.curation_models import (Blank, CurationArtifact, Row, TreeNode, Value, VerifiedArtifact)


def _curation(**over):
    base = {
        "tree": [{"node_id": "mus", "sample_type": "MUS", "label": "mice"},
                 {"node_id": "seq", "sample_type": "D.SEQ", "label": "RNA-seq",
                  "skeleton_rows": ["geo:GSM1"], "parents": [{"node_id": "mus", "assay": "RNA-seq"}]}],
        "rows": [{"row_key": "node:mus:1", "node_id": "mus",
                  "values": {"Sex": {"value": "female", "evidence": ["E:methods:p1"]},
                             "Age": {"blank": "not_in_sources"}}},
                 {"row_key": "geo:GSM1", "node_id": "seq", "values": {}}],
        "excluded": [], "protocols": [], "decisions": [], "gaps": [], "errata": [],
    }
    base.update(over)
    return base


def test_a_cell_is_a_value_or_a_blank():
    cur = CurationArtifact.model_validate(_curation())
    cells = cur.rows[0].values
    assert isinstance(cells["Sex"], Value) and cells["Sex"].evidence == ["E:methods:p1"]
    assert isinstance(cells["Age"], Blank) and cells["Age"].blank == "not_in_sources"


def test_a_cell_with_both_a_value_and_a_blank_is_refused():
    bad = _curation()
    bad["rows"][0]["values"]["Sex"] = {"value": "female", "blank": "conflict"}
    with pytest.raises(ValidationError):
        CurationArtifact.model_validate(bad)


def test_duplicate_node_or_row_keys_are_refused():
    with pytest.raises(ValidationError, match="node_id"):
        CurationArtifact.model_validate(_curation(tree=_curation()["tree"] * 2))
    rows = _curation()["rows"]
    with pytest.raises(ValidationError, match="row_key"):
        CurationArtifact.model_validate(_curation(rows=rows + rows[:1]))


def test_the_curation_round_trips_through_json():
    cur = CurationArtifact.model_validate(_curation())
    assert CurationArtifact.model_validate_json(cur.model_dump_json()) == cur


def test_a_verified_artifact_carries_its_curation():
    cur = CurationArtifact.model_validate(_curation())
    ver = VerifiedArtifact(curation=cur, held=[], gaps=[], findings=[], uid_plan=[], bounced=False)
    assert VerifiedArtifact.model_validate_json(ver.model_dump_json()).curation == cur
