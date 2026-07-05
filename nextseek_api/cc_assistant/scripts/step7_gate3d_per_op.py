#!/usr/bin/env python3
"""Gate 3D per-op forced-CC live harness (amendment 2026-07-05).

Nine FRESH-session forced-CC turns — one per bin op — proving the full flow
**user query -> CC route -> op -> answer**. Each op runs its own real CC turn
via the production ``cc_engine.run_cc_turn`` (fresh ``cc_state_key`` == cc_run_id,
no ``session_id`` / no ``--resume``), so every op carries a real Bedrock cost
from the CC result frame. Replaces the direct-exec ``CCCapabilityGateMatrix``,
which never routed through CC.

Run inside the live ``nextseek`` container (after sidecar deploy, proxy alias
healthy, ``STEP7_LLM_LEDGER=1``):

  docker exec -e SEEK_TEST_USER=demo -e SEEK_TEST_PASS=demopassword \\
    -e NEXTSEEK_CC_GATE_USER_ID=demo -e NEXTSEEK_CC_GATE_PROJECT=1-published-data \\
    -e NEXTSEEK_CC_MAX_BUDGET_USD=10 \\
    -e STEP7_PER_OP_BUNDLE_DIR=<bundle> \\
    [-e STEP7_PER_OP_ONLY=nextseek-report]  # smoke one op \\
    nextseek sh -lc 'cd /app && uv run python nextseek_api/cc_assistant/scripts/step7_gate3d_per_op.py'
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django  # noqa: E402

django.setup()

from nextseek_api.cc_assistant import (  # noqa: E402
    cc_config,
    cc_engine,
    cc_summary,
    cc_trace,
    step7_per_op_evidence as ev,
)
from nextseek_api.cc_assistant.tests.validate_cc_acceptance import OPUS  # noqa: E402

# Per-turn Bedrock budget cap — the ported/UI value (live NEXTSEEK_CC_MAX_BUDGET_USD).
# Passed to each run_cc_turn; the engine also emits --max-budget-usd from the env.
PER_TURN_BUDGET = float(os.environ.get("NEXTSEEK_CC_MAX_BUDGET_USD", "0.50"))
# Total-run guard: abort before starting an op if cumulative spend would exceed
# this. 8 ops x $0.50 = $4 worst case, inside the <$10 total E2E budget.
TOTAL_BUDGET_CAP = float(os.environ.get("STEP7_PER_OP_TOTAL_BUDGET_USD", "10"))
# Project hard turn cap (cc_engine._TIMEOUT_HARD_MAX). The outer thread join must
# not impose a bound different from the engine's own 180s watchdog.
TURN_TIMEOUT = 180

# The 8 LOCKED per-op E2E questions (user-approved 2026-07-05), verbatim from the
# canonical chat_nextseek/e2e/catalog.json (distinct from the SKILL.md doc
# examples). No "use the nextseek plugin" preamble — the per-op SKILL.md/CLAUDE.md
# instruct the agent; the query is the natural user request. One question per op.
OP_QUERIES: dict[str, str] = {
    "nextseek-entity-extract": "What monkeys exist in the database?",
    "nextseek-parse": "Find RNA samples with a RIN score greater than 7.",
    "nextseek-api-read": "Retrieve all samples associated with: NHP-220630FLY-5-PUB.",
    "nextseek-graph": "Find PBMC samples from the GBM study that also have sequencing data.",
    "nextseek-report": "How many samples were uploaded for IMPACT from 2023 to 2025?",
    "nextseek-generate-submission": (
        "Build me a GEO Submission for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB."
    ),
    "nextseek-api-write": "Update the scientist field on NHP-220630FLY-1-PUB to Jane Doe",
    "nextseek-plan": (
        "Find me D.SEQ samples for the SHA lab, then narrow those results to samples "
        "collected after 2023."
    ),
}


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _read_transcript_steps(cc_state_mnt: str | None, *, since: float):
    """Parse the fresh session's claude transcript from its cc-state store and
    return (steps, cc_session_id, raw_bytes). Empty if none found."""
    if not cc_state_mnt:
        return [], None, b""
    root = Path(cc_state_mnt) / "projects"
    jsonl = cc_engine._newest_jsonl_under(root, min_mtime=since - 1) if root.exists() else None
    if not jsonl:
        return [], None, b""
    raw = jsonl.read_bytes()
    parsed = cc_summary.parse_transcript(raw)
    trace = cc_trace.extract_trace(
        parsed, cc_session_id="", ts="", files_created=[], files_modified=[],
    )
    return trace.steps, (trace.cc_session_id or jsonl.stem), raw


def _run_one_op(op: str, *, bundle: Path, user_id: str, project: str,
                api_user: str, api_pass: str) -> ev.OpRow:
    query = OP_QUERIES[op]
    cc_run_id = uuid.uuid4().hex  # fresh, docker-name-safe
    cc_state_key = cc_run_id       # fresh session store; NO session_id -> no resume
    paths = cc_config.CCPaths.from_env()
    events: list = []
    errbox: dict = {}
    reply = ""
    cost = 0.0
    cc_session_id = None

    def _send(event: str, data: dict) -> None:
        events.append((event, dict(data)))

    since = time.time()

    def _target():
        try:
            cc_engine.run_cc_turn(
                query=query, model_id=OPUS, send_event=_send,
                user_id=user_id, project_dirname=project, run_id=cc_run_id,
                paths=paths, cc_state_key=cc_state_key, session_id=None,
                api_user=api_user, api_pass=api_pass, max_budget_usd=PER_TURN_BUDGET,
                turn_timeout=TURN_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            errbox["err"] = repr(exc)
            events.append(("query_error", {"error": repr(exc)}))

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    # The engine's own watchdog force-removes the container at TURN_TIMEOUT and
    # returns; a small grace lets that unwind emit its terminal event without
    # extending the actual turn budget past the 180s hard cap.
    t.join(timeout=TURN_TIMEOUT + 15)

    terminal = next(((e, d) for e, d in reversed(events)
                     if e in ("query_complete", "query_error")), None)
    is_error = terminal is None or terminal[0] == "query_error"
    if terminal is not None:
        _, data = terminal
        cost = float(data.get("total_cost_usd") or 0.0)
        reply = str(data.get("reply") or "")
        cc_session_id = data.get("cc_session_id")

    from nextseek_api.cc_assistant.cc_provision import build_user_dirs
    ud = build_user_dirs(paths, project, user_id, session_id=cc_state_key)
    steps, tx_session, raw = _read_transcript_steps(ud.cc_state_mnt, since=since)
    if not cc_session_id:
        cc_session_id = tx_session

    if raw:
        (bundle / f"transcript-{op}-{cc_run_id}.jsonl").write_bytes(raw)

    invocation = ev.extract_op_invocation(steps, op)
    row = ev.evaluate_op_row(
        op=op, cc_run_id=cc_run_id, cc_session_id=cc_session_id,
        is_error=is_error, cost_usd=cost, invocation=invocation,
        answer_excerpt=reply, transport=ev_transport(op),
    )
    if errbox:
        row.problems.append(f"raised: {errbox['err']}")
    _write_json(bundle / f"op-{op}.json", row.to_dict())
    return row


def ev_transport(op: str) -> str:
    return "viewset" if op in ("nextseek-query", "nextseek-plan") else "sidecar"


def main() -> int:
    user_id = os.environ.get("NEXTSEEK_CC_GATE_USER_ID", "demo")
    project = os.environ.get("NEXTSEEK_CC_GATE_PROJECT", "1-published-data")
    api_user = os.environ.get("SEEK_TEST_USER", "demo")
    api_pass = os.environ.get("SEEK_TEST_PASS", "demopassword")
    bundle = Path(os.environ.get("STEP7_PER_OP_BUNDLE_DIR")
                  or (_REPO_ROOT / "nextseek_api" / "cc_assistant" / "tests"
                      / "acceptance_evidence" / "step7" / f"per-op-{uuid.uuid4().hex[:8]}"))
    bundle.mkdir(parents=True, exist_ok=True)

    only = os.environ.get("STEP7_PER_OP_ONLY", "").strip()
    ops = [only] if only else list(ev.BIN_OPS)
    for o in ops:
        if o not in ev.BIN_OPS:
            raise SystemExit(f"unknown op: {o}")

    rows: list[ev.OpRow] = []
    cumulative = 0.0
    aborted = False
    for op in ops:
        # Pre-emptive total-budget guard: never START an op that could push the
        # cumulative spend past the <$10 total cap.
        if cumulative + PER_TURN_BUDGET > TOTAL_BUDGET_CAP:
            print(f"[per-op] TOTAL BUDGET GUARD: cum ${cumulative:.4f} + per-turn "
                  f"${PER_TURN_BUDGET} would exceed ${TOTAL_BUDGET_CAP} — stop before {op}", flush=True)
            aborted = True
            break
        print(f"[per-op] === {op} ===", flush=True)
        row = _run_one_op(op, bundle=bundle, user_id=user_id, project=project,
                          api_user=api_user, api_pass=api_pass)
        cumulative += row.cost_usd
        status = "PASS" if not row.problems else f"FAIL {row.problems}"
        print(f"[per-op] {op}: cost=${row.cost_usd:.4f} invoked={row.invoked} "
              f"cum=${cumulative:.4f} -> {status}", flush=True)
        rows.append(row)

    matrix = {r.op: r.to_dict() for r in rows}
    _write_json(bundle / "plugin_ops_matrix.json", matrix)
    fresh_violations = ev.assert_fresh_sessions(rows)
    summary = {
        "ops_run": [r.op for r in rows],
        "all_pass": (all(not r.problems for r in rows) and not fresh_violations
                     and len(rows) == len(ops) and not aborted),
        "aborted_on_budget": aborted,
        "fresh_session_violations": fresh_violations,
        "total_cost_usd": cumulative,
        "per_turn_budget_usd": PER_TURN_BUDGET,
        "total_budget_cap_usd": TOTAL_BUDGET_CAP,
        "failing_ops": {r.op: r.problems for r in rows if r.problems},
    }
    _write_json(bundle / "per_op_summary.json", summary)
    print(f"[per-op] SUMMARY all_pass={summary['all_pass']} "
          f"total=${cumulative:.4f} failing={list(summary['failing_ops'])}", flush=True)
    print(f"[per-op] bundle={bundle}", flush=True)
    return 0 if summary["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
