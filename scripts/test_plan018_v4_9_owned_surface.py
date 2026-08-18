"""Regression tests for the Plan 018 V4-9 owned-surface inventory."""

from __future__ import annotations

import importlib.util
import json
import shutil
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
    shutil.copy2(SCRIPT, checkout / SCRIPT.relative_to(ROOT))
    shutil.copy2(MANIFEST, checkout / MANIFEST.relative_to(ROOT))
    return checkout


def test_checked_in_manifest_validates_against_its_source_diff():
    module = _module()
    manifest = json.loads(MANIFEST.read_text())

    assert module.validate_manifest(manifest, root=ROOT) == []
    assert manifest["schema"] == "plan018-v4-9-owned-surface/v1"
    assert manifest["identity"]["base_sha"] == "6881b6a870d68a6efaeb483b111cb9244488c5f9"
    assert manifest["identity"]["source_sha"] == "b0a581af14a64026b4fd500a86972c3739439d38"


def test_every_source_candidate_is_listed_once_and_has_a_resolvable_oracle():
    module = _module()
    manifest = json.loads(MANIFEST.read_text())
    candidates = module.source_candidates(manifest, root=ROOT)
    entries = manifest["entries"]

    assert {candidate.path for candidate in candidates} == {entry["path"] for entry in entries}
    assert len(entries) == len({entry["path"] for entry in entries})
    assert all(module.oracle_errors(entry, root=ROOT) == [] for entry in entries)
    assert not ({entry["path"] for entry in entries} & {entry["path"] for entry in manifest["control_entries"]})


def test_no_accepted_plan018_path_is_excluded_as_an_unrelated_integration():
    module = _module()
    manifest = json.loads(MANIFEST.read_text())

    excluded = [
        entry
        for entry in manifest["entries"]
        if entry["classification"] == "integrated_non_v4"
    ]

    assert excluded
    assert all(
        not any(source.startswith("accepted_ownership:") for source in entry["sources"])
        for entry in excluded
    )


def test_current_v4_runtime_and_task6_surfaces_are_not_excluded():
    module = _module()

    assert module.classify_path("dmac/settings.py")["cluster"] == "v4_9_runtime_config"
    assert module.classify_path("docker/nextseek.env.example")["cluster"] == "v4_9_runtime_config"
    assert module.classify_path("startup/templates/nextseek.env.template")["cluster"] == "v4_9_runtime_config"
    assert module.classify_path("docker/eval-task6/Dockerfile")["cluster"] == "v4_9_task6_replay"
    assert module.classify_path("scripts/plan018_v4_9_task6_replay.py")["cluster"] == "v4_9_task_controls"


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


def test_new_file_under_an_excluded_integration_prefix_still_requires_manifest_update(
    git_checkout: Path,
):
    added = git_checkout / "chat_frontend" / "src" / "future-plan018-surface.ts"
    added.write_text("export const future = true;\n")
    _git(git_checkout, "add", str(added.relative_to(git_checkout)))
    _commit(git_checkout, "test: add path beneath classified integration prefix")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "new path absent from authoritative manifest" in result.stderr


def test_current_git_deletion_of_owned_path_is_rejected(git_checkout: Path):
    _git(git_checkout, "rm", "nessie_tests/runner.py")
    _commit(git_checkout, "test: delete owned path")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "owned path deleted without explicit current change" in result.stderr


def test_staged_git_addition_is_rejected(git_checkout: Path):
    added = git_checkout / "staged-unclassified" / "new-surface.bin"
    added.parent.mkdir()
    added.write_bytes(b"new")
    _git(git_checkout, "add", str(added.relative_to(git_checkout)))

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "unclassified new path" in result.stderr


def test_staged_git_deletion_of_owned_path_is_rejected(git_checkout: Path):
    _git(git_checkout, "rm", "nessie_tests/runner.py")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "owned path deleted without explicit current change" in result.stderr


def test_staged_git_rename_of_owned_path_is_rejected(git_checkout: Path):
    _git(git_checkout, "mv", "nessie_tests/runner.py", "nessie_tests/runner_staged.py")

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "owned path renamed without explicit current change" in result.stderr
    assert "nessie_tests/runner.py -> nessie_tests/runner_staged.py" in result.stderr


@pytest.mark.parametrize("action", ("delete", "rename"))
def test_unstaged_owned_deletion_or_rename_is_rejected(git_checkout: Path, action: str):
    old = git_checkout / "nessie_tests" / "runner.py"
    if action == "delete":
        old.unlink()
    else:
        old.rename(old.with_name("runner_unstaged.py"))

    result = _validate_current(git_checkout)

    assert result.returncode == 1
    assert "owned path deleted without explicit current change" in result.stderr or "owned path renamed without explicit current change" in result.stderr


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


def test_mutated_machine_readable_ownership_rules_are_rejected_before_regeneration(git_checkout: Path):
    rules_path = git_checkout / "evidence" / "plan018-v4-0-accepted-ownership-rules.json"
    rules = json.loads(rules_path.read_text())
    rules["rules"].append({"id": "injected", "match": "glob", "glob": "evil/**", "owner": "injected"})
    rules_path.write_text(json.dumps(rules, indent=2) + "\n")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "generate", "--root", str(git_checkout)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1
    assert "immutable ownership rules document digest mismatch" in result.stderr
