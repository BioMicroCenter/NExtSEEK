"""The report op's protocol fetches and downloads stop at the op's time left (approach 1, piece 2; W1-2).

Inside an op's call_scope every protocol request waits at most what is left, nothing is sent with the floor or less
left, and the report flow hands the op's deadline to the fetch and download loops. Outside an op (an NS turn) the
request keeps its 90 s and the loops get no deadline.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from chat_nextseek import call_scope
from chat_nextseek.helpers.tools.nextseek_api import OUT_OF_TIME_NOT_SENT
from chat_nextseek.reports import outputs, protocols

CFG = SimpleNamespace(API_USER="u", API_PASS="p", NEXTSEEK_BASE_URL="http://web")
BASE = "http://web"


class _Resp:
    ok = True
    status_code = 200
    text = "{}"

    def json(self):
        return {}


@pytest.fixture
def sent(monkeypatch):
    seen = []

    def fake_get(url, **kwargs):
        seen.append(kwargs.get("timeout"))
        return _Resp()

    monkeypatch.setattr(protocols.requests, "get", fake_get)
    return seen


@pytest.fixture
def op_clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(call_scope, "_monotonic", lambda: now[0])
    return now


def test_outside_an_op_a_protocol_request_keeps_its_90_s(sent):
    assert protocols._request_protocol_record(CFG, BASE, "P.1")["ok"] is True
    assert sent == [90.0]


def test_inside_an_op_a_protocol_request_waits_at_most_the_time_left(sent, op_clock):
    with call_scope.scope(deadline_s=12):
        protocols._request_protocol_record(CFG, BASE, "P.1")
    assert sent == [12.0]


def test_with_no_time_left_no_protocol_request_is_sent(sent, op_clock):
    with call_scope.scope(deadline_s=12):
        op_clock[0] += 11.0  # 1 s left: under the floor
        out = protocols._request_protocol_record(CFG, BASE, "P.1")
    assert sent == []
    assert out["ok"] is False
    assert out["error"] == OUT_OF_TIME_NOT_SENT


def test_a_passed_deadline_fetches_nothing(sent):
    refs = [{"source": "protocol_name", "value": "P.1", "raw": "P.1"}]
    assert protocols.fetch_protocols(CFG, refs, deadline=time.monotonic()) == {}
    assert sent == []


class _Stop(Exception):
    pass


@pytest.fixture
def report_flow(monkeypatch, tmp_path):
    """generate_report_outputs up to its protocol block; returns the deadlines the fetch and download loops got."""
    seen = {}
    monkeypatch.setattr(outputs, "fetch_reporter_metadata", lambda config, uids: {"ok": True})
    monkeypatch.setattr(outputs, "annotate_metadata_with_sampletypes", lambda config, md: md)
    monkeypatch.setattr(outputs, "extract_protocol_refs_from_metadata",
                        lambda md: [{"source": "protocol_name", "value": "P.1", "raw": "P.1"}])

    def fake_fetch(config, refs, *, deadline=None):
        seen["fetch"] = deadline
        return {"P.1": {"ok": True}}

    def fake_download(payloads, base_dir, config=None, *, deadline=None, **kwargs):
        seen["download"] = deadline
        raise _Stop

    monkeypatch.setattr(outputs, "fetch_protocols", fake_fetch)
    monkeypatch.setattr(outputs, "download_and_extract_protocol_blobs", fake_download)

    def run():
        with pytest.raises(_Stop):
            outputs.generate_report_outputs(
                config=CFG, user_query="q", parser_plan={}, reporter_plan=SimpleNamespace(report_type=None),
                uids=["U1"], log_dir=tmp_path, report_writer_fn=lambda *a: None, per_sample_reports=False,
            )
        return seen

    return run


def test_the_report_flow_hands_the_ops_deadline_to_both_loops(report_flow):
    with call_scope.scope(deadline_s=12):
        seen = report_flow()
    for key in ("fetch", "download"):
        assert seen[key] is not None
        assert 0 < seen[key] - time.monotonic() <= 12


def test_outside_an_op_the_report_flow_sets_no_deadline(report_flow):
    assert report_flow() == {"fetch": None, "download": None}
