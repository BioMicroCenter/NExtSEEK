#!/usr/bin/env python3
"""One-off live probe: forced-CC turn with 1c memory mount layered on 1b layout.

Run inside the nextseek container (uses host docker.sock + Bedrock proxy = real spend):

  docker exec -e PROBE_PROJECT_DIRNAME=personal-demo-demo nextseek \\
    sh -lc 'cd /app && uv run python nextseek_api/cc_assistant/evidence/run_1c_claude_md_live_probe.py'

Budget cap: $0.50 via --max-budget-usd passed through run_cc_turn.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from nextseek_api.cc_assistant import cc_config, cc_engine, router as cc_router
from nextseek_api.cc_assistant.cc_provision import build_user_dirs

USER_ID = os.environ.get("PROBE_USER_ID", "demo")
PROJECT_DIRNAME = os.environ.get("PROBE_PROJECT_DIRNAME", f"personal-{USER_ID}-{USER_ID}")
CC_STATE_KEY = os.environ.get("PROBE_CC_STATE_KEY", "1c-claude-md-probe")
MAX_BUDGET = float(os.environ.get("PROBE_MAX_BUDGET_USD", "0.50"))

QUERY = """Configuration probe — reply with ONLY a JSON object (no markdown fences):

{"saw_write_safety": <bool>, "saw_user_memory_marker": <bool>, "notes": "<one sentence>"}

Definitions:
- saw_write_safety: true iff your loaded project/user instructions include the exact phrase "Write-safety on NExtSEEK"
- saw_user_memory_marker: true iff your loaded instructions include the exact string "USER_MEMORY_MARKER=1C_PROBE_ALPHA"

You MAY use the Read tool to inspect /home/user/CLAUDE.md and /home/user/.claude/CLAUDE.md before answering. Be literal — do not guess."""


def main() -> int:
    paths = cc_config.CCPaths.from_env()
    dirs = build_user_dirs(paths, PROJECT_DIRNAME, USER_ID, session_id=CC_STATE_KEY)
    probe_host = os.environ.get("PROBE_MEMORY_HOST")
    if not probe_host:
        probe_host = str(Path(dirs.memory_mnt) / "CLAUDE.md").replace(
            paths.user_root_mount.rstrip("/"), paths.host_user_root.rstrip("/"), 1)
    probe_container = str(Path(dirs.memory_mnt) / "CLAUDE.md")
    if Path(probe_container).is_file():
        pass
    elif Path(probe_host).is_file():
        pass
    else:
        print(
            f"ERROR: probe memory missing at container={probe_container} host={probe_host}",
            file=sys.stderr,
        )
        return 2

    orig_build = cc_engine._build_volumes

    def _build_with_probe_memory(**kwargs):
        vols = orig_build(**kwargs)
        vols[probe_host] = {"bind": "/home/user/.claude/CLAUDE.md", "mode": "ro"}
        return vols

    cc_engine._build_volumes = _build_with_probe_memory  # type: ignore[method-assign]

    run_id = f"probe-{uuid.uuid4().hex[:8]}"
    events: list[tuple[str, dict]] = []

    def send_event(name: str, data: dict) -> None:
        events.append((name, data))
        print(f"EVENT {name}: {json.dumps(data)[:800]}")

    ok, detail = cc_engine.cc_runner_available()
    if not ok:
        print(f"ERROR: CC runner unavailable: {detail}", file=sys.stderr)
        return 3

    api_user = os.environ.get("PROBE_API_USER", "demo")
    api_pass = os.environ.get("PROBE_API_PASS", "demopassword")

    print(
        f"Starting live probe run_id={run_id} project_dirname={PROJECT_DIRNAME} "
        f"cc_state_key={CC_STATE_KEY}"
    )
    print(f"Memory bind: {probe_host} -> /home/user/.claude/CLAUDE.md:ro")

    model_id = cc_router._resolve_cc_model_id()
    print(f"Model: {model_id}")

    cc_engine.run_cc_turn(
        query=QUERY,
        model_id=model_id,
        send_event=send_event,
        user_id=USER_ID,
        project_dirname=PROJECT_DIRNAME,
        run_id=run_id,
        paths=paths,
        session_id=None,
        cc_state_key=CC_STATE_KEY,
        api_user=api_user,
        api_pass=api_pass,
        max_budget_usd=MAX_BUDGET,
    )

    terminal = next(((n, d) for n, d in reversed(events) if n in ("query_complete", "query_error")), None)
    if not terminal:
        print("ERROR: no terminal event", file=sys.stderr)
        return 4

    name, data = terminal
    print(f"\nTERMINAL {name}:")
    print(json.dumps(data, indent=2)[:4000])

    if name == "query_error":
        return 5

    reply = data.get("reply", "")
    try:
        # Extract JSON from reply (agent may wrap it)
        start = reply.find("{")
        end = reply.rfind("}") + 1
        parsed = json.loads(reply[start:end]) if start >= 0 and end > start else {}
    except json.JSONDecodeError:
        parsed = {}

    merge_ok = parsed.get("saw_write_safety") and parsed.get("saw_user_memory_marker")
    print(f"\nPARSED: {parsed}")
    print(f"VERDICT live-merge: {'CONFIRMED' if merge_ok else 'NOT CONFIRMED'}")
    return 0 if merge_ok else 6


if __name__ == "__main__":
    raise SystemExit(main())
