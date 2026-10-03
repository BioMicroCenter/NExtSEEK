"""laya in front of the router (JevLevROUTING, SPEC s2, s6, s7).

Mode comes from env on every call; the calibration and options files, and both hashes, are read
once per process. Stdlib only (urllib): no torch, and router.py imports this lazily inside decide().
Nothing here logs or stores query text. Any failure becomes a ``gate`` reason, never a raise
into the router.
"""
from __future__ import annotations

import json
import logging
import math
import os
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_URL = "http://laya-router:8080"
DEADLINE_S = 1.5
MAX_STATE_TOKENS = 256
AUDIT_RATE = 0.05
SHADOW_ENV = "NESSIE_LAYA_SHADOW"
LIVE_ENV = "NESSIE_LAYA_LIVE"
KEY_ENV = "LAYA_API_KEY"
OPTIONS_PATH = Path(__file__).with_name("laya_options.json")
CALIBRATION_PATH = Path(__file__).with_name("laya_calibration.json")

RECORD_KEYS = ("mode", "route", "probabilities", "answer_confidence", "calibrated", "calibrated_confidence",
               "threshold", "gate", "latency_ms", "revision", "options_hash", "prompt_hash", "state_tokens",
               "truncated", "margin", "error", "baml_route")

_cfg: list = []          # [config dict or None] once loaded
_logged: set = set()     # one log line per process per key


def _reset() -> None:
    """Forget the loaded files and the once-per-process log flags (tests)."""
    _cfg.clear()
    _logged.clear()


def _once(key: str, level: int, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        logger.log(level, msg, *args)


def _load() -> dict | None:
    """Calibration + options + both hashes, or None (one ERROR) when a file is missing or malformed."""
    if _cfg:
        return _cfg[0]
    cfg = None
    try:
        from NessieAI.router import laya_common
        cal = json.loads(CALIBRATION_PATH.read_text())
        opts = json.loads(OPTIONS_PATH.read_text())
        options = {o["key"]: o["text"] for o in opts["options"]}
        threshold, temperature = float(cal["threshold"]), float(cal["temperature"])
        if not 0.5 <= threshold <= 0.99 or not (math.isfinite(temperature) and temperature > 0) \
                or cal["question_type"] != "choice":
            raise ValueError("calibration out of range")
        cfg = {
            "revision": str(cal["revision"]), "temperature": temperature, "threshold": threshold,
            "prompt": opts["prompt"], "options": options,
            # the whole options file, as build_options.py and fit_calibration.py hash it
            "options_hash": laya_common.options_hash(opts), "prompt_hash": laya_common.prompt_hash(),
            "cal_options_hash": cal["options_hash"], "cal_prompt_hash": cal["prompt_hash"],
        }
    except Exception as exc:  # noqa: BLE001 - a bad file means off
        logger.error("laya off: calibration or options file unusable (%s)", type(exc).__name__)
    _cfg.append(cfg)
    return cfg


def mode() -> str:
    """``off`` | ``shadow`` | ``live``. Live needs the exact revision; posterior routing means off."""
    from NessieAI.router import posterior_selector
    cfg = None
    if posterior_selector.posterior_routing_enabled():
        _once("posterior", logging.ERROR, "laya off: posterior routing is enabled")
        md = "off"
    elif (cfg := _load()) is None:
        md = "off"
    else:
        live = os.environ.get(LIVE_ENV, "").strip()
        if live and live == cfg["revision"]:
            md = "live"
        else:
            if live:
                _once("live", logging.ERROR, "laya live refused: %s does not match revision %s", LIVE_ENV,
                      cfg["revision"])
            md = "shadow" if os.environ.get(SHADOW_ENV, "0").strip() == "1" else "off"
    _once("startup", logging.INFO, "laya routing mode=%s revision=%s options_hash=%s prompt_hash=%s", md,
          cfg and cfg["revision"], cfg and cfg["options_hash"], cfg and cfg["prompt_hash"])
    return md


def _non_latin(text: str) -> bool:
    return any(c.isalpha() and "LATIN" not in unicodedata.name(c, "") for c in text)


def _post(body: dict) -> dict:
    """POST /route to the sidecar. Raises; _call classifies. Own socket timeout, bounded by the job's deadline."""
    req = urllib.request.Request(
        SIDECAR_URL + "/route", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get(KEY_ENV, "")})
    with urllib.request.urlopen(req, timeout=DEADLINE_S) as resp:
        return json.loads(resp.read())


