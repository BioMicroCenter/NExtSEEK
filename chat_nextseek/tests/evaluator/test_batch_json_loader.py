from __future__ import annotations

import json

import pytest

from chat_nextseek.evaluator.reports import load_queries


def test_load_queries_keeps_text_behavior(tmp_path):
    path = tmp_path / "queries.txt"
    path.write_text("\n# note\nfirst\nsecond\n", encoding="utf-8")
    assert load_queries(path) == ["first", "second"]


def test_load_queries_reads_repo_native_json_shape(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(
        json.dumps(
            [
                {"id": "A-1", "question": "Find me mice treated with NDMA."},
                {"id": "A-2", "question": "Show me GBM samples.", "notes": "ignored"},
            ]
        ),
        encoding="utf-8",
    )
    assert load_queries(path) == ["Find me mice treated with NDMA.", "Show me GBM samples."]


def test_load_queries_reads_testing_json_full_test_shape(tmp_path):
    path = tmp_path / "testing.json"
    path.write_text(
        json.dumps(
            {
                "smart_test": {"tests": []},
                "full_test": {
                    "description": "full regression",
                    "tests": [
                        {"id": "Search-Basic-1", "query": "Find me mice treated with NDMA."},
                        {"id": "Search-Basic-2", "query": "Show me GBM samples."},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_queries(path) == ["Find me mice treated with NDMA.", "Show me GBM samples."]


def test_load_queries_rejects_non_list_json(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps({"question": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list of query objects or a testing.json full_test payload"):
        load_queries(path)


def test_load_queries_reports_missing_question_with_entry_id(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps([{"id": "A-3", "notes": "missing"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="A-3"):
        load_queries(path)


def test_load_queries_reports_missing_query_with_entry_id_for_testing_json(tmp_path):
    path = tmp_path / "testing.json"
    path.write_text(
        json.dumps(
            {
                "full_test": {
                    "tests": [{"id": "Search-Basic-3", "notes": "missing"}],
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Search-Basic-3"):
        load_queries(path)
