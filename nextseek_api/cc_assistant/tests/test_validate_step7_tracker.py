"""Hermetic tests for validate_step7_compose_deploy helpers that the ignored file normally covers."""
from __future__ import annotations

import json
from pathlib import Path

from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (
    Context,
    MBP_SNAPSHOT_BASENAME,
    _resolve_tracker_source,
    _tracker_step3_status,
    format_report,
    main,
)


def test_main_usage_and_format_report(capsys):
    assert main(["prog"]) == 2
    err = capsys.readouterr().err
    assert "usage:" in err
    text = format_report(True, [("a", True, "ok")])
    assert "ALL CHECKS PASSED" in text
    text = format_report(False, [("a", False, "bad")])
    assert "STEP 7 PREFLIGHT GATE FAILED" in text


def test_tracker_status_and_resolve_paths(tmp_path):
    assert _tracker_step3_status("nope") is None
    assert _tracker_step3_status({"steps": [{"id": "2", "status": "x"}]}) is None
    assert _tracker_step3_status({"steps": [{"id": "3", "status": "closed"}]}) == "closed"

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = Context(run_dir=run_dir, preflight={}, meta={}, repo_root=repo)
    path, tracker, detail = _resolve_tracker_source(ctx)
    assert path is None and "no integration_plan_path" in detail

    outside = tmp_path / "plan.json"
    outside.write_text("{")
    ctx = Context(
        run_dir=run_dir,
        preflight={"step3_deploy_gate": {"integration_plan_path": str(outside)}},
        meta={},
        repo_root=repo,
    )
    path, tracker, detail = _resolve_tracker_source(ctx)
    assert path is None and "unreadable" in detail

    outside.write_text(json.dumps({"steps": []}))
    path, tracker, detail = _resolve_tracker_source(ctx)
    assert path == outside and tracker is not None

    inside = run_dir / "random.json"
    inside.write_text("{}")
    ctx = Context(
        run_dir=run_dir,
        preflight={"step3_deploy_gate": {"integration_plan_path": str(inside)}},
        meta={"host_label": "dev-vm"},
        repo_root=repo,
    )
    path, tracker, detail = _resolve_tracker_source(ctx)
    assert path is None and "MBP exception" in detail

    snap = run_dir / MBP_SNAPSHOT_BASENAME
    snap.write_text("{}")
    ctx = Context(
        run_dir=run_dir,
        preflight={"step3_deploy_gate": {"integration_plan_path": str(inside)}},
        meta={"host_label": "mbp"},
        repo_root=repo,
    )
    path, tracker, detail = _resolve_tracker_source(ctx)
    assert path is None and "requires integration_plan_path" in detail

    ctx = Context(
        run_dir=run_dir,
        preflight={"step3_deploy_gate": {"integration_plan_path": str(snap)}},
        meta={"host_label": "MacBook Pro"},
        repo_root=repo,
    )
    path, tracker, detail = _resolve_tracker_source(ctx)
    assert path is None and "canonical_integration_plan_sha256" in detail

    import hashlib
    digest = hashlib.sha256(snap.read_bytes()).hexdigest()
    ctx = Context(
        run_dir=run_dir,
        preflight={"step3_deploy_gate": {
            "integration_plan_path": str(snap),
            "canonical_integration_plan_sha256": digest,
            "integration_plan_sha256": "dead",
        }},
        meta={"host_label": "mbp"},
        repo_root=repo,
    )
    path, tracker, detail = _resolve_tracker_source(ctx)
    assert path is None and "sha256 mismatch" in detail

    ctx = Context(
        run_dir=run_dir,
        preflight={"step3_deploy_gate": {
            "integration_plan_path": str(snap),
            "canonical_integration_plan_sha256": digest,
            "integration_plan_sha256": digest,
        }},
        meta={"host_label": "mbp"},
        repo_root=repo,
    )
    path, tracker, detail = _resolve_tracker_source(ctx)
    assert path == snap and tracker == {}
