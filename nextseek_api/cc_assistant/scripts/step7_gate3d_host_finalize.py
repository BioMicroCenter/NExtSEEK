#!/usr/bin/env python3
"""Gate 3D host-side finalize — preflight + validate (option C split).

The live ``nextseek`` image has no ``.git``; preflight git probes and
``validate_run`` transcript re-verification run on the host worktree after
the live bundle is copied out of the container::

  docker cp nextseek:/app/nextseek_api/cc_assistant/tests/acceptance_evidence/step7/<run_id> \\
    <worktree>/nextseek_api/cc_assistant/tests/acceptance_evidence/step7/

  cd <worktree> && uv run python nextseek_api/cc_assistant/scripts/step7_gate3d_host_finalize.py \\
    nextseek_api/cc_assistant/tests/acceptance_evidence/step7/<run_id>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nextseek_api.cc_assistant.tests import step7_preflight_collector as preflight_mod  # noqa: E402
from nextseek_api.cc_assistant.tests.validate_step7_compose_deploy import (  # noqa: E402
    format_report,
    validate_run,
)

PORT_SOURCE = os.environ.get("DMAC_PORT_SOURCE", "/home/taishajo/work/dmac-assistant")


def _write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def collect_and_write_preflight(bundle: Path, *, repo_root: Path) -> dict:
    git = preflight_mod.default_git_probe(repo_root)
    docker = preflight_mod.default_docker_probe()
    port_commit = "unknown"
    try:
        port_commit = subprocess.run(
            ["git", "-C", PORT_SOURCE, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    env = os.environ.copy()
    if "INTEGRATION_PLAN_PATH" not in env:
        default_plan = Path("/home/taishajo/work/state/integration-plan.json")
        if default_plan.is_file():
            env["INTEGRATION_PLAN_PATH"] = str(default_plan)
    data = preflight_mod.collect_preflight(
        repo_root=repo_root,
        git=git,
        docker=docker,
        env=env,
        port_source_path=PORT_SOURCE,
        port_source_commit=port_commit,
        had_host_bind_data=Path("/srv/dmac/users").exists(),
    )
    _write_json(bundle / "preflight.json", data)
    return data


def sync_meta_from_preflight(bundle: Path, preflight: dict) -> None:
    meta_path = bundle / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    commit = preflight.get("commit") or (preflight.get("step3_deploy_gate") or {}).get("deploy_commit")
    meta["repo_commit"] = commit
    meta["repo_branch"] = preflight.get("branch", meta.get("repo_branch", "cc-step7-compose-native"))
    _write_json(meta_path, meta)
    manifest_path = bundle / "live_bundle_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["repo_commit"] = commit
        _write_json(manifest_path, manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate 3D host finalize (preflight + validate)")
    parser.add_argument("bundle_dir", type=Path, help="Absolute or worktree-relative bundle path")
    parser.add_argument(
        "--repo-root", type=Path, default=_REPO_ROOT,
        help="Git worktree for validate_run transcript re-verification",
    )
    args = parser.parse_args(argv)

    bundle = args.bundle_dir if args.bundle_dir.is_absolute() else (_REPO_ROOT / args.bundle_dir)
    bundle = bundle.resolve()
    if not bundle.is_dir():
        print(f"bundle not found: {bundle}", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve()
    preflight = collect_and_write_preflight(bundle, repo_root=repo_root)
    sync_meta_from_preflight(bundle, preflight)

    all_ok, checks = validate_run(bundle, repo_root=repo_root)
    print(format_report(all_ok, checks))
    print(f"\nGate 3D host finalize: {bundle}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
