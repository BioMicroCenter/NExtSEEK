import json, re

import pytest

from scripts.laya.label_page import build_page, ingest


def _rows():
    return [
        {"hash": "a" * 64, "query": "How many </script> samples?", "family": "FAMSECRET", "entity": "ENTSECRET",
         "route": "container_cc", "truth_kind": "family", "split": "heldout",
         "history": [{"user_message": "earlier question", "assistant_reply": "earlier answer",
                      "router_choice": "nextseek_query", "status": "completed"}]},
        {"hash": "b" * 64, "query": "Who won the game?", "family": "f2", "entity": "e2", "route": "unrelated",
         "truth_kind": "family", "split": "heldout", "history": []},
    ]


def _draft(tmp_path):
    p = tmp_path / "draft.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _rows()) + "\n")
    return p


def test_page_is_one_offline_file_with_full_chat_and_no_hints(tmp_path):
    out = tmp_path / "page.html"
    build_page.build(_draft(tmp_path), out)
    html = out.read_text()
    assert not re.search(r"https?://|//cdn|src=\"//", html)          # no external URL
    assert "earlier question" in html and "earlier answer" in html and "Who won the game?" in html
    for hint in ("FAMSECRET", "ENTSECRET", "container_cc", "truth_kind"):  # blind labelling
        assert hint not in html
    assert "</script> samples" not in html                           # data cannot close the script tag
    assert "relay" not in html                                        # no publish hook until a publish step exists
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1))
    assert [r["hash"] for r in data] == sorted(r["hash"] for r in data)


def test_ingest_round_trips_labels_and_keeps_unlabelled_family_default(tmp_path):
    export = tmp_path / "labels.json"
    export.write_text(json.dumps({"labels": {"a" * 64: "NS"}}))
    out = tmp_path / "labelled.jsonl"
    n = ingest.ingest(_draft(tmp_path), export, out)
    rows = {r["hash"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert rows["a" * 64]["route"] == "nextseek_query" and rows["a" * 64]["truth_kind"] == "human"
    assert rows["b" * 64]["route"] == "unrelated" and rows["b" * 64]["truth_kind"] == "family"
    assert n == 1


@pytest.mark.parametrize("labels", [{"c" * 64: "NS"}, {"a" * 64: "maybe"}])
def test_ingest_rejects_unknown_hash_or_label(tmp_path, labels):
    export = tmp_path / "labels.json"
    export.write_text(json.dumps({"labels": labels}))
    with pytest.raises(ValueError):
        ingest.ingest(_draft(tmp_path), export, tmp_path / "o.jsonl")
