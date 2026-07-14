#!/usr/bin/env python3
"""Approval-artifact-driven full-UI paid E2E (SPEC-dev-merge-and-e2e-verification.md §7 / Task 12).

Approval artifact schema (frozen before spend; runner FAILS on drift):
{"base_url": "...", "require_non_8000": true, "max_total_usd": 15.0,
 "forbidden_phrases": ["backend is unreachable", "I cannot access", ...],
 "instance_identity": {...} (optional, pass-through, e.g. Task-17 compose/env-file hash),
 "questions": [{"id": "...", "family": "...", "name": "...",
                "turns": [{"label": "main", "query": "...",
                           "pass_criteria": [{"field": "...", "op": "...", "value": ...}]}]}]}

Usage:
    python full_ui_e2e.py --approval <approval.json> --run-dir <dir> [--db-env dev|prod]
    python full_ui_e2e.py --approval <approval.json> --run-dir <dir> --validate-only

============================================================================
Task 12 Step 1 — pinned cost readout (verified against the merged tree, not
guessed; grep -rn "cost_usd" nextseek_api/ chat_nextseek/src | grep -v test):

  nextseek_api/cc_assistant/cc_engine.py:790-799   builds
      result_meta={"num_turns": ..., "duration_ms": ..., "cost_usd": data.get("total_cost_usd")}
      (data.get("total_cost_usd") is the terminal query_complete WS frame's cost
      field) and passes it to cc_trace.extract_trace(...) -> a CCTrace pydantic
      model (chat_nextseek/e2e/playwright/ws.py-adjacent schema; the model's
      `cost_usd: float | None = None` field, chat_nextseek/e2e ... PLAN-3-ui-based-io.md:569/644)
      then `data["cc_traces"] = [trace.model_dump()]`.
  nextseek_api/cc_assistant/cc_turn_complete.py:9-30 (TurnCompletePayload /
      serialize_cc_chat_log_entry) persists that SAME list verbatim as
      `chat_log_entry["cc_traces"]` -- i.e. each chat_log entry carries its own
      turn's cc_traces.
  nextseek_api/cc_assistant/cc_turn_complete.py:47-62 (apply_turn_to_extra_state)
      writes it to BOTH stores in extra_state: (a) nested inside the appended
      `chat_log` entry (`es["chat_log"][-1]["cc_traces"]`) and (b) a flat
      FIFO-capped top-level mirror `es["cc_traces"]` (SPEC-3 E5/§6.5).
  cc_engine.py:792 writes that SAME `data.get("total_cost_usd")` into the
      cc_traces row -- so the cc_traces `cost_usd` and the query_complete result
      frame's `total_cost_usd` are the identical value. This script reads the
      frame value directly (see the F2 note below), NOT via a MySQL cc_traces
      read; the block above is retained only as the origin trace of that value.
      (There is NO persisted `cost_source` field on the chat session row; that
      key lives only in the standalone step7 gate harness and is NOT gated here.)
============================================================================

Task 12 fixes (F2/F3/F4), verified against the real merged-tree shapes below
(NOT the illustrative task-12-brief.md pseudocode, which is explicitly
illustrative/adapt-to-real-shapes):

F4 (import) -- the top-level `e2e` package lives at chat_nextseek/e2e/ (sibling
of chat_nextseek/src/, i.e. NOT part of the installed `chat_nextseek` dist); it
is importable only when chat_nextseek/ is on sys.path. Pinned explicitly below
(2026-07-08 hardening) rather than relying on ambient PYTHONPATH / an editable
install picking it up.

F3 (reply text) -- chat_nextseek/e2e/playwright/runner.py::run_variant_browser
(verified :56-223) returns turn_results entries shaped EXACTLY
{"label", "passed", "elapsed_s", "criteria_results"[, "error"]} -- there is NO
"last_reply" key (confirmed by reading the function body: turn_results.append()
is called at :148-151 on the exception path and :185-188 on the success path;
neither ever adds a reply-text field). Reading tr["last_reply"] would silently
`.get()` to "" and defeat the forbidden-phrase gate. The reply text IS
persisted per turn, however -- `_write_turn_artifacts` (:266-274) writes
`turns/<label>/ui_text.json` = {"latest_assistant_reply": <DOM text>} on every
turn that reaches criteria evaluation, and the WS query_complete payload is
separately dumped verbatim to `turns/<label>/complete.json`, which carries a
top-level "reply" key (used as `console_text` at :164). Turns that raise before
that point write only `turns/<label>/error.txt` (:147) -- no reply artifact
exists for them, and `_turn_reply_from_artifacts` below returns "" in that case
(forbidden_hits("") is always empty, which is correct: an error turn cannot
contain a forbidden UI phrase because no UI text was ever rendered).

F2 (cost) -- SUPERSEDED 2026-07-13. Cost is now read from the EXACT CC result
frame the poll payload already carries -- `query_complete.data.total_cost_usd`
-- captured by run_variant_browser as `cc_cost_usd` and persisted per-turn in
`complete.json`. This is the identical value the server also persists to
`extra_state.cc_traces[*].cost_usd` (translate.py:155 puts the terminal result
frame's `total_cost_usd` on the query_complete event; cc_engine.py:792 writes
that SAME `data.get("total_cost_usd")` into the cc_traces row), so reading the
frame is exactly as precise as a cc_traces DB read -- one hop earlier, with no
MySQL credential / schema-qualification dependency. This matches both prior
live harnesses: step7_per_op_evidence (cost_source="claude_code_result", the
result frame is authoritative) and the off-box poll_e2e.cc_cost. Fail-closed:
every CC-routed turn costs a real Bedrock turn, so a missing or non-positive
`total_cost_usd` is a hard FAILURE, never silently treated as free (the earlier
MySQL `_session_cost_usd` / cc_traces read is removed). NS turns carry no cost.
The Task-12 "Step 1 pinned cost readout" block above is retained as the origin
trace of the SAME value; it no longer describes the runtime cost path.

F4a (session_id) -- run_variant_browser DOES capture the NExtSEEK chat
session_id internally (`captured_session_id`, set at :102-112 from the
`/assistant/query/async` POST response body's "session_id" key) but the
original merged-tree code did NOT return or persist it (the final dict at
:216-223 has no "session_id" key, and no artifact file recorded it either).
Per the brief's explicit instruction ("update run_variant_browser ... with a
regression test"), this task EXTENDS run_variant_browser (in
chat_nextseek/e2e/playwright/runner.py) to (a) return "session_id":
captured_session_id in its result dict and (b) persist it to
`out_dir/session_id.txt` so `--validate-only` can recover it from raw
artifacts alone, without trusting summary.json or a live return value. See
chat_nextseek/tests/test_e2e_playwright_runner.py::
test_run_variant_browser_returns_and_persists_session_id for the regression
test proving both.

Hard invariant (2026-07-09 hardening: "explicit base_url binding ... no
ambient ChatConfig") -- the real chat_nextseek.config.ChatConfig is a heavy,
ambient-env-reading singleton (builds every LLM provider client, opens a PROD
MySQL connection, loads live-DB catalogs, all at __init__ time) whose browser
target the runner would read from `NEXTSEEK_UI_URL`/`NEXTSEEK_BASE_URL`
environment variables -- exactly the kind of implicit binding that can
validate a live/stale instance instead of the one named in the frozen
approval artifact. This script therefore does NOT import or construct
ChatConfig at all (also keeps its own import surface to the e2e package + the
mysql helper + stdlib, per the Task 12 spec, so hermetic tests never need
pydantic/playwright/mysql-connector installed). Instead `_OrchestratorConfig`
below is an explicit, minimal duck-typed stand-in that run_variant_browser's
`_ui_url()`/`_connect_db()` call sites already tolerate via getattr/hasattr,
constructed directly from `approval["base_url"]` (never from NEXTSEEK_* env)
plus the same MYSQL_* environment variables ChatConfig._connect_db reads
(MYSQL_HOST_DEV/MYSQL_HOST_PROD/MYSQL_PORT/MYSQL_USER/MYSQL_DEV_PASSWORD/
MYSQL_PROD_PASSWORD) -- picking the DB host is not an instance-drift risk the
way NEXTSEEK_BASE_URL is, since the approval artifact does not name a DB host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# F4 fix (2026-07-08): the top-level `e2e` package lives at chat_nextseek/e2e/
# (sibling of src/, NOT in the installed chat_nextseek dist); it is importable
# only when chat_nextseek/ is on sys.path (dev's conftest.py does this for its
# own tests). This standalone orchestrator must pin it explicitly:
#   scripts/full_ui_e2e.py -> parents[0]=scripts [1]=cc_assistant
#   [2]=nextseek_api [3]=<repo root> ; <repo root>/chat_nextseek is the package parent.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "chat_nextseek"))

from e2e.catalog import PassCriterion, Turn, Variant  # noqa: E402  (dev e2e package, path-pinned above)
from e2e.criteria import check_pass  # noqa: E402
from e2e.playwright.mysql import fetch_chat_session_row  # noqa: E402  (the mysql helper, per Step 1)
from e2e.playwright.runner import run_variant_browser  # noqa: E402


# ── Explicit config binding (no ambient ChatConfig — see module docstring) ──


class _OrchestratorConfig:
    """Minimal explicit stand-in for chat_nextseek.config.ChatConfig.

    Duck-types exactly what run_variant_browser's helpers read off `config`:
    `NEXTSEEK_UI_URL` (chat_nextseek/e2e/playwright/runner.py:_ui_url, :23-33),
    `API_USER`/`API_PASS` (runner.py:_login_django_session, :41-53), and
    `_connect_db(env=...)` (runner.py:_fetch_with_retry -> mysql.py
    fetch_chat_session_row -> config._connect_db(env=env)). NEXTSEEK_UI_URL is
    bound ONLY from approval["base_url"] -- never read from NEXTSEEK_* env, so
    the browser target can never silently diverge from what was approved.
    """

    def __init__(self, base_url: str) -> None:
        self.NEXTSEEK_UI_URL = base_url
        self.API_USER = os.environ.get("API_USER", "demo")
        self.API_PASS = os.environ.get("API_PASS", "demopassword")
        self.MYSQL_HOST_DEV = os.environ.get("MYSQL_HOST_DEV")
        self.MYSQL_HOST_PROD = os.environ.get("MYSQL_HOST_PROD")
        self.MYSQL_PORT = int(os.environ.get("MYSQL_PORT") or 3306)
        self.MYSQL_USER = os.environ.get("MYSQL_USER")
        self.MYSQL_DEV_PASSWORD = os.environ.get("MYSQL_DEV_PASSWORD")
        self.MYSQL_PROD_PASSWORD = os.environ.get("MYSQL_PROD_PASSWORD")

    def db_identity(self, env: str = "dev") -> dict:
        """Non-secret DB connection identity recorded into run-dir evidence
        (host/port/user only — never the password)."""
        host = self.MYSQL_HOST_DEV if env == "dev" else self.MYSQL_HOST_PROD
        return {"env": env, "host": host, "port": self.MYSQL_PORT, "user": self.MYSQL_USER}

    def _connect_db(self, env: str = "dev"):
        """Mirrors chat_nextseek.config.ChatConfig._connect_db exactly (same
        env vars, same missing `database=` kwarg — a pre-existing upstream
        quirk in ChatConfig itself, not this orchestrator's to fix) so cost /
        trio-match reads hit the same schema the live runner would.
        `mysql.connector` is imported lazily here (not at module scope) so this
        script — and hermetic tests that never call `_connect_db` — stay
        importable without the driver installed."""
        target = (env or "dev").strip().lower()
        host = self.MYSQL_HOST_DEV if target == "dev" else self.MYSQL_HOST_PROD
        password = self.MYSQL_DEV_PASSWORD if target == "dev" else self.MYSQL_PROD_PASSWORD
        if not host or not self.MYSQL_USER or not password:
            print(f"[full_ui_e2e][DB] credentials not configured for env {target!r}")
            return None
        try:
            import mysql.connector  # noqa: PLC0415
        except ImportError:
            print("[full_ui_e2e][DB] mysql-connector-python not installed")
            return None
        try:
            return mysql.connector.connect(
                host=host, port=self.MYSQL_PORT, user=self.MYSQL_USER, password=password,
                charset="utf8mb4", collation="utf8mb4_unicode_ci", use_pure=True,
            )
        except Exception as exc:  # pragma: no cover — live DB path
            print(f"[full_ui_e2e][DB] connection to {target} failed: {exc!r}")
            return None


def _build_config(approval: dict) -> _OrchestratorConfig:
    return _OrchestratorConfig(approval["base_url"])


class _ArtifactChatPage:
    """Read-only DOM shim for `ui_text.*` criteria recompute (--validate-only).

    Only `latest_assistant_reply()` is genuinely recoverable from a persisted
    artifact after the browser has closed; `bubble_count()`/`has_artifact()`/
    `stepper_status()` were live DOM queries with no durable artifact, so they
    resolve to None here — which fails closed (not silently passes) in
    e2e.criteria._check_one for every op that needs a real value.
    """

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def latest_assistant_reply(self) -> str:
        return self._reply

    def bubble_count(self):
        return None

    def has_artifact(self, _name: str):
        return None

    def stepper_status(self, _name: str):
        return None


# ── Approval + forbidden-phrase helpers ──────────────────────────────────


def load_approval(path: Path) -> dict:
    data = json.loads(path.read_text())
    data["_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def forbidden_hits(reply: str, phrases: list[str]) -> list[str]:
    low = (reply or "").lower()
    return [p for p in phrases if p.lower() in low]


# ── F3: reply text from persisted per-turn artifacts ─────────────────────


def _turn_reply_from_artifacts(q_dir: Path, tr: dict) -> str:
    """Read the assistant reply for one turn from the runner's persisted
    per-turn artifacts (turn_results entries carry no reply text — see the F3
    note in the module docstring). Prefers `ui_text.json`'s
    `latest_assistant_reply` (the UI-rendered text actually shown to the
    user — the thing forbidden-phrase checks care about); falls back to
    `complete.json`'s `reply` key (the WS query_complete payload) when the UI
    artifact is absent. Returns "" (never raises) for an errored turn that
    has neither — errored turns write only error.txt."""
    turn_dir = q_dir / "turns" / tr["label"]

    ui_text_path = turn_dir / "ui_text.json"
    if ui_text_path.exists():
        try:
            data = json.loads(ui_text_path.read_text(encoding="utf-8"))
            reply = data.get("latest_assistant_reply")
            if reply:
                return reply
        except (json.JSONDecodeError, OSError):
            pass

    complete_path = turn_dir / "complete.json"
    if complete_path.exists():
        try:
            data = json.loads(complete_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("reply") or ""
        except (json.JSONDecodeError, OSError):
            pass

    return ""


def _session_id_from_artifacts(q_dir: Path) -> str | None:
    """Read the NExtSEEK chat session_id persisted by run_variant_browser's
    F4a extension (`out_dir/session_id.txt`), independent of the live return
    value — this is what --validate-only recomputes from."""
    p = q_dir / "session_id.txt"
    if p.exists():
        sid = p.read_text(encoding="utf-8").strip()
        return sid or None
    return None


# ── Route awareness (2026-07-13): the merged chat UI routes each turn via the
# BAML router (nextseek_api/cc_assistant/router.py) to either the deterministic
# NS pipeline or the Container-CC agent. They persist DIFFERENT shapes and cost
# is persisted ONLY on the CC route:
#   - NS  (pipeline_adapter): query_complete.data has `debug` (+ `files`); the NS
#         branch of services/cc_assistant.py runs run_query() with no
#         cc_engine/on_turn_complete, so NO cc_traces / cost row exists.
#   - CC  (cc_engine.run_cc_turn): query_complete.data has `total_cost_usd` +
#         `cc_session_id` (no `debug`); cost is persisted to extra_state.cc_traces.
# So cost is gated on the CC route only; NS questions pass on criteria + session
# + no-forbidden without a cost row. `route` is inlined here (not imported from
# e2e.playwright.poll) to keep this orchestrator's hermetic-test import surface
# to e2e.catalog/criteria/mysql/runner + stdlib.


def _detect_route_from_data(data: dict) -> str:
    """Classify a persisted query_complete.data dict: `ns` | `cc` | `unknown`."""
    if not isinstance(data, dict):
        return "unknown"
    if "debug" in data:
        return "ns"
    if "total_cost_usd" in data or "cc_session_id" in data:
        return "cc"
    return "unknown"


def _route_from_artifacts(q_dir: Path) -> str:
    """Recompute a question's route from its persisted per-turn complete.json
    (query_complete.data). Returns the last non-unknown turn's route, matching
    run_variant_browser's `variant_route`."""
    turns_dir = q_dir / "turns"
    if not turns_dir.is_dir():
        return "unknown"
    route = "unknown"
    for complete_path in sorted(turns_dir.glob("*/complete.json")):
        try:
            data = json.loads(complete_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        r = _detect_route_from_data(data)
        if r != "unknown":
            route = r
    return route


def _cc_frame_cost_from_artifacts(q_dir: Path) -> float | None:
    """Sum the CC result frame's total_cost_usd across a question's persisted
    per-turn complete.json (query_complete.data). This frame value IS the exact
    per-turn cost (the identical number the server also persists to
    extra_state.cc_traces — translate.py:155 / cc_engine.py:792), so it is the
    authoritative cost source for --validate-only; no DB round-trip. The files
    are manifest-covered, so a tampered cost is still caught by the run-dir
    integrity check."""
    turns_dir = q_dir / "turns"
    if not turns_dir.is_dir():
        return None
    total = 0.0
    found = False
    for complete_path in sorted(turns_dir.glob("*/complete.json")):
        try:
            data = json.loads(complete_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        v = data.get("total_cost_usd") if isinstance(data, dict) else None
        if isinstance(v, (int, float)):
            found = True
            total += float(v)
    return round(total, 6) if found else None


def _downloaded_keys_from_artifacts(q_dir: Path) -> set[str]:
    """Reconstruct the set of downloaded artifact identifiers persisted by
    run_variant_browser (`out_dir/downloaded_keys.json`) so a --validate-only
    replay resolves ui_text.artifacts.<id>.downloaded from disk alone."""
    p = q_dir / "downloaded_keys.json"
    if p.exists():
        try:
            keys = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(keys, list):
                return {str(k) for k in keys}
        except (json.JSONDecodeError, OSError):
            pass
    return set()


# ── Artifact integrity manifest (used by --validate-only) ────────────────


def _artifact_manifest(q_dir: Path) -> dict[str, str]:
    """sha256 of every file under q_dir (excluding the manifest itself),
    keyed by path relative to q_dir. This is the raw-evidence integrity
    anchor recompute uses to detect any post-run file mutation, addition, or
    deletion (answer text, trace, session id, mysql dump, downloaded
    artifact, ...)."""
    manifest: dict[str, str] = {}
    if not q_dir.is_dir():
        return manifest
    for p in sorted(q_dir.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            manifest[str(p.relative_to(q_dir))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest


def _write_manifest(q_dir: Path) -> str:
    manifest_path = q_dir / "manifest.json"
    manifest_path.write_text(json.dumps(_artifact_manifest(q_dir), indent=2, sort_keys=True), encoding="utf-8")
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


# ── Main (live run) ───────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approval", required=True, type=Path)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--db-env", default="dev", choices=("dev", "prod"))
    args = ap.parse_args()
    approval = load_approval(args.approval)
    run_dir = args.run_dir
    summary_path = run_dir / "summary.json"

    if args.validate_only:  # re-evaluate a finished run's artifacts, never trust summary alone
        ok = recompute_run_dir(run_dir, approval, db_env=args.db_env)
        print("VALIDATE:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    port = urlparse(approval["base_url"]).port or 80
    if approval.get("require_non_8000") and port == 8000:
        print("FATAL: require_non_8000 but target port is 8000")
        return 1

    run_dir.mkdir(parents=True, exist_ok=True)
    config = _build_config(approval)  # explicit base_url binding — no ambient ChatConfig
    db_identity = config.db_identity(args.db_env)
    identity = {
        "base_url": approval["base_url"],
        "db_identity": db_identity,
        "instance_identity": approval.get("instance_identity"),
    }
    identity_path = run_dir / "identity.json"
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True), encoding="utf-8")
    identity_sha256 = hashlib.sha256(identity_path.read_bytes()).hexdigest()

    results: list[dict] = []
    total_cost = 0.0
    for q in approval["questions"]:
        variant = Variant(family=q["family"], id=q["id"], name=q["name"], turns=[
            Turn(label=t["label"], query=t["query"],
                 pass_criteria=[PassCriterion(**c) for c in t.get("pass_criteria", [])])
            for t in q["turns"]
        ])
        q_dir = run_dir / q["id"]
        declared_route = q.get("route")  # optional expected route ("ns"|"cc")
        out = run_variant_browser(variant, config, q_dir)

        hits = [h for tr in out.get("turn_results", [])
                for h in forbidden_hits(_turn_reply_from_artifacts(q_dir, tr), approval["forbidden_phrases"])]

        session_id = out.get("session_id") or _session_id_from_artifacts(q_dir)
        route = out.get("route") or _route_from_artifacts(q_dir)

        # A question that declares an expected route but returns the other shape
        # is itself a routing regression this E2E exists to catch.
        route_mismatch = bool(declared_route) and route != "unknown" and route != declared_route

        # Cost is the EXACT CC result-frame total_cost_usd the runner captured
        # from the poll payload (== the value the server persists to
        # extra_state.cc_traces; translate.py:155 / cc_engine.py:792). No MySQL
        # round-trip. It exists ONLY on the CC route; NS carries no cost.
        cost = out.get("cc_cost_usd")
        cost_required = route == "cc"
        total_cost += cost or 0.0

        manifest_sha256 = _write_manifest(q_dir)  # written LAST so it covers every other artifact

        # Fail-closed CC cost gate: every CC-routed turn costs a real Bedrock
        # turn, so a missing or non-positive cost means the true spend was not
        # captured (matching step7_per_op_evidence cost_usd > 0).
        cost_ok = (cost is not None and cost > 0) if cost_required else True
        passed = (out.get("status") == "passed" and not hits
                  and session_id is not None
                  and cost_ok
                  and not route_mismatch)
        results.append({
            "id": q["id"], "passed": passed, "forbidden_hits": hits,
            "session_id": session_id, "route": route,
            "declared_route": declared_route, "route_mismatch": route_mismatch,
            "cost_usd": cost,
            "failed_criteria": out.get("failed_criteria", []),
            "trace_path": str(q_dir / "trace.zip"),
            "manifest_sha256": manifest_sha256,
        })
        print(("PASS" if passed else "FAIL"), q["id"], f"[{route}]", f"${cost}")

    all_passed = all(r["passed"] for r in results) and total_cost <= approval["max_total_usd"]
    summary_path.write_text(json.dumps({
        "approval_sha256": approval["_sha256"],
        "base_url": approval["base_url"],
        "runner_module": getattr(run_variant_browser, "__module__", ""),
        "db_identity": db_identity,
        "instance_identity": approval.get("instance_identity"),
        "identity_sha256": identity_sha256,
        "all_passed": all_passed,
        "total_cost_usd": round(total_cost, 4),
        "results": results,
    }, indent=2, sort_keys=True), encoding="utf-8")
    print(f"TOTAL ${total_cost:.4f} / cap ${approval['max_total_usd']}  ->",
          "PASS" if all_passed else "FAIL")
    return 0 if all_passed else 1


# ── --validate-only: recompute everything from raw artifacts + DB rows ───


def recompute_run_dir(run_dir: Path, approval: dict, *, db_env: str = "dev") -> bool:
    """Re-derive PASS/FAIL, forbidden-phrase hits, session ids, and cost from
    RAW artifacts + a FRESH DB read — never trusts summary.json's own
    verdicts, only uses it as a claim to be cross-checked against
    independently recomputed values. Fails closed: any missing/renamed
    artifact, drifted approval hash, mismatched identity, or mutated saved
    value fails the whole run.

    Local-file manifest hashing (see `_artifact_manifest`) catches ordinary
    post-run tampering of any single tracked file. The session_id and cost
    checks are additionally anchored to a FRESH MySQL read (external ground
    truth outside this run_dir) rather than only to file hashes, since a
    local-only hash chain can in principle be regenerated in lockstep by
    whoever mutated the file it protects — the DB row cannot.
    """
    summary_path = run_dir / "summary.json"
    identity_path = run_dir / "identity.json"
    if not summary_path.exists() or not identity_path.exists():
        print("VALIDATE: missing summary.json or identity.json")
        return False
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"VALIDATE: unreadable JSON artifact: {exc}")
        return False

    if summary.get("approval_sha256") != approval["_sha256"]:
        print("VALIDATE: approval sha256 drift")
        return False

    if identity.get("base_url") != approval["base_url"]:
        print("VALIDATE: base_url identity drift (actual browser URL != approval)")
        return False
    if (approval.get("instance_identity") is not None
            and identity.get("instance_identity") != approval.get("instance_identity")):
        print("VALIDATE: instance-2 identity drift")
        return False
    recomputed_identity_sha = hashlib.sha256(identity_path.read_bytes()).hexdigest()
    if summary.get("identity_sha256") != recomputed_identity_sha:
        print("VALIDATE: identity.json mutated after run (sha256 mismatch)")
        return False
    if summary.get("db_identity") != identity.get("db_identity"):
        print("VALIDATE: DB identity drift between summary.json and identity.json")
        return False

    port = urlparse(approval["base_url"]).port or 80
    if approval.get("require_non_8000") and port == 8000:
        print("VALIDATE: require_non_8000 but approval base_url is port 8000")
        return False

    config = _build_config(approval)
    summary_by_id = {r["id"]: r for r in summary.get("results", [])}
    total_cost = 0.0
    all_ok = True

    for q in approval["questions"]:
        qid = q["id"]
        q_dir = run_dir / qid
        recorded = summary_by_id.get(qid)
        if recorded is None:
            print(f"VALIDATE: {qid}: missing from summary.json")
            all_ok = False
            continue

        # 1. Local artifact-integrity check: every hash in the persisted
        #    manifest must still match the file on disk, and no tracked file
        #    may have been added/removed.
        manifest_path = q_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"VALIDATE: {qid}: missing manifest.json")
            all_ok = False
            continue
        recomputed_manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if recorded.get("manifest_sha256") != recomputed_manifest_sha:
            print(f"VALIDATE: {qid}: manifest.json mutated after run (sha256 mismatch)")
            all_ok = False
            continue
        try:
            persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"VALIDATE: {qid}: manifest.json unreadable")
            all_ok = False
            continue
        live_manifest = _artifact_manifest(q_dir)
        if live_manifest != persisted_manifest:
            print(f"VALIDATE: {qid}: tracked artifact mutated/added/removed after run")
            all_ok = False
            continue

        trace_path = q_dir / "trace.zip"
        if not trace_path.exists() or trace_path.stat().st_size == 0:
            print(f"VALIDATE: {qid}: missing/empty trace.zip")
            all_ok = False
            continue

        # 2. Session id: recomputed from the raw artifact, cross-checked
        #    against the recorded value (not just internally self-consistent).
        session_id = _session_id_from_artifacts(q_dir)
        if not session_id:
            print(f"VALIDATE: {qid}: no session_id.txt artifact")
            all_ok = False
            continue
        if session_id != recorded.get("session_id"):
            print(f"VALIDATE: {qid}: session_id mutated "
                  f"(summary={recorded.get('session_id')!r} artifact={session_id!r})")
            all_ok = False
            continue

        # 3. Route: recomputed from persisted complete.json, cross-checked
        #    against the recorded route + the (optional) declared route.
        route = _route_from_artifacts(q_dir)
        if recorded.get("route") is not None and route != recorded.get("route"):
            print(f"VALIDATE: {qid}: route mutated "
                  f"(summary={recorded.get('route')!r} artifact={route!r})")
            all_ok = False
            continue
        declared_route = q.get("route")
        route_mismatch = bool(declared_route) and route != "unknown" and route != declared_route
        if route_mismatch != bool(recorded.get("route_mismatch")):
            print(f"VALIDATE: {qid}: route_mismatch drift "
                  f"(summary={recorded.get('route_mismatch')!r} recomputed={route_mismatch!r})")
            all_ok = False
            continue

        # 4. Cost: gated on the CC route only (NS persists none). CC cost is the
        #    EXACT result-frame total_cost_usd recomputed from the persisted
        #    complete.json (manifest-hash-covered, so a tampered cost breaks the
        #    integrity check) — no DB round-trip. Fail-closed: a CC turn always
        #    costs a real Bedrock turn, so a missing/non-positive cost fails. NS:
        #    no cost row required, and the recorded cost must not claim a spend
        #    that never existed.
        if route == "cc":
            cost = _cc_frame_cost_from_artifacts(q_dir)
            if cost is None or cost <= 0:
                print(f"VALIDATE: {qid}: no positive CC result-frame cost for session {session_id}")
                all_ok = False
                continue
            if round(cost, 4) != round(float(recorded.get("cost_usd") if recorded.get("cost_usd") is not None else -1), 4):
                print(f"VALIDATE: {qid}: cost_usd mutated (summary={recorded.get('cost_usd')!r} recomputed={cost!r})")
                all_ok = False
                continue
            total_cost += cost
        else:
            if recorded.get("cost_usd"):
                print(f"VALIDATE: {qid}: NS route recorded a nonzero cost_usd={recorded.get('cost_usd')!r}")
                all_ok = False
                continue

        # 5. Per-turn: query text unmutated, forbidden-phrase recompute, and a
        #    best-effort DSL criteria recompute against artifact-backed context.
        q_passed = not route_mismatch
        downloaded = _downloaded_keys_from_artifacts(q_dir)
        mysql_log = fetch_chat_session_row(config, session_id, env=db_env)

        for turn in q["turns"]:
            label = turn["label"]
            turn_dir = q_dir / "turns" / label
            query_path = turn_dir / "query.txt"
            if not query_path.exists():
                print(f"VALIDATE: {qid}/{label}: missing query.txt")
                q_passed = False
                continue
            if query_path.read_text(encoding="utf-8") != turn["query"]:
                print(f"VALIDATE: {qid}/{label}: query.txt mutated")
                q_passed = False
                continue

            error_path = turn_dir / "error.txt"
            complete_path = turn_dir / "complete.json"
            if not error_path.exists() and not complete_path.exists():
                print(f"VALIDATE: {qid}/{label}: neither complete.json nor error.txt present")
                q_passed = False
                continue
            if error_path.exists():
                q_passed = False  # an errored live turn can never recompute to "passed"
                continue

            reply = _turn_reply_from_artifacts(q_dir, {"label": label})
            turn_hits = forbidden_hits(reply, approval["forbidden_phrases"])
            if turn_hits:
                q_passed = False

            debug: dict = {}
            console_text = reply
            try:
                payload = json.loads(complete_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    debug = payload.get("debug") or {}
                    console_text = payload.get("reply") or reply
            except json.JSONDecodeError:
                print(f"VALIDATE: {qid}/{label}: complete.json unreadable")
                q_passed = False
                continue

            browser_ctx = {"chat_page": _ArtifactChatPage(reply), "downloaded_artifacts": downloaded}
            turn_passed, _crit_results = check_pass(
                debug, [PassCriterion(**c) for c in turn.get("pass_criteria", [])],
                last_reply=console_text, browser_ctx=browser_ctx, console_text=console_text,
                mysql_chat_log=mysql_log if mysql_log else None, run_root=q_dir,
            )
            if not turn_passed:
                q_passed = False

        recorded_passed = bool(recorded.get("passed"))
        if q_passed != recorded_passed:
            print(f"VALIDATE: {qid}: pass/fail drift (summary={recorded_passed} recomputed={q_passed})")
            all_ok = False
        elif not q_passed:
            all_ok = False

    if round(total_cost, 4) != round(float(summary.get("total_cost_usd") or 0.0), 4):
        print(f"VALIDATE: total_cost_usd drift (summary={summary.get('total_cost_usd')!r} "
              f"recomputed={round(total_cost, 4)!r})")
        all_ok = False
    if total_cost > approval["max_total_usd"]:
        print("VALIDATE: recomputed total cost exceeds cap")
        all_ok = False

    if bool(summary.get("all_passed")) != all_ok:
        print(f"VALIDATE: all_passed drift (summary={summary.get('all_passed')!r} recomputed={all_ok!r})")
        all_ok = False

    return all_ok


if __name__ == "__main__":
    sys.exit(main())
