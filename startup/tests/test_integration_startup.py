"""Integration tests: real subsystems chained together.

Unlike the unit suites (which mock at module boundaries), these run real
subprocesses (git), real state files on disk, and real multi-command CLI
flows — only the docker daemon boundary is faked, except for the explicitly
opt-in real-docker lane at the bottom.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from startup import cli
from startup.lib.instance import InstanceState, save_instance
from startup.steps import registry_push
from startup.steps.registry_push import GHCR_ENV_OVERRIDE_VAR, compute_baseline_tag
from startup.tests.test_registry_push import TODAY, TOKEN, _happy_run_dispatcher

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_the_rebuild_ci_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """`rebuild` ends by running the CI smoke suite in a subprocess.

    Nothing in this file is about CI, and no unit test may launch a real pytest
    run against a real stack, so the hook returns 0 for every test here. The
    hook's own behaviour (argv, env, --no-ci, the failing-CI exit) is covered in
    test_cli_commands.py.
    """
    from startup.ci import runner as ci_runner

    monkeypatch.setattr(ci_runner, "run_ci", lambda *args, **kwargs: 0)


# ---------------------------------------------------------------------------
# compute_baseline_tag against a REAL git repository
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return out.stdout.strip()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "file.txt").write_text("v1\n")
    _git(tmp_path, "add", "file.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_baseline_tag_from_real_clean_repo(git_repo: Path) -> None:
    sha = _git(git_repo, "rev-parse", "--short", "HEAD")
    assert compute_baseline_tag(git_repo, today=TODAY) == f"baseline-20260806-{sha}"


def test_baseline_tag_from_real_dirty_repo(git_repo: Path) -> None:
    (git_repo / "file.txt").write_text("modified\n")
    sha = _git(git_repo, "rev-parse", "--short", "HEAD")
    assert compute_baseline_tag(git_repo, today=TODAY) == f"baseline-20260806-{sha}-dirty"


def test_baseline_tag_outside_any_git_repo_falls_back_to_date(tmp_path: Path) -> None:
    isolated = tmp_path / "no-repo"
    isolated.mkdir()
    tag = compute_baseline_tag(isolated, today=TODAY)
    # Inside a git checkout the parent repo could still resolve; outside one the
    # fallback is date-only. Both are date-prefixed; assert the invariant.
    assert tag.startswith("baseline-20260806")


# ---------------------------------------------------------------------------
# rebuild → state marker → doctor: a real multi-command chain
# ---------------------------------------------------------------------------

def test_rebuild_then_doctor_reports_pushed_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebuild command's push writes the real state marker; a subsequent
    doctor run reads that same file and reports the baseline green."""
    (tmp_path / "startup").mkdir()
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    save_instance(
        tmp_path,
        InstanceState(
            name="nextseek", prefix="",
            ports={"nextseek": 8000, "seek": 3000, "neo4j_http": 7474, "neo4j_bolt": 7687},
            compose_project_name="nextseek", created="2026-08-06T00:00:00",
        ),
    )
    cred = tmp_path / "ghcr.env"
    cred.write_text(f"GHCR_USER=tavjo\nGHCR_TOKEN={TOKEN}\n")
    monkeypatch.setenv(GHCR_ENV_OVERRIDE_VAR, str(cred))

    calls: list[list[str]] = []
    with patch("startup.steps.rollback_tags.create_verified", return_value=()), \
         patch("startup.lib.docker_ops.compose_build"), \
         patch("startup.lib.docker_ops.compose_up"), \
         patch("startup.steps.registry_push.subprocess.run") as mock_run:
        mock_run.side_effect = _happy_run_dispatcher(calls)
        result = runner.invoke(cli.app, ["rebuild", "--component", "custom-stack"])
    assert result.exit_code == 0, result.output
    assert "off-box rollback baseline pushed" in result.output

    marker = tmp_path / "startup" / registry_push.STATE_FILENAME
    assert marker.exists()

    with patch("startup.steps.doctor.prereqs") as mock_prereqs, \
         patch("startup.steps.doctor.validate") as mock_validate:
        mock_prereqs.run_all.return_value = []
        mock_validate.run_all_health_checks.return_value = []
        result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "off-box baseline" in result.output
    # rich wraps long lines at console width — compare whitespace-free
    compact = "".join(result.output.split())
    assert "all4first-partyimagesprotected" in compact


def test_rebuild_without_credentials_then_doctor_goes_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No credential: the rebuild still succeeds (exit 0) but nudges, and
    doctor stays red until a push lands — the whole point of the design."""
    (tmp_path / "startup").mkdir()
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    save_instance(
        tmp_path,
        InstanceState(
            name="nextseek", prefix="",
            ports={"nextseek": 8000, "seek": 3000, "neo4j_http": 7474, "neo4j_bolt": 7687},
            compose_project_name="nextseek", created="2026-08-06T00:00:00",
        ),
    )
    monkeypatch.setenv(GHCR_ENV_OVERRIDE_VAR, str(tmp_path / "absent.env"))

    calls: list[list[str]] = []
    with patch("startup.steps.rollback_tags.create_verified", return_value=()), \
         patch("startup.lib.docker_ops.compose_build"), \
         patch("startup.lib.docker_ops.compose_up"), \
         patch("startup.steps.registry_push.subprocess.run") as mock_run:
        mock_run.side_effect = _happy_run_dispatcher(calls)
        result = runner.invoke(cli.app, ["rebuild"])
    assert result.exit_code == 0, result.output
    assert "NOT PUSHED" in result.output
    assert "write:packages" in result.output

    with patch("startup.steps.doctor.prereqs") as mock_prereqs, \
         patch("startup.steps.doctor.validate") as mock_validate:
        mock_prereqs.run_all.return_value = []
        mock_validate.run_all_health_checks.return_value = []
        result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "no_credentials" in result.output


# ---------------------------------------------------------------------------
# Opt-in REAL docker lane (NEXTSEEK_STARTUP_DOCKER_TESTS=1)
# ---------------------------------------------------------------------------

requires_real_docker = pytest.mark.skipif(
    not os.environ.get("NEXTSEEK_STARTUP_DOCKER_TESTS"),
    reason="set NEXTSEEK_STARTUP_DOCKER_TESTS=1 to run real-docker integration tests",
)


@requires_real_docker
def test_baked_secret_gate_against_real_clean_image() -> None:
    """alpine has none of the gate's config/secret paths — a real docker run
    must PASS it. Exercises the actual docker plumbing end to end."""
    ok, detail = registry_push.baked_secret_gate("alpine:latest")
    assert ok is True, detail