def _call(cfg: dict, query: str, history, rec: dict) -> None:
    """Fill ``rec`` from the sidecar's reply and set ``rec['gate']``."""
    from NessieAI.router import laya_common
    state = laya_common.condense(query, history or [])  # decide() defaults history to None; condense takes a list
    try:
        reply = _post({"state": state, "question_id": "route", "prompt": cfg["prompt"], "options": cfg["options"]})
    except (TimeoutError, socket.timeout):
        rec["gate"] = "timeout"
        return
    except urllib.error.HTTPError:
        rec["gate"] = "http_error"
        return
    except urllib.error.URLError as exc:
        rec["gate"] = "timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "http_error"
        return
    except OSError:
        rec["gate"] = "http_error"
        return
    except ValueError:  # json.JSONDecodeError
        rec["gate"] = "bad_json"
        return
    try:
        probs = {k: float(reply["probabilities"][k]) for k in cfg["options"]}
        rec.update(revision=str(reply["revision"]), probabilities=probs,
                   answer_confidence=float(reply["answer_confidence"]),
                   state_tokens=int(reply["state_tokens"]), truncated=bool(reply["truncated"]))
    except (KeyError, TypeError, ValueError):
        rec["gate"] = "bad_json"
        return
    if not (all(math.isfinite(v) and v >= 0 for v in probs.values()) and sum(probs.values()) > 0):
        rec["gate"] = "bad_json"  # NaN, inf, negative or all zero: no ranking to trust
        return
    cal = laya_common.apply_temperature(probs, cfg["temperature"])
    ranked = sorted(cal, key=cal.get, reverse=True)
    rec.update(calibrated=cal, route=ranked[0], calibrated_confidence=cal[ranked[0]],
               margin=cal[ranked[0]] - cal[ranked[1]])
    if rec["revision"] != cfg["revision"]:
        rec["gate"] = "revision_mismatch"
    elif rec["truncated"]:
        rec["gate"] = "truncated"
    elif rec["state_tokens"] > MAX_STATE_TOKENS:
        rec["gate"] = "too_many_tokens"
    elif rec["route"] == "unrelated":
        rec["gate"] = "unrelated"
    elif not rec["calibrated_confidence"] >= cfg["threshold"]:  # a NaN never clears it
        rec["gate"] = "below_threshold"
    else:
        rec["gate"] = "pass"


def _work(cfg: dict, query: str, history, rec: dict) -> None:
    t0 = time.monotonic()
    try:
        if (cfg["options_hash"], cfg["prompt_hash"]) != (cfg["cal_options_hash"], cfg["cal_prompt_hash"]):
            rec["gate"] = "hash_mismatch"
        elif _non_latin(query):
            rec["gate"] = "non_latin"
        else:
            from NessieAI.router import followup
            if followup.followup_mode() == "cc":
                rec["gate"] = "followup_cc"
            else:
                _call(cfg, query, history, rec)
    except Exception as exc:  # noqa: BLE001 - never into the router
        rec["gate"] = "exception"
        rec["error"] = type(exc).__name__
    rec["latency_ms"] = int((time.monotonic() - t0) * 1000)


class Job:
    """One laya call on a helper thread with a hard deadline counted from start."""

    def __init__(self, cfg: dict, md: str, query: str, history):
        self.t0 = time.monotonic()
        self.rec = dict.fromkeys(RECORD_KEYS)
        self.rec.update(mode=md, revision=cfg["revision"], options_hash=cfg["options_hash"],
                        prompt_hash=cfg["prompt_hash"], threshold=cfg["threshold"], gate=None)
        self.thread = threading.Thread(target=_work, args=(cfg, query, history, self.rec), daemon=True)
        self.thread.start()

    def result(self) -> dict:
        """The record, waiting at most what is left of the deadline; a late reply is a timeout."""
        self.thread.join(max(0.0, DEADLINE_S - (time.monotonic() - self.t0)))
        out = {k: self.rec[k] for k in RECORD_KEYS}
        if self.thread.is_alive():
            out.update(gate="timeout", latency_ms=int(DEADLINE_S * 1000))
        return out


def start(query: str, history, md: str) -> Job:
    cfg = _load()
    if cfg is None:
        raise RuntimeError("laya not configured")
    return Job(cfg, md, query, history)
