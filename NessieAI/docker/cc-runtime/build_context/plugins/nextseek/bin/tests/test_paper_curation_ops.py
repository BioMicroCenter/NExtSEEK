"""evidence-search and curation-check: the curation agent's only tools, local and read-only."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _batch_upload_runner as runner  # noqa: E402

EVIDENCE = {"paper": {"doi": "10.1/x"}, "deposits": [], "gaps": [],
            "items": [{"id": "E:methods:p1", "text": "All mice were female C57BL/6J.", "source": "Methods p1"},
                      {"id": "E:methods:p2", "text": "RNA was sequenced.", "source": "Methods p2"}],
            "skeleton": []}
CONTEXT = {"project": {"chosen": None}, "house_assays": [], "export": {"uids": []},
           "sample_types": [{"code": "MUS", "parents": [], "attributes": [{"title": "Sex"}]}]}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    for name, data in (("evidence.json", EVIDENCE), ("context.json", CONTEXT),
                       ("rulebook.json", {"rulebook_version": 1, "rules": []})):
        (tmp_path / name).write_text(json.dumps(data))
    monkeypatch.setenv("NEXTSEEK_PAPER_INPUT_DIR", str(tmp_path))
    return tmp_path


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def test_search_by_words_and_by_id(inputs, capsys):
    assert runner.main(["evidence-search", "--query", "female mice"]) == 0
    assert [i["id"] for i in _out(capsys)["items"]] == ["E:methods:p1"]
    assert runner.main(["evidence-search", "--id", "E:methods:p2"]) == 0
    assert _out(capsys)["items"][0]["text"] == "RNA was sequenced."


def test_curation_check_reports_errors(inputs, tmp_path, capsys):
    cur = tmp_path / "curation.json"
    cur.write_text(json.dumps({"tree": [{"node_id": "mus", "sample_type": "MUS", "label": "m"}],
                               "rows": [{"row_key": "node:mus:1", "node_id": "mus",
                                         "values": {"Sex": {"value": "male", "evidence": ["E:methods:p1"]}}}]}))
    assert runner.main(["curation-check", "--file", str(cur)]) == 0
    out = _out(capsys)
    assert out["errors"] == 1 and out["findings"][0]["code"] == "value_not_in_sources"


def test_curation_check_says_when_there_is_no_file_yet(inputs, tmp_path, capsys):
    assert runner.main(["curation-check", "--file", str(tmp_path / "nope.json")]) == 0
    assert _out(capsys)["errors"] == 1


def test_curation_check_reports_broken_json_as_a_shape_error(inputs, tmp_path, capsys):
    cur = tmp_path / "curation.json"
    cur.write_text("{not json")
    assert runner.main(["curation-check", "--file", str(cur)]) == 0
    assert _out(capsys)["findings"][0]["code"] == "shape"
