"""Tests for startup.steps.registry_push — automated off-box baseline push.

DEPLOYMENT.md §5.2 automation: after a rebuild of the canonical instance, gate
the fresh image for baked secrets, tag it, and push it to the private GHCR
package — WITHOUT ever failing the deploy. Every failure mode must degrade to
a loud nudge (banner + doctor check + state marker), never an exception or a
nonzero exit.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import orjson
import pytest

from startup.steps import registry_push
from startup.lib.rebuild_policy import ImagePolicy
from startup.steps.registry_push import (
    GHCR_ENV_OVERRIDE_VAR,
    REGISTRY_IMAGE,
    Credentials,
    baked_secret_gate,
    check_registry_baseline,
    compute_baseline_tag,
    credential_env_path,
    load_credentials,
    push_baseline,
    push_baselines,
    read_state,
)

TOKEN = "ghp_fake_token_for_tests_only"


# ---------------------------------------------------------------------------
# Credential file loading
# ---------------------------------------------------------------------------

def test_load_credentials_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_credentials(tmp_path / "nope.env") is None


def test_load_credentials_missing_token_key_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "ghcr.env"
    p.write_text("GHCR_USER=tavjo\n")
    assert load_credentials(p) is None


def test_load_credentials_parses_user_and_token(tmp_path: Path) -> None:
    p = tmp_path / "ghcr.env"
    p.write_text(
        "# GHCR push credential\n"
        "export GHCR_USER=tavjo\n"
        f'GHCR_TOKEN="{TOKEN}"\n'
    )
    creds = load_credentials(p)
    assert creds == Credentials(user="tavjo", token=TOKEN)


def test_credential_env_path_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GHCR_ENV_OVERRIDE_VAR, "/custom/ghcr.env")
    assert credential_env_path() == Path("/custom/ghcr.env")


def test_credential_env_path_defaults_under_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GHCR_ENV_OVERRIDE_VAR, raising=False)
    monkeypatch.setenv("HOME", "/home/deployer")
    assert credential_env_path() == Path("/home/deployer/.config/nextseek/ghcr.env")


# ---------------------------------------------------------------------------
# Baseline tag computation
# ---------------------------------------------------------------------------

TODAY = datetime.date(2026, 8, 6)


@patch("startup.steps.registry_push.subprocess.run")
def test_tag_clean_repo_is_date_plus_sha(mock_run: MagicMock) -> None:
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="abc1234\n", stderr=""),   # rev-parse
        MagicMock(returncode=0, stdout="", stderr=""),            # status --porcelain
    ]
    assert compute_baseline_tag(Path("/repo"), today=TODAY) == "baseline-20260806-abc1234"


@patch("startup.steps.registry_push.subprocess.run")
def test_tag_dirty_repo_gets_dirty_suffix(mock_run: MagicMock) -> None:
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="abc1234\n", stderr=""),
        MagicMock(returncode=0, stdout=" M dmac/settings.py\n", stderr=""),
    ]
    assert compute_baseline_tag(Path("/repo"), today=TODAY) == "baseline-20260806-abc1234-dirty"


@patch("startup.steps.registry_push.subprocess.run")
def test_tag_git_failure_falls_back_to_date_only(mock_run: MagicMock) -> None:
    mock_run.side_effect = OSError("git not found")
    assert compute_baseline_tag(Path("/repo"), today=TODAY) == "baseline-20260806"


# ---------------------------------------------------------------------------
# Baked-secret gate (DEPLOYMENT.md §5.2)
# ---------------------------------------------------------------------------

def _gate_run_dispatcher(ls_output: str, env_keys: str = ""):
    """Return a subprocess.run side_effect serving the gate's two probes."""

    def dispatch(cmd, **kwargs):
        joined = " ".join(cmd)
        if "cut" in joined:
            return MagicMock(returncode=0, stdout=env_keys, stderr="")
        return MagicMock(returncode=0, stdout=ls_output, stderr="")

    return dispatch


