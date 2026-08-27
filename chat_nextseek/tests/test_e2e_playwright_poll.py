"""Unit tests for the poll-capture transport + payload adapters (poll.py).

These are pure functions (no browser, no network): PollCapture drives an
injected get_progress with injected sleep/now, and the adapters read the
documented {status, progress[], result} poll shape.
"""
import pytest

from e2e.playwright.poll import (
    PollCapture,
    artifact_files,
    build_debug,
    cc_cost,
    detect_route,
    query_complete_data,
)


def _payload(status, data=None, *, extra_events=None):
    progress = list(extra_events or [])
    if data is not None:
        progress.append({"event": "query_complete", "data": data})
    return {"status": status, "progress": progress}


# ── PollCapture ──────────────────────────────────────────────────────────────


def test_poll_returns_first_terminal_payload():
    seq = iter([
        _payload("pending"),
        _payload("running"),
        _payload("completed", {"reply": "done", "debug": {}}),
    ])
    cap = PollCapture(sleep=lambda _s: None, now=lambda: 0.0)
    out = cap.poll_until_complete(lambda _t: next(seq), "task-1", timeout_s=100, interval_s=0)
    assert out["status"] == "completed"


def test_poll_error_status_is_terminal():
    cap = PollCapture(sleep=lambda _s: None, now=lambda: 0.0)
    out = cap.poll_until_complete(lambda _t: _payload("error"), "task-1", timeout_s=100, interval_s=0)
    assert out["status"] == "error"


def test_poll_polls_at_least_once_even_at_zero_timeout():
    cap = PollCapture(sleep=lambda _s: None, now=lambda: 0.0)
    out = cap.poll_until_complete(lambda _t: _payload("completed", {"reply": "x", "debug": {}}),
                                  "task-1", timeout_s=0, interval_s=0)
    assert out["status"] == "completed"


def test_poll_raises_timeout_when_never_terminal():
    ticks = iter([0.0, 1.0, 2.0, 99.0, 100.0, 101.0])
    cap = PollCapture(sleep=lambda _s: None, now=lambda: next(ticks))
    with pytest.raises(TimeoutError):
        cap.poll_until_complete(lambda _t: _payload("running"), "task-1", timeout_s=50, interval_s=0)


# ── route classification ─────────────────────────────────────────────────────


def test_detect_route_ns_from_debug():
    assert detect_route(_payload("completed", {"reply": "r", "debug": {"parser_plan": {}}})) == "ns"


def test_detect_route_cc_from_total_cost():
    assert detect_route(_payload("completed", {"reply": "r", "total_cost_usd": 0.1})) == "cc"


def test_detect_route_cc_from_cc_session_id():
    assert detect_route(_payload("completed", {"reply": "r", "cc_session_id": "s"})) == "cc"


def test_detect_route_unknown_when_no_query_complete():
    assert detect_route(_payload("error")) == "unknown"


def test_cc_cost_reads_total_cost_usd():
    assert cc_cost(_payload("completed", {"reply": "r", "total_cost_usd": 0.37})) == 0.37


def test_cc_cost_none_for_ns_payload():
    assert cc_cost(_payload("completed", {"reply": "r", "debug": {}})) is None


# ── build_debug ──────────────────────────────────────────────────────────────


def test_build_debug_returns_persisted_debug():
    data = {"reply": "r", "debug": {"parser_plan": {"mode": "graph_query"}}}
    assert build_debug(_payload("completed", data))["parser_plan"]["mode"] == "graph_query"


def test_build_debug_backfills_api_ok_from_search_complete():
    data = {"reply": "r", "debug": {"api_result_meta": {}}}
    sc = {"event": "search_complete", "data": {"source": "api", "ok": True}}
    debug = build_debug(_payload("completed", data, extra_events=[sc]))
    assert debug["api_result_meta"]["ok"] is True


def test_build_debug_backfills_neo4j_ok_from_search_complete():
    data = {"reply": "r", "debug": {"graph_result": {}}}
    sc = {"event": "search_complete", "data": {"source": "neo4j", "ok": True}}
    debug = build_debug(_payload("completed", data, extra_events=[sc]))
    assert debug["graph_result"]["ok"] is True


# ── artifact_files (download-key selection) ──────────────────────────────────


def test_artifact_files_prefers_data_artifacts_unsuffixed_key():
    """The UI download button + endpoint use the un-suffixed key from
    data.artifacts (geo_seq_workbooks), NOT the flat data.files manifest which
    suffixes list-valued artifacts (geo_seq_workbooks_0)."""
    data = {
        "reply": "r",
        "artifacts": [
            {"artifact_type": "file", "key": "geo_seq_workbooks", "filename": "geo_seq.xlsx"},
            {"artifact_type": "text", "key": "summary"},  # not a downloadable file/table
        ],
        "files": [{"key": "geo_seq_workbooks_0"}],  # the wrong (suffixed) manifest key
    }
    files = artifact_files(_payload("completed", data))
    assert [f["key"] for f in files] == ["geo_seq_workbooks"]


def test_artifact_files_includes_table_artifacts():
    data = {"reply": "r", "artifacts": [{"artifact_type": "table", "key": "results"}]}
    assert [f["key"] for f in artifact_files(_payload("completed", data))] == ["results"]


def test_artifact_files_falls_back_to_flat_files_when_no_artifacts():
    data = {"reply": "r", "files": [{"key": "export_0"}]}
    assert [f["key"] for f in artifact_files(_payload("completed", data))] == ["export_0"]


def test_artifact_files_empty_when_none_present():
    assert artifact_files(_payload("completed", {"reply": "r", "debug": {}})) == []


def test_query_complete_data_empty_when_task_errored_before_emit():
    assert query_complete_data(_payload("error")) == {}
