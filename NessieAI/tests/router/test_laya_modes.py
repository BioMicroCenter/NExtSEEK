"""laya mode, the live-revision guard and the gate (SPEC s2, s6.2; tests 3, 4 last cases)."""
from __future__ import annotations

import socket
import urllib.error

import pytest

from NessieAI.router import laya_common
from NessieAI.tests.router._laya_support import REV, fake_post, reply, setup


def _run(laya, query="find mouse samples", history=None, md="live"):
    return laya.start(query, history or [], md).result()


@pytest.mark.parametrize("shadow,live,want", [
    ("0", "", "off"), ("", "", "off"), ("yes", "", "off"), ("1", "", "shadow"),
    ("0", REV, "live"), ("1", REV, "live"),
    ("1", "1", "shadow"), ("0", "true", "off"), ("1", "yes", "shadow"), ("1", "20250101-000000000000", "shadow"),
])
def test_mode_table(tmp_path, monkeypatch, shadow, live, want):
    laya = setup(tmp_path, monkeypatch, shadow=shadow, live=live)
    assert laya.mode() == want


def test_unset_env_is_off(tmp_path, monkeypatch):
    laya = setup(tmp_path, monkeypatch)
    monkeypatch.delenv("NESSIE_LAYA_SHADOW")
    monkeypatch.delenv("NESSIE_LAYA_LIVE")
    assert laya.mode() == "off"


def test_posterior_on_means_off_with_one_error(tmp_path, monkeypatch, caplog):
    laya = setup(tmp_path, monkeypatch, shadow="1", live=REV, posterior=True)
    assert laya.mode() == "off" and laya.mode() == "off"
    assert sum("posterior" in r.message for r in caplog.records if r.levelname == "ERROR") == 1


def test_refused_live_logs_one_error(tmp_path, monkeypatch, caplog):
    laya = setup(tmp_path, monkeypatch, shadow="1", live="1")
    laya.mode(); laya.mode()
    errs = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errs) == 1 and "live refused" in errs[0].message


@pytest.mark.parametrize("kind", ["missing", "malformed", "bad_threshold"])
def test_bad_files_mean_off_with_one_error(tmp_path, monkeypatch, caplog, kind):
    laya = setup(tmp_path, monkeypatch, shadow="1", live=REV, write_files=kind != "missing",
                 threshold=0.2 if kind == "bad_threshold" else 0.8)
    if kind == "malformed":
        laya.CALIBRATION_PATH.write_text("{nope")
    assert laya.mode() == "off" and laya.mode() == "off"
    assert sum(r.levelname == "ERROR" for r in caplog.records) == 1


@pytest.mark.parametrize("temperature", [0.0, -1.0, float("nan"), float("inf")])
def test_a_temperature_that_is_not_finite_and_positive_means_off(tmp_path, monkeypatch, caplog, temperature):
    laya = setup(tmp_path, monkeypatch, shadow="1", live=REV, temperature=temperature)
    assert laya.mode() == "off" and laya.mode() == "off"
    assert sum(r.levelname == "ERROR" for r in caplog.records) == 1


def test_startup_info_line_once(tmp_path, monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO)
    laya = setup(tmp_path, monkeypatch, shadow="1")
    laya.mode(); laya.mode()
    lines = [r.message for r in caplog.records if r.message.startswith("laya routing mode=")]
    assert lines == [f"laya routing mode=shadow revision={REV} options_hash=oh prompt_hash=ph"]


def test_pass_and_record(tmp_path, monkeypatch):
    laya = setup(tmp_path, monkeypatch, live=REV)
    calls = fake_post(monkeypatch, reply())
    rec = _run(laya)
    assert rec["gate"] == "pass" and rec["route"] == "container_cc" and rec["mode"] == "live"
    assert set(rec) == set(laya.RECORD_KEYS)
    assert rec["margin"] == pytest.approx(0.95) and rec["revision"] == REV
    assert calls[0]["state"] == "STATE" and calls[0]["options"]["unrelated"] == "t-unrelated"


