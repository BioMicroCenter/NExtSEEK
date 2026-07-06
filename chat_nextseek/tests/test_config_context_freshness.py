"""Freshness-gate regression tests for ChatConfig context-file refresh.

Root cause these pin (2026-07-05 T5.5 shakeout, BUG-2): the old gate used the
context files' own mtime as the "cache is fresh today" signal. A stale
`projects_db.json` baked into the image on the SAME day the container runs has
a today mtime, so the gate treated it as fresh and SKIPPED the DB refresh —
leaving the deployed instance resolving projects from stale baked data.

The fix tracks "a DB refresh happened today" with a dedicated marker file that
ONLY a successful refresh writes (never baked into the image), so a freshly
baked context file can no longer suppress the first-load refresh, while the
once-per-day caching intent is preserved.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from chat_nextseek.config import ChatConfig


_TARGET_FILES = [
    "sampletypes_db.json",
    "min_sampletypes_db.json",
    "assays_db.json",
    "min_assays_db.json",
    "projects_db.json",
]


def _bare_config(context_dir: Path) -> ChatConfig:
    cfg = ChatConfig.__new__(ChatConfig)  # bypass __init__ (no Django/DB)
    cfg.CONTEXT_DIR = str(context_dir)
    return cfg


def _write_all_targets_today(context_dir: Path) -> None:
    """Simulate a freshly-baked image: every context file present with a
    today mtime, but NO refresh marker (nothing has actually hit the DB)."""
    for name in _TARGET_FILES:
        (context_dir / name).write_text("[]", encoding="utf-8")


def _install_fetch_spy(cfg: ChatConfig, *, succeeds: bool):
    calls = {"n": 0}

    def _fake_fetch(env: str = "prod"):
        calls["n"] += 1
        if not succeeds:
            return {}  # DB unreachable / no export
        # A real refresh writes the files; emulate that so target files exist.
        for name in _TARGET_FILES:
            (Path(cfg.CONTEXT_DIR) / name).write_text("[]", encoding="utf-8")
        return {"projects_full": Path(cfg.CONTEXT_DIR) / "projects_db.json"}

    cfg._fetch_context_files_from_db = _fake_fetch  # type: ignore[assignment]
    return calls


def test_baked_today_files_do_not_suppress_first_refresh(tmp_path):
    """The bug: baked files with today mtime + no marker MUST still refresh."""
    _write_all_targets_today(tmp_path)
    cfg = _bare_config(tmp_path)
    calls = _install_fetch_spy(cfg, succeeds=True)

    cfg._ensure_context_files()

    assert calls["n"] == 1, "refresh must run on first load despite today-mtime baked files"


def test_second_same_day_load_skips_refresh(tmp_path):
    """Once a refresh succeeds today, a second load the same day skips it."""
    _write_all_targets_today(tmp_path)
    cfg = _bare_config(tmp_path)
    calls = _install_fetch_spy(cfg, succeeds=True)

    cfg._ensure_context_files()
    cfg._ensure_context_files()

    assert calls["n"] == 1, "daily caching intent must be preserved after a successful refresh"


def test_stale_marker_triggers_refresh_next_day(tmp_path):
    """A marker from a prior day must not count as fresh."""
    _write_all_targets_today(tmp_path)
    cfg = _bare_config(tmp_path)
    calls = _install_fetch_spy(cfg, succeeds=True)

    cfg._ensure_context_files()  # writes today marker
    marker = Path(cfg.CONTEXT_DIR) / cfg._REFRESH_MARKER_NAME
    yesterday = time.time() - 24 * 3600
    os.utime(marker, (yesterday, yesterday))

    cfg._ensure_context_files()
    assert calls["n"] == 2, "a prior-day marker must trigger a fresh refresh"


def test_missing_target_file_triggers_refresh(tmp_path):
    """A missing context file forces a refresh even if the marker is today."""
    _write_all_targets_today(tmp_path)
    cfg = _bare_config(tmp_path)
    calls = _install_fetch_spy(cfg, succeeds=True)

    cfg._ensure_context_files()  # marker now today
    (Path(cfg.CONTEXT_DIR) / "projects_db.json").unlink()

    cfg._ensure_context_files()
    assert calls["n"] == 2, "a missing target file must force a refresh"


def test_failed_refresh_does_not_write_marker(tmp_path):
    """DB unreachable: no marker written, so the next load retries (never
    caches a failure as 'fresh for today')."""
    _write_all_targets_today(tmp_path)
    cfg = _bare_config(tmp_path)
    calls = _install_fetch_spy(cfg, succeeds=False)

    cfg._ensure_context_files()
    cfg._ensure_context_files()

    marker = Path(cfg.CONTEXT_DIR) / cfg._REFRESH_MARKER_NAME
    assert not marker.exists(), "a failed refresh must not write the today marker"
    assert calls["n"] == 2, "a failed refresh must be retried on the next load"


def test_committed_baked_projects_db_resolves_published_data_to_1(tmp_path):
    """Option C: the baked projects_db.json shipped in the source tree must
    resolve 'Published Data' (and its aliases) to project_id 1 via the same
    _merge_project_name_to_id the config uses — guarding against a future
    stale-catalog regression (the BUG-2 symptom was 'Published Data' -> 6)."""
    import json

    baked = (
        Path(__file__).resolve().parents[1]
        / "src" / "chat_nextseek" / "context" / "projects_db.json"
    )
    projects = json.loads(baked.read_text(encoding="utf-8"))

    cfg = _bare_config(tmp_path)
    merged = cfg._merge_project_name_to_id({"PUBLISHED": 6}, projects)

    assert merged["PUBLISHED DATA"] == 1
    assert merged["PUBLISHED"] == 1  # stale hardcoded 6 must be overridden
    assert merged["PUB"] == 1
