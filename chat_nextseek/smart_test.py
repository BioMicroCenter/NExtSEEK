"""
Smart Test Suite for chat_nextseek.

Runs diagnostic questions covering every routing path.
If a top-level test fails, 3 targeted debug sub-queries fire automatically.

A 30-second pause is inserted before every query (setup, main, debug)
to avoid LLM rate limits.

Output layout:

  outputs/smart_test_YYMMDD_HHMMSS_<mode>_<pipeline>/
    console.txt          combined stdout
    summary.txt          human-readable scorecard
    summary.json         machine-readable results
    report.html          self-contained per-run HTML report
    T1/
      query.txt
      reply.txt
      debug.json
      <YYMMDD_HHMMSS_user>/   orchestrator run dir (moved here after the query)
    T1_D1/               (only if T1 failed)
      ...

  outputs/combined_<ts_std>_<ts_plan>.html   side-by-side standard vs planner report
                                              (auto-generated after --both finishes)

Test definitions and pass criteria live in testing.json (repo root).

Pass criteria fields:
  run_query mode   — parser_plan.mode, entity_sampletype_codes, api_plan.endpoint, …
  run_query_plan   — plan_first_candidate_mode, plan_first_step_tool, plan_entity_sampletype_codes, …

Running tests:
  uv run smart_test.py                     # all tests, default mixed profile
  uv run smart_test.py -m gcp             # GCP profile
  uv run smart_test.py -p                 # planner pipeline (run_query_plan) for all tests
  uv run smart_test.py -m aws:opus -p     # Opus planner
  uv run smart_test.py --only T1,T5,T9   # subset of tests by ID
  uv run smart_test.py --both            # standard then planner (3-min pause), auto combined report
  uv run smart_test.py --list             # list tests and exit

Regenerating reports:
  uv run smart_test.py -i                          # interactive picker — choose dir(s) from outputs/
  uv run smart_test.py --report <dir>              # regenerate report.html for one run
  uv run smart_test.py --report-combined <a> <b>   # regenerate combined report from two runs
"""

from __future__ import annotations

import argparse
import html as _html_lib
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ground truth loading
# ---------------------------------------------------------------------------

_GT_PATH = Path(__file__).parent / "testing.json"


def load_tests(only: list[str] | None = None, full_test: bool = False) -> list[dict]:
    """Load tests from testing.json. smart_test now contains T1-T21
    (T1-T13 routing/correctness, T14-T17 wizard, T18-T21 memory). Multi-turn
    tests (those with `turns`) automatically get session_mode='shared';
    everything else defaults to 'fresh'."""
    with open(_GT_PATH) as fh:
        data = json.load(fh)
    section = "full_test" if full_test else "smart_test"
    tests = data[section]["tests"]
    if not full_test:
        for t in tests:
            t.setdefault("session_mode", "shared" if t.get("turns") else "fresh")
    if full_test:
        for t in tests:
            t.setdefault("name", t.get("notes", t["id"]))
            t.setdefault("pass_criteria", [])
            t.setdefault("planner_pass_criteria", [])
            t.setdefault("debug_queries", [])
            t.setdefault("setup_queries", [])
    if only:
        tests = [t for t in tests if t["id"] in only]
    return tests


# ---------------------------------------------------------------------------
# Pass-criteria evaluation
# ---------------------------------------------------------------------------

def _resolve_field(debug: dict, field: str) -> Any:
    """
    Resolve a field from the run_query or run_query_plan debug dict.

    Exact aliases  (run_query):
      entity_sampletype_codes    list of code strings from entity_result.sampletypes
      entity_assay_codes         list of code strings from entity_result.assays
      entity_projects            list of project strings from entity_result.projects
      graph_cypher               graph_plan.cypher
      neo4j_ok                   graph_result.ok
      api_ok                     api_result_meta.ok

    Exact aliases  (run_query_plan):
      plan_entity_sampletype_codes    entity.sampletypes[].code
      plan_entity_assay_codes         entity.assays[].code
      plan_entity_projects            entity.projects
      plan_first_candidate_mode       parser.candidates[0].mode
      plan_first_candidate_report_type parser.candidates[0].report_type
      plan_first_step_tool            planner.steps[0].tool
      plan_step1_ok                   step_results[1].ok  (int or str key)

    Prefix aliases — can be extended with dot-navigation:
      api_plan.requestBody.*            run_query  — e.g. api_plan.requestBody.sample_type
      plan_step1_api_plan.*             run_query_plan — e.g. plan_step1_api_plan.requestBody.sample_type
                                        (requires orchestrator to surface api_plan in step_results)

    Dot-notation fallback:
      Any other field navigated as nested dict keys, e.g. parser_plan.mode
    """
    def _codes(items: list[dict], key: str = "code") -> list:
        return [item.get(key) for item in items if item.get(key)]

    def _first_candidate(d: dict) -> dict:
        cands = (d.get("parser") or {}).get("candidates") or []
        return cands[0] if cands else {}

    def _first_step(d: dict) -> dict:
        steps = (d.get("planner") or {}).get("steps") or []
        return steps[0] if steps else {}

    def _step1_result(d: dict) -> dict:
        sr = d.get("step_results") or {}
        return sr.get(1) or sr.get("1") or {}

    def _get_with_aliases(val: Any, part: str) -> Any:
        if not isinstance(val, dict):
            return None
        if part in val:
            return val.get(part)
        aliases = {
            "sample_type": ("sampletype",),
            "sampletype": ("sample_type",),
        }
        for alt in aliases.get(part, ()):
            if alt in val:
                return val.get(alt)
        return None

    # ── Exact aliases ────────────────────────────────────────────────────
    _ALIASES: dict[str, Any] = {
        # run_query
        "entity_sampletype_codes": lambda d: _codes(
            (d.get("entity_result") or {}).get("sampletypes", [])
        ),
        "entity_assay_codes": lambda d: _codes(
            (d.get("entity_result") or {}).get("assays", [])
        ),
        "entity_projects": lambda d: (d.get("entity_result") or {}).get("projects", []),
        "graph_cypher": lambda d: (d.get("graph_plan") or {}).get("cypher") or "",
        "neo4j_ok":     lambda d: (d.get("graph_result") or {}).get("ok", False),
        "api_ok":       lambda d: (d.get("api_result_meta") or {}).get("ok", False),
        # run_query_plan
        "plan_entity_sampletype_codes": lambda d: _codes(
            (d.get("entity") or {}).get("sampletypes", [])
        ),
        "plan_entity_assay_codes": lambda d: _codes(
            (d.get("entity") or {}).get("assays", [])
        ),
        "plan_entity_projects": lambda d: (d.get("entity") or {}).get("projects", []),
        "plan_first_candidate_mode":        lambda d: _first_candidate(d).get("mode"),
        "plan_first_candidate_report_type": lambda d: _first_candidate(d).get("report_type"),
        "plan_first_step_tool":             lambda d: _first_step(d).get("tool"),
        "plan_step1_ok":                    lambda d: _step1_result(d).get("ok", False),
    }

    if field in _ALIASES:
        return _ALIASES[field](debug)

    # ── Prefix aliases (alias + optional dot-path continuation) ──────────
    # Each entry: prefix → resolver that returns the base dict/value.
    # If the field is "prefix.a.b.c", we resolve the prefix then navigate a.b.c.
    _PREFIX_ALIASES: dict[str, Any] = {
        # run_query_plan: step_results[1]["api_plan"] (surfaced by orchestrator for new_search steps)
        "plan_step1_api_plan": lambda d: _step1_result(d).get("api_plan") or {},
    }

    for prefix, resolver in _PREFIX_ALIASES.items():
        if field == prefix or field.startswith(prefix + "."):
            base = resolver(debug)
            remainder = field[len(prefix) + 1:] if field != prefix else ""
            if not remainder:
                return base
            val: Any = base
            for part in remainder.split("."):
                val = _get_with_aliases(val, part)
                if val is None:
                    return None
            return val

    # ── Dot-notation fallback ────────────────────────────────────────────
    parts = field.split(".")
    val = debug
    for part in parts:
        val = _get_with_aliases(val, part)
        if val is None:
            return None
    return val


def _strip_html_for_compare(text: Any) -> str:
    """Strip HTML tags + normalize whitespace, for `mentions` / `matches_re` ops
    on `last_reply` (which has post-processed markdown but may still carry stray
    HTML)."""
    if text is None:
        return ""
    import re as _re

    s = _re.sub(r"<[^>]+>", "", str(text))
    return s


