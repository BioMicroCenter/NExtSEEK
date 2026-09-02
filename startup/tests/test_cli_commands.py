"""CliRunner-driven tests for every startup CLI command.

These run the real typer app end-to-end — real argument parsing, real
`.instance.json` round-trips through save_instance/load_instance, real prompt
handling — with the step modules mocked at the cli namespace boundary. They
exist to cover the orchestration in cli.py itself: phase ordering, branch
selection (populated-vs-load, --no-seed, filestore fallback), failure exits,
and the interactive confirm loop.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from startup import cli
from startup.ci import runner as ci_runner
from startup.lib.instance import InstanceState, load_instance, save_instance
from startup.steps.config import InvalidSeekPublicUrl
from startup.steps.prereqs import PrereqResult

runner = CliRunner()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated repo skeleton with cli.REPO_ROOT pointed at it."""
    (tmp_path / "startup").mkdir()
    (tmp_path / "docker").mkdir()
    (tmp_path / "chat_nextseek").mkdir()
    (tmp_path / "chat_nextseek" / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    return tmp_path


def _ok_prereq() -> PrereqResult:
    return PrereqResult(name="docker", ok=True, detail="Docker version 27")


@pytest.fixture()
def steps(repo: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Mock every step module on the cli namespace; return the mocks."""
    proxy_env = repo / "docker" / "proxy-secret.env"
    proxy_env.write_text("AWS_BEARER_TOKEN_BEDROCK=tok\n")

    prereqs = MagicMock()
    prereqs.run_all.return_value = [_ok_prereq()]

    config = MagicMock()
    config.InvalidSeekPublicUrl = InvalidSeekPublicUrl
    config.default_values.return_value = SimpleNamespace(neo4j_password="pw")
    config.resolve_seek_public_url.return_value = "http://localhost:3000"
    config.render_proxy_secret_env.return_value = proxy_env

    seed = MagicMock()
    seed.seed_files_present.return_value = []  # nothing missing
    seed.mysql_db_is_populated.return_value = True
    seed.neo4j_is_populated.return_value = True

    seed_filestore = MagicMock()
    seed_filestore.filestore_is_populated.return_value = True

    schema_fixups = MagicMock()
    schema_fixups.apply_all.return_value = [("dmac.assistant_chat_session.extra_state", "already present")]

    seek_settings = MagicMock()
    seek_settings.apply_site_base_host.return_value = "set to http://localhost:3000"

    seed_cleanup = MagicMock()
    seed_cleanup.clear_stale_chat_sessions.return_value = 0

    users = MagicMock()
    users.verify_users_present.return_value = []

    validate = MagicMock()
    validate.run_all_health_checks.return_value = [
        SimpleNamespace(name="http", ok=True, detail="200", warn=False)
    ]

    build = MagicMock()
    volumes = MagicMock()
    volumes.ensure_volumes.return_value = ["v1"]
    volumes.REQUIRED_VOLUMES = ["v1", "v2"]

    mocks = SimpleNamespace(
        prereqs=prereqs, config=config, seed=seed, seed_filestore=seed_filestore,
        schema_fixups=schema_fixups, seek_settings=seek_settings,
        seed_cleanup=seed_cleanup, users=users, validate=validate,
        build=build, volumes=volumes,
    )
    for name in vars(mocks):
        monkeypatch.setattr(cli, name, getattr(mocks, name))
    monkeypatch.setattr(cli, "allocate_ports", lambda desired: dict(desired))
    return mocks


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

def test_install_happy_path_writes_instance_and_runs_all_phases(repo: Path, steps) -> None:
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 0, result.output
    state = load_instance(repo)
    assert state is not None
    assert state.compose_project_name == "nextseek"
    steps.build.start_seek_side.assert_called_once()
    steps.build.build_and_start_nextseek.assert_called_once()
    steps.build.start_cc_stack.assert_called_once()
    steps.validate.run_all_health_checks.assert_called_once()


def test_install_fails_fast_when_prereqs_fail(repo: Path, steps) -> None:
    steps.prereqs.run_all.return_value = [
        PrereqResult(name="docker", ok=False, detail="not installed", remediation="install it")
    ]
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 1
    steps.build.build_and_start_nextseek.assert_not_called()


def test_install_fails_when_vendored_chat_nextseek_missing(repo: Path, steps) -> None:
    (repo / "chat_nextseek" / "pyproject.toml").unlink()
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 1


def test_install_rejects_invalid_seek_public_url_with_exit_2(repo: Path, steps) -> None:
    steps.config.resolve_seek_public_url.side_effect = InvalidSeekPublicUrl("bad URL")
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 2


def test_install_prompt_accepts_after_unrecognized_and_toggle(repo: Path, steps) -> None:
    """Loop: junk input re-prompts, --no-seed toggles, y proceeds — and the
    toggled flag actually routes install down the no-seed branch."""
    result = runner.invoke(cli.app, ["install"], input="junk\n--no-seed\ny\n")
    assert result.exit_code == 0, result.output
    steps.build.start_databases.assert_called_once()   # no-seed still starts DBs
    steps.seed.load_mysql_dump.assert_not_called()
    steps.seed_filestore.load_filestore.assert_not_called()


def test_install_prompt_no_aborts(repo: Path, steps) -> None:
    result = runner.invoke(cli.app, ["install"], input="n\n")
    assert result.exit_code != 0
    assert load_instance(repo) is None  # aborted before instance save
    steps.build.build_and_start_nextseek.assert_not_called()


def test_install_existing_instance_with_port_offset_warns_then_aborts(
    repo: Path, steps,
) -> None:
    _saved_state(repo)

    result = runner.invoke(cli.app, ["install", "--port-offset", "100"], input="n\n")

    assert result.exit_code != 0
    assert "Existing install detected" in result.output
    steps.build.build_and_start_nextseek.assert_not_called()


def test_install_loads_seeds_when_databases_empty(repo: Path, steps) -> None:
    steps.seed.mysql_db_is_populated.return_value = False
    steps.seed.neo4j_is_populated.return_value = False
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 0, result.output
    loaded_dbs = [c.args[1] for c in steps.seed.load_mysql_dump.call_args_list]
    assert loaded_dbs == ["dmac", "seek_production"]
    steps.seed.load_neo4j_dump.assert_called_once()


def test_install_fails_when_seed_files_missing(repo: Path, steps) -> None:
    steps.seed.seed_files_present.return_value = ["dmac.sql.gz"]
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 1
    steps.build.build_and_start_nextseek.assert_not_called()


def test_install_renders_schema_fixup_and_site_host_warn_branches(repo: Path, steps) -> None:
    steps.schema_fixups.apply_all.return_value = [
        ("dmac.t.c", "applied"), ("dmac.t2.c2", "table missing"),
    ]
    steps.seek_settings.apply_site_base_host.return_value = "differs: existing=x"
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 0, result.output


def test_install_filestore_download_failure_is_nonfatal(repo: Path, steps) -> None:
    steps.seed_filestore.filestore_is_populated.return_value = False
    steps.seed_filestore.archive_present.return_value = False
    steps.seed_filestore.download_archive.side_effect = OSError("S3 unreachable")
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 0, result.output
    steps.seed_filestore.load_filestore.assert_not_called()


def test_install_filestore_downloads_then_loads(repo: Path, steps) -> None:
    steps.seed_filestore.filestore_is_populated.return_value = False
    # absent before download, present after
    steps.seed_filestore.archive_present.side_effect = [False, True]
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 0, result.output
    steps.seed_filestore.download_archive.assert_called_once()
    steps.seed_filestore.load_filestore.assert_called_once()


def test_install_reports_stale_chats_and_missing_users(repo: Path, steps) -> None:
    steps.seed_cleanup.clear_stale_chat_sessions.return_value = 7
    steps.users.verify_users_present.return_value = ["demo"]
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 0, result.output
    assert "7 stale chat" in result.output
    assert "missing users" in result.output


def test_install_exits_1_when_health_checks_fail(repo: Path, steps) -> None:
    steps.validate.run_all_health_checks.return_value = [
        SimpleNamespace(name="http", ok=False, detail="502", warn=False),
        SimpleNamespace(name="soft", ok=True, detail="meh", warn=True),
    ]
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 1


def test_install_warns_when_proxy_token_empty(repo: Path, steps) -> None:
    steps.config.render_proxy_secret_env.return_value.write_text(
        "AWS_BEARER_TOKEN_BEDROCK=\n"
    )
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 0, result.output
    assert "AWS_BEARER_TOKEN_BEDROCK is EMPTY" in result.output


def test_install_impl_rejects_leaked_typer_sentinels() -> None:
    import typer

    with pytest.raises(TypeError, match="OptionInfo sentinel"):
        cli._install_impl(instance=typer.Option(None), yes=True)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@patch("startup.steps.doctor.diagnose")
def test_doctor_exit_0_when_all_green(mock_diag: MagicMock, repo: Path) -> None:
    mock_diag.return_value = [("a", True, "fine"), ("b", True, "also fine")]
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    mock_diag.assert_called_once_with(repo, scope="full")


@patch("startup.steps.doctor.diagnose")
def test_doctor_app_scope_is_explicit(mock_diag: MagicMock, repo: Path) -> None:
    mock_diag.return_value = [("app", True, "bounded")]
    result = runner.invoke(cli.app, ["doctor", "--scope", "app"])
    assert result.exit_code == 0
    mock_diag.assert_called_once_with(repo, scope="app")


def test_doctor_rejects_unknown_scope(repo: Path) -> None:
    result = runner.invoke(cli.app, ["doctor", "--scope", "tiny"])
    assert result.exit_code == 2
    assert "unknown doctor scope" in result.output


@patch("startup.steps.doctor.diagnose")
def test_doctor_exit_1_when_any_check_fails(mock_diag: MagicMock, repo: Path) -> None:
    mock_diag.return_value = [("a", True, "fine"), ("b", False, "broken")]
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "broken" in result.output


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def _saved_state(repo: Path, **overrides) -> InstanceState:
    state = InstanceState(
        name="nextseek",
        prefix="",
        ports=overrides.pop(
            "ports",
            {"nextseek": 8000, "seek": 3000, "neo4j_http": 7474, "neo4j_bolt": 7687},
        ),
        compose_project_name="nextseek",
        created="2026-08-06T00:00:00",
        seek_public_url=overrides.pop("seek_public_url", "https://seek.example.org"),
        ci_profile=overrides.pop("ci_profile", ""),
    )
    save_instance(repo, state)
    return state


def test_reset_without_instance_exits_1(repo: Path) -> None:
    result = runner.invoke(cli.app, ["reset", "--yes"])
    assert result.exit_code == 1


@patch("startup.lib.docker_ops.compose_down")
def test_reset_drops_volumes_removes_config_and_reinstalls(
    mock_down: MagicMock, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _saved_state(repo)
    for rel in ["docker/db.env", "docker/nextseek.env", ".env"]:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x=1\n")
    reinstall = MagicMock()
    monkeypatch.setattr(cli, "install", reinstall)

    result = runner.invoke(cli.app, ["reset", "--yes"])
    assert result.exit_code == 0, result.output
    mock_down.assert_called_once()
    assert mock_down.call_args.kwargs["volumes"] is True
    assert not (repo / "docker" / "db.env").exists()
    assert not (repo / "startup" / ".instance.json").exists()
    # reinstall carries the stored SEEK URL and forces reseeding
    kwargs = reinstall.call_args.kwargs
    assert kwargs["no_seed"] is False
    assert kwargs["yes"] is True
    assert kwargs["seek_public_url"] == "https://seek.example.org"


@patch("startup.lib.docker_ops.compose_down")
def test_reset_keep_config_preserves_config_files(
    mock_down: MagicMock, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _saved_state(repo)
    (repo / "docker" / "db.env").write_text("x=1\n")
    monkeypatch.setattr(cli, "install", MagicMock())

    result = runner.invoke(cli.app, ["reset", "--yes", "--keep-config"])
    assert result.exit_code == 0, result.output
    assert (repo / "docker" / "db.env").exists()


@patch("startup.lib.docker_ops.compose_down")
def test_reset_confirmation_abort_leaves_stack_alone(mock_down: MagicMock, repo: Path) -> None:
    _saved_state(repo)
    result = runner.invoke(cli.app, ["reset"], input="n\n")
    assert result.exit_code != 0
    mock_down.assert_not_called()


# ---------------------------------------------------------------------------
# rebuild (no-instance guard; the push wiring is covered in test_registry_push)
# ---------------------------------------------------------------------------

def test_rebuild_without_instance_exits_1(repo: Path) -> None:
    result = runner.invoke(cli.app, ["rebuild"])
    assert result.exit_code == 1
    assert "no instance found" in result.output


def test_rebuild_rejects_unverified_source_tree(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from startup.lib import deploy_source

    _saved_state(repo)
    monkeypatch.setattr(
        deploy_source,
        "resolve_verified_source",
        lambda runtime, source: (_ for _ in ()).throw(
            deploy_source.DeploySourceError("not exact origin/dev")
        ),
    )

    result = runner.invoke(cli.app, ["rebuild", "--source-tree", str(repo)])

    assert result.exit_code == 1
    assert "not exact origin/dev" in result.output


def test_rebuild_reports_verified_rollback_tag(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from startup.lib import docker_ops
    from startup.steps import registry_push, rollback_tags

    _saved_state(repo)
    monkeypatch.setattr(
        rollback_tags,
        "create_verified",
        lambda images, build_root: (
            SimpleNamespace(tag="nextseek-nextseek:pre-test", image_id="sha256:abc"),
        ),
    )
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kwargs: None)
    monkeypatch.setattr(docker_ops, "compose_up", lambda **kwargs: None)
    monkeypatch.setattr(registry_push, "push_baselines", lambda *args, **kwargs: ())
    monkeypatch.setattr(ci_runner, "run_ci", lambda *args, **kwargs: 0)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert "rollback tag verified: nextseek-nextseek:pre-test" in result.output


# ---------------------------------------------------------------------------
# seed-filestore
# ---------------------------------------------------------------------------

def test_seed_filestore_without_instance_exits_1(repo: Path, steps) -> None:
    result = runner.invoke(cli.app, ["seed-filestore"])
    assert result.exit_code == 1


def test_seed_filestore_skips_when_populated(repo: Path, steps) -> None:
    _saved_state(repo)
    steps.seed_filestore.archive_present.return_value = True
    steps.seed_filestore.filestore_is_populated.return_value = True
    result = runner.invoke(cli.app, ["seed-filestore"])
    assert result.exit_code == 0, result.output
    steps.seed_filestore.load_filestore.assert_not_called()


def test_seed_filestore_force_reseeds_even_when_populated(repo: Path, steps) -> None:
    _saved_state(repo)
    steps.seed_filestore.archive_present.return_value = True
    steps.seed_filestore.filestore_is_populated.return_value = True
    result = runner.invoke(cli.app, ["seed-filestore", "--force"])
    assert result.exit_code == 0, result.output
    steps.seed_filestore.load_filestore.assert_called_once()


def test_seed_filestore_download_failure_exits_1_with_remediation(repo: Path, steps) -> None:
    _saved_state(repo)
    steps.seed_filestore.archive_present.return_value = False
    steps.seed_filestore.download_archive.side_effect = OSError("S3 down")
    steps.seed_filestore.FILESTORE_ARCHIVE = "startup/seed/filestore.tar.gz"
    steps.seed_filestore.FILESTORE_URL = "https://example/filestore.tar.gz"
    result = runner.invoke(cli.app, ["seed-filestore"])
    assert result.exit_code == 1
    assert "Fetch it manually" in result.output


def test_seed_filestore_downloads_then_loads(repo: Path, steps) -> None:
    _saved_state(repo)
    steps.seed_filestore.archive_present.return_value = False
    steps.seed_filestore.filestore_is_populated.return_value = False
    result = runner.invoke(cli.app, ["seed-filestore"])
    assert result.exit_code == 0, result.output
    steps.seed_filestore.download_archive.assert_called_once()
    steps.seed_filestore.load_filestore.assert_called_once()


# ---------------------------------------------------------------------------
# dump-db (maintainer-only)
# ---------------------------------------------------------------------------

def test_dump_db_without_source_env_exits_2(repo: Path) -> None:
    result = runner.invoke(cli.app, ["dump-db"])
    assert result.exit_code == 2
    assert "maintainer-only" in result.output


@patch("subprocess.run")
def test_dump_db_runs_both_dump_scripts(mock_run: MagicMock, repo: Path) -> None:
    regen = repo / "startup" / "seed" / "regenerate"
    regen.mkdir(parents=True)
    (regen / "dump-source.env").write_text("DB=dev\n")
    mock_run.return_value = MagicMock(returncode=0)
    result = runner.invoke(cli.app, ["dump-db"])
    assert result.exit_code == 0, result.output
    assert mock_run.call_count == 2


# ---------------------------------------------------------------------------
# ci (the smoke-suite shim) and the rebuild hook
#
# The shim's whole job is the argv and the environment it hands the suite, so
# every test here asserts those, not merely that something was invoked. The
# subprocess itself is recorded, never run: startup/ has no pytest-requests-
# playwright environment and must never grow one.
# ---------------------------------------------------------------------------

def _record_ci_subprocess(
    monkeypatch: pytest.MonkeyPatch, returncode: int = 0, junit_xml: str | None = None,
) -> list[SimpleNamespace]:
    """Record every subprocess the runner launches; never launch one.

    With junit_xml, the fake writes it where the argv's --junitxml= points, the
    way a real pytest run would, so the shim's summary can be asserted on.
    """
    calls: list[SimpleNamespace] = []

    def fake_run(cmd, cwd=None, env=None, **kwargs):
        calls.append(SimpleNamespace(cmd=list(cmd), cwd=cwd, env=dict(env or {})))
        if junit_xml is not None:
            target = next(a[len("--junitxml="):] for a in cmd if a.startswith("--junitxml="))
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(junit_xml)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(ci_runner.subprocess, "run", fake_run)
    return calls


def test_ci_without_instance_exits_1(repo: Path) -> None:
    result = runner.invoke(cli.app, ["ci"])
    assert result.exit_code == 1
    assert "no instance found" in result.output


def test_ci_builds_the_expected_argv_and_env(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    calls = _record_ci_subprocess(monkeypatch)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    call = calls[0]
    assert call.cmd == [
        "uv", "run", "--no-project",
        "--with", "pytest", "--with", "requests", "--with", "playwright",
        "pytest", "ci/smoke/",
        "--base-url", "http://127.0.0.1:8000",
        f"--junitxml={repo / 'startup' / '.ci-last-run.xml'}",
    ]
    assert call.cwd == repo
    assert call.env["CI_BOX_PROFILE"] == "local"
    assert call.env["PYTHONDONTWRITEBYTECODE"] == "1"
    # Never set on the unforced path: its presence is what lets a widening run.
    assert "CI_FORCE_PROFILE_CONFIRM" not in call.env
    assert "CI passed" in result.output


def test_ci_base_url_follows_the_instance_port(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local", ports={"nextseek": 8100, "seek": 3100})
    calls = _record_ci_subprocess(monkeypatch)

    assert runner.invoke(cli.app, ["ci"]).exit_code == 0
    assert "--base-url" in calls[0].cmd
    assert calls[0].cmd[calls[0].cmd.index("--base-url") + 1] == "http://127.0.0.1:8100"


def test_ci_absent_box_profile_means_prod(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: an unconfigured box gets the most restrictive profile."""
    _saved_state(repo)  # ci_profile defaults to ""
    calls = _record_ci_subprocess(monkeypatch)

    assert runner.invoke(cli.app, ["ci"]).exit_code == 0
    assert calls[0].env["CI_BOX_PROFILE"] == "prod"


def test_ci_passes_wait_ready_and_profile_through(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    calls = _record_ci_subprocess(monkeypatch)

    result = runner.invoke(cli.app, ["ci", "--wait-ready", "--profile", "prod"])

    assert result.exit_code == 0, result.output
    assert "--wait-ready" in calls[0].cmd
    assert calls[0].cmd[-2:] == ["--profile", "prod"]
    assert calls[0].env["CI_BOX_PROFILE"] == "dev"


def test_ci_inherits_the_ambient_environment(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEXTSEEK_CI_ENV and the CI_SMOKE_* overrides have to reach the suite."""
    _saved_state(repo, ci_profile="local")
    monkeypatch.setenv("NEXTSEEK_CI_ENV", "/somewhere/ci.env")
    calls = _record_ci_subprocess(monkeypatch)

    assert runner.invoke(cli.app, ["ci"]).exit_code == 0
    assert calls[0].env["NEXTSEEK_CI_ENV"] == "/somewhere/ci.env"


def test_ci_force_profile_declined_runs_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="prod")
    calls = _record_ci_subprocess(monkeypatch)

    result = runner.invoke(cli.app, ["ci", "--force-profile", "local"], input="n\n")

    assert result.exit_code == 1
    assert calls == []
    assert "Widen the CI profile to 'local'" in result.output


def test_ci_force_profile_accepted_confirms_for_that_call_only(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="prod")
    calls = _record_ci_subprocess(monkeypatch)

    result = runner.invoke(cli.app, ["ci", "--force-profile", "local"], input="y\n")

    assert result.exit_code == 0, result.output
    assert calls[0].cmd[-2:] == ["--force-profile", "local"]
    assert calls[0].env["CI_FORCE_PROFILE_CONFIRM"] == "yes"
    # The box's own declaration is untouched by a forced run.
    assert calls[0].env["CI_BOX_PROFILE"] == "prod"
    assert load_instance(repo).ci_profile == "prod"
    assert "CI_FORCE_PROFILE_CONFIRM" not in os.environ


def test_ci_exits_with_the_suite_return_code(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _record_ci_subprocess(monkeypatch, returncode=2)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 2
    assert "CI failed: exit 2, no report written" in result.output
    assert "DEPLOYMENT.md" in result.output


def _mock_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything a rebuild touches before the CI hook, stubbed out."""
    from startup.lib import docker_ops
    from startup.steps import registry_push, rollback_tags

    monkeypatch.setattr(
        rollback_tags, "create_verified", lambda images, build_root: ()
    )
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kwargs: None)
    monkeypatch.setattr(docker_ops, "compose_up", lambda **kwargs: None)
    monkeypatch.setattr(registry_push, "push_baselines", lambda *args, **kwargs: ())


def test_rebuild_runs_ci_with_the_readiness_gate(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    calls: list[SimpleNamespace] = []

    def fake_run_ci(repo_root, state, **kwargs):
        calls.append(SimpleNamespace(repo_root=repo_root, state=state, kwargs=kwargs))
        return 0

    monkeypatch.setattr(ci_runner, "run_ci", fake_run_ci)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0].repo_root == repo
    assert calls[0].state.ci_profile == "dev"
    assert calls[0].kwargs == {"wait_ready": True}
    assert "CI passed" in result.output


def test_rebuild_no_ci_skips_the_hook(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    calls = _record_ci_subprocess(monkeypatch)

    result = runner.invoke(cli.app, ["rebuild", "--no-ci"])

    assert result.exit_code == 0, result.output
    assert calls == []
    assert "running CI after rebuild" not in result.output


def test_rebuild_exits_with_the_ci_return_code(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing CI is reported and exits non-zero. It never rolls the deploy
    back: undoing a rebuild is a larger action than the one it reacts to, so the
    shim points at DEPLOYMENT.md and leaves the decision to the deployer."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    monkeypatch.setattr(ci_runner, "run_ci", lambda *args, **kwargs: 3)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 3
    # rich wraps long lines at console width — compare whitespace-free
    compact = "".join(result.output.split())
    assert "CIfailedafterrebuild:exit3,noreportwritten" in compact
    assert "Therebuilditselfsucceededandisrunning" in compact
    assert "--no-ciskipsthisstep" in compact
    assert "SeeDEPLOYMENT.mdfortherollbackprocedureifthefailuresareregressions" in compact


def test_rebuild_no_restart_does_not_run_ci_against_the_old_containers(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The suite tests the running stack over HTTP. With the restart deferred
    those containers still carry the previous image, so a run would be a
    statement about the old code either way."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    calls = _record_ci_subprocess(monkeypatch)
    ran: list[int] = []
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: ran.append(1) or 0)

    result = runner.invoke(cli.app, ["rebuild", "--no-restart"])

    assert result.exit_code == 0, result.output
    assert ran == []
    assert calls == []
    compact = "".join(result.output.split())
    assert "CIskipped:runtimerestartwasdeferred" in compact
    assert "donotcarrythenewimage" in compact


def test_rebuild_of_an_image_only_component_still_runs_ci(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cc-agent has no persistent container, so there is no deferred restart to
    invalidate the run. The skip must key on a DEFERRED restart, not on the
    absence of one."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    ran: list[dict] = []
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: ran.append(k) or 0)

    result = runner.invoke(cli.app, ["rebuild", "--component", "cc-agent"])

    assert result.exit_code == 0, result.output
    assert ran == [{"wait_ready": True}]
    assert "CI skipped" not in result.output


def test_run_ci_reports_a_missing_uv_instead_of_a_traceback(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """The runner shells out to uv. If it is not installed the operator gets a
    sentence, not a FileNotFoundError out of a deploy command."""
    def explode(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "uv")

    monkeypatch.setattr(ci_runner.subprocess, "run", explode)
    state = _saved_state(repo, ci_profile="local")

    rc = ci_runner.run_ci(repo, state, wait_ready=False)

    assert rc == 127
    assert "'uv' is not on PATH" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# --ci-profile (install) and the doctor lines that report it
# ---------------------------------------------------------------------------

def test_install_defaults_the_ci_profile_to_prod(repo: Path, steps) -> None:
    """Fail closed. A box nobody told about CI gets the narrowest profile."""
    assert runner.invoke(cli.app, ["install", "--yes"]).exit_code == 0
    assert load_instance(repo).ci_profile == "prod"


@pytest.mark.parametrize("value", ["local", "dev", "prod"])
def test_install_round_trips_the_ci_profile_into_instance_json(
    repo: Path, steps, value: str,
) -> None:
    result = runner.invoke(cli.app, ["install", "--yes", "--ci-profile", value])
    assert result.exit_code == 0, result.output
    assert load_instance(repo).ci_profile == value
    # It is shown before anything is written, not only stored.
    assert f"CI profile          {value}" in result.output


def test_install_rejects_an_unknown_ci_profile_with_exit_2(repo: Path, steps) -> None:
    """And before the banner: nothing is written, no volume is touched."""
    result = runner.invoke(cli.app, ["install", "--yes", "--ci-profile", "production"])
    assert result.exit_code == 2
    assert "unknown ci profile" in result.output
    assert "local, dev, prod" in result.output
    assert load_instance(repo) is None


@patch("startup.lib.docker_ops.compose_down")
def test_reset_carries_the_declared_ci_profile_across_the_wipe(
    mock_down: MagicMock, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """.instance.json is deleted by reset, so an unforwarded value would silently
    re-declare a dev box as prod."""
    _saved_state(repo, ci_profile="dev")
    reinstall = MagicMock()
    monkeypatch.setattr(cli, "install", reinstall)

    assert runner.invoke(cli.app, ["reset", "--yes"]).exit_code == 0
    assert reinstall.call_args.kwargs["ci_profile"] == "dev"


def _flat(output: str) -> str:
    """Doctor output with its line wrapping collapsed.

    rich wraps a long detail onto the next line at whatever width the captured
    console guesses, so asserting against a single output LINE tests the wrap
    point rather than the message.
    """
    return " ".join(output.split())


@patch("startup.steps.doctor.validate.run_all_health_checks", return_value=[])
@patch("startup.steps.doctor.prereqs.run_all", return_value=[])
@patch("startup.steps.doctor.registry_push.check_registry_baseline",
       return_value=("registry baseline", True, "ok"))
def test_doctor_reports_the_declared_ci_profile(
    _push: MagicMock, _pre: MagicMock, _health: MagicMock,
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _saved_state(repo, ci_profile="dev")
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(tmp_path / "nothing.env"))

    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "CI profile: dev (startup/.instance.json)" in _flat(result.output)


@patch("startup.steps.doctor.validate.run_all_health_checks", return_value=[])
@patch("startup.steps.doctor.prereqs.run_all", return_value=[])
@patch("startup.steps.doctor.registry_push.check_registry_baseline",
       return_value=("registry baseline", True, "ok"))
def test_doctor_reports_an_absent_ci_profile_as_prod(
    _push: MagicMock, _pre: MagicMock, _health: MagicMock,
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _saved_state(repo)  # ci_profile defaults to ""
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(tmp_path / "nothing.env"))

    result = runner.invoke(cli.app, ["doctor"])
    assert "CI profile: absent -> prod" in _flat(result.output)


@patch("startup.steps.doctor.validate.run_all_health_checks", return_value=[])
@patch("startup.steps.doctor.prereqs.run_all", return_value=[])
@patch("startup.steps.doctor.registry_push.check_registry_baseline",
       return_value=("registry baseline", True, "ok"))
def test_doctor_names_the_credential_keys_and_never_their_values(
    _push: MagicMock, _pre: MagicMock, _health: MagicMock,
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The whole point of the check: it says the keys are there, not what they say."""
    secret_user, secret_pass = "ci_smoke_realname", "hunter2-not-in-any-log"
    env_file = tmp_path / "ci.env"
    env_file.write_text(
        f"# comment\nCI_SMOKE_USER={secret_user}\nCI_SMOKE_PASS={secret_pass}\n"
    )
    _saved_state(repo, ci_profile="local")
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(env_file))

    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "names CI_SMOKE_USER, CI_SMOKE_PASS" in _flat(result.output)
    assert secret_user not in result.output, "doctor printed a credential value"
    assert secret_pass not in result.output, "doctor printed a credential value"


@patch("startup.steps.doctor.validate.run_all_health_checks", return_value=[])
@patch("startup.steps.doctor.prereqs.run_all", return_value=[])
@patch("startup.steps.doctor.registry_push.check_registry_baseline",
       return_value=("registry baseline", True, "ok"))
def test_doctor_says_what_a_missing_credential_file_will_break(
    _push: MagicMock, _pre: MagicMock, _health: MagicMock,
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _saved_state(repo, ci_profile="local")
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(tmp_path / "absent.env"))

    result = runner.invoke(cli.app, ["doctor"])
    # Reported, not failed: a box that does not run CI is not broken.
    assert result.exit_code == 0, result.output
    flat = _flat(result.output)
    assert "CI credentials:" in flat
    assert "absent -- ./startup.sh rebuild will exit 2" in flat
    assert "--no-ci" in flat


@patch("startup.steps.doctor.validate.run_all_health_checks", return_value=[])
@patch("startup.steps.doctor.prereqs.run_all", return_value=[])
@patch("startup.steps.doctor.registry_push.check_registry_baseline",
       return_value=("registry baseline", True, "ok"))
def test_doctor_reports_a_credential_file_missing_a_key(
    _push: MagicMock, _pre: MagicMock, _health: MagicMock,
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    env_file = tmp_path / "ci.env"
    env_file.write_text("CI_SMOKE_USER=someone\n")
    _saved_state(repo, ci_profile="local")
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(env_file))

    result = runner.invoke(cli.app, ["doctor"])
    assert "does not name CI_SMOKE_PASS" in _flat(result.output)
    assert "someone" not in result.output



# ---------------------------------------------------------------------------
# ci: what the operator sees before and after the run
# ---------------------------------------------------------------------------

_JUNIT_GREEN = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="1" tests="3" time="5.2">
<properties><property name="readiness_seconds" value="3"/></properties>
<testcase classname="a" name="p1" time="0.1"/>
<testcase classname="a" name="p2" time="0.1"/>
<testcase classname="a" name="x1" time="0.0"><skipped type="pytest.xfail" message="known"/></testcase>
</testsuite></testsuites>
"""

_JUNIT_RED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="0" tests="2" time="2.0">
<testcase classname="a" name="p1" time="0.1"/>
<testcase classname="a" name="f1" time="0.1"><failure message="boom">tb</failure></testcase>
</testsuite></testsuites>
"""


def _squash(text: str) -> str:
    """rich wraps at console width; compare with ALL whitespace removed (the
    older _flat above keeps spaces)."""
    return "".join(text.split())


def test_ci_prints_a_banner_before_running(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _record_ci_subprocess(monkeypatch)
    result = runner.invoke(cli.app, ["ci", "--wait-ready"])
    assert result.exit_code == 0, result.output
    flat = _squash(result.output)
    assert "CIprofile:local(startup/.instance.json)" in flat
    assert "stack:http://127.0.0.1:8000" in flat
    assert "credentials:" in flat
    assert "readiness" in flat
    assert "command:uvrun--no-project" in flat
    assert "pytestci/smoke/" in flat


def test_ci_banner_says_when_the_profile_is_absent(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="")
    _record_ci_subprocess(monkeypatch)
    result = runner.invoke(cli.app, ["ci"])
    assert "CIprofile:prod(absent" in _squash(result.output)


def test_ci_summarises_the_junit_file_it_asked_for(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _record_ci_subprocess(monkeypatch, junit_xml=_JUNIT_GREEN)
    result = runner.invoke(cli.app, ["ci"])
    assert result.exit_code == 0, result.output
    assert "CIpassed:2passed,1xfailedin0:05(readiness0:03)" in _squash(result.output)


def test_ci_failure_reports_counts_and_the_report_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _record_ci_subprocess(monkeypatch, returncode=1, junit_xml=_JUNIT_RED)
    result = runner.invoke(cli.app, ["ci"])
    assert result.exit_code == 1
    flat = _squash(result.output)
    assert "CIfailed:1failed,1passedin0:02" in flat
    assert ".ci-last-run.xml" in flat


def test_ci_without_a_report_falls_back_to_the_exit_code(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _record_ci_subprocess(monkeypatch, returncode=2)   # e.g. a refused profile: no tests ran
    result = runner.invoke(cli.app, ["ci"])
    assert result.exit_code == 2
    assert "CIfailed:exit2,noreportwritten" in _squash(result.output)


def test_ci_never_reports_a_stale_report(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A junit file from an earlier run must not be summarised as this run's."""
    _saved_state(repo, ci_profile="local")
    stale = repo / "startup" / ".ci-last-run.xml"
    stale.write_text(_JUNIT_GREEN)
    _record_ci_subprocess(monkeypatch, returncode=2)   # this run writes nothing
    result = runner.invoke(cli.app, ["ci"])
    assert "2passed" not in _squash(result.output)
    assert not stale.exists()


# --------------------------------------------------------------------------- #
# install's CI pointer: the suite cannot run here yet, so say what it needs
# --------------------------------------------------------------------------- #


def test_install_ci_next_steps_name_the_profile_and_both_entry_points(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(tmp_path / "ci.env"))
    flat = _squash("\n".join(cli._ci_next_step_lines("local")))
    assert "profile'local'" in flat
    assert "./startup.shci" in flat
    assert "./startup.shrebuild" in flat


def test_install_ci_next_steps_spell_out_the_prerequisites_when_creds_are_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A fresh install has no credential file and no account that has logged in,
    which is exactly why install cannot just run the suite itself."""
    missing = tmp_path / "ci.env"
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(missing))
    lines = cli._ci_next_step_lines("local")
    flat = _squash("\n".join(lines))
    assert str(missing) in "\n".join(lines)
    assert "CI_SMOKE_USER" in flat and "CI_WRITE_USER" in flat
    assert "/login/" in flat


def test_install_ci_next_steps_shorten_to_a_run_command_once_creds_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    present = tmp_path / "ci.env"
    present.write_text("CI_SMOKE_USER=x\n")
    monkeypatch.setenv("NEXTSEEK_CI_ENV", str(present))
    flat = _squash("\n".join(cli._ci_next_step_lines("dev")))
    assert "CI_SMOKE_USER" not in flat, (
        "the setup instructions are for a box that still needs them"
    )
    assert "./startup.shci" in flat


def test_install_ci_next_steps_abbreviate_a_home_relative_credential_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same `~` treatment the CI banner gives it: an absolute /home/<user> path
    in operator-facing output is noise, and on a shared box it is another
    person's username."""
    monkeypatch.delenv("NEXTSEEK_CI_ENV", raising=False)
    joined = "\n".join(cli._ci_next_step_lines("prod"))
    assert "~/.config/nextseek/ci.env" in joined
    assert str(Path.home()) not in joined
