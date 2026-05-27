"""Manifest tests."""
import json
from pathlib import Path

import pytest

from e2e.manifest import (
    Manifest, ManifestEntry, write_manifest, load_manifest, filter_for_rerun
)


def _make_entry(vid: str, status: str = "passed") -> ManifestEntry:
    return ManifestEntry(id=vid, family=vid.split(".")[0], status=status, elapsed_s=1.0)


def test_round_trip(tmp_path: Path):
    m = Manifest(
        started_at="2026-05-20T12:00:00+00:00",
        ended_at="2026-05-20T12:10:00+00:00",
        ratio=0.33, seed=42, profile="mixed",
        variants=[_make_entry("adv.basic"), _make_entry("graph.count", "failed")],
    )
    p = tmp_path / "manifest.json"
    write_manifest(m, p)
    loaded = load_manifest(p)
    assert loaded.seed == 42
    assert len(loaded.variants) == 2
    assert loaded.variants[1].status == "failed"


def test_filter_for_rerun_all(tmp_path: Path):
    m = Manifest(
        started_at="x", ended_at="x", ratio=1.0, seed=1, profile="mixed",
        variants=[_make_entry("adv.basic"), _make_entry("graph.count", "failed")],
    )
    ids = filter_for_rerun(m, failed_only=False)
    assert ids == ["adv.basic", "graph.count"]


def test_filter_for_rerun_failed_only(tmp_path: Path):
    m = Manifest(
        started_at="x", ended_at="x", ratio=1.0, seed=1, profile="mixed",
        variants=[_make_entry("adv.basic"), _make_entry("graph.count", "failed"),
                  _make_entry("p.tower", "skipped")],
    )
    ids = filter_for_rerun(m, failed_only=True)
    assert ids == ["graph.count"]


def test_failed_only_excludes_skipped_and_passed(tmp_path: Path):
    """filter_for_rerun(failed_only=True) keeps only entries with status=='failed'."""
    m = Manifest(
        started_at="x", ended_at="x", ratio=1.0, seed=1, profile="mixed",
        variants=[
            _make_entry("adv.a", "passed"),
            _make_entry("adv.b", "failed"),
            _make_entry("adv.c", "skipped"),
            _make_entry("adv.d", "failed"),
        ],
    )
    ids = filter_for_rerun(m, failed_only=True)
    assert ids == ["adv.b", "adv.d"]


def test_rerun_all_excludes_skipped(tmp_path: Path):
    """filter_for_rerun(failed_only=False) keeps passed+failed but drops skipped.

    Rationale: a 'skipped' variant had unsatisfied requires_env; replaying it
    without setting that env would just produce another skip. The user opts
    back in by setting the env and re-running normally.
    """
    m = Manifest(
        started_at="x", ended_at="x", ratio=1.0, seed=1, profile="mixed",
        variants=[
            _make_entry("adv.a", "passed"),
            _make_entry("adv.b", "failed"),
            _make_entry("adv.c", "skipped"),
        ],
    )
    ids = filter_for_rerun(m, failed_only=False)
    assert ids == ["adv.a", "adv.b"]


def test_manifest_entry_rejects_unknown_status():
    with pytest.raises(ValueError):
        ManifestEntry(id="x", family="x", status="weird")


def test_write_manifest_emits_indented_json(tmp_path: Path):
    """Manifest on disk is indented JSON so humans can diff it."""
    m = Manifest(
        started_at="x", ended_at="x", ratio=0.5, seed=7, profile="mixed",
        variants=[_make_entry("a.b")],
    )
    p = tmp_path / "manifest.json"
    write_manifest(m, p)
    text = p.read_text()
    assert "\n  " in text  # indented
    payload = json.loads(text)
    assert payload["seed"] == 7