def _resolve_field_v2(
    debug: dict,
    field: str,
    *,
    session: Any = None,
    last_reply: str | None = None,
) -> Any:
    """Resolve fields used by smart_test_v2 — wizard state, chat_log, session-state
    accessors, the most recent assistant reply, etc. Falls through to the legacy
    `_resolve_field` for everything else (preserves T1-T13 compat)."""

    # ── wizard.* (current session_state[nfcore_wizard]) ──────────────────────
    if field.startswith("wizard."):
        if session is None:
            return None
        wz = session.get("nfcore_wizard") or {}
        sub = field[len("wizard."):]
        if sub == "active":
            return bool(wz.get("active"))
        if sub == "step":
            return wz.get("step")
        if sub == "pipeline":
            return wz.get("pipeline")
        if sub == "uid_count":
            return len(((wz.get("selection") or {}).get("uids")) or [])
        if sub == "cohort_count":
            cc = (wz.get("selection") or {}).get("cohort_criteria")
            if cc is None:
                # If wizard hasn't reached the cohorts step, treat as 1 (single-cohort default)
                return 1
            return len(cc) or 1
        if sub == "enrichment_count":
            return len(((wz.get("selection") or {}).get("enrichment_fields")) or [])
        if sub == "metadata_summary_present":
            return (wz.get("pinned_context") or {}).get("metadata_summary") is not None
        # generic dot-navigation fallback within wizard state
        val: Any = wz
        for part in sub.split("."):
            if not isinstance(val, dict):
                return None
            val = val.get(part)
        return val

    # ── wizard_agent.last_action / last_extracted_keys ────────────────────────
    if field == "wizard_agent.last_action":
        return session.get("nfcore_wizard_last_action") if session is not None else None
    if field == "wizard_agent.last_extracted_keys":
        return session.get("nfcore_wizard_last_extracted_keys") if session is not None else None

    # ── chat_log.* ───────────────────────────────────────────────────────────
    if field.startswith("chat_log."):
        if session is None:
            return None
        log = session.get("chat_log") or []
        sub = field[len("chat_log."):]
        if sub == "length":
            return len(log)
        if sub == "latest_mode":
            return log[-1].get("mode") if log else None
        if sub == "latest_bundle_id":
            return log[-1].get("bundle_id") if log else None
        # fallback: dot-nav on the most recent turn
        if log:
            val = log[-1]
            for part in sub.split("."):
                if not isinstance(val, dict):
                    return None
                val = val.get(part)
            return val
        return None

    # ── results_history.length ────────────────────────────────────────────────
    if field == "results_history.length":
        if session is None:
            return None
        return len(session.get("results_history") or [])

    # ── last_reply (string, with HTML stripped for mentions/regex matching) ──
    if field == "last_reply":
        return _strip_html_for_compare(last_reply)

    # ── last_target_result_id (from parser_plan on the most recent turn) ─────
    if field == "last_target_result_id":
        # Standard pipeline puts it in parser_plan; planner puts it in parser candidates.
        pp = debug.get("parser_plan") or {}
        if "target_result_id" in pp:
            return pp.get("target_result_id")
        # planner candidate metadata
        cands = (debug.get("parser") or {}).get("candidates") or []
        if cands:
            return (cands[0].get("metadata") or {}).get("target_result_id")
        return None

    # ── Everything else: legacy resolver ──────────────────────────────────────
    return _resolve_field(debug, field)


def _check_criterion(debug: dict, crit: dict, *, session: Any = None, last_reply: str | None = None) -> tuple[bool, str]:
    field = crit["field"]
    op = crit["op"]
    expected = crit.get("value")
    # Use v2 resolver if v2-specific fields are present OR if session/last_reply were passed.
    if session is not None or last_reply is not None:
        actual = _resolve_field_v2(debug, field, session=session, last_reply=last_reply)
    else:
        actual = _resolve_field(debug, field)

    if op == "eq":
        passed = actual == expected
        reason = (
            f"{field} == {actual!r}"
            if passed
            else f"{field}: expected {expected!r}, got {actual!r}"
        )
    elif op == "contains":
        if isinstance(actual, list):
            passed = expected in actual
        elif isinstance(actual, str):
            passed = bool(expected) and expected in actual
        else:
            passed = False
        reason = (
            f"{field} contains {expected!r}"
            if passed
            else f"{field} ({actual!r}) does not contain {expected!r}"
        )
    elif op == "nonempty":
        passed = bool(actual)
        reason = (
            f"{field} is non-empty"
            if passed
            else f"{field} is empty/None (got {actual!r})"
        )
    elif op == "true":
        passed = actual is True
        reason = (
            f"{field} is True"
            if passed
            else f"{field} is {actual!r} (expected True)"
        )
    elif op == "gte":
        try:
            passed = int(actual) >= int(expected)
            reason = (
                f"{field}={actual} >= {expected}"
                if passed
                else f"{field}={actual!r} not >= {expected!r}"
            )
        except (TypeError, ValueError):
            passed = False
            reason = f"{field}={actual!r} not numeric for >= {expected!r}"
    elif op == "lte":
        try:
            passed = int(actual) <= int(expected)
            reason = (
                f"{field}={actual} <= {expected}"
                if passed
                else f"{field}={actual!r} not <= {expected!r}"
            )
        except (TypeError, ValueError):
            passed = False
            reason = f"{field}={actual!r} not numeric for <= {expected!r}"
    elif op == "mentions":
        # Case-insensitive substring of `expected` inside the `actual` string.
        if isinstance(actual, str) and isinstance(expected, str):
            passed = expected.lower() in actual.lower()
        else:
            passed = False
        reason = (
            f"{field} mentions {expected!r}"
            if passed
            else f"{field} does not mention {expected!r} (got: {str(actual)[:120]!r}…)"
        )
    elif op == "matches_re":
        import re as _re
        try:
            passed = bool(_re.search(str(expected), str(actual), flags=_re.IGNORECASE))
        except _re.error as e:
            passed = False
            reason = f"{field}: invalid regex {expected!r}: {e}"
        else:
            reason = (
                f"{field} matches /{expected}/"
                if passed
                else f"{field} ({str(actual)[:120]!r}) does not match /{expected}/"
            )
    else:
        passed = False
        reason = f"unknown op {op!r}"

    return passed, reason


def check_pass(
    debug: dict,
    criteria: list[dict],
    *,
    session: Any = None,
    last_reply: str | None = None,
) -> tuple[bool, list[dict]]:
    results = []
    for crit in criteria:
        # Skip comment-only entries (used in testing.json to annotate criteria
        # without adding an actual assertion). A valid criterion must have both
        # `field` and `op`.
        if not isinstance(crit, dict) or "field" not in crit or "op" not in crit:
            continue
        passed, reason = _check_criterion(debug, crit, session=session, last_reply=last_reply)
        results.append({**crit, "passed": passed, "reason": reason})
    return all(r["passed"] for r in results), results


def _pick_criteria(test_or_dq: dict, use_planner: bool) -> list[dict]:
    """Return planner_pass_criteria if use_planner and present, else pass_criteria."""
    if use_planner and test_or_dq.get("planner_pass_criteria"):
        return test_or_dq["planner_pass_criteria"]
    return test_or_dq.get("pass_criteria", [])


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _make_session(config, user_id: str):
    from chat_nextseek.session import SQLiteSessionState, MySQLSessionState

    if config.SESSION_DB_TYPE == "sqlite":
        return SQLiteSessionState(config.SESSION_DB_PATH, user_id)
    if config.SESSION_DB_TYPE == "mysql":
        return MySQLSessionState(
            {
                "user": config.SESSION_DB_USER,
                "password": config.SESSION_DB_PASSWORD,
                "host": config.SESSION_DB_HOST,
                "database": config.SESSION_DB_NAME,
                "port": config.SESSION_DB_PORT,
            },
            user_id,
        )
    raise RuntimeError(f"Unknown SESSION_DB_TYPE: {config.SESSION_DB_TYPE!r}")


def _reset_logging(session) -> None:
    for key in (
        "run_root_dir", "log_dir", "console_log_path",
        "chat_log_path", "api_log_path", "prompts_log_path",
    ):
        session[key] = None
    session["config_snapshot_logged"] = False


# ---------------------------------------------------------------------------
# Query runner with pace throttle
# ---------------------------------------------------------------------------

_query_count = 0  # module-level counter so first query skips the sleep


def _run_query(session, config, query: str, pipeline: str, tee_write) -> dict:
    """Run one query, sleeping 30 s before it (except the very first call)."""
    global _query_count
    if _query_count > 0:
        tee_write(f"\n  [pace] Waiting 30 s before next query…")
        time.sleep(30)
    _query_count += 1

    from chat_nextseek.orchestrator import run_query, run_query_plan  # noqa: PLC0415

    _reset_logging(session)
    stdout_before = sys.stdout
    stderr_before = sys.stderr
    try:
        if pipeline == "run_query_plan":
            return run_query_plan(session, config, query)
        return run_query(session, config, query)
    finally:
        stdout_after = sys.stdout
        stderr_after = sys.stderr
        sys.stdout = stdout_before
        sys.stderr = stderr_before
        if stdout_after is not stdout_before and hasattr(stdout_after, "close"):
            stdout_after.close()
        if stderr_after is not stderr_before and stderr_after is not stdout_after and hasattr(stderr_after, "close"):
            stderr_after.close()


# ---------------------------------------------------------------------------
# Run-dir mover
# ---------------------------------------------------------------------------

def _move_run_dir(session, dest_dir: Path) -> None:
    """Move the orchestrator's per-run output dir into dest_dir (idempotent)."""
    run_root = session.get("run_root_dir")
    if not run_root:
        return
    src = Path(run_root)
    if not src.exists():
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest_dir / src.name))


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------

