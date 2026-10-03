"""Shared setup for the laya tests: files in tmp_path, laya_common stubbed, the sidecar faked."""
from __future__ import annotations

import json

from NessieAI.router import laya, laya_common, posterior_selector

REV = "20261003-abcdef123456"
KEYS = ("nextseek_query", "container_cc", "unrelated")


def fake_apply_temperature(probs, T):
    w = {k: v ** (1.0 / T) for k, v in probs.items()}
    z = sum(w.values())
    return {k: v / z for k, v in w.items()}


def setup(tmp_path, monkeypatch, *, shadow="0", live="", temperature=1.0, threshold=0.8,
          cal_options_hash="oh", cal_prompt_hash="ph", posterior=False, followup="split", write_files=True):
    """Calibration and options files, stubbed hashes, env set, caches cleared. Returns the laya module."""
    cal = tmp_path / "cal.json"
    opts = tmp_path / "opts.json"
    if write_files:
        cal.write_text(json.dumps({
            "revision": REV, "options_hash": cal_options_hash, "prompt_hash": cal_prompt_hash,
            "question_type": "choice", "option_count": 3, "temperature": temperature, "threshold": threshold,
            "fitted_on": {"n": 1, "date": "2026-10-03"}}))
        opts.write_text(json.dumps({
            "question_id": "route", "prompt": "Which engine?",
            "options": [{"key": k, "text": "t-" + k} for k in KEYS], "source_hashes": {}, "options_hash": "oh"}))
    monkeypatch.setattr(laya, "CALIBRATION_PATH", cal)
    monkeypatch.setattr(laya, "OPTIONS_PATH", opts)
    monkeypatch.setattr(laya_common, "condense", lambda q, h: "STATE", raising=False)
    monkeypatch.setattr(laya_common, "options_hash", lambda o: "oh", raising=False)
    monkeypatch.setattr(laya_common, "prompt_hash", lambda root=None: "ph", raising=False)
    monkeypatch.setattr(laya_common, "apply_temperature", fake_apply_temperature, raising=False)
    monkeypatch.setattr(posterior_selector, "posterior_routing_enabled", lambda: posterior)
    monkeypatch.setenv("NESSIE_LAYA_SHADOW", shadow)
    monkeypatch.setenv("NESSIE_LAYA_LIVE", live)
    monkeypatch.setenv("NESSIE_FOLLOWUP_ROUTING", followup)
    laya._reset()
    return laya


def reply(ns=0.02, cc=0.97, un=0.01, revision=REV, state_tokens=100, truncated=False):
    return {"revision": revision, "probabilities": {"nextseek_query": ns, "container_cc": cc, "unrelated": un},
            "answer_confidence": max(ns, cc, un), "state_tokens": state_tokens, "truncated": truncated}


def fake_post(monkeypatch, outcome):
    """laya._post returns ``outcome`` (a dict) or raises it (an exception). Returns the call list."""
    calls = []

    def _post(body):
        calls.append(body)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(laya, "_post", _post)
    return calls
