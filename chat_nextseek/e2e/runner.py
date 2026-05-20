"""Per-variant runner: build session, iterate turns, evaluate criteria, persist artifacts."""
from __future__ import annotations

import json
import shutil
import time
import traceback
from pathlib import Path
from typing import Any

from chat_nextseek.orchestrator import run_query
from chat_nextseek.session import SQLiteSessionState

from e2e.catalog import Variant
from e2e.criteria import check_pass


def _make_session(config, user_id: str):
    """Build a fresh SQLiteSessionState. Pulled out so tests can stub it."""
    return SQLiteSessionState(config.SESSION_DB_PATH, user_id)


def _reset_logging(session) -> None:
    """Clear per-run logging state so each turn writes to its own run_root_dir."""
    for key in (
        "run_root_dir", "log_dir", "console_log_path",
        "chat_log_path", "api_log_path", "prompts_log_path",
    ):
        try:
            session[key] = None
        except Exception:
            pass
    try:
        session["config_snapshot_logged"] = False
    except Exception:
        pass


def _capture_run_root(session, dest_dir: Path) -> Path | None:
    """Move the orchestrator's per-query run dir into dest_dir; return new path."""
    run_root = session.get("run_root_dir") if hasattr(session, "get") else None
    if not run_root:
        return None
    src = Path(run_root)
    if not src.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))
    return dest


def run_variant(
    variant: Variant,
    config: Any,
    out_dir: Path,
    *,
    pace_seconds: int = 15,
) -> dict:
    """Execute one variant: fresh session, run turns in order, eval criteria.

    Returns a dict suitable for ManifestEntry construction:
      {id, family, status: passed|failed|skipped, elapsed_s, failed_criteria, turn_results}
    """
    variant_dir = out_dir / variant.id
    variant_dir.mkdir(parents=True, exist_ok=True)

    session = _make_session(config, f"e2e-{variant.id.replace('.', '-')}")

    turn_results: list[dict] = []
    overall_passed = True
    overall_failed_criteria: list[str] = []
    elapsed_total = 0.0

    for i, turn in enumerate(variant.turns):
        turn_dir = variant_dir / "turns" / turn.label
        turn_dir.mkdir(parents=True, exist_ok=True)

        # Pace between turns (skip before the very first turn)
        if pace_seconds > 0 and i > 0:
            time.sleep(pace_seconds)

        _reset_logging(session)

        t0 = time.perf_counter()
        try:
            result = run_query(session, config, turn.query)
            elapsed = time.perf_counter() - t0
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            tb = traceback.format_exc()
            (turn_dir / "query.txt").write_text(turn.query, encoding="utf-8")
            (turn_dir / "error.txt").write_text(tb, encoding="utf-8")
            _capture_run_root(session, turn_dir)
            turn_results.append({
                "label": turn.label, "passed": False, "elapsed_s": round(elapsed, 2),
                "error": str(exc), "criteria_results": [],
            })
            overall_passed = False
            overall_failed_criteria.append(f"{turn.label}: EXCEPTION {exc}")
            elapsed_total += elapsed
            continue

        elapsed_total += elapsed

        (turn_dir / "query.txt").write_text(turn.query, encoding="utf-8")
        (turn_dir / "reply.txt").write_text(result.get("reply", ""), encoding="utf-8")
        (turn_dir / "debug.json").write_text(
            json.dumps(result.get("debug", {}), indent=2, default=str), encoding="utf-8"
        )
        run_root = _capture_run_root(session, turn_dir)

        passed, crit_results = check_pass(
            result.get("debug", {}),
            turn.pass_criteria,
            session=session,
            last_reply=result.get("reply", ""),
            run_root=run_root,
        )
        turn_results.append({
            "label": turn.label, "passed": passed, "elapsed_s": round(elapsed, 2),
            "criteria_results": crit_results,
        })
        if not passed:
            overall_passed = False
            for cr in crit_results:
                if not cr.get("passed"):
                    overall_failed_criteria.append(f"{turn.label}: {cr.get('field')}")

    return {
        "id": variant.id,
        "family": variant.family,
        "status": "passed" if overall_passed else "failed",
        "elapsed_s": round(elapsed_total, 2),
        "failed_criteria": overall_failed_criteria,
        "turn_results": turn_results,
    }