def _write_test_artifacts(test_out_dir: Path, query: str, result: dict) -> None:
    test_out_dir.mkdir(parents=True, exist_ok=True)
    (test_out_dir / "query.txt").write_text(query, encoding="utf-8")
    (test_out_dir / "reply.txt").write_text(result.get("reply", ""), encoding="utf-8")
    (test_out_dir / "debug.json").write_text(
        json.dumps(result.get("debug", {}), indent=2, default=str), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Per-test runner
# ---------------------------------------------------------------------------

def run_one_test(
    test: dict,
    config,
    out_dir: Path,
    tee_write,
    *,
    use_planner: bool = False,
) -> dict:
    """
    Run one top-level test + debug sub-tests if it fails.
    tee_write(msg) writes a line to console.txt and stdout simultaneously.
    """
    tid = test["id"]
    name = test["name"]
    pipeline = "run_query_plan" if use_planner else test.get("pipeline", "run_query")
    ts_tag = datetime.now().strftime("%H%M%S")
    criteria = _pick_criteria(test, use_planner)

    def _log(msg: str = "") -> None:
        print(msg)
        tee_write(msg)

    _log()
    _log("=" * 72)
    _log(f"[{tid}]  {name}  {'[planner]' if use_planner else ''}")
    _log("=" * 72)

    session = _make_session(config, f"st-{ts_tag}-{tid.lower()}")
    test_out_dir = out_dir / tid

    # ── Setup queries ─────────────────────────────────────────────────────
    for setup_q in test.get("setup_queries", []):
        _log(f"  [setup]  {setup_q!r}")
        try:
            _run_query(session, config, setup_q, pipeline, tee_write)
        except Exception as exc:
            _log(f"  [setup ERROR]  {exc}")
        _move_run_dir(session, test_out_dir)

    # ── Main query ────────────────────────────────────────────────────────
    query = test["query"]
    _log(f"  QUERY:  {query!r}")
    t0 = time.perf_counter()

    try:
        result = _run_query(session, config, query, pipeline, tee_write)
        elapsed = time.perf_counter() - t0
        _move_run_dir(session, test_out_dir)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc()
        _log(f"  [EXCEPTION]  {exc}")
        test_out_dir.mkdir(parents=True, exist_ok=True)
        (test_out_dir / "query.txt").write_text(query, encoding="utf-8")
        (test_out_dir / "error.txt").write_text(tb, encoding="utf-8")
        _move_run_dir(session, test_out_dir)
        return {
            "id": tid, "name": name, "query": query,
            "passed": False, "error": str(exc),
            "elapsed": round(elapsed, 2),
            "criteria_results": [], "debug_results": [],
        }

    _write_test_artifacts(test_out_dir, query, result)
    debug = result.get("debug", {})
    passed, crit_results = check_pass(debug, criteria)

    status = "PASS" if passed else "FAIL"
    _log(f"  RESULT:  {status}  ({elapsed:.1f}s)")
    for cr in crit_results:
        mark = "✓" if cr["passed"] else "✗"
        _log(f"    {mark}  {cr['reason']}")

    # ── Debug sub-queries (only on failure) ───────────────────────────────
    debug_results: list[dict] = []

    if not passed and test.get("debug_queries"):
        _log()
        _log(f"  → Launching {len(test['debug_queries'])} debug sub-queries…")

        for dq in test["debug_queries"]:
            did = dq["id"]
            dq_criteria = _pick_criteria(dq, use_planner)

            _log()
            _log(f"    [{did}]  {dq['name']}")
            _log(f"    Purpose:  {dq['purpose']}")
            _log(f"    Query:    {dq['query']!r}")

            # Fresh session; re-run setup if the parent test needed it
            dts = datetime.now().strftime("%H%M%S%f")
            dq_session = _make_session(config, f"st-{dts}-{did.lower()}")
            dq_out_dir = out_dir / did
            for setup_q in test.get("setup_queries", []):
                try:
                    _run_query(dq_session, config, setup_q, pipeline, tee_write)
                except Exception:
                    pass
                _move_run_dir(dq_session, dq_out_dir)

            dt0 = time.perf_counter()
            try:
                dq_result = _run_query(dq_session, config, dq["query"], pipeline, tee_write)
                d_elapsed = time.perf_counter() - dt0
                _move_run_dir(dq_session, dq_out_dir)
            except Exception as exc:
                d_elapsed = time.perf_counter() - dt0
                tb = traceback.format_exc()
                _log(f"    [EXCEPTION]  {exc}")
                dq_out_dir.mkdir(parents=True, exist_ok=True)
                (dq_out_dir / "query.txt").write_text(dq["query"], encoding="utf-8")
                (dq_out_dir / "error.txt").write_text(tb, encoding="utf-8")
                _move_run_dir(dq_session, dq_out_dir)
                debug_results.append({
                    "id": did, "name": dq["name"], "purpose": dq["purpose"],
                    "query": dq["query"], "passed": False, "error": str(exc),
                    "elapsed": round(d_elapsed, 2), "criteria_results": [],
                })
                continue

            _write_test_artifacts(dq_out_dir, dq["query"], dq_result)
            dq_debug = dq_result.get("debug", {})
            dq_passed, dq_crit_results = check_pass(dq_debug, dq_criteria)

            dq_status = "PASS" if dq_passed else "FAIL"
            _log(f"    RESULT:  {dq_status}  ({d_elapsed:.1f}s)")
            for cr in dq_crit_results:
                mark = "✓" if cr["passed"] else "✗"
                _log(f"      {mark}  {cr['reason']}")

            debug_results.append({
                "id": did, "name": dq["name"], "purpose": dq["purpose"],
                "query": dq["query"], "passed": dq_passed,
                "elapsed": round(d_elapsed, 2),
                "criteria_results": dq_crit_results,
            })

    return {
        "id": tid, "name": name, "query": query,
        "passed": passed, "elapsed": round(elapsed, 2),
        "criteria_results": crit_results,
        "debug_results": debug_results,
    }


def run_one_test_v2(
    test: dict,
    config,
    out_dir: Path,
    tee_write,
    *,
    use_planner: bool = False,
) -> dict:
    """smart_test_v2 per-test runner.

    Differs from `run_one_test`:
    - Supports `turns` (multi-turn) shape with per-turn pass_criteria.
    - Supports `session_mode: 'shared'` so all turns of one test share the
      same SQLite session (chat_log + results_history + wizard state persist).
    - Resolves new v2 special fields (wizard.*, chat_log.*, last_reply, etc.)
      via `_resolve_field_v2`.
    - Legacy tests (no `turns`) fall back to the existing `run_one_test`
      behavior; this function only takes over when `turns` is present.
    """
    if not test.get("turns"):
        return run_one_test(test, config, out_dir, tee_write, use_planner=use_planner)

    tid = test["id"]
    name = test["name"]
    pipeline = "run_query_plan" if use_planner else test.get("pipeline", "run_query")
    session_mode = test.get("session_mode", "shared")
    ts_tag = datetime.now().strftime("%H%M%S")

    def _log(msg: str = "") -> None:
        print(msg)
        tee_write(msg)

    _log()
    _log("=" * 72)
    _log(f"[{tid}]  {name}  [v2 turns={len(test['turns'])} session={session_mode}]")
    _log("=" * 72)

    test_out_dir = out_dir / tid
    test_out_dir.mkdir(parents=True, exist_ok=True)

    # Single shared session for all turns of this test (the standard v2 mode).
    # For 'fresh' mode we'd make a new session per turn — that's a future option;
    # nothing in v2 currently asks for it.
    shared_session = _make_session(config, f"st2-{ts_tag}-{tid.lower()}")

    turn_results: list[dict] = []
    overall_pass = True

    for i, turn in enumerate(test["turns"]):
        label = turn.get("label") or f"turn_{i+1}"
        query = turn.get("query") or ""
        criteria = (
            turn.get("planner_pass_criteria")
            if use_planner and turn.get("planner_pass_criteria")
            else turn.get("pass_criteria") or []
        )

        _log()
        _log(f"  [{label}]  {query!r}")

        t0 = time.perf_counter()
        try:
            result = _run_query(shared_session, config, query, pipeline, tee_write)
            elapsed = time.perf_counter() - t0
            _move_run_dir(shared_session, test_out_dir)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - t0
            tb = traceback.format_exc()
            _log(f"    [EXCEPTION]  {exc}")
            turn_out_dir = test_out_dir / label
            turn_out_dir.mkdir(parents=True, exist_ok=True)
            (turn_out_dir / "query.txt").write_text(query, encoding="utf-8")
            (turn_out_dir / "error.txt").write_text(tb, encoding="utf-8")
            _move_run_dir(shared_session, turn_out_dir)
            turn_results.append({
                "label": label, "query": query, "passed": False,
                "error": str(exc), "elapsed": round(elapsed, 2),
                "criteria_results": [],
            })
            overall_pass = False
            continue

        # Per-turn artifacts
        turn_out_dir = test_out_dir / label
        _write_test_artifacts(turn_out_dir, query, result)

        debug = result.get("debug") or {}
        reply = result.get("reply") or ""
        passed, crit_results = check_pass(
            debug, criteria, session=shared_session, last_reply=reply
        )

        status = "PASS" if passed else "FAIL"
        _log(f"    RESULT:  {status}  ({elapsed:.1f}s)")
        for cr in crit_results:
            mark = "✓" if cr["passed"] else "✗"
            _log(f"      {mark}  {cr['reason']}")

        turn_results.append({
            "label": label, "query": query, "passed": passed,
            "elapsed": round(elapsed, 2),
            "criteria_results": crit_results,
            "reply_preview": (reply or "")[:280],
        })
        if not passed:
            overall_pass = False

    return {
        "id": tid,
        "name": name,
        "query": test.get("turns", [{}])[0].get("query", ""),  # first turn for summary
        "passed": overall_pass,
        "elapsed": round(sum(t.get("elapsed", 0) for t in turn_results), 2),
        "criteria_results": [],
        "debug_results": [],
        "turn_results": turn_results,
        "session_mode": session_mode,
    }


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

def run_smart_suite(
    mode: str | None,
    only: list[str] | None,
    use_planner: bool = False,
    full_test: bool = False,
) -> int:
    global _query_count
    _query_count = 0  # reset for each suite run

    if mode:
        os.environ["NEXTSEEK_MODE"] = mode

    from chat_nextseek.config import ChatConfig  # noqa: PLC0415

    config = ChatConfig()

    ts = datetime.now().strftime("%y%m%d_%H%M%S")
    mode_tag = mode or "mixed"
    pipeline_tag = "planner" if use_planner else "standard"
    suite_tag = "full_test" if full_test else "smart_test"
    out_dir = Path("outputs") / f"{suite_tag}_{ts}_{mode_tag}_{pipeline_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    console_path = out_dir / "console.txt"

    tag = suite_tag

    tests = load_tests(only, full_test=full_test)
    if not tests:
        print(f"[{tag}]  No tests matched: {only}")
        return 1

    print(f"\n[{tag}]  Output:   {out_dir}")
    print(f"[{tag}]  Mode:     {mode_tag}")
    print(f"[{tag}]  Pipeline: {pipeline_tag}")
    if full_test:
        n_with_criteria = sum(1 for t in tests if t.get("pass_criteria"))
        print(f"[{tag}]  Suite:    full_test ({len(tests)} questions, {n_with_criteria} with pass criteria)")

    all_results: list[dict] = []

    with open(console_path, "w", encoding="utf-8") as log_fh:
        suite_label = "Full Test Suite" if full_test else "Smart Test Suite"
        header = (
            f"{suite_label}\n"
            f"Started:  {datetime.now().isoformat()}\n"
            f"Mode:     {mode_tag}\n"
            f"Pipeline: {pipeline_tag}\n"
            f"Tests:    {[t['id'] for t in tests]}\n"
        )
        log_fh.write(header)
        print(header)

        def _tee(msg: str = "") -> None:
            log_fh.write(msg + "\n")
            log_fh.flush()

        inter_test_pause = 30 if full_test else 0

        for i, test in enumerate(tests):
            # Dispatch on shape: tests with `turns` use the multi-turn runner
            # (shared session, per-turn criteria); legacy single-query tests
            # use the original runner with optional setup_queries.
            if test.get("turns"):
                result = run_one_test_v2(
                    test, config, out_dir, _tee, use_planner=use_planner
                )
            else:
                result = run_one_test(
                    test, config, out_dir, _tee, use_planner=use_planner
                )
            all_results.append(result)

            if inter_test_pause and i < len(tests) - 1:
                _tee(f"  [pause {inter_test_pause}s before next test…]")
                time.sleep(inter_test_pause)

        # ── Scorecard ─────────────────────────────────────────────────────
        n_pass = sum(1 for r in all_results if r["passed"])
        n_total = len(all_results)

        lines = [
            "",
            "=" * 72,
            f"SCORECARD:  {n_pass}/{n_total} top-level tests passed  [{pipeline_tag}]",
            "=" * 72,
        ]
        for r in all_results:
            tag = "PASS" if r["passed"] else "FAIL"
            line = f"  [{tag}]  {r['id']:5s}  {r['name']}"
            if not r["passed"]:
                dr = r.get("debug_results", [])
                dp = sum(1 for d in dr if d["passed"])
                if dr:
                    line += f"   (debug: {dp}/{len(dr)} sub-tests passed)"
            lines.append(line)

        failed = [r for r in all_results if not r["passed"]]
        if failed:
            lines.append("")
            lines.append("Failing criteria:")
            for r in failed:
                lines.append(f"  {r['id']}:")
                for cr in r.get("criteria_results", []):
                    if not cr["passed"]:
                        lines.append(f"    ✗  {cr['reason']}")
                dr = r.get("debug_results", [])
                if dr:
                    lines.append("    Debug sub-test summary:")
                    for d in dr:
                        dtag = "PASS" if d["passed"] else "FAIL"
                        lines.append(f"      [{dtag}]  {d['id']}  {d['name']}")

        scorecard = "\n".join(lines)
        print(scorecard)
        log_fh.write(scorecard + "\n")

    summary = {
        "timestamp": ts,
        "mode": mode_tag,
        "pipeline": pipeline_tag,
        "passed": n_pass,
        "total": n_total,
        "pass_rate": round(n_pass / n_total * 100, 1) if n_total else 0,
        "results": all_results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "summary.txt").write_text(scorecard + "\n", encoding="utf-8")

    try:
        report_path = generate_html_report(out_dir)
        print(f"[{tag}]  Report:   {report_path}")
    except Exception as exc:
        print(f"[{tag}]  HTML report failed: {exc}")

    # If a sibling run exists for the other pipeline, auto-generate combined report (smart_test only)
    if not full_test:
        try:
            sibling_pipeline = "standard" if pipeline_tag == "planner" else "planner"
            siblings = sorted(
                (out_dir.parent).glob(f"smart_test_*_{mode_tag}_{sibling_pipeline}"),
                key=lambda p: p.name,
            )
            if siblings:
                sibling = siblings[-1]  # most recent
                combined_path = generate_combined_html_report(out_dir, sibling)
                print(f"[{tag}]  Combined: {combined_path}")
        except Exception as exc:
            print(f"[{tag}]  Combined report failed: {exc}")

    print(f"[{tag}]  Done. Results in: {out_dir}")
    return 0 if n_pass == n_total else 1


# ---------------------------------------------------------------------------
# HTML report generator
# ---------------------------------------------------------------------------

def _h(v: Any) -> str:
    return _html_lib.escape(str(v) if v is not None else "")


def _badge(text: str, color: str = "#64748b") -> str:
    return (
        f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:12px;'
        f'font-size:11px;font-weight:600;font-family:monospace;white-space:nowrap">{_h(text)}</span>'
    )


_MODE_COLORS: dict[str, str] = {
    "new_search":           "#10b981",
    "refine_last_search":   "#0ea5e9",
    "ask_about_last_results":"#6366f1",
    "memory_lookup":        "#6366f1",
    "reporter":             "#f59e0b",
    "report_generation":    "#f97316",
    "graph_query":          "#8b5cf6",
    "system_question":      "#64748b",
    "unsupported":          "#ef4444",
    "plan":                 "#ec4899",
}


def _mode_badge(mode: str | None) -> str:
    return _badge(mode or "—", _MODE_COLORS.get(mode or "", "#94a3b8"))


def _agent_block(title: str, color: str, content: str) -> str:
    return (
        f'<div style="border-left:3px solid {color};padding:6px 12px;margin:4px 0;'
        f'background:#f8fafc;border-radius:0 4px 4px 0">'
        f'<div style="font-size:10px;font-weight:700;color:{color};text-transform:uppercase;'
        f'letter-spacing:.06em;margin-bottom:4px">{_h(title)}</div>'
        f'{content}'
        f'</div>'
    )


def _json_pre(obj: Any) -> str:
    if obj is None:
        return '<span style="color:#94a3b8;font-size:11px">—</span>'
    return (
        f'<pre style="margin:4px 0;padding:6px 8px;background:#0f172a;color:#e2e8f0;'
        f'border-radius:4px;font-size:11px;overflow-x:auto;white-space:pre-wrap;'
        f'word-break:break-all">{_h(json.dumps(obj, indent=2, default=str))}</pre>'
    )


def _render_entity(entity: dict) -> str:
    st  = [f"{s['code']} ({s['name']})" for s in entity.get("sampletypes", []) if s.get("code")]
    asy = [a["code"] for a in entity.get("assays", []) if a.get("code")]
    kw  = entity.get("keywords", [])
    proj = entity.get("projects", [])
    lines = []
    if st:   lines.append(f"Sampletypes: {', '.join(st)}")
    if asy:  lines.append(f"Assays: {', '.join(asy)}")
    if kw:   lines.append(f"Keywords: {', '.join(kw)}")
    if proj: lines.append(f"Projects: {', '.join(proj)}")
    body = "<br>".join(_h(l) for l in lines) if lines else '<span style="color:#94a3b8">no entities extracted</span>'
    return _agent_block("Entity Agent", "#6366f1", body)


def _render_pipeline_standard(debug: dict) -> str:
    parts: list[str] = []

    entity = debug.get("entity_result") or {}
    if entity:
        parts.append(_render_entity(entity))

    parser = debug.get("parser_plan") or {}
    if parser:
        mode    = parser.get("mode", "")
        intent  = parser.get("intent_summary", "")
        ep      = parser.get("target_endpoint", "")
        notes   = parser.get("notes", "")
        body = _mode_badge(mode)
        if intent: body += f'<div style="margin-top:4px;font-size:12px;color:#334155">{_h(intent)}</div>'
        if ep:     body += f'<div style="font-size:11px;color:#64748b;font-family:monospace">{_h(ep)}</div>'
        if notes:  body += f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">{_h(notes)}</div>'
        parts.append(_agent_block("Parser Agent", "#0ea5e9", body))

    api_plan = debug.get("api_plan") or {}
    if api_plan and api_plan.get("endpoint"):
        method = api_plan.get("method", "")
        ep     = api_plan.get("endpoint", "")
        body   = api_plan.get("requestBody")
        params = api_plan.get("queryParameters") or {}
        content = f'<code style="font-size:12px">{_h(method)} {_h(ep)}</code>'
        if body:   content += _json_pre(body)
        if params: content += f'<div style="font-size:11px;color:#64748b">params: {_h(json.dumps(params))}</div>'
        parts.append(_agent_block("API Agent", "#10b981", content))

    rp = debug.get("reporter_plan") or {}
    if rp:
        tags  = [_mode_badge(rp.get("reporter_mode"))]
        smode = rp.get("summary_mode")
        rtype = rp.get("report_type")
        proj  = rp.get("project", "")
        uids  = rp.get("uids", [])
        notes = rp.get("notes", "")
        if smode: tags.append(_badge(smode, "#fb923c"))
        if rtype: tags.append(_badge(rtype, "#f97316"))
        content = " ".join(tags)
        if proj:  content += f'<div style="font-size:12px;margin-top:4px">Project: <b>{_h(proj)}</b></div>'
        if uids:  content += f'<div style="font-size:11px;color:#64748b">UIDs: {_h(", ".join(str(u) for u in uids[:6]))}{"…" if len(uids) > 6 else ""}</div>'
        if notes: content += f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">{_h(notes)}</div>'
        parts.append(_agent_block("Reporter Plan", "#f59e0b", content))

    gp = debug.get("graph_plan") or {}
    if gp:
        cypher = gp.get("cypher", "")
        expl   = gp.get("explanation", "")
        content = ""
        if expl:   content += f'<div style="font-size:12px;color:#334155;margin-bottom:4px">{_h(expl)}</div>'
        if cypher: content += f'<pre style="margin:0;padding:6px 8px;background:#0f172a;color:#a5f3fc;border-radius:4px;font-size:11px;overflow-x:auto;white-space:pre-wrap">{_h(cypher)}</pre>'
        parts.append(_agent_block("Graph Agent", "#8b5cf6", content))

    # Result line
    result_parts: list[str] = []
    api_meta = debug.get("api_result_meta") or {}
    if api_meta.get("ok") is not None:
        ok  = api_meta["ok"]
        col = "#10b981" if ok else "#ef4444"
        result_parts.append(_badge(f"API {api_meta.get('status_code', '')}", col))
        if api_meta.get("url"):
            result_parts.append(f'<span style="font-size:11px;color:#64748b;font-family:monospace"> {_h(api_meta["url"])}</span>')

    gr = debug.get("graph_result") or {}
    if gr.get("ok") is not None:
        ok  = gr["ok"]
        col = "#10b981" if ok else "#ef4444"
        result_parts.append(_badge(f"Neo4j {'OK' if ok else 'ERR'} · {gr.get('count', 0)} rows", col))

    rr = debug.get("reporter_result") or {}
    if rr.get("ok") is not None:
        ok    = rr["ok"]
        col   = "#10b981" if ok else "#ef4444"
        label = f"Reporter {'OK' if ok else 'ERR'}"
        sm    = rr.get("summary_mode", "")
        rows  = rr.get("rows_returned", "")
        if sm:   label += f" [{sm}]"
        if rows: label += f" · {rows} rows"
        result_parts.append(_badge(label, col))

    if result_parts:
        parts.append(_agent_block("Result", "#334155", " ".join(result_parts)))

    return "".join(parts)


def _render_pipeline_planner(debug: dict) -> str:
    parts: list[str] = []

    entity = debug.get("entity") or {}
    if entity:
        parts.append(_render_entity(entity))

    parser = debug.get("parser") or {}
    if parser:
        intent     = parser.get("intent_summary", "")
        candidates = parser.get("candidates") or []
        notes      = parser.get("notes", "")
        content    = ""
        if intent: content += f'<div style="font-size:12px;color:#334155;margin-bottom:6px">{_h(intent)}</div>'
        for i, c in enumerate(candidates):
            mode    = c.get("mode", "")
            ep      = c.get("target_endpoint", "")
            rat     = c.get("rationale", "")
            conf    = c.get("confidence")
            rtype   = c.get("report_type")
            line    = _mode_badge(mode)
            if rtype: line += " " + _badge(rtype, "#f97316")
            if conf is not None: line += f' <span style="font-size:11px;color:#94a3b8">conf={conf:.2f}</span>'
            if ep:  line += f'<div style="font-size:11px;color:#64748b;font-family:monospace">{_h(ep)}</div>'
            if rat: line += f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">{_h(rat[:220])}{"…" if len(rat) > 220 else ""}</div>'
            content += f'<div style="margin-bottom:4px">#{i+1}: {line}</div>'
        if notes: content += f'<div style="font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;padding-top:4px;margin-top:4px">{_h(notes)}</div>'
        parts.append(_agent_block("Multi-Parser", "#0ea5e9", content))

    planner = debug.get("planner") or {}
    if planner:
        intent  = planner.get("intent_summary", "")
        steps   = planner.get("steps") or []
        notes   = planner.get("notes", "")
        content = ""
        if intent: content += f'<div style="font-size:12px;color:#334155;margin-bottom:6px">{_h(intent)}</div>'
        for step in steps:
            sid      = step.get("step_id", "?")
            tool     = step.get("tool", "")
            ctx      = step.get("context_prompt", "")
            ep       = step.get("target_endpoint", "")
            snotes   = step.get("notes", "")
            content += (
                f'<div style="margin-bottom:6px;padding:4px 8px;background:#f1f5f9;border-radius:4px">'
                f'<span style="font-size:11px;font-weight:700;color:#64748b">Step {sid}:</span> '
                f'{_badge(tool, "#ec4899")}'
            )
            if ep:     content += f' <code style="font-size:11px;color:#64748b">{_h(ep)}</code>'
            if ctx:    content += f'<div style="font-size:11px;color:#475569;margin-top:2px">{_h(ctx[:180])}{"…" if len(ctx) > 180 else ""}</div>'
            if snotes: content += f'<div style="font-size:11px;color:#94a3b8">{_h(snotes)}</div>'
            content += "</div>"
        if notes: content += f'<div style="font-size:11px;color:#94a3b8">{_h(notes)}</div>'
        parts.append(_agent_block("Planner", "#ec4899", content))

    step_results = debug.get("step_results") or {}
    if step_results:
        content = ""
        for sid, sr in step_results.items():
            ok       = sr.get("ok")
            tool     = sr.get("tool", "")
            count    = sr.get("count")
            error    = sr.get("error")
            api_plan = sr.get("api_plan") or {}
            ok_col   = "#10b981" if ok else ("#ef4444" if ok is False else "#94a3b8")
            content += (
                f'<div style="margin-bottom:6px">'
                f'<span style="font-size:11px;font-weight:700;color:#64748b">Step {sid}:</span> '
                f'{_badge(tool, "#64748b")} '
                f'{_badge("OK" if ok else ("ERR" if ok is False else "?"), ok_col)}'
            )
            if count is not None: content += f' <span style="font-size:11px;color:#64748b">{count} results</span>'
            if error: content += f'<div style="font-size:11px;color:#ef4444">{_h(str(error))}</div>'
            if api_plan.get("endpoint"):
                content += (
                    f'<div style="margin-top:4px;padding:4px 8px;background:#f1f5f9;border-radius:4px">'
                    f'<code style="font-size:11px">{_h(api_plan.get("method",""))} {_h(api_plan["endpoint"])}</code>'
                )
                if api_plan.get("requestBody"): content += _json_pre(api_plan["requestBody"])
                content += "</div>"
            content += "</div>"
        parts.append(_agent_block("Step Results", "#10b981", content))

    evaluator = debug.get("evaluator") or {}
    if evaluator:
        status = evaluator.get("overall_status", "")
        color = "#10b981" if status == "success" else ("#f59e0b" if status == "partial" else "#ef4444")
        content = (
            f'{_badge(status or "unknown", color)} '
            f'{_badge("answered" if evaluator.get("answered_query") else "not answered", "#334155")} '
            f'{_badge("consistent" if evaluator.get("execution_consistent") else "inconsistent", "#475569")}'
        )
        if evaluator.get("user_safe_summary"):
            content += f'<div style="margin-top:6px;font-size:12px;color:#475569">{_h(evaluator["user_safe_summary"])}</div>'
        if evaluator.get("what_went_wrong"):
            content += f'<div style="margin-top:4px;font-size:11px;color:#64748b">{_h(evaluator["what_went_wrong"])}</div>'
        parts.append(_agent_block("Evaluator", "#0f766e", content))

    return "".join(parts)


def _render_pipeline(debug: dict) -> str:
    if debug.get("mode") == "plan":
        return _render_pipeline_planner(debug)
    return _render_pipeline_standard(debug)


def _render_criteria_table(criteria_results: list[dict]) -> str:
    if not criteria_results:
        return '<span style="color:#94a3b8;font-size:12px">No criteria evaluated</span>'
    rows = ""
    for cr in criteria_results:
        ok       = cr.get("passed", False)
        icon     = "✓" if ok else "✗"
        icon_col = "#10b981" if ok else "#ef4444"
        row_bg   = "#f0fdf4" if ok else "#fef2f2"
        rows += (
            f'<tr style="background:{row_bg}">'
            f'<td style="padding:4px 8px;font-size:15px;color:{icon_col};width:24px;text-align:center">{icon}</td>'
            f'<td style="padding:4px 8px;font-family:monospace;font-size:11px;color:#334155;white-space:nowrap">{_h(cr.get("field",""))}</td>'
            f'<td style="padding:4px 8px;font-size:12px;color:#475569">{_h(cr.get("reason",""))}</td>'
            f'</tr>'
        )
    return (
        '<table style="width:100%;border-collapse:collapse;margin:8px 0">'
        '<colgroup><col style="width:24px"><col style="width:240px"><col></colgroup>'
        + rows + "</table>"
    )


def _render_test_block(result: dict, *, indent: bool = False) -> str:
    tid      = result["id"]
    name     = result["name"]
    query    = result["query"]
    passed   = result.get("passed", False)
    elapsed  = result.get("elapsed", 0)
    error    = result.get("error")
    purpose  = result.get("purpose", "")
    criteria = result.get("criteria_results", [])
    debug    = result.get("_debug", {})
    reply    = result.get("_reply", "")

    pass_col   = "#10b981" if passed else "#ef4444"
    pass_label = "PASS" if passed else "FAIL"
    card_border = "#d1fae5" if passed else "#fee2e2"
    card_bg     = "#f0fdf4" if passed else "#fff5f5"
    margin_left = "margin-left:28px;" if indent else ""

    sections = ""

    if error:
        sections += (
            f'<div style="margin-top:6px;padding:8px;background:#fef2f2;border-radius:4px;'
            f'font-size:12px;color:#ef4444;font-family:monospace">{_h(str(error))}</div>'
        )

    sections += _render_criteria_table(criteria)

    pipeline_html = _render_pipeline(debug) if debug else ""
    if pipeline_html:
        sections += (
            f'<details style="margin-top:8px">'
            f'<summary style="cursor:pointer;font-size:12px;font-weight:600;color:#64748b;'
            f'user-select:none;outline:none">Agent Pipeline</summary>'
            f'<div style="margin-top:6px">{pipeline_html}</div>'
            f'</details>'
        )

    if reply.strip():
        sections += (
            f'<details style="margin-top:8px">'
            f'<summary style="cursor:pointer;font-size:12px;font-weight:600;color:#64748b;'
            f'user-select:none;outline:none">Reply</summary>'
            f'<div style="margin-top:6px;padding:10px;background:#f8fafc;border-radius:4px;'
            f'font-size:12px;color:#334155;white-space:pre-wrap;line-height:1.5">{_h(reply.strip())}</div>'
            f'</details>'
        )

    purpose_html = (
        f'<div style="font-size:12px;color:#64748b;margin-bottom:4px;font-style:italic">{_h(purpose)}</div>'
        if purpose else ""
    )

    return (
        f'<div style="{margin_left}border:1px solid {card_border};border-left:4px solid {pass_col};'
        f'background:{card_bg};border-radius:6px;padding:12px 16px;margin:8px 0">'
        f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">'
        f'<span style="font-family:monospace;font-size:12px;font-weight:700;color:#334155">{_h(tid)}</span>'
        f'<span style="font-size:13px;font-weight:600;color:#1e293b;flex:1;min-width:0">{_h(name)}</span>'
        f'<span style="background:{pass_col};color:#fff;padding:2px 10px;border-radius:12px;'
        f'font-size:11px;font-weight:700;flex-shrink:0">{pass_label}</span>'
        f'<span style="font-size:11px;color:#94a3b8;flex-shrink:0">{elapsed:.1f}s</span>'
        f'</div>'
        f'{purpose_html}'
        f'<div style="font-size:12px;color:#475569;background:#f1f5f9;padding:6px 10px;'
        f'border-radius:4px;margin-bottom:8px;font-style:italic">"{_h(query)}"</div>'
        f'{sections}'
        f'</div>'
    )


def generate_html_report(out_dir: Path, *, reeval_criteria: bool = True) -> Path:
    """
    Read summary.json + per-test debug.json/reply.txt from out_dir.
    Write a self-contained report.html and return its path.

    If reeval_criteria=True (default), re-evaluates pass criteria from testing.json
    against the saved debug.json files.  This lets --report regeneration pick up
    newly-added criteria without re-running queries.
    """
    out_dir = Path(out_dir)
    summary_path = out_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"No summary.json in {out_dir}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # Build test-id → criteria lookup from testing.json (both sections)
    _criteria_map: dict[str, list[dict]] = {}
    if reeval_criteria and _GT_PATH.exists():
        try:
            gt = json.loads(_GT_PATH.read_text(encoding="utf-8"))
            for section in ("smart_test", "full_test"):
                for t in gt.get(section, {}).get("tests", []):
                    _criteria_map[t["id"]] = t.get("pass_criteria", [])
        except Exception:
            pass  # fall back to stored criteria

    def _load(result: dict) -> None:
        tid = result["id"]
        dbp = out_dir / tid / "debug.json"
        rp  = out_dir / tid / "reply.txt"
        result["_debug"] = json.loads(dbp.read_text(encoding="utf-8")) if dbp.exists() else {}
        result["_reply"] = rp.read_text(encoding="utf-8") if rp.exists() else ""
        # Re-evaluate criteria if available
        if reeval_criteria and tid in _criteria_map and result["_debug"]:
            criteria = _criteria_map[tid]
            passed, crit_results = check_pass(result["_debug"], criteria)
            result["passed"] = passed
            result["criteria_results"] = crit_results

    for r in summary["results"]:
        _load(r)
        for dr in r.get("debug_results", []):
            _load(dr)

    # Recompute aggregates after re-evaluation
    if reeval_criteria:
        n_pass_new = sum(1 for r in summary["results"] if r.get("passed", False))
        summary["passed"] = n_pass_new
        summary["pass_rate"] = round(n_pass_new / len(summary["results"]) * 100, 1) if summary["results"] else 0.0

    ts         = summary.get("timestamp", "")
    mode       = summary.get("mode", "")
    pipeline   = summary.get("pipeline", "")
    n_pass     = summary.get("passed", 0)
    n_total    = summary.get("total", 0)
    pass_rate  = summary.get("pass_rate", 0.0)
    bar_pct    = int(pass_rate)
    bar_color  = "#10b981" if pass_rate >= 80 else ("#f59e0b" if pass_rate >= 50 else "#ef4444")

    # Summary pill strip
    pills = ""
    for r in summary["results"]:
        col = "#10b981" if r["passed"] else "#ef4444"
        pills += (
            f'<span title="{_h(r["name"])}" style="display:inline-block;background:{col};color:#fff;'
            f'border-radius:4px;padding:2px 7px;font-size:11px;font-weight:700;'
            f'font-family:monospace;margin:2px;cursor:default">{_h(r["id"])}</span>'
        )

    # Test cards
    cards = ""
    for r in summary["results"]:
        cards += _render_test_block(r)
        for dr in r.get("debug_results", []):
            cards += _render_test_block(dr, indent=True)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NExtSEEK Smart Test — {_h(ts)} {_h(pipeline)}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; background: #f1f5f9; color: #1e293b;
}}
.header {{
  background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
  color: #f8fafc; padding: 24px 32px;
}}
.header h1 {{ margin: 0 0 4px; font-size: 22px; font-weight: 800; letter-spacing: -.3px; }}
.header .meta {{ font-size: 13px; color: #94a3b8; margin-bottom: 10px; }}
.header .score {{ font-size: 36px; font-weight: 800; color: #f8fafc; line-height: 1; }}
.header .score small {{ font-size: 16px; color: #94a3b8; font-weight: 400; margin-left: 6px; }}
.progress-bar {{ height: 6px; background: #334155; border-radius: 4px; margin-top: 10px; max-width: 320px; }}
.progress-fill {{ height: 100%; border-radius: 4px; background: {bar_color}; width: {bar_pct}%; transition: width .4s; }}
.container {{ max-width: 980px; margin: 24px auto; padding: 0 16px 40px; }}
.pill-strip {{ background: #fff; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px;
              box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
.pill-strip h3 {{ margin: 0 0 8px; font-size: 12px; color: #64748b; font-weight: 700;
                  text-transform: uppercase; letter-spacing: .06em; }}
details > summary {{ list-style: none; }}
details > summary::-webkit-details-marker {{ display: none; }}
details > summary::before {{ content: "▸ "; font-size: 10px; }}
details[open] > summary::before {{ content: "▾ "; }}
</style>
</head>
<body>
<div class="header">
  <h1>NExtSEEK Smart Test Report</h1>
  <div class="meta">{_h(ts)} &nbsp;·&nbsp; mode: <b>{_h(mode)}</b> &nbsp;·&nbsp; pipeline: <b>{_h(pipeline)}</b></div>
  <div class="score">{n_pass}/{n_total}<small>({pass_rate:.1f}%)</small></div>
  <div class="progress-bar"><div class="progress-fill"></div></div>
</div>
<div class="container">
  <div class="pill-strip">
    <h3>Test Overview</h3>
    {pills}
  </div>
  {cards}
</div>
</body>
</html>"""

    out_path = out_dir / "report.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Combined HTML report (standard + planner side by side)
# ---------------------------------------------------------------------------

def _render_test_col(result: dict) -> str:
    """Compact single-column card for use inside the combined side-by-side view."""
    passed   = result.get("passed", False)
    elapsed  = result.get("elapsed", 0)
    error    = result.get("error")
    criteria = result.get("criteria_results", [])
    debug    = result.get("_debug", {})
    reply    = result.get("_reply", "")

    pass_col   = "#10b981" if passed else "#ef4444"
    pass_label = "PASS" if passed else "FAIL"

    sections = (
        f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">'
        f'<span style="background:{pass_col};color:#fff;padding:2px 10px;border-radius:12px;'
        f'font-size:11px;font-weight:700">{pass_label}</span>'
        f'<span style="font-size:11px;color:#94a3b8">{elapsed:.1f}s</span>'
        f'</div>'
    )

    if error:
        sections += (
            f'<div style="padding:6px 8px;background:#fef2f2;border-radius:4px;'
            f'font-size:11px;color:#ef4444;font-family:monospace;margin-bottom:6px">{_h(str(error))}</div>'
        )

    sections += _render_criteria_table(criteria)

    pipeline_html = _render_pipeline(debug) if debug else ""
    if pipeline_html:
        sections += (
            f'<details style="margin-top:8px">'
            f'<summary style="cursor:pointer;font-size:12px;font-weight:600;color:#64748b;'
            f'user-select:none;outline:none">Agent Pipeline</summary>'
            f'<div style="margin-top:6px">{pipeline_html}</div>'
            f'</details>'
        )
    if reply.strip():
        sections += (
            f'<details style="margin-top:8px">'
            f'<summary style="cursor:pointer;font-size:12px;font-weight:600;color:#64748b;'
            f'user-select:none;outline:none">Reply</summary>'
            f'<div style="margin-top:6px;padding:8px;background:#f8fafc;border-radius:4px;'
            f'font-size:12px;color:#334155;white-space:pre-wrap;line-height:1.5">{_h(reply.strip())}</div>'
            f'</details>'
        )
    return sections


def _render_debug_rows(debug_results: list[dict]) -> str:
    """Render debug sub-tests as indented cards below a combined row."""
    if not debug_results:
        return ""
    html = '<div style="margin-left:24px;margin-top:0">'
    for dr in debug_results:
        html += _render_test_block(dr)
    html += "</div>"
    return html


def generate_combined_html_report(dir_a: Path, dir_b: Path) -> Path:
    """
    Merge two smart-test output directories (one standard, one planner) into a
    single side-by-side comparison report.  Writes combined_report.html to the
    shared parent directory and returns the path.
    """
    dir_a, dir_b = Path(dir_a), Path(dir_b)

    def _load_summary(d: Path) -> dict:
        s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        for r in s["results"]:
            _tid = r["id"]
            for key in ("debug_results",):
                for dr in r.get(key, []):
                    did = dr["id"]
                    dbp = d / did / "debug.json"
                    rp  = d / did / "reply.txt"
                    dr["_debug"] = json.loads(dbp.read_text(encoding="utf-8")) if dbp.exists() else {}
                    dr["_reply"] = rp.read_text(encoding="utf-8") if rp.exists() else ""
            dbp = d / _tid / "debug.json"
            rp  = d / _tid / "reply.txt"
            r["_debug"] = json.loads(dbp.read_text(encoding="utf-8")) if dbp.exists() else {}
            r["_reply"] = rp.read_text(encoding="utf-8") if rp.exists() else ""
        return s

    sa = _load_summary(dir_a)
    sb = _load_summary(dir_b)

    # Ensure standard is left, planner is right
    if sa.get("pipeline") == "planner":
        sa, sb = sb, sa
        dir_a, dir_b = dir_b, dir_a

    # Index by test ID
    ra_by_id = {r["id"]: r for r in sa["results"]}
    rb_by_id = {r["id"]: r for r in sb["results"]}
    all_ids  = [r["id"] for r in sa["results"]]  # preserve order

    def _score_bar(n_pass: int, n_total: int, pass_rate: float) -> str:
        col = "#10b981" if pass_rate >= 80 else ("#f59e0b" if pass_rate >= 50 else "#ef4444")
        return (
            f'<div style="font-size:28px;font-weight:800;color:#f8fafc;line-height:1">'
            f'{n_pass}/{n_total}<small style="font-size:14px;color:#94a3b8;font-weight:400;margin-left:6px">({pass_rate:.1f}%)</small></div>'
            f'<div style="height:5px;background:#334155;border-radius:4px;margin-top:8px;max-width:220px">'
            f'<div style="height:100%;border-radius:4px;background:{col};width:{int(pass_rate)}%"></div></div>'
        )

    # Overview pill strips
    def _pills(summary: dict) -> str:
        out = ""
        for r in summary["results"]:
            col = "#10b981" if r["passed"] else "#ef4444"
            out += (
                f'<span title="{_h(r["name"])}" style="display:inline-block;background:{col};color:#fff;'
                f'border-radius:4px;padding:2px 7px;font-size:11px;font-weight:700;'
                f'font-family:monospace;margin:2px;cursor:default">{_h(r["id"])}</span>'
            )
        return out

    # Build rows
    rows_html = ""
    for tid in all_ids:
        ra = ra_by_id.get(tid, {})
        rb = rb_by_id.get(tid, {})
        name  = ra.get("name") or rb.get("name") or tid
        query = ra.get("query") or rb.get("query") or ""

        pa = ra.get("passed", False)
        pb = rb.get("passed", False)
        both_pass  = pa and pb
        both_fail  = not pa and not pb
        row_accent = "#10b981" if both_pass else ("#f59e0b" if (pa or pb) else "#ef4444")

        col_a = _render_test_col(ra) if ra else '<span style="color:#94a3b8;font-size:12px">not run</span>'
        col_b = _render_test_col(rb) if rb else '<span style="color:#94a3b8;font-size:12px">not run</span>'

        debug_rows_a = _render_debug_rows(ra.get("debug_results", []))
        debug_rows_b = _render_debug_rows(rb.get("debug_results", []))

        rows_html += f'''
<div style="border:1px solid #e2e8f0;border-left:4px solid {row_accent};
    background:#fff;border-radius:8px;padding:14px 16px;margin:10px 0;
    box-shadow:0 1px 3px rgba(0,0,0,.06)">
  <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:8px">
    <span style="font-family:monospace;font-size:12px;font-weight:700;color:#334155">{_h(tid)}</span>
    <span style="font-size:14px;font-weight:600;color:#1e293b;flex:1">{_h(name)}</span>
  </div>
  <div style="font-size:12px;color:#475569;background:#f1f5f9;padding:5px 10px;
      border-radius:4px;margin-bottom:10px;font-style:italic">"{_h(query)}"</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div style="border:1px solid #e2e8f0;border-radius:6px;padding:10px 12px;background:#fafafa">
      <div style="font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;
          letter-spacing:.06em;margin-bottom:8px">Standard</div>
      {col_a}
    </div>
    <div style="border:1px solid #e2e8f0;border-radius:6px;padding:10px 12px;background:#fafafa">
      <div style="font-size:10px;font-weight:700;color:#ec4899;text-transform:uppercase;
          letter-spacing:.06em;margin-bottom:8px">Planner</div>
      {col_b}
    </div>
  </div>
  {(f'<div style="margin-top:10px"><div style="font-size:11px;font-weight:700;color:#64748b;margin-bottom:4px">Standard debug sub-tests</div>{debug_rows_a}</div>' if debug_rows_a else "")}
  {(f'<div style="margin-top:10px"><div style="font-size:11px;font-weight:700;color:#ec4899;margin-bottom:4px">Planner debug sub-tests</div>{debug_rows_b}</div>' if debug_rows_b else "")}
</div>'''

    ts_a = sa.get("timestamp", "")
    ts_b = sb.get("timestamp", "")
    mode = sa.get("mode") or sb.get("mode") or "mixed"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NExtSEEK Smart Test — Combined {_h(ts_a)} / {_h(ts_b)}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; background: #f1f5f9; color: #1e293b;
}}
.header {{
  background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
  color: #f8fafc; padding: 24px 32px;
  display: grid; grid-template-columns: 1fr 1fr; gap: 24px;
}}
.header h1 {{ margin: 0 0 4px; font-size: 22px; font-weight: 800;
              letter-spacing: -.3px; grid-column: 1 / -1; }}
.header .meta {{ font-size: 13px; color: #94a3b8; grid-column: 1 / -1; margin-bottom: 6px; }}
.score-block h2 {{ margin: 0 0 6px; font-size: 13px; font-weight: 700;
                   text-transform: uppercase; letter-spacing: .06em; color: #94a3b8; }}
.container {{ max-width: 1100px; margin: 24px auto; padding: 0 16px 40px; }}
.overview {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }}
.overview-card {{ background: #fff; border-radius: 8px; padding: 12px 16px;
                  box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
.overview-card h3 {{ margin: 0 0 8px; font-size: 11px; color: #64748b; font-weight: 700;
                     text-transform: uppercase; letter-spacing: .06em; }}
details > summary {{ list-style: none; }}
details > summary::-webkit-details-marker {{ display: none; }}
details > summary::before {{ content: "▸ "; font-size: 10px; }}
details[open] > summary::before {{ content: "▾ "; }}
@media (max-width: 700px) {{
  .header {{ grid-template-columns: 1fr; }}
  .overview, div[style*="grid-template-columns:1fr 1fr"] {{ grid-template-columns: 1fr !important; }}
}}
</style>
</head>
<body>
<div class="header">
  <h1>NExtSEEK Smart Test — Combined Report</h1>
  <div class="meta">mode: <b>{_h(mode)}</b> &nbsp;·&nbsp; standard: {_h(ts_a)} &nbsp;·&nbsp; planner: {_h(ts_b)}</div>
  <div class="score-block">
    <h2>Standard</h2>
    {_score_bar(sa["passed"], sa["total"], sa["pass_rate"])}
  </div>
  <div class="score-block">
    <h2>Planner</h2>
    {_score_bar(sb["passed"], sb["total"], sb["pass_rate"])}
  </div>
</div>
<div class="container">
  <div class="overview">
    <div class="overview-card">
      <h3>Standard Overview</h3>
      {_pills(sa)}
    </div>
    <div class="overview-card">
      <h3>Planner Overview</h3>
      {_pills(sb)}
    </div>
  </div>
  {rows_html}
</div>
</body>
</html>"""

    # Write to shared parent, or cwd if different parents
    parent_a, parent_b = dir_a.parent, dir_b.parent
    out_parent = parent_a if parent_a == parent_b else Path(".")
    out_path = out_parent / f"combined_{ts_a}_{ts_b}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Interactive report picker
# ---------------------------------------------------------------------------

def _interactive_report() -> int:
    """
    List smart_test_* dirs in outputs/, let the user pick one or two, then
    generate the appropriate HTML report.
    """
    outputs = Path("outputs")
    dirs = sorted(
        [d for d in outputs.iterdir() if d.is_dir() and d.name.startswith("smart_test_")],
        key=lambda d: d.name,
    )

    if not dirs:
        print("No smart_test_* directories found in outputs/")
        return 1

    print("\nAvailable smart test output directories:\n")
    for i, d in enumerate(dirs, 1):
        # Show a brief summary if summary.json exists
        summary_path = d / "summary.json"
        tag = ""
        if summary_path.exists():
            try:
                s = json.loads(summary_path.read_text(encoding="utf-8"))
                tag = f"  [{s.get('pipeline','?')}  {s.get('passed',0)}/{s.get('total',0)} passed]"
            except Exception:
                pass
        print(f"  {i:>2}.  {d.name}{tag}")

    print()
    print("Enter one number for a single report, or two numbers (e.g. '1 3') for a combined report.")
    print("Ctrl-C to cancel.\n")

    try:
        raw = input("> ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 0

    parts = raw.split()
    if len(parts) == 1:
        try:
            idx = int(parts[0]) - 1
            chosen = dirs[idx]
        except (ValueError, IndexError):
            print(f"Invalid selection: {raw!r}")
            return 1
        report_path = generate_html_report(chosen)
        print(f"report written → {report_path}")
        return 0
    elif len(parts) == 2:
        try:
            a, b = dirs[int(parts[0]) - 1], dirs[int(parts[1]) - 1]
        except (ValueError, IndexError):
            print(f"Invalid selection: {raw!r}")
            return 1
        report_path = generate_combined_html_report(a, b)
        print(f"combined report written → {report_path}")
        return 0
    else:
        print(f"Enter one or two numbers, got: {raw!r}")
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _list_tests(full_test: bool = False) -> None:
    tests = load_tests(full_test=full_test)
    if full_test:
        n_with_criteria = sum(1 for t in tests if t.get("pass_criteria"))
        print(f"\nFull test suite  ({len(tests)} questions, {n_with_criteria} with pass criteria)")
        print(f"Source: {_GT_PATH.name} → full_test\n")
        for t in tests:
            nc = len(t.get("pass_criteria", []))
            crit_tag = f" [{nc} criteria]" if nc else " [no criteria]"
            print(f"  {t['id']:30s}{crit_tag}  {t['query'][:60]}")
            if t.get("notes"):
                print(f"  {'':30s}           ↳ {t['notes'][:60]}")
        print()
    else:
        print(f"\nSmart test suite  ({len(tests)} top-level tests)")
        print(f"Source: {_GT_PATH.name} → smart_test\n")
        for t in tests:
            has_planner = bool(t.get("planner_pass_criteria"))
            planner_tag = " [planner_criteria ✓]" if has_planner else ""
            session_tag = (
                f" [session={t['session_mode']}]"
                if t.get("session_mode") and t.get("session_mode") != "fresh"
                else ""
            )
            print(f"  {t['id']:5s}  [{t.get('path',''):30s}]  {t['name']}{planner_tag}{session_tag}")
            if t.get("turns"):
                print(f"          Turns ({len(t['turns'])}):")
                for tn in t["turns"]:
                    label = tn.get("label", "?")
                    q = (tn.get("query") or "")[:60]
                    n_crit = len(tn.get("pass_criteria") or [])
                    print(f"            • {label:30s} {q!r}  ({n_crit} criteria)")
            else:
                print(f"          Query: {t.get('query','')[:70]}")
                print(f"          Pass on: {', '.join(c['field'] for c in t.get('pass_criteria', []))}")
                for dq in t.get("debug_queries", []):
                    print(f"          ↳ {dq['id']:6s}  {dq['name']}")
            print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smart diagnostic test suite for chat_nextseek.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Running tests:\n"
            "  uv run smart_test.py                      all tests, mixed profile\n"
            "  uv run smart_test.py -m gcp              GCP profile\n"
            "  uv run smart_test.py -p                  planner pipeline (run_query_plan)\n"
            "  uv run smart_test.py --both              standard then planner (3-min pause)\n"
            "  uv run smart_test.py -m aws:opus -p      Opus + planner\n"
            "  uv run smart_test.py --only T1,T5,T9    subset by ID\n"
            "  uv run smart_test.py --list              list tests and exit\n"
            "\n"
            "Regenerating reports:\n"
            "  uv run smart_test.py -i                          interactive dir picker\n"
            "  uv run smart_test.py --report <dir>              regenerate report.html\n"
            "  uv run smart_test.py --report-combined <a> <b>   combined report from two runs\n"
        ),
    )
    parser.add_argument(
        "-m", "--mode",
        choices=[
            "mixed", "oai", "gcp", "anth",
            "aws:son", "aws:opus", "aws:ds", "aws:qwen-nxt", "aws:glm",
        ],
        default=None,
        help="LLM backend profile (default: mixed).",
    )
    parser.add_argument(
        "-p", "--planner",
        action="store_true",
        default=False,
        help="Use run_query_plan (planner pipeline) for all tests; evaluates planner_pass_criteria.",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        metavar="T1,T2,...",
        help="Comma-separated list of test IDs to run (e.g. T1,T5,T9).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all tests and exit.",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        metavar="OUT_DIR",
        help="Re-generate report.html from an existing output directory and exit.",
    )
    parser.add_argument(
        "--report-combined",
        type=str,
        nargs=2,
        default=None,
        metavar=("DIR_A", "DIR_B"),
        help="Generate a side-by-side combined report from two output directories and exit.",
    )
    parser.add_argument(
        "--both",
        action="store_true",
        default=False,
        help="Run standard pipeline first, wait 3 minutes, then run planner pipeline.",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        default=False,
        help=(
            "Interactively pick output dir(s) from outputs/ for report generation. "
            "Enter one number for a single report, or two (e.g. '1 3') for a combined report."
        ),
    )
    parser.add_argument(
        "-ft", "--full-test",
        action="store_true",
        default=False,
        dest="full_test",
        help=(
            "Run the full regression test set (testing.json → full_test, 103 questions). "
            "No pass criteria — PASS = completed without exception. "
            "Same output layout as smart_test."
        ),
    )
    args = parser.parse_args(argv)

    if args.list:
        _list_tests(full_test=args.full_test)
        return 0

    if args.interactive:
        return _interactive_report()

    if args.report:
        report_path = generate_html_report(Path(args.report))
        print(f"report written → {report_path}")
        return 0

    if args.report_combined:
        report_path = generate_combined_html_report(
            Path(args.report_combined[0]), Path(args.report_combined[1])
        )
        print(f"combined report written → {report_path}")
        return 0

    only = [x.strip() for x in args.only.split(",")] if args.only else None

    suite_label = "FULL TEST" if args.full_test else "SMART TEST"

    if args.both:
        print("\n" + "=" * 60)
        print(f"  {suite_label} — standard mode (run_query)")
        print("=" * 60)
        rc_std = run_smart_suite(mode=args.mode, only=only, use_planner=False, full_test=args.full_test)
        print("\n" + "=" * 60)
        print(f"  Standard run finished at {datetime.now().strftime('%H:%M:%S')}.")
        print("  Waiting 3 minutes before planner run…")
        print("=" * 60)
        time.sleep(180)
        print("\n" + "=" * 60)
        print(f"  {suite_label} — planner mode (run_query_plan)")
        print("=" * 60)
        rc_plan = run_smart_suite(mode=args.mode, only=only, use_planner=True, full_test=args.full_test)
        return rc_std if rc_std != 0 else rc_plan

    return run_smart_suite(mode=args.mode, only=only, use_planner=args.planner, full_test=args.full_test)


if __name__ == "__main__":
    raise SystemExit(main())
