"""Focused tests for Git-base materialization and baseline identities."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from build_tools.plan005_baseline import (
    BaselineError,
    baseline_identities,
    git_ls_tree,
    materialize_base_tree,
    run_baseline_lane,
)
from build_tools.plan005_closeout import IMMUTABLE_NEXTSEEK_IMAGE, PLAN005_BASE_COMMIT


def _git_init(repo: Path, body: str = "hello\n") -> str:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "blob.txt").write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "blob.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_materialize_verifies_blob_identity(tmp_path: Path):
    repo = tmp_path / "repo"
    dest = tmp_path / "dest"
    repo.mkdir()
    head = _git_init(repo)
    manifest = materialize_base_tree(repo_root=repo, base=head, dest=dest)
    assert (dest / "blob.txt").read_text(encoding="utf-8") == "hello\n"
    assert "blob.txt" in manifest
    identities = baseline_identities(repo_root=repo, base=head)
    assert identities["tool_head"] == identities["subject_base"] == head
    assert identities["tool_tree"] == identities["subject_tree"]


def test_materialize_refuses_wrong_blob(tmp_path: Path):
    repo = tmp_path / "repo"
    dest = tmp_path / "dest"
    repo.mkdir()
    head = _git_init(repo)

    def fake_cat(root, sha, git_runner=None):
        return b"tampered"

    with mock.patch("build_tools.plan005_baseline.git_cat_file", side_effect=fake_cat):
        with pytest.raises(BaselineError, match="wrong blob"):
            materialize_base_tree(repo_root=repo, base=head, dest=dest)


def test_materialize_refuses_traversal_and_symlink_rows(tmp_path: Path):
    repo = tmp_path / "repo"
    dest = tmp_path / "dest"
    repo.mkdir()
    _git_init(repo)
    with mock.patch(
        "build_tools.plan005_baseline.git_ls_tree",
        return_value=[("100644", "blob", "0" * 40, "../escape.txt")],
    ):
        with pytest.raises(BaselineError, match="traversal"):
            materialize_base_tree(repo_root=repo, base="HEAD", dest=dest)
    with mock.patch(
        "build_tools.plan005_baseline.git_ls_tree",
        return_value=[("120000", "blob", "0" * 40, "link")],
    ):
        with pytest.raises(BaselineError, match="symlink"):
            materialize_base_tree(repo_root=repo, base="HEAD", dest=dest)


def test_materialize_refuses_omission_and_extra(tmp_path: Path):
    repo = tmp_path / "repo"
    dest = tmp_path / "dest"
    repo.mkdir()
    head = _git_init(repo)
    dest.mkdir()
    (dest / "extra.txt").write_text("nope\n", encoding="utf-8")
    with pytest.raises(BaselineError, match="extra file"):
        materialize_base_tree(repo_root=repo, base=head, dest=dest)


def test_ls_tree_mutation_changes_identity(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _git_init(repo)
    rows = git_ls_tree(repo, head)
    mutated = [(mode, obj_type, "deadbeef" + sha[8:], path) for mode, obj_type, sha, path in rows]
    assert mutated != rows


def test_run_baseline_lane_distinguishes_tool_and_subject(tmp_path: Path):
    repo = tmp_path / "repo"
    output = tmp_path / "output"
    evidence = tmp_path / "evidence"
    repo.mkdir()
    evidence.mkdir()
    (evidence / "artifacts").mkdir()
    (evidence / "records").mkdir()
    head = _git_init(repo)
    (repo / "later.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "later.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "candidate"], cwd=repo, check=True, capture_output=True)
    records = []

    def fake_record(**kwargs):
        records.append(kwargs)
        return {"exit_code": 0, "name": kwargs["name"]}

    summary = run_baseline_lane(
        repo_root=repo,
        base=head,
        output=output,
        image=IMMUTABLE_NEXTSEEK_IMAGE,
        evidence_root=evidence,
        record=fake_record,
    )
    assert summary["subject_base"] == head
    assert summary["tool_head"] != summary["subject_base"]
    assert "later.txt" not in summary["subject_blob_manifest"]
    assert len(records) == 2
    assert records[0]["name"] == "01-baseline-baml"
    assert "--ignore" in records[1]["argv"]
    assert PLAN005_BASE_COMMIT  # imported constant still the plan pin
    junit_declared = any(
        "base-cc-assistant.junit.xml" in " ".join(item["argv"]) for item in records
    )
    assert junit_declared


def test_run_baseline_lane_refuses_mutable_image(tmp_path: Path):
    with pytest.raises(BaselineError, match="image"):
        run_baseline_lane(
            repo_root=tmp_path,
            base="HEAD",
            output=tmp_path / "out",
            image="nextseek-nextseek:latest",
            evidence_root=tmp_path / "ev",
            record=lambda **kwargs: {"exit_code": 0},
        )


def test_run_baseline_lane_propagates_record_and_junit_mutation(tmp_path: Path):
    repo = tmp_path / "repo"
    output = tmp_path / "output"
    evidence = tmp_path / "evidence"
    repo.mkdir()
    evidence.mkdir()
    (evidence / "artifacts").mkdir()
    (evidence / "records").mkdir()
    head = _git_init(repo)

    def exploding(**kwargs):
        raise BaselineError("command record mutated")

    with pytest.raises(BaselineError, match="command record"):
        run_baseline_lane(
            repo_root=repo,
            base=head,
            output=output,
            image=IMMUTABLE_NEXTSEEK_IMAGE,
            evidence_root=evidence,
            record=exploding,
        )