@pytest.mark.parametrize("outcome,gate", [
    (TimeoutError(), "timeout"), (socket.timeout(), "timeout"),
    (urllib.error.URLError(TimeoutError()), "timeout"),
    (urllib.error.URLError("refused"), "http_error"),
    (urllib.error.HTTPError("u", 401, "bad key", {}, None), "http_error"),
    (ValueError("not json"), "bad_json"), ({"nope": 1}, "bad_json"),
    (reply(revision="other"), "revision_mismatch"),
    (reply(truncated=True), "truncated"), (reply(state_tokens=257), "too_many_tokens"),
    (reply(ns=0.5, cc=0.45, un=0.05), "below_threshold"),
    (reply(ns=0.01, cc=0.01, un=0.98), "unrelated"),
])
def test_gate_reasons(tmp_path, monkeypatch, outcome, gate):
    laya = setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, outcome)
    assert _run(laya)["gate"] == gate


@pytest.mark.parametrize("ns,cc,un", [(0.2, float("nan"), 0.1), (0.2, float("inf"), 0.1), (-0.5, 0.97, 0.01),
                                      (0.0, 0.0, 0.0)])
def test_non_finite_negative_or_all_zero_probabilities_are_bad_json(tmp_path, monkeypatch, ns, cc, un):
    laya = setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, reply(ns=ns, cc=cc, un=un))
    assert _run(laya)["gate"] == "bad_json"


def test_a_nan_calibrated_confidence_never_clears_the_threshold(tmp_path, monkeypatch):
    laya = setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, reply())
    monkeypatch.setattr(laya_common, "apply_temperature", lambda probs, T: {k: float("nan") for k in probs})
    assert _run(laya)["gate"] == "below_threshold"


@pytest.mark.parametrize("kw", [{"cal_options_hash": "x"}, {"cal_prompt_hash": "x"}])
def test_hash_mismatch_gate_without_a_call(tmp_path, monkeypatch, kw):
    laya = setup(tmp_path, monkeypatch, live=REV, **kw)
    calls = fake_post(monkeypatch, reply())
    assert _run(laya)["gate"] == "hash_mismatch" and calls == []


@pytest.mark.parametrize("query", ["what is IL-1β", "样本"])
def test_non_latin_gate_without_a_call(tmp_path, monkeypatch, query):
    laya = setup(tmp_path, monkeypatch, live=REV)
    calls = fake_post(monkeypatch, reply())
    assert _run(laya, query)["gate"] == "non_latin" and calls == []


def test_latin_accents_pass(tmp_path, monkeypatch):
    laya = setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, reply())
    assert _run(laya, "résumé of samples")["gate"] == "pass"


def test_followup_cc_gate_without_a_call(tmp_path, monkeypatch):
    laya = setup(tmp_path, monkeypatch, live=REV, followup="cc")
    calls = fake_post(monkeypatch, reply())
    assert _run(laya)["gate"] == "followup_cc" and calls == []


def test_exception_inside_laya_code_is_a_gate(tmp_path, monkeypatch):
    laya = setup(tmp_path, monkeypatch, live=REV)
    fake_post(monkeypatch, RuntimeError("boom"))
    rec = _run(laya)
    assert rec["gate"] == "exception" and rec["error"] == "RuntimeError"


def test_deadline_is_a_hard_timeout(tmp_path, monkeypatch):
    import time
    laya = setup(tmp_path, monkeypatch, live=REV)
    monkeypatch.setattr(laya, "DEADLINE_S", 0.1)
    monkeypatch.setattr(laya, "_post", lambda body: time.sleep(1) or reply())
    t0 = time.monotonic()
    assert _run(laya)["gate"] == "timeout"
    assert time.monotonic() - t0 < 0.6


def test_temperature_is_applied(tmp_path, monkeypatch):
    laya = setup(tmp_path, monkeypatch, live=REV, temperature=2.0, threshold=0.5)
    fake_post(monkeypatch, reply(ns=0.2, cc=0.7, un=0.1))
    rec = _run(laya)
    assert rec["calibrated_confidence"] < 0.7 and rec["answer_confidence"] == 0.7
