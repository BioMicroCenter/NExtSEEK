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
  chat_nextseek/e2e/playwright/mysql.py:fetch_chat_session_row(config,
      session_id, env) queries `assistant_chat_session.extra_state` and returns
      ONLY `extra_state.get("chat_log")` (never the top-level cc_traces mirror),
      so this script sums `cost_usd` across `chat_log[*].cc_traces[*]` -- there is
      NO persisted `cost_source` field anywhere on the chat session row (that key
      lives only in the standalone step7 gate harness, e.g.
      scripts/step7_gate3d_per_op.py; it is NOT part of this schema and MUST NOT
      be gated on here). See `_session_cost_usd` below.
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

F2 (cost) -- implemented per the Step-1 pin above: `_session_cost_usd` sums
`cost_usd` across every `chat_log[i]["cc_traces"][j]["cost_usd"]` returned by
`e2e.playwright.mysql.fetch_chat_session_row` for the captured session id, and
returns None (not 0.0) when the session has no chat_log rows, no cc_traces
entries, or no cc_traces entry carrying a non-None cost_usd -- i.e. "no real
cost row" is a hard FAILURE, never silently treated as free.

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


# ── F2: cost readout, summed from real cc_traces DB rows ─────────────────


def _session_cost_usd(config: _OrchestratorConfig, session_id: str | None, *, env: str = "dev") -> float | None:
    """Sum cost_usd across extra_state.chat_log[*].cc_traces[*] for session_id
    (Step-1 pinned source). Returns None — a FAILURE, never 0.0 — when the
    session id is missing, the DB row/chat_log is missing, or no chat_log
    entry carries a cc_traces item with a real (non-None) cost_usd. A
    genuinely-recorded $0.00 cost (cost_usd == 0.0 present in a real cc_traces
    entry) is NOT the same as an absent record and correctly returns 0.0."""
    if not session_id:
        return None
    chat_log = fetch_chat_session_row(config, session_id, env=env)
    total = 0.0
    found = False
    for entry in chat_log or []:
        for trace in (entry.get("cc_traces") or []):
            cost = trace.get("cost_usd")
            if cost is None:
                continue
            found = True
            total += float(cost)
    return round(total, 6) if found else None


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
        out = run_variant_browser(variant, config, q_dir)

        hits = [h for tr in out.get("turn_results", [])
                for h in forbidden_hits(_turn_reply_from_artifacts(q_dir, tr), approval["forbidden_phrases"])]

        session_id = out.get("session_id") or _session_id_from_artifacts(q_dir)
        cost = _session_cost_usd(config, session_id, env=args.db_env)
        total_cost += cost or 0.0

        manifest_sha256 = _write_manifest(q_dir)  # written LAST so it covers every other artifact

        passed = (out.get("status") == "passed" and not hits
                  and session_id is not None and cost is not None)
        results.append({
            "id": q["id"], "passed": passed, "forbidden_hits": hits,
            "session_id": session_id, "cost_usd": cost,
            "failed_criteria": out.get("failed_criteria", []),
            "trace_path": str(q_dir / "trace.zip"),
            "manifest_sha256": manifest_sha256,
        })
        print(("PASS" if passed else "FAIL"), q["id"], f"${cost}")

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

        # 3. Cost: FRESH DB read (external ground truth), cross-checked
        #    against the recorded value.
        cost = _session_cost_usd(config, session_id, env=db_env)
        if cost is None:
            print(f"VALIDATE: {qid}: no real cc_traces cost row for session {session_id}")
            all_ok = False
            continue
        if round(cost, 4) != round(float(recorded.get("cost_usd") if recorded.get("cost_usd") is not None else -1), 4):
            print(f"VALIDATE: {qid}: cost_usd mutated (summary={recorded.get('cost_usd')!r} recomputed={cost!r})")
            all_ok = False
            continue
        total_cost += cost

        # 4. Per-turn: query text unmutated, forbidden-phrase recompute, and a
        #    best-effort DSL criteria recompute against artifact-backed context.
        q_passed = True
        downloaded = {p.name for p in (q_dir / "artifacts").glob("*") if p.is_file()} \
            if (q_dir / "artifacts").is_dir() else set()
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
                browser_ctx=browser_ctx, console_text=console_text,
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
