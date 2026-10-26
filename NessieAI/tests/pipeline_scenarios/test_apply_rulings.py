import json

from NessieAI.tests.pipeline_scenarios import apply_rulings as ar


def _write(tmp_path, stories):
    d = tmp_path / "stories"
    d.mkdir()
    (d / "bulk_rna.json").write_text(json.dumps({"family": "bulk_rna", "stories": stories}))
    return d


def test_accept_and_change(tmp_path):
    d = _write(tmp_path, [
        {"id": "a", "question": "q", "cohort": "c", "route": "nextseek_query", "acceptable": [["rnaseq"]],
         "ruling": {"state": "proposed", "date": "2026-10-26", "note": ""}},
        {"id": "b", "question": "q", "cohort": "c", "route": "nextseek_query", "acceptable": [["rnaseq"]],
         "ruling": {"state": "proposed", "date": "2026-10-26", "note": ""}}])
    changed = ar.apply_rulings({"a": {"state": "accepted"},
                                "b": {"state": "changed", "note": "fork is fine",
                                      "set": {"acceptable": [["rnaseq"], ["rnaseq", "rnasplice"]]}}},
                               "2026-10-30", directory=d)
    rows = {s["id"]: s for s in json.loads((d / "bulk_rna.json").read_text())["stories"]}
    assert changed == ["a", "b"]
    assert rows["a"]["ruling"] == {"state": "accepted", "date": "2026-10-30", "note": ""}
    assert rows["b"]["acceptable"] == [["rnaseq"], ["rnaseq", "rnasplice"]]
    assert rows["b"]["ruling"]["state"] == "changed" and rows["b"]["ruling"]["note"] == "fork is fine"


def test_an_unknown_id_or_field_is_refused(tmp_path):
    d = _write(tmp_path, [{"id": "a", "question": "q", "cohort": "c", "route": "nextseek_query",
                           "ruling": {"state": "proposed", "date": "2026-10-26", "note": ""}}])
    import pytest
    with pytest.raises(KeyError):
        ar.apply_rulings({"zz": {"state": "accepted"}}, "2026-10-30", directory=d)
    with pytest.raises(ValueError):
        ar.apply_rulings({"a": {"state": "changed", "set": {"id": "x"}}}, "2026-10-30", directory=d)
    with pytest.raises(ValueError, match="must_nots"):
        ar.apply_rulings({"a": {"state": "changed", "set": {"must_nots": ["rnaseq"]}}}, "2026-10-30", directory=d)