@patch("startup.steps.registry_push.subprocess.run")
def test_gate_passes_with_only_env_example(mock_run: MagicMock) -> None:
    mock_run.side_effect = _gate_run_dispatcher("/app/docker/nextseek.env.example\n")
    ok, detail = baked_secret_gate("img:latest")
    assert ok is True


@patch("startup.steps.registry_push.subprocess.run")
def test_gate_fails_on_local_settings(mock_run: MagicMock) -> None:
    mock_run.side_effect = _gate_run_dispatcher(
        "/app/docker/nextseek.env.example\n/app/dmac/local_settings.py\n"
    )
    ok, detail = baked_secret_gate("img:latest")
    assert ok is False
    assert "local_settings.py" in detail


@patch("startup.steps.registry_push.subprocess.run")
def test_gate_allows_known_benign_luriakey_env(mock_run: MagicMock) -> None:
    """/app/.env with ONLY the LURIAKEY key is the user-accepted 2026-08-05
    known-benign residue (a file path, not a credential)."""
    mock_run.side_effect = _gate_run_dispatcher(
        "/app/docker/nextseek.env.example\n/app/.env\n", env_keys="LURIAKEY\n"
    )
    ok, detail = baked_secret_gate("img:latest")
    assert ok is True


@patch("startup.steps.registry_push.subprocess.run")
def test_gate_rejects_env_with_credential_keys_naming_keys_only(mock_run: MagicMock) -> None:
    mock_run.side_effect = _gate_run_dispatcher(
        "/app/.env\n", env_keys="LURIAKEY\nAWS_BEARER_TOKEN_BEDROCK\n"
    )
    ok, detail = baked_secret_gate("img:latest")
    assert ok is False
    assert "AWS_BEARER_TOKEN_BEDROCK" in detail  # key NAME may be shown, value never


