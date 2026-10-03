"""Merge-stage contract checks across the laya units (JevLevROUTING PLAN s3).

The unit tests stub each other's halves; these do not. U1's loader and client run against U3's real
laya_common, a calibration file from U3's real writer (scripts/laya/fit_calibration.py) and the checked-in
laya_options.json, and talk to U2's real wrapper (docker/laya/serve_wrapper.py) on loopback with a fake model.
No weights, no network beyond 127.0.0.1.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import threading

import pytest

from NessieAI.router import laya, laya_common, posterior_selector
from NessieAI.router import router as cc_router

REPO = pathlib.Path(__file__).resolve().parents[3]
REV = "20261003-abcdef123456"
KEY = "integration-key"


def _module(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeAgent:
    """laya.Agent.system_one's reply shape for one choice question; probabilities rounded to 4 dp as laya does."""

    def __init__(self):
        self.calls = []

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        (qid, q), = questions.items()
        probs = {k: {"nextseek_query": 0.97, "container_cc": 0.02}.get(k, 0.01) for k in q["criteria"]}
        return {"answers": {qid: {"type": "choice", "probabilities": probs, "answer_confidence": 0.97}},
                "usage": {"state_tokens": 40, "truncated": False}}


@pytest.fixture()
def real_config(tmp_path, monkeypatch):
    """A calibration file written by U3's calibration_doc for the checked-in options and today's prompt files."""
    fit = _module("scripts/laya/fit_calibration.py", "fit_calibration_under_test")
    options = json.loads(laya.OPTIONS_PATH.read_text())
    rows = [{"probabilities": {"nextseek_query": 0.9, "container_cc": 0.06, "unrelated": 0.04},
             "teacher_route": "nextseek_query", "truth_route": None, "either": False}] * 40
    doc, _ = fit.calibration_doc(rows, REV, options, laya_common.prompt_hash(), date_iso="2026-10-03")
    cal = tmp_path / "laya_calibration.json"
    cal.write_text(json.dumps(doc))
    monkeypatch.setattr(laya, "CALIBRATION_PATH", cal)
    monkeypatch.setattr(posterior_selector, "posterior_routing_enabled", lambda: False)
    monkeypatch.setenv("NESSIE_FOLLOWUP_ROUTING", "split")
    monkeypatch.setenv("NESSIE_LAYA_SHADOW", "0")
    monkeypatch.setenv("NESSIE_LAYA_LIVE", "")
    laya._reset()
    yield doc
    laya._reset()


@pytest.fixture()
def sidecar(monkeypatch):
    wrapper = _module("docker/laya/serve_wrapper.py", "serve_wrapper_under_test")
    agent = FakeAgent()
    server = wrapper.make_server(agent, revision=REV, api_key=KEY, host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(laya, "SIDECAR_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("LAYA_API_KEY", KEY)
    yield agent
    server.shutdown()
    server.server_close()


def test_a_calibration_from_the_real_writer_turns_shadow_and_live_on(real_config, monkeypatch):
    monkeypatch.setenv("NESSIE_LAYA_SHADOW", "1")
    assert laya.mode() == "shadow"
    laya._reset()
    monkeypatch.setenv("NESSIE_LAYA_LIVE", REV)
    assert laya.mode() == "live"


def test_the_client_and_the_wrapper_agree_on_the_wire(real_config, sidecar, monkeypatch):
    monkeypatch.setenv("NESSIE_LAYA_LIVE", REV)
    rec = laya.start("How many mouse liver samples are there?", [], laya.mode()).result()
    assert rec["gate"] == "pass", rec
    assert rec["route"] == "nextseek_query" and rec["revision"] == REV and rec["state_tokens"] == 40
    state, questions = sidecar.calls[0]
    opts = json.loads(laya.OPTIONS_PATH.read_text())
    assert state == laya_common.condense("How many mouse liver samples are there?", [])
    assert questions == {"route": {"type": "choice", "instructions": opts["prompt"],
                                   "criteria": {o["key"]: o["text"] for o in opts["options"]}}}


def test_live_decide_fast_paths_and_shadow_keeps_baml(real_config, sidecar, monkeypatch):
    baml = cc_router.RouteDecision(route=cc_router.ROUTE_CC, model_class="opus", model_id="m",
                                   reasoning="baml", source="baml")
    monkeypatch.setattr(cc_router, "_legacy_decide", lambda q, h=None: baml)
    monkeypatch.setattr(cc_router.random, "random", lambda: 0.99)  # no audit
    monkeypatch.setenv("NESSIE_LAYA_SHADOW", "1")
    shadow = cc_router.decide("How many mouse liver samples are there?")
    assert (shadow.route, shadow.source, shadow.laya["mode"], shadow.laya["gate"]) == ("container_cc", "baml",
                                                                                        "shadow", "pass")
    laya._reset()
    monkeypatch.setenv("NESSIE_LAYA_LIVE", REV)
    live = cc_router.decide("How many mouse liver samples are there?")
    assert (live.route, live.source, live.router_model) == ("nextseek_query", "laya", "laya:" + REV)
