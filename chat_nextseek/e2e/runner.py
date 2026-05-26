"""Per-variant runner: build session, iterate turns, evaluate criteria, persist artifacts."""
from __future__ import annotations

import json
import random
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chat_nextseek.orchestrator import run_query
from chat_nextseek.session import SQLiteSessionState

from e2e.catalog import Catalog, Variant, load_catalog
from e2e.criteria import check_pass
from e2e.manifest import (
    Manifest,
    ManifestEntry,
    filter_for_rerun,
    load_manifest,
    write_manifest,
)
from e2e.report import generate_html_report
from e2e.sampler import sample


def _probe_ui_reachable(ui_url: str, *, api_user: str, api_pass: str, timeout_s: float = 5.0) -> bool:
    """HTTP HEAD against /nextseek_api/assistant/me/. Returns True iff 200 OK."""
    import requests  # noqa: PLC0415
    try:
        r = requests.head(
            f"{ui_url.rstrip('/')}/nextseek_api/assistant/me/",
            auth=(api_user, api_pass),
            timeout=timeout_s,
            allow_redirects=False,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


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
                    overall_failed_criteria.append(f"{turn.label}: {cr.get('field')} ({cr.get('op')}={cr.get('value')!r})")

    return {
        "id": variant.id,
        "family": variant.family,
        "status": "passed" if overall_passed else "failed",
        "elapsed_s": round(elapsed_total, 2),
        "failed_criteria": overall_failed_criteria,
        "turn_results": turn_results,
    }


# ── Suite-level orchestration ────────────────────────────────────────────


def run_main(
    catalog_path: Path,
    *,
    ratio: float = 0.33,
    seed: int | None = None,
    family: str | None = None,
    variant_id: str | None = None,
    rerun_manifest: Path | None = None,
    failed_only: bool = False,
    pace_seconds: int = 15,
    profile: str = "mixed",
    out_root: Path = Path("outputs"),
    skip_cli: bool = False,
    skip_playwright: bool = False,
    headed: bool = False,
    video: bool = False,
) -> int:
    """Top-level entry: build the run plan, execute, write manifest + report.

    Returns exit code 0 if every executed variant passes, 1 otherwise.
    """
    # Late import — ChatConfig is heavy and pulls in env / catalog parsing
    from chat_nextseek.config import ChatConfig  # noqa: PLC0415

    cat: Catalog = load_catalog(catalog_path)

    # ── Plan: which variants run this invocation ─────────────────────────
    if rerun_manifest is not None:
        prior = load_manifest(rerun_manifest)
        ids = set(filter_for_rerun(prior, failed_only=failed_only))
        plan = [v for fam in cat.families.values() for v in fam.variants if v.id in ids]
        seed = seed or prior.seed
        ratio = prior.ratio
    elif variant_id is not None:
        plan = [v for fam in cat.families.values() for v in fam.variants if v.id == variant_id]
        if not plan:
            print(f"[e2e] variant not found: {variant_id}")
            return 2
    elif family is not None:
        fam = cat.families.get(family)
        if fam is None:
            print(f"[e2e] family not found: {family}")
            return 2
        plan = list(fam.variants)
    else:
        actual_seed = seed if seed is not None else int(time.time())
        rng = random.Random(actual_seed)
        plan = sample(cat, ratio=ratio, rng=rng)
        seed = actual_seed

    if not plan:
        print("[e2e] no variants planned (nothing to do)")
        return 0

    ts = datetime.now().strftime("%y%m%d_%H%M%S")
    out_dir = out_root / f"e2e_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    print(f"[e2e] running {len(plan)} variants → {out_dir}")

    config = ChatConfig()
    entries: list[ManifestEntry] = []
    if not skip_cli:
        for v in plan:
            print(f"[e2e]   ▶ {v.id}")
            result = run_variant(v, config, out_dir, pace_seconds=pace_seconds)
            entry = ManifestEntry(
                id=result["id"], family=result["family"],
                status=result["status"], elapsed_s=result["elapsed_s"],
                failed_criteria=result.get("failed_criteria", []),
            )
            entries.append(entry)
            print(f"[e2e]   {entry.status.upper()}  {v.id}  ({entry.elapsed_s}s)")

    # ── Phase 2: Playwright dispatch on tagged variants ──────────────────
    if not skip_playwright:
        from e2e.playwright.runner import run_variant_browser  # noqa: PLC0415
        pw_plan = [v for v in plan if "playwright" in v.tags]
        if pw_plan:
            ui_url = getattr(config, "NEXTSEEK_UI_URL", None) or "http://localhost:8000"
            api_user = getattr(config, "API_USER", "demo")
            api_pass = getattr(config, "API_PASS", "demopassword")
            if _probe_ui_reachable(ui_url, api_user=api_user, api_pass=api_pass):
                pw_root = out_dir / "playwright"
                pw_root.mkdir(parents=True, exist_ok=True)
                for v in pw_plan:
                    print(f"[e2e]   ▶ {v.id}.browser")
                    pw_result = run_variant_browser(
                        v, config, pw_root / v.id,
                        pace_seconds=pace_seconds,
                        headed=headed,
                        video=video,
                    )
                    entry = ManifestEntry(
                        id=f"{pw_result['id']}.browser",
                        family=pw_result["family"],
                        status=pw_result["status"],
                        elapsed_s=pw_result["elapsed_s"],
                        failed_criteria=pw_result.get("failed_criteria", []),
                    )
                    entries.append(entry)
                    print(f"[e2e]   {entry.status.upper()}  {v.id}.browser  ({entry.elapsed_s}s)")
            else:
                print(f"[e2e] UI not reachable at {ui_url} — skipping {len(pw_plan)} browser variant(s)")
                for v in pw_plan:
                    entries.append(ManifestEntry(
                        id=f"{v.id}.browser", family=v.family,
                        status="skipped", elapsed_s=0.0,
                        reason=f"UI not reachable at {ui_url}",
                    ))

    ended = datetime.now(timezone.utc).isoformat()

    manifest = Manifest(
        started_at=started, ended_at=ended,
        ratio=ratio, seed=seed or 0, profile=profile,
        variants=entries,
    )
    manifest_path = out_dir / "manifest.json"
    write_manifest(manifest, manifest_path)

    report_path = generate_html_report(manifest, out_dir)

    n_pass = sum(1 for e in entries if e.status == "passed")
    n_fail = sum(1 for e in entries if e.status == "failed")
    n_total = len(entries)
    print(f"[e2e] {n_pass}/{n_total} passed — manifest={manifest_path}, report={report_path}")
    return 0 if n_fail == 0 else 1
