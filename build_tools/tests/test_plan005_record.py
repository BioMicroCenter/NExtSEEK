"""Focused tests for the Plan 005 shell-free evidence recorder."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from build_tools.plan005_closeout import COMMAND_TIMEOUT_SECONDS, SEQUENCE_BUDGET_SECONDS
from build_tools.plan005_record import (
    RecordError,
    parse_docker_image,
    record_command,
    remaining_sequence_budget,
    refuse_mutable_image,
    refuse_secret_bearing,
    refuse_writable_mounts,
)


IMAGE = "sha256:879406139db3581c6f1b040a5bdcef40385a62780af01e71d2766003e3745a81"


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "plan005@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "plan005"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    evidence = tmp_path / "evidence"
    repo.mkdir()
    evidence.mkdir()
    (evidence / "artifacts").mkdir()
    (evidence / "records").mkdir()
    _git_init(repo)
    return repo, evidence


def _ok_runner(argv, **kwargs):
    completed = mock.Mock()
    completed.returncode = 0
    completed.stdout = b"out"
    completed.stderr = b""
    return completed


def test_parse_docker_image_skips_flags():
    argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        "/tmp/a:/evidence",
        IMAGE,
        "true",
    ]
    assert parse_docker_image(argv) == IMAGE


def test_refuse_mutable_image_tag():
    with pytest.raises(RecordError, match="mutable"):
        refuse_mutable_image(
            ["docker", "run", "--network", "none", "nextseek-nextseek:latest", "true"]
        )


def test_refuse_secret_bearing_argv_and_env():
    with pytest.raises(RecordError, match="secret-bearing argv"):
        refuse_secret_bearing(["echo", "--password=x"], {})
    with pytest.raises(RecordError, match="secret-bearing environment"):
        refuse_secret_bearing(["echo"], {"API_TOKEN": "abc"})


def test_refuse_writable_root_and_prior_artifact(tmp_path: Path):
    evidence = tmp_path / "evidence"
    writable = evidence / "artifacts" / "export-check"
    prior = evidence / "artifacts" / "coverage-run"
    evidence.mkdir()
    writable.parent.mkdir(parents=True)
    prior.mkdir(parents=True)
    with pytest.raises(RecordError, match="evidence-root"):
        refuse_writable_mounts(
            ["docker", "run", "-v", f"{evidence}:/all-evidence", "img"],
            evidence_root=evidence,
            writable_output=writable,
        )
    with pytest.raises(RecordError, match="prior-artifact"):
        refuse_writable_mounts(
            ["docker", "run", "-v", f"{prior}:/evidence", "img"],
            evidence_root=evidence,
            writable_output=writable,
        )


def test_record_writes_bytes_hashes_and_git_snapshot(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    payload = record_command(
        evidence_root=evidence,
        name="02-export-check",
        argv=["/bin/echo", "hello-plan005"],
        writable_output=evidence / "artifacts" / "export-check",
        repo_root=repo,
        env={"PATH": "/usr/bin", "HOME": str(tmp_path)},
        runner=subprocess.run,
    )
    assert payload["exit_code"] == 0
    assert payload["stdout_bytes"] > 0
    assert payload["stdout_sha256"]
    assert payload["pre"]["head"] == payload["post"]["head"]
    record_path = evidence / "records" / "02-export-check" / "record.json"
    saved = json.loads(record_path.read_text(encoding="utf-8"))
    assert saved["name"] == "02-export-check"
    assert "PATH" in saved["env_keys"]


def test_record_refuses_overwrite_and_reuse(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    kwargs = dict(
        evidence_root=evidence,
        name="08-build-tools",
        argv=["/bin/true"],
        writable_output=evidence / "artifacts" / "build-tools",
        repo_root=repo,
        env={"PATH": "/usr/bin"},
        runner=_ok_runner,
    )
    record_command(**kwargs)
    with pytest.raises(RecordError, match="overwrite"):
        record_command(**kwargs)


def test_record_refuses_dirty_head_without_declared_output(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    (repo / "dirty.txt").write_text("nope\n", encoding="utf-8")
    with pytest.raises(RecordError, match="dirty HEAD"):
        record_command(
            evidence_root=evidence,
            name="03-surfaces-check",
            argv=["/bin/true"],
            writable_output=evidence / "artifacts" / "surfaces-check",
            repo_root=repo,
            env={"PATH": "/usr/bin"},
            runner=_ok_runner,
        )


def test_record_allows_declared_repo_output_dirty(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    out = repo / "dmac_assistant" / "baml_client"
    out.mkdir(parents=True)
    (out / "generated.py").write_text("x\n", encoding="utf-8")
    payload = record_command(
        evidence_root=evidence,
        name="04-baml-setup",
        argv=["/bin/true"],
        writable_output=evidence / "artifacts" / "baml-setup",
        repo_root=repo,
        env={"PATH": "/usr/bin"},
        declared_repo_output=out,
        ensure_declared_repo_output=True,
        runner=_ok_runner,
    )
    assert payload["exit_code"] == 0
    assert out.is_dir()


def test_record_refuses_mutating_prior_artifact(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    first = evidence / "artifacts" / "export-check"
    record_command(
        evidence_root=evidence,
        name="02-export-check",
        argv=["/bin/true"],
        writable_output=first,
        repo_root=repo,
        env={"PATH": "/usr/bin"},
        runner=_ok_runner,
    )
    (first / "junit.xml").write_text("<test/>", encoding="utf-8")

    def mutate_runner(argv, **kwargs):
        (first / "junit.xml").write_text("<mutated/>", encoding="utf-8")
        return _ok_runner(argv, **kwargs)

    with pytest.raises(RecordError, match="prior evidence mutated"):
        record_command(
            evidence_root=evidence,
            name="08-build-tools",
            argv=["/bin/true"],
            writable_output=evidence / "artifacts" / "build-tools",
            repo_root=repo,
            env={"PATH": "/usr/bin"},
            runner=mutate_runner,
        )


def test_record_refuses_timeout_inflation(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    with pytest.raises(RecordError, match="timeout inflation"):
        record_command(
            evidence_root=evidence,
            name="08-build-tools",
            argv=["/bin/true"],
            writable_output=evidence / "artifacts" / "build-tools",
            repo_root=repo,
            command_timeout_seconds=COMMAND_TIMEOUT_SECONDS + 1,
            runner=_ok_runner,
        )


def test_remaining_budget_uses_first_record_start(tmp_path: Path):
    evidence = tmp_path / "evidence"
    record_dir = evidence / "records" / "02-export-check"
    record_dir.mkdir(parents=True)
    (record_dir / "record.json").write_text(
        json.dumps({"start_monotonic": 100.0}) + "\n", encoding="utf-8"
    )
    remaining = remaining_sequence_budget(
        evidence, sequence_budget_seconds=SEQUENCE_BUDGET_SECONDS, now=100.0 + SEQUENCE_BUDGET_SECONDS + 1
    )
    assert remaining < 0


def test_record_refuses_exhausted_budget(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    record_dir = evidence / "records" / "02-export-check"
    record_dir.mkdir(parents=True)
    (record_dir / "record.json").write_text(
        json.dumps({"start_monotonic": 0.0}) + "\n", encoding="utf-8"
    )
    with mock.patch("build_tools.plan005_record.time.monotonic", return_value=SEQUENCE_BUDGET_SECONDS + 10):
        with pytest.raises(RecordError, match="budget exhausted"):
            record_command(
                evidence_root=evidence,
                name="03-surfaces-check",
                argv=["/bin/true"],
                writable_output=evidence / "artifacts" / "surfaces-check",
                repo_root=repo,
                env={"PATH": "/usr/bin"},
                runner=_ok_runner,
            )


def test_record_refuses_repo_local_evidence(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    inside = repo / "evidence-root" / "artifacts" / "export-check"
    inside.parent.mkdir(parents=True)
    (repo / "evidence-root" / "records").mkdir(parents=True, exist_ok=True)
    with pytest.raises(RecordError, match="repository-local"):
        record_command(
            evidence_root=repo / "evidence-root",
            name="02-export-check",
            argv=["/bin/true"],
            writable_output=repo / "evidence-root" / "artifacts" / "export-check",
            repo_root=repo,
            env={"PATH": "/usr/bin"},
            runner=_ok_runner,
        )


def test_record_refuses_docker_without_network_none(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    with pytest.raises(RecordError, match="network none"):
        record_command(
            evidence_root=evidence,
            name="02-export-check",
            argv=["docker", "run", "--rm", IMAGE, "true"],
            writable_output=evidence / "artifacts" / "export-check",
            repo_root=repo,
            env={"PATH": "/usr/bin"},
            runner=_ok_runner,
        )


def test_record_refuses_pytest_cov_and_xdist(tmp_path: Path):
    repo, evidence = _layout(tmp_path)
    with pytest.raises(RecordError, match="pytest-cov"):
        record_command(
            evidence_root=evidence,
            name="12-coverage-run",
            argv=["python", "-m", "pytest", "--cov=pkg"],
            writable_output=evidence / "artifacts" / "coverage-run",
            repo_root=repo,
            env={"PATH": "/usr/bin"},
            runner=_ok_runner,
        )
    with pytest.raises(RecordError, match="xdist"):
        record_command(
            evidence_root=evidence,
            name="12-coverage-run",
            argv=["python", "-m", "pytest", "-n", "auto"],
            writable_output=evidence / "artifacts" / "coverage-run",
            repo_root=repo,
            env={"PATH": "/usr/bin"},
            runner=_ok_runner,
        )