# ---------------------------------------------------------------------------
# push_baseline orchestration
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repo root with startup/ dir and a valid credential file wired in."""
    (tmp_path / "startup").mkdir()
    cred = tmp_path / "ghcr.env"
    cred.write_text(f"GHCR_USER=tavjo\nGHCR_TOKEN={TOKEN}\n")
    monkeypatch.setenv(GHCR_ENV_OVERRIDE_VAR, str(cred))
    return tmp_path


def _happy_run_dispatcher(calls: list[list[str]]):
    """Record every argv; serve gate/tag/login/push/logout all-success."""

    def dispatch(cmd, **kwargs):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if "cut" in joined:
            return MagicMock(returncode=0, stdout="LURIAKEY\n", stderr="")
        if cmd[:2] == ["docker", "run"]:
            return MagicMock(returncode=0, stdout="/app/docker/nextseek.env.example\n", stderr="")
        if cmd[:2] == ["docker", "push"]:
            return MagicMock(
                returncode=0,
                stdout="baseline-x: digest: sha256:feedface size: 856\n",
                stderr="",
            )
        if cmd[:2] == ["git", "-C"] and "rev-parse" in joined:
            return MagicMock(returncode=0, stdout="abc1234\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return dispatch


@patch("startup.steps.registry_push.subprocess.run")
def test_push_success_sequence_and_marker(mock_run: MagicMock, repo: Path) -> None:
    calls: list[list[str]] = []
    mock_run.side_effect = _happy_run_dispatcher(calls)

    outcome = push_baseline(repo, compose_project_name="nextseek", today=TODAY)

    assert outcome.status == "pushed"
    assert outcome.tag == f"{REGISTRY_IMAGE}:baseline-20260806-abc1234"
    flat = [" ".join(c) for c in calls]
    assert any(f"docker tag nextseek-nextseek:latest {outcome.tag}" == f for f in flat)
    assert any(f.startswith("docker login ghcr.io -u tavjo") for f in flat)
    assert any(f"docker push {outcome.tag}" == f for f in flat)
    assert flat[-1] == "docker logout ghcr.io"
    # The token must never appear in any argv (it goes via stdin).
    assert all(TOKEN not in part for c in calls for part in c)

    state = read_state(repo)
    assert state["last_success"]["tag"] == outcome.tag
    assert state["last_success"]["digest"] == "sha256:feedface"
    assert state["last_attempt"]["status"] == "pushed"


@patch("startup.steps.registry_push.subprocess.run")
def test_push_uses_clean_git_root_but_records_runtime_state(
    mock_run: MagicMock, repo: Path
) -> None:
    calls: list[list[str]] = []
    mock_run.side_effect = _happy_run_dispatcher(calls)
    clean_source = repo / "clean-source"

    outcome = push_baseline(
        repo,
        compose_project_name="nextseek",
        today=TODAY,
        git_root=clean_source,
    )

    assert outcome.status == "pushed"
    git_calls = [call for call in calls if call[:2] == ["git", "-C"]]
    assert git_calls
    assert all(call[2] == str(clean_source) for call in git_calls)
    assert read_state(repo)["last_attempt"]["status"] == "pushed"


@patch("startup.steps.registry_push.subprocess.run")
def test_gate_failure_pushes_nothing_and_records(mock_run: MagicMock, repo: Path) -> None:
    def dispatch(cmd, **kwargs):
        if cmd[:2] == ["docker", "run"]:
            return MagicMock(returncode=0, stdout="/app/dmac/local_settings.py\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = dispatch
    outcome = push_baseline(repo, compose_project_name="nextseek", today=TODAY)

    assert outcome.status == "gate_failed"
    called = [" ".join(c.args[0]) for c in mock_run.call_args_list]
    assert not any("login" in c or "push" in c or "docker tag" in c for c in called)
    assert read_state(repo)["last_attempt"]["status"] == "gate_failed"
    assert read_state(repo)["last_success"] is None


@patch("startup.steps.registry_push.subprocess.run")
def test_missing_credentials_skips_push_with_nudge(
    mock_run: MagicMock, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(GHCR_ENV_OVERRIDE_VAR, str(repo / "absent.env"))
    calls: list[list[str]] = []
    mock_run.side_effect = _happy_run_dispatcher(calls)

    outcome = push_baseline(repo, compose_project_name="nextseek", today=TODAY)

    assert outcome.status == "no_credentials"
    assert "write:packages" in outcome.remediation
    assert str(repo / "absent.env") in outcome.remediation
    flat = [" ".join(c) for c in calls]
    assert not any("login" in f or "docker push" in f for f in flat)
    assert read_state(repo)["last_attempt"]["status"] == "no_credentials"


@patch("startup.steps.registry_push.subprocess.run")
def test_push_failure_is_nonfatal_and_still_logs_out(mock_run: MagicMock, repo: Path) -> None:
    calls: list[list[str]] = []
    happy = _happy_run_dispatcher(calls)

    def dispatch(cmd, **kwargs):
        if cmd[:2] == ["docker", "push"]:
            calls.append(list(cmd))
            return MagicMock(returncode=1, stdout="", stderr="denied: token expired")
        return happy(cmd, **kwargs)

    mock_run.side_effect = dispatch
    outcome = push_baseline(repo, compose_project_name="nextseek", today=TODAY)

    assert outcome.status == "push_failed"
    assert "token expired" in outcome.detail
    assert [" ".join(c) for c in calls][-1] == "docker logout ghcr.io"
    state = read_state(repo)
    assert state["last_attempt"]["status"] == "push_failed"
    assert state["last_success"] is None


@patch("startup.steps.registry_push.subprocess.run")
def test_unexpected_exception_never_escapes(mock_run: MagicMock, repo: Path) -> None:
    mock_run.side_effect = OSError("docker binary vanished")
    outcome = push_baseline(repo, compose_project_name="nextseek", today=TODAY)
    assert outcome.status == "error"
    assert "docker binary vanished" in outcome.detail


@patch("startup.steps.registry_push.subprocess.run")
def test_non_canonical_instance_is_skipped_without_docker_calls(
    mock_run: MagicMock, repo: Path
) -> None:
    outcome = push_baseline(repo, compose_project_name="devmerge", today=TODAY)
    assert outcome.status == "skipped"
    mock_run.assert_not_called()
    # skipped attempts must NOT overwrite the marker (would pollute doctor on
    # secondary instances)
    assert read_state(repo) is None


@patch("startup.steps.registry_push.push_baseline")
def test_push_baselines_maps_each_local_image_to_its_registry(
    mock_push: MagicMock, repo: Path
) -> None:
    mock_push.side_effect = [
        registry_push.PushOutcome(status="pushed", registry_image="ghcr.io/org/a"),
        registry_push.PushOutcome(status="pushed", registry_image="ghcr.io/org/b"),
    ]
    images = (
        ImagePolicy(local_image="local-a:latest", registry_image="ghcr.io/org/a"),
        ImagePolicy(local_image="local-b:latest", registry_image="ghcr.io/org/b"),
    )
    outcomes = push_baselines(repo, "nextseek", images)
    assert len(outcomes) == 2
    assert mock_push.call_args_list[0].kwargs == {
        "compose_project_name": "nextseek",
        "local_image": "local-a:latest",
        "registry_image": "ghcr.io/org/a",
    }
    assert mock_push.call_args_list[1].kwargs["local_image"] == "local-b:latest"


# ---------------------------------------------------------------------------
# Doctor check
# ---------------------------------------------------------------------------

def _write_state(repo: Path, payload: dict) -> None:
    (repo / "startup" / ".ghcr-push-state.json").write_bytes(orjson.dumps(payload))


def test_doctor_warns_when_never_pushed(tmp_path: Path) -> None:
    (tmp_path / "startup").mkdir()
    name, ok, detail = check_registry_baseline(tmp_path)
    assert name == "off-box baseline"
    assert ok is False
    assert "never" in detail.lower()


def test_doctor_fails_when_last_attempt_failed(tmp_path: Path) -> None:
    (tmp_path / "startup").mkdir()
    _write_state(
        tmp_path,
        {
            "last_success": {"at": "2026-08-06T12:00:00", "tag": "t", "digest": "d"},
            "last_attempt": {
                "at": "2026-08-07T12:00:00",
                "status": "push_failed",
                "detail": "denied: token expired",
            },
        },
    )
    name, ok, detail = check_registry_baseline(tmp_path)
    assert ok is False
    assert "token expired" in detail


def test_doctor_ok_when_last_attempt_pushed(tmp_path: Path) -> None:
    (tmp_path / "startup").mkdir()
    _write_state(
        tmp_path,
        {
            "last_success": {
                "at": "2026-08-07T12:00:00",
                "tag": f"{REGISTRY_IMAGE}:baseline-20260807-abc1234",
                "digest": "sha256:feedface",
            },
            "last_attempt": {"at": "2026-08-07T12:00:00", "status": "pushed", "detail": ""},
        },
    )
    name, ok, detail = check_registry_baseline(tmp_path)
    assert ok is True
    assert "baseline-20260807-abc1234" in detail


def test_doctor_fails_when_new_marker_omits_first_party_images(tmp_path: Path) -> None:
    (tmp_path / "startup").mkdir()
    _write_state(
        tmp_path,
        {
            "images": {
                REGISTRY_IMAGE: {
                    "last_success": {"at": "2026-08-14T12:00:00", "tag": "t"},
                    "last_attempt": {
                        "at": "2026-08-14T12:00:00",
                        "status": "pushed",
                        "detail": "",
                    },
                }
            }
        },
    )
    _, ok, detail = check_registry_baseline(tmp_path)
    assert ok is False
    assert "never pushed" in detail
    assert "nextseek-cc-agent" in detail


@patch("startup.steps.registry_push.subprocess.run")
def test_first_non_app_push_migrates_legacy_app_state(
    mock_run: MagicMock, repo: Path
) -> None:
    _write_state(
        repo,
        {
            "last_success": {"at": "2026-08-06T12:00:00", "tag": "legacy-app"},
            "last_attempt": {
                "at": "2026-08-06T12:00:00",
                "status": "pushed",
                "detail": "",
            },
        },
    )
    calls: list[list[str]] = []
    mock_run.side_effect = _happy_run_dispatcher(calls)
    outcome = push_baseline(
        repo,
        compose_project_name="nextseek",
        local_image="dmac-assistant:poc",
        registry_image="ghcr.io/biomicrocenter/nextseek-cc-agent",
        today=TODAY,
    )
    assert outcome.status == "pushed"
    state = read_state(repo)
    assert state["images"][REGISTRY_IMAGE]["last_success"]["tag"] == "legacy-app"


def test_load_credentials_ignores_lines_without_equals(tmp_path: Path) -> None:
    p = tmp_path / "ghcr.env"
    p.write_text(f"JUNK LINE\nGHCR_USER=tavjo\nGHCR_TOKEN={TOKEN}\n")
    assert load_credentials(p) == Credentials(user="tavjo", token=TOKEN)


@patch("startup.steps.registry_push.subprocess.run")
def test_tag_rev_parse_nonzero_falls_back_to_date_only(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="not a git repo")
    assert compute_baseline_tag(Path("/repo"), today=TODAY) == "baseline-20260806"


@patch("startup.steps.registry_push.subprocess.run")
def test_gate_fails_closed_when_probe_cannot_run(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=125, stdout="", stderr="no such image")
    ok, detail = baked_secret_gate("img:latest")
    assert ok is False
    assert "could not run" in detail


@patch("startup.steps.registry_push.subprocess.run")
def test_gate_fails_closed_when_env_keys_unreadable(mock_run: MagicMock) -> None:
    def dispatch(cmd, **kwargs):
        if "cut" in " ".join(cmd):
            return MagicMock(returncode=1, stdout="", stderr="cut: /app/.env: No such file")
        return MagicMock(returncode=0, stdout="/app/.env\n", stderr="")

    mock_run.side_effect = dispatch
    ok, detail = baked_secret_gate("img:latest")
    assert ok is False
    assert "key names unreadable" in detail


@patch("startup.steps.registry_push.subprocess.run")
def test_docker_tag_failure_is_push_failed_without_login(mock_run: MagicMock, repo: Path) -> None:
    calls: list[list[str]] = []

    def dispatch(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "tag"]:
            return MagicMock(returncode=1, stdout="", stderr="no such image")
        if cmd[:2] == ["docker", "run"]:
            return MagicMock(returncode=0, stdout="/app/docker/nextseek.env.example\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = dispatch
    outcome = push_baseline(repo, compose_project_name="nextseek", today=TODAY)
    assert outcome.status == "push_failed"
    assert "docker tag failed" in outcome.detail
    assert not any(c[:2] == ["docker", "login"] for c in calls)


@patch("startup.steps.registry_push.subprocess.run")
def test_login_failure_skips_push_but_still_logs_out(mock_run: MagicMock, repo: Path) -> None:
    calls: list[list[str]] = []
    happy = _happy_run_dispatcher(calls)

    def dispatch(cmd, **kwargs):
        if cmd[:2] == ["docker", "login"]:
            calls.append(list(cmd))
            return MagicMock(returncode=1, stdout="", stderr="unauthorized: bad credentials")
        return happy(cmd, **kwargs)

    mock_run.side_effect = dispatch
    outcome = push_baseline(repo, compose_project_name="nextseek", today=TODAY)
    assert outcome.status == "push_failed"
    assert "docker login failed" in outcome.detail
    flat = [" ".join(c) for c in calls]
    assert not any(f.startswith("docker push") for f in flat)
    assert flat[-1] == "docker logout ghcr.io"


@patch("startup.steps.registry_push.subprocess.run")
def test_push_output_without_digest_line_yields_none_digest(mock_run: MagicMock, repo: Path) -> None:
    calls: list[list[str]] = []
    happy = _happy_run_dispatcher(calls)

    def dispatch(cmd, **kwargs):
        if cmd[:2] == ["docker", "push"]:
            calls.append(list(cmd))
            return MagicMock(returncode=0, stdout="layers pushed, no digest echoed\n", stderr="")
        return happy(cmd, **kwargs)

    mock_run.side_effect = dispatch
    outcome = push_baseline(repo, compose_project_name="nextseek", today=TODAY)
    assert outcome.status == "pushed"
    assert outcome.digest is None


def test_read_state_returns_none_on_corrupt_marker(tmp_path: Path) -> None:
    (tmp_path / "startup").mkdir()
    (tmp_path / "startup" / ".ghcr-push-state.json").write_text("{not json")
    assert read_state(tmp_path) is None


@patch("startup.steps.registry_push.ui")
def test_render_outcome_all_branches(mock_ui: MagicMock) -> None:
    from startup.steps.registry_push import PushOutcome, render_outcome

    render_outcome(PushOutcome(status="pushed", tag="t"))
    mock_ui.ok.assert_called_once()
    render_outcome(PushOutcome(status="skipped", detail="secondary instance"))
    mock_ui.info.assert_called_once()
    render_outcome(PushOutcome(status="gate_failed", detail="bad", remediation="clean it"))
    mock_ui.banner.assert_called_once()
    mock_ui.remediation.assert_called_once()
    render_outcome(PushOutcome(status="no_credentials", detail="none"))
    assert mock_ui.fail.call_count == 2


@patch("startup.steps.doctor.load_instance", return_value=None)
@patch("startup.steps.doctor.prereqs")
def test_doctor_diagnose_includes_baseline_check_even_without_instance(
    mock_prereqs: MagicMock, mock_load: MagicMock, tmp_path: Path
) -> None:
    from startup.steps.doctor import diagnose

    (tmp_path / "startup").mkdir()
    mock_prereqs.run_all.return_value = []
    names = [name for name, _, _ in diagnose(tmp_path)]
    assert "off-box baseline" in names


# ---------------------------------------------------------------------------
# CLI wiring: rebuild must trigger the push, and must survive its failure
# ---------------------------------------------------------------------------

def _instance_state():
    from startup.lib.instance import InstanceState

    return InstanceState(
        name="nextseek",
        prefix="",
        ports={"nextseek": 8000, "seek": 3000, "neo4j_http": 7474, "neo4j_bolt": 7687},
        compose_project_name="nextseek",
        created="2026-08-06T00:00:00",
    )


@patch("startup.cli.load_instance")
def test_rebuild_nextseek_triggers_baseline_push(
    mock_load: MagicMock,
) -> None:
    from typer.testing import CliRunner

    from startup.cli import app

    mock_load.return_value = _instance_state()
    with patch("startup.steps.rollback_tags.create_verified", return_value=()), \
         patch("startup.lib.docker_ops.compose_build") as mock_build, \
         patch("startup.lib.docker_ops.compose_up") as mock_up, \
         patch("startup.steps.registry_push.push_baselines", return_value=()) as mock_push:
        result = CliRunner().invoke(app, ["rebuild"])
    assert result.exit_code == 0
    assert mock_build.call_args.kwargs["services"] == ("nextseek",)
    assert mock_up.call_args.kwargs["services"] == (
        "nextseek",
        "attribute_mutation_worker",
        "attribute_mutation_dispatcher",
        "attribute_mutation_recovery_scheduler",
    )
    assert mock_up.call_args.kwargs["no_deps"] is True
    assert mock_up.call_args.kwargs["force_recreate"] is True
    mock_push.assert_called_once()
    assert mock_push.call_args.kwargs["compose_project_name"] == "nextseek"


@patch("startup.cli.load_instance")
def test_rebuild_can_build_clean_source_and_recreate_from_runtime_root(
    mock_load: MagicMock,
) -> None:
    from typer.testing import CliRunner

    from startup import cli

    mock_load.return_value = _instance_state()
    clean_source = Path("/clean/origin-dev")
    with patch(
        "startup.lib.deploy_source.resolve_verified_source",
        return_value=clean_source,
    ), patch("startup.steps.rollback_tags.create_verified", return_value=()), patch(
        "startup.lib.docker_ops.compose_build"
    ) as mock_build, patch("startup.lib.docker_ops.compose_up") as mock_up, patch(
        "startup.steps.registry_push.push_baselines", return_value=()
    ) as mock_push:
        result = CliRunner().invoke(
            cli.app,
            ["rebuild", "--source-tree", str(clean_source)],
        )

    assert result.exit_code == 0, result.output
    assert mock_build.call_args.kwargs["project_dir"] == clean_source
    assert mock_up.call_args.kwargs["project_dir"] == cli.REPO_ROOT
    assert mock_push.call_args.kwargs["git_root"] == clean_source


@patch("startup.cli.load_instance")
def test_rebuild_cc_agent_builds_without_starting_container(mock_load: MagicMock) -> None:
    from typer.testing import CliRunner

    from startup.cli import app

    mock_load.return_value = _instance_state()
    with patch("startup.steps.rollback_tags.create_verified", return_value=()), \
         patch("startup.lib.docker_ops.compose_build") as mock_build, \
         patch("startup.lib.docker_ops.compose_up") as mock_up, \
         patch("startup.steps.registry_push.push_baselines", return_value=()) as mock_push:
        result = CliRunner().invoke(app, ["rebuild", "--component", "cc-agent"])
    assert result.exit_code == 0
    assert mock_build.call_args.kwargs["services"] == ("cc-agent",)
    mock_up.assert_not_called()
    mock_push.assert_called_once()


@patch("startup.cli.load_instance")
def test_rebuild_survives_push_step_blowing_up(mock_load: MagicMock) -> None:
    """Even if the push step somehow raises despite its contract, the rebuild
    command must still exit 0 — the deploy is never hostage to the registry."""
    from typer.testing import CliRunner

    from startup.cli import app

    mock_load.return_value = _instance_state()
    with patch("startup.steps.rollback_tags.create_verified", return_value=()), \
         patch("startup.lib.docker_ops.compose_build"), \
         patch("startup.lib.docker_ops.compose_up"), \
         patch(
             "startup.steps.registry_push.push_baselines",
             side_effect=RuntimeError("contract violated"),
         ):
        result = CliRunner().invoke(app, ["rebuild"])
    assert result.exit_code == 0


@patch("startup.cli.load_instance")
def test_rebuild_aborts_before_build_when_rollback_tag_fails(mock_load: MagicMock) -> None:
    from typer.testing import CliRunner

    from startup.cli import app
    from startup.steps.rollback_tags import RollbackTagError

    mock_load.return_value = _instance_state()
    with patch(
        "startup.steps.rollback_tags.create_verified",
        side_effect=RollbackTagError("missing source"),
    ), patch("startup.lib.docker_ops.compose_build") as mock_build:
        result = CliRunner().invoke(app, ["rebuild"])
    assert result.exit_code == 1
    assert "missing source" in result.output
    mock_build.assert_not_called()


@patch("startup.cli.load_instance")
def test_rebuild_rejects_unknown_component(mock_load: MagicMock) -> None:
    from typer.testing import CliRunner

    from startup.cli import app

    mock_load.return_value = _instance_state()
    result = CliRunner().invoke(app, ["rebuild", "--component", "nextseek_nginx"])
    assert result.exit_code == 2
    assert "unknown rebuild component" in result.output
