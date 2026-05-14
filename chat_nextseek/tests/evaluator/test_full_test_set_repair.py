from __future__ import annotations

import json
from pathlib import Path

from chat_nextseek.evaluator.reports import load_queries


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_testing_json_full_test_parses():
    path = REPO_ROOT / "testing.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "full_test" in data
    full_test = data["full_test"]
    assert isinstance(full_test, dict)
    assert isinstance(full_test.get("tests"), list)
    assert len(full_test["tests"]) == 103
    for entry in full_test["tests"]:
        assert "id" in entry
        assert "query" in entry


def test_load_queries_reads_repo_full_test_corpus():
    path = REPO_ROOT / "testing.json"
    queries = load_queries(path)
    assert len(queries) == 103
    assert queries[0]
