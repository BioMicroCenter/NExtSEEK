"""Regression tests for the Plan 018 V4-9 owned-surface inventory."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plan018_v4_9_owned_surface.py"
MANIFEST = ROOT / "evidence" / "plan018-v4-9-owned-surface.json"


def _module():
    spec = importlib.util.spec_from_file_location("plan018_v4_9_owned_surface", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "-c", "user.name=V4-9 test", "-c", "user.email=v49@example.invalid", "commit", "-m", message)


def _validate_current(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "validate", "--root", str(repo), "--current"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@pytest.fixture()
def git_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", "--no-local", str(ROOT), str(checkout)], check=True)
    return checkout


def test_checked_in_manifest_validates_against_its_source_diff():
    module = _module()
    manifest = json.loads(MANIFEST.read_text())

    assert module.validate_manifest(manifest, root=ROOT) == []
    assert manifest["schema"] == "plan018-v4-9-owned-surface/v1"
    assert manifest["identity"]["base_sha"] == "6881b6a870d68a6efaeb483b111cb9244488c5f9"
    assert manifest["identity"]["source_sha"] == "517dffd18554a409e5d8e4b7fe43c8ffbb03bb09"


def test_every_source_candidate_is_listed_once_and_has_a_resolvable_oracle():
    module = _module()
    manifest = json.loads(MANIFEST.read_text())
    candidates = module.source_candidates(manifest, root=ROOT)
    entries = manifest["entries"]

    assert {candidate.path for candidate in candidates} == {entry["path"] for entry in entries}
    assert len(entries) == len({entry["path"] for entry in entries})
    assert all(module.oracle_errors(entry, root=ROOT) == [] for entry in entries)


def test_nessie_skill_and_template_are_regeneration_surfaces_with_resolvable_oracles():
    module = _module()
    for path in (
        "nessie_tests/output-skill/SKILL.md",
        "nessie_tests/output-skill/templates/report.html.tpl",
        "nessie_tests/output-skill-bayesian/templates/report_bayes.html.tpl",
    ):
        entry = module.classify_path(path)
        assert entry["classification"] == "configuration"
        assert entry["oracle"]["id"] == "nessie_output_regeneration"
        assert module.oracle_errors(entry, root=ROOT) == []


def test_current_git_addition_is_rejected(git_checkout: Path):
    added = git_checkout / "unclassified-plan018-v4-9" / "new-surface.bin"
    added.parent.mkdir()
    added.write_bytes(b"new")
    _git(git_checkout, "add", str(added.relative_to(git_checkout)))
    _commit(git_checkout, "test: add unknown surface")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "unclassified new path" in result.stderr


def test_current_git_deletion_of_owned_path_is_rejected(git_checkout: Path):
    _git(git_checkout, "rm", "nessie_tests/runner.py")
    _commit(git_checkout, "test: delete owned path")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "owned path deleted without explicit current change" in result.stderr


def test_current_git_rename_of_owned_path_is_rejected_with_both_sides(git_checkout: Path):
    _git(git_checkout, "mv", "nessie_tests/runner.py", "nessie_tests/runner_renamed.py")
    _commit(git_checkout, "test: rename owned path")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "owned path renamed without explicit current change" in result.stderr
    assert "nessie_tests/runner.py -> nessie_tests/runner_renamed.py" in result.stderr


def test_arbitrary_control_entry_injection_is_rejected(git_checkout: Path):
    manifest_path = git_checkout / "evidence" / "plan018-v4-9-owned-surface.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["control_entries"].append(
        {
            "path": "evil/control.py",
            "classification": "validator_tooling",
            "cluster": "v4_9_owned_surface_control",
            "oracle": {"id": "validator_self", "target": "scripts/plan018_v4_9_owned_surface.py"},
            "rationale": "injected",
            "sources": ["task_1_control"],
            "change": "task_1_control",
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "control_entries do not equal" in result.stderr


def test_immutable_accepted_ownership_identity_mismatch_is_rejected(git_checkout: Path):
    manifest_path = git_checkout / "evidence" / "plan018-v4-9-owned-surface.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["identity"]["accepted_ownership"]["map_blob_sha1"] = "0" * 40
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "immutable accepted ownership identity mismatch" in result.stderr
