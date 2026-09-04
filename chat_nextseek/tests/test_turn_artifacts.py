"""Every finished turn hands the UI its downloadable files.

`_emit_query_complete` already carried a `files` manifest, but only the
reporter branch ever passed `artifacts`, and `artifacts` is the only channel
the browser reads.  So graph_query, new_search/refine_last_search and the
planner route each registered their outputs and then offered the user nothing.
"""

import pytest

from chat_nextseek.orchestrator import _artifacts_for, _emit_query_complete


SEARCH_BUNDLE = {
    "id": 1,
    "mode": "new_search",
    "files": [{
        "key": "api_result",
        "label": "Full API result JSON",
        "path": "/app/outputs/run/files/api_result.json",
        "filename": "api_result.json",
        "mime": "application/json",
        "kind": "api",
    }],
}


def test_a_search_bundle_yields_its_result_file():
    assert [(a["artifact_type"], a["key"]) for a in _artifacts_for(SEARCH_BUNDLE)] == [
        ("file", "api_result")
    ]


def test_no_bundle_yields_nothing():
    assert _artifacts_for(None) is None


def test_a_bundle_with_only_internal_traces_yields_nothing():
    bundle = {
        "id": 1,
        "mode": "graph_query",
        "files": [{
            "key": "graph_debug", "label": "Graph query debug JSON",
            "path": "/app/outputs/run/files/graph.json",
            "filename": "graph.json", "mime": "application/json", "kind": "graph",
        }],
    }

    assert _artifacts_for(bundle) is None


def test_a_missing_host_application_degrades_instead_of_failing_the_turn(monkeypatch):
    """chat_nextseek also runs standalone, where nextseek_api is absent. An
    unavailable artifact builder must cost the turn its download buttons, not
    the whole answer."""
    import builtins

    real_import = builtins.__import__

    def no_nextseek_api(name, *args, **kwargs):
        if name.startswith("nextseek_api"):
            raise ImportError("no host application")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_nextseek_api)

    assert _artifacts_for(SEARCH_BUNDLE) is None


def test_query_complete_carries_the_artifacts_to_the_browser():
    events = []

    payload = _emit_query_complete(
        lambda name, data: events.append((name, data)),
        "Found 5,613 samples.",
        {},
        1,
        artifacts=_artifacts_for(SEARCH_BUNDLE),
        files=SEARCH_BUNDLE["files"],
    )

    assert payload["artifacts"][0]["key"] == "api_result"
    assert events[0][0] == "query_complete"
    assert events[0][1]["artifacts"][0]["label"] == "Full API result JSON"
