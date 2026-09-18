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
    (tmp_path / "NessieAI" / "chat_nextseek").mkdir(parents=True)
    (tmp_path / "NessieAI" / "chat_nextseek" / "pyproject.toml").write_text("[project]\n")
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

    disk_preflight = MagicMock()
    disk_preflight.run_preflight.return_value = SimpleNamespace(
        proceed=True, floor_gb=20, freed_gb=0.0, reason="above floor"
    )

    mocks = SimpleNamespace(
        prereqs=prereqs, config=config, seed=seed, seed_filestore=seed_filestore,
        schema_fixups=schema_fixups, seek_settings=seek_settings,
        seed_cleanup=seed_cleanup, users=users, validate=validate,
        build=build, volumes=volumes, disk_preflight=disk_preflight,
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
    (repo / "NessieAI" / "chat_nextseek" / "pyproject.toml").unlink()
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 1
    assert "NessieAI/chat_nextseek/ is missing" in result.output


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
def test_reset_removes_the_proxy_token_at_both_homes(
    mock_down: MagicMock, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reset claims to remove config. A box that never moved its token after
    the NessieAI move still holds it at the old path, and reset must not
    leave the real token behind there."""
    _saved_state(repo)
    homes = [
        repo / "NessieAI" / "docker" / "bedrock-proxy" / "proxy-secret.env",
        repo / "docker" / "bedrock-proxy" / "proxy-secret.env",
    ]
    for p in homes:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('AWS_BEARER_TOKEN_BEDROCK="ABSK-x"\n')
    monkeypatch.setattr(cli, "install", MagicMock())

    result = runner.invoke(cli.app, ["reset", "--yes"])

    assert result.exit_code == 0, result.output
    assert [p for p in homes if p.exists()] == []


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
    from startup.steps import disk_preflight, registry_push, rollback_tags, validate

    _saved_state(repo)
    monkeypatch.setattr(
        rollback_tags,
        "create_verified",
        lambda images, build_root: (
            SimpleNamespace(
                source="nextseek-nextseek:latest",
                tag="nextseek-nextseek:pre-test",
                image_id="sha256:abc",
            ),
        ),
    )
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kwargs: None)
    monkeypatch.setattr(docker_ops, "compose_up", lambda **kwargs: None)
    monkeypatch.setattr(registry_push, "push_baselines", lambda *args, **kwargs: ())
    monkeypatch.setattr(
        validate,
        "check_first_party_images",
        lambda compose_project_name: validate.HealthResult(
            name="first-party images", ok=True, detail="all 4 present"
        ),
    )
    monkeypatch.setattr(
        disk_preflight, "run_preflight",
        lambda **kwargs: SimpleNamespace(proceed=True, floor_gb=20, freed_gb=0.0, reason="ok"),
    )
    monkeypatch.setattr(ci_runner, "run_ci", lambda *args, **kwargs: 0)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert "rollback tag verified: nextseek-nextseek:pre-test" in result.output
    # A source that WAS tagged must not also be announced as a first build.
    assert "FIRST BUILD" not in result.output


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

def _suite_call(calls: list[SimpleNamespace]) -> SimpleNamespace:
    """The one call that invoked the suite. Fails loudly if there is not exactly one.

    The shim makes other subprocess calls now (`docker inspect`, to name its CI
    record), so indexing calls[0] would pin the test to their ORDER. What matters
    is that the suite is invoked once, with the right argv.
    """
    suite = [c for c in calls if "pytest" in c.cmd]
    assert len(suite) == 1, f"expected one suite invocation, got {len(suite)}: {calls}"
    return suite[0]


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
        # Only the suite invocation carries --junitxml. The shim also asks docker
        # for the running image so it can name its markdown record, and that call
        # writes no report -- so look for the flag rather than assume every
        # subprocess is the suite.
        target = next((a[len("--junitxml="):] for a in cmd
                       if str(a).startswith("--junitxml=")), None)
        if junit_xml is not None and target is not None:
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
    call = _suite_call(calls)
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
    # Narrowed to prod, the run gets prod's rule: no Nessie lane.
    assert calls[0].cmd[-3:] == ["--profile", "prod", "--no-nessie"]
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
    # A box declaring prod never runs the Nessie lane, however far it is widened.
    assert calls[0].cmd[-3:] == ["--force-profile", "local", "--no-nessie"]
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


def _stub_stack_health(
    monkeypatch: pytest.MonkeyPatch,
    *,
    runtimes_ok: bool = True,
    runtimes_detail: str = "nextseek + nextseek_nginx running",
    images_ok: bool = True,
    image_detail: str = "all 4 present",
    context: tuple[bool, str, bool] = (
        True, "dmac-assistant:poc bakes all 6 canonical context files", False),
) -> list[dict]:
    """The stack-health step, answered without asking docker. Returns the calls,
    each as the keyword arguments it was given, so a test can see the checkout
    the cc-agent context was compared with."""
    from startup.steps import validate

    context_ok, context_detail, context_warn = context
    calls: list[dict] = []

    def health(repo_root, env, compose_project_name, **kwargs):
        calls.append({"repo_root": repo_root, **kwargs})
        return validate.StackHealth(
            blocking=(validate.HealthResult("app + front door", runtimes_ok, runtimes_detail),),
            advisory=(
                validate.HealthResult("first-party images", images_ok, image_detail),
                validate.HealthResult("cc services", True, "bedrock-proxy + nextseek-sidecar running"),
                validate.HealthResult("cc-agent context", context_ok, context_detail,
                                      warn=context_warn),
            ),
        )

    monkeypatch.setattr(validate, "stack_health", health)
    return calls


@pytest.fixture(autouse=True)
def _the_stack_is_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here may ask a real docker daemon about a real stack. Tests about
    a down stack override this."""
    _stub_stack_health(monkeypatch)


def _stub_nessie_prerequisites(
    monkeypatch: pytest.MonkeyPatch, *results: tuple[str, bool, str],
) -> list[tuple]:
    """The Nessie lane's prerequisites, answered without asking docker. Returns
    the calls, so a test can prove the check ran, or did not."""
    from startup.steps import validate

    answer = tuple(validate.HealthResult(n, ok, d) for n, ok, d in results) or (
        validate.HealthResult("bedrock proxy token", True, "token present"),
        validate.HealthResult("first-party images", True, "all 4 present"),
        validate.HealthResult("cc services", True, "bedrock-proxy + nextseek-sidecar running"),
        validate.HealthResult("CC runner", True, "(True, 'ok')"),
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        validate, "nessie_prerequisites",
        lambda repo_root, env, compose_project_name:
            calls.append((repo_root, compose_project_name)) or answer,
    )
    return calls


@pytest.fixture(autouse=True)
def _the_nessie_prerequisites_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local or dev box turns the Nessie lane on, which checks the CC stack
    through docker. Tests about a missing prerequisite override this."""
    _stub_nessie_prerequisites(monkeypatch)


def _mock_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    *,
    images_ok: bool = True,
    image_detail: str = "all 4 present",
    runtimes_ok: bool = True,
    runtimes_detail: str = "nextseek + nextseek_nginx running",
) -> None:
    """Everything a rebuild touches before the CI hook, stubbed out.

    ``create_verified`` returning ``()`` is not a shortcut: it is what the real
    one returns when none of the requested sources exist yet, so these tests all
    exercise the first-build path.
    """
    from startup.lib import docker_ops
    from startup.steps import disk_preflight, registry_push, rollback_tags

    monkeypatch.setattr(
        rollback_tags, "create_verified", lambda images, build_root: ()
    )
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kwargs: None)
    monkeypatch.setattr(docker_ops, "compose_up", lambda **kwargs: None)
    monkeypatch.setattr(registry_push, "push_baselines", lambda *args, **kwargs: ())
    _stub_stack_health(
        monkeypatch,
        runtimes_ok=runtimes_ok, runtimes_detail=runtimes_detail,
        images_ok=images_ok, image_detail=image_detail,
    )
    monkeypatch.setattr(
        disk_preflight, "run_preflight",
        lambda **kwargs: SimpleNamespace(proceed=True, floor_gb=20, freed_gb=0.0, reason="ok"),
    )


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
    # A bare rebuild is an app rebuild, and dev allows the Nessie lane.
    assert calls[0].kwargs == {"wait_ready": True, "nessie": True}
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
    # A component rebuild runs the suite without the Nessie lane.
    assert ran == [{"wait_ready": True, "nessie": False}]
    assert "CI skipped" not in result.output


def _record_compose_up(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    from startup.lib import docker_ops

    calls: list[dict] = []
    monkeypatch.setattr(docker_ops, "compose_up", lambda **kwargs: calls.append(kwargs))
    return calls


def test_rebuild_starts_a_stopped_front_door_without_recreating_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped nginx survived every rebuild on 2026-09-10, and the suite then
    spent its whole readiness floor on a refused port. `up` without
    --force-recreate is a no-op on a running nginx and a start on a stopped one;
    --no-deps keeps it from touching anything else, which is the rule an
    unscoped recreate taught on 2026-09-02."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    calls = _record_compose_up(monkeypatch)
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    front_door = [c for c in calls if "nextseek_nginx" in c["services"]]
    assert len(front_door) == 1, calls
    assert list(front_door[0]["services"]) == ["nextseek_nginx"]
    assert front_door[0]["no_deps"] is True
    assert front_door[0].get("force_recreate", False) is False


def test_rebuild_starts_the_front_door_after_the_app_restart(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    calls = _record_compose_up(monkeypatch)
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    runner.invoke(cli.app, ["rebuild"])

    assert [list(c["services"]) for c in calls] == [["nextseek"], ["nextseek_nginx"]]


def test_rebuild_no_restart_leaves_the_front_door_alone(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-restart asks for no runtime to be touched. That includes nginx."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    calls = _record_compose_up(monkeypatch)

    result = runner.invoke(cli.app, ["rebuild", "--no-restart"])

    assert result.exit_code == 0, result.output
    assert calls == []


_NGINX_DOWN = ("not running: nextseek_nginx -- start it with: "
               "docker compose up -d --no-deps nextseek_nginx")


def test_rebuild_does_not_run_ci_against_a_stack_that_is_down(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stack health is step 1 of CI. With the front door down every test fails
    the same way, so the suite is not started, and the rebuild says why."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch, runtimes_ok=False, runtimes_detail=_NGINX_DOWN)
    ran: list[dict] = []
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: ran.append(k) or 0)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1
    assert ran == []
    compact = "".join(result.output.split())
    assert "notrunning:nextseek_nginx" in compact
    assert "CInotrun" in compact
    assert "CIpassed" not in compact


def test_rebuild_prints_stack_health_before_the_ci_run(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["rebuild"])

    out = result.output
    assert out.index("app + front door") < out.index("running CI after rebuild")
    assert out.index("cc services") < out.index("running CI after rebuild")


def test_rebuild_hands_the_health_lines_to_the_ci_record(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One file per deploy says everything about it: health and suite together."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch, images_ok=False, image_detail="ABSENT: dmac-assistant:poc")
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)
    written: list[dict] = []
    monkeypatch.setattr(ci_runner, "write_report", lambda *a, **k: written.append(k))

    runner.invoke(cli.app, ["rebuild"])

    assert len(written) == 1
    assert ("first-party images", False, "ABSENT: dmac-assistant:poc") in written[0]["health"]
    assert written[0]["health"][0][0] == "app + front door"


def test_ci_does_not_run_the_suite_against_a_stack_that_is_down(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _stub_stack_health(monkeypatch, runtimes_ok=False, runtimes_detail=_NGINX_DOWN)
    calls = _record_ci_subprocess(monkeypatch)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 1
    assert [c for c in calls if "pytest" in c.cmd] == []
    compact = "".join(result.output.split())
    assert "notrunning:nextseek_nginx" in compact
    assert "CInotrun" in compact


def test_ci_still_runs_the_suite_when_only_an_advisory_check_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ci` answers one question, what the suite says. An absent CC image is
    printed but does not change that answer; `rebuild` is what exits red on it."""
    _saved_state(repo, ci_profile="local")
    _stub_stack_health(monkeypatch, images_ok=False, image_detail="ABSENT: dmac-assistant:poc")
    calls = _record_ci_subprocess(monkeypatch)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 0, result.output
    _suite_call(calls)
    assert "ABSENT: dmac-assistant:poc" in result.output


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
# the Nessie lane: on for app rebuilds and `ci` on local and dev, never on prod
# ---------------------------------------------------------------------------

def _spy_ci(monkeypatch: pytest.MonkeyPatch, rc: int = 0) -> SimpleNamespace:
    """Record what build_command, run_ci and write_report were asked; run nothing.

    build_command is the real one (its argv is what the banner prints); run_ci is
    replaced, so no suite runs; running_image is stubbed, so no docker is asked.
    """
    seen = SimpleNamespace(build=[], run=[], report=[])
    real_build = ci_runner.build_command

    def build(*args, **kwargs):
        seen.build.append(kwargs)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(ci_runner, "build_command", build)
    monkeypatch.setattr(ci_runner, "run_ci",
                        lambda *a, **k: seen.run.append(k) or rc)
    monkeypatch.setattr(ci_runner, "running_image", lambda *a, **k: (None, None))
    monkeypatch.setattr(ci_runner, "write_report",
                        lambda *a, **k: seen.report.append(k) or None)
    return seen


@pytest.mark.parametrize("profile", ["local", "dev"])
def test_ci_runs_the_nessie_lane_on_a_local_or_dev_box(
    repo: Path, monkeypatch: pytest.MonkeyPatch, profile: str,
) -> None:
    _saved_state(repo, ci_profile=profile)
    checked = _stub_nessie_prerequisites(monkeypatch)
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 0, result.output
    assert [k["nessie"] for k in seen.build] == [True]
    assert [k["nessie"] for k in seen.run] == [True]
    assert checked == [(repo, "nextseek")]


def test_ci_no_nessie_turns_the_lane_off_and_checks_nothing_for_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    checked = _stub_nessie_prerequisites(monkeypatch)
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["ci", "--no-nessie"])

    assert result.exit_code == 0, result.output
    assert [k["nessie"] for k in seen.build] == [False]
    assert [k["nessie"] for k in seen.run] == [False]
    assert checked == []
    assert seen.report[0]["nessie_summary"] is None
    assert seen.report[0]["nessie_ran"] is False


@pytest.mark.parametrize("profile", ["prod", ""])
def test_ci_never_runs_the_nessie_lane_on_prod_or_an_undeclared_box(
    repo: Path, monkeypatch: pytest.MonkeyPatch, profile: str,
) -> None:
    """An absent profile is prod (fail closed), and prod allows no model spend."""
    _saved_state(repo, ci_profile=profile)
    checked = _stub_nessie_prerequisites(monkeypatch)
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 0, result.output
    assert [k["nessie"] for k in seen.run] == [False]
    assert checked == []


def test_ci_narrowed_to_prod_runs_no_nessie_lane(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dev box asked to run as prod gets prod's rule: the suite would skip the
    lane by its profiles marker, so its prerequisites must not stop the run."""
    _saved_state(repo, ci_profile="dev")
    checked = _stub_nessie_prerequisites(monkeypatch)
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["ci", "--profile", "prod"])

    assert result.exit_code == 0, result.output
    assert [k["nessie"] for k in seen.run] == [False]
    assert checked == []


def test_ci_stops_before_the_suite_when_a_nessie_prerequisite_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _stub_nessie_prerequisites(
        monkeypatch,
        ("bedrock proxy token", False, "EMPTY: CC model calls are disabled"),
        ("first-party images", True, "all 4 present"),
        ("cc services", True, "bedrock-proxy + nextseek-sidecar running"),
        ("CC runner", True, "(True, 'ok')"),
    )
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 1
    assert seen.run == []
    flat = _squash(result.output)
    assert "bedrockproxytoken" in flat
    assert "TheNessielanecannotrun:bedrockproxytoken:EMPTY" in flat
    assert "--no-nessie" in flat
    assert "rebuilditselfsucceeded" not in flat


def test_ci_hands_the_nessie_summary_to_the_ci_record(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    seen = _spy_ci(monkeypatch)
    summary = {"questions": [], "spent_usd": 0.0}
    monkeypatch.setattr(ci_runner, "read_nessie_summary", lambda repo_root: summary)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 0, result.output
    assert seen.report[0]["nessie_summary"] is summary
    assert seen.report[0]["nessie_ran"] is True


def test_ci_tells_the_ci_record_the_lane_ran_when_it_wrote_no_summary(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lane that died before chat_run's teardown writes no summary but may leave a
    trace. The record must still file that evidence (spec 3.4), so it is told the
    lane ran even though there is no summary to render."""
    _saved_state(repo, ci_profile="local")
    seen = _spy_ci(monkeypatch, rc=1)
    monkeypatch.setattr(ci_runner, "read_nessie_summary", lambda repo_root: None)

    result = runner.invoke(cli.app, ["ci"])

    assert result.exit_code == 1, result.output
    assert seen.report[0]["nessie_ran"] is True
    assert seen.report[0]["nessie_summary"] is None


def test_rebuild_tells_the_ci_record_the_lane_ran_when_it_wrote_no_summary(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    seen = _spy_ci(monkeypatch, rc=1)
    monkeypatch.setattr(ci_runner, "read_nessie_summary", lambda repo_root: None)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1, result.output
    assert seen.report[0]["nessie_ran"] is True
    assert seen.report[0]["nessie_summary"] is None


def test_rebuild_of_the_app_runs_the_nessie_lane_on_a_local_box(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _mock_rebuild(monkeypatch)
    checked = _stub_nessie_prerequisites(monkeypatch)
    seen = _spy_ci(monkeypatch)
    summary = {"questions": [], "spent_usd": 0.0}
    monkeypatch.setattr(ci_runner, "read_nessie_summary", lambda repo_root: summary)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert [k["nessie"] for k in seen.build] == [True]
    assert [k["nessie"] for k in seen.run] == [True]
    assert checked == [(repo, "nextseek")]
    assert seen.report[0]["nessie_summary"] is summary


def test_rebuild_of_a_component_skips_the_nessie_lane(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four component rebuilds must not pay for four runs (spec decision 2)."""
    _saved_state(repo, ci_profile="local")
    _mock_rebuild(monkeypatch)
    checked = _stub_nessie_prerequisites(monkeypatch)
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["rebuild", "--component", "cc-agent"])

    assert result.exit_code == 0, result.output
    assert [k["nessie"] for k in seen.run] == [False]
    assert checked == []
    assert seen.report[0]["nessie_summary"] is None
    assert seen.report[0]["nessie_ran"] is False


def test_rebuild_no_nessie_turns_the_lane_off(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    checked = _stub_nessie_prerequisites(monkeypatch)
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["rebuild", "--no-nessie"])

    assert result.exit_code == 0, result.output
    assert [k["nessie"] for k in seen.run] == [False]
    assert checked == []


def test_rebuild_on_prod_never_runs_the_nessie_lane(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="prod")
    _mock_rebuild(monkeypatch)
    checked = _stub_nessie_prerequisites(monkeypatch)
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert [k["nessie"] for k in seen.run] == [False]
    assert checked == []


def test_rebuild_stops_before_the_suite_when_a_nessie_prerequisite_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    _stub_nessie_prerequisites(
        monkeypatch,
        ("bedrock proxy token", True, "token present"),
        ("first-party images", True, "all 4 present"),
        ("cc services", False, "not running: nextseek-sidecar"),
        ("CC runner", True, "(True, 'ok')"),
    )
    seen = _spy_ci(monkeypatch)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1
    assert seen.run == []
    flat = _squash(result.output)
    assert "Therebuilditselfsucceeded,buttheNessielanecannotrun" in flat
    assert "ccservices:notrunning:nextseek-sidecar" in flat
    assert "--no-nessie" in flat


@pytest.mark.parametrize("component, expected", [
    ("app", True), ("nextseek", True),
    ("cc-agent", False), ("agent", False),
    ("bedrock-proxy", False), ("proxy", False),
    ("nextseek-sidecar", False), ("sidecar", False),
    ("custom-stack", False), ("all", False),
])
def test_only_an_app_rebuild_counts_as_one(component: str, expected: bool) -> None:
    """A bare rebuild and --component app (or its alias) are app rebuilds; every
    other component, custom-stack included, is not."""
    from startup.lib.rebuild_policy import resolve_component

    assert cli._is_app_rebuild(resolve_component(component, "nextseek")) is expected


def test_a_bare_rebuild_is_an_app_rebuild() -> None:
    """The option's own default, read from the command, not restated here."""
    import inspect

    from startup.lib.rebuild_policy import resolve_component

    default = inspect.signature(cli.rebuild).parameters["component"].default.default
    assert cli._is_app_rebuild(resolve_component(default, "nextseek")) is True


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


def test_rebuild_exits_non_zero_when_a_first_party_image_is_absent_afterwards(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The incident this exists for: a deploy that finishes green while
    dmac-assistant:poc is gone, taking Container-CC down until a user sends a
    chat turn. `./startup.sh doctor` reports it too, but doctor's exit code is
    read by nothing -- the rebuild hook's is."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(
        monkeypatch,
        images_ok=False,
        image_detail=(
            "ABSENT: dmac-assistant:poc -- build with: "
            "./startup.sh rebuild --component cc-agent"
        ),
    )
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1
    compact = "".join(result.output.split())
    assert "ABSENT:dmac-assistant:poc" in compact
    assert "--componentcc-agent" in compact
    # The absent image must not suppress the CI run: it is a separate defect,
    # and the suite is the slow half of the deploy to throw away.
    assert "CIpassed" in compact


def test_rebuild_image_health_failure_yields_to_a_failing_ci_code(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both broken: report both, exit with CI's code, which is the specific one."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch, images_ok=False, image_detail="ABSENT: dmac-assistant:poc")
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 3)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 3
    compact = "".join(result.output.split())
    assert "ABSENT:dmac-assistant:poc" in compact
    assert "CIfailedafterrebuild" in compact


def test_rebuild_with_no_ci_still_exits_non_zero_on_absent_images(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch, images_ok=False, image_detail="ABSENT: dmac-assistant:poc")

    result = runner.invoke(cli.app, ["rebuild", "--no-ci"])

    assert result.exit_code == 1
    assert "ABSENT: dmac-assistant:poc" in result.output


_STALE_CONTEXT = (
    False,
    "STALE: dmac-assistant:poc bakes 1 of 6 canonical context files unlike the "
    "checkout: capabilities.md (differs). The CC agent reads its baked copy until "
    "you run: ./startup.sh rebuild --component cc-agent",
    False,
)


def test_rebuild_exits_non_zero_when_the_cc_agent_bakes_stale_context(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap this exists for: a canonical context file was edited and only the
    app was rebuilt. Nothing else fails, and the agent serves the old copy."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    _stub_stack_health(monkeypatch, context=_STALE_CONTEXT)
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1
    compact = "".join(result.output.split())
    assert "✗cc-agentcontext:STALE" in compact
    assert "capabilities.md(differs)" in compact
    assert "./startup.shrebuild--componentcc-agent" in compact
    # A stale agent changes nothing the smoke suite asks, so it still runs.
    assert "CIpassed" in compact


def test_rebuild_with_no_ci_still_exits_non_zero_on_stale_context(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    _stub_stack_health(monkeypatch, context=_STALE_CONTEXT)

    result = runner.invoke(cli.app, ["rebuild", "--no-ci"])

    assert result.exit_code == 1
    assert "capabilities.md (differs)" in result.output


def test_ci_prints_stale_context_but_answers_what_the_suite_says(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="local")
    _stub_stack_health(monkeypatch, context=_STALE_CONTEXT)
    calls = _record_ci_subprocess(monkeypatch)

    result = runner.invoke(cli.app, ["ci", "--no-nessie"])

    assert result.exit_code == 0, result.output
    _suite_call(calls)
    assert "capabilities.md (differs)" in result.output


def test_rebuild_compares_the_cc_agent_context_with_the_checkout_it_built(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    calls = _stub_stack_health(monkeypatch)

    runner.invoke(cli.app, ["rebuild", "--no-ci"])

    assert [call["checkout"] for call in calls] == [repo]


def test_rebuild_from_a_source_tree_compares_the_context_with_that_tree(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The runtime checkout may carry another operator's edits (DEPLOYMENT.md
    §3.3); the images were built from the clean tree, so that is the one the
    agent's baked copy must equal."""
    from startup.lib import deploy_source

    clean = tmp_path / "clean"
    clean.mkdir()
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    monkeypatch.setattr(deploy_source, "resolve_verified_source",
                        lambda runtime, source: clean)
    calls = _stub_stack_health(monkeypatch)

    result = runner.invoke(cli.app, ["rebuild", "--no-ci", "--source-tree", str(clean)])

    assert result.exit_code == 0, result.output
    assert [call["checkout"] for call in calls] == [clean]


def test_stack_health_prints_a_skipped_check_as_a_warning(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check that compared nothing must not print as a green tick."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    _stub_stack_health(monkeypatch, context=(
        True, "skipped: dmac-assistant:poc is absent, so there is no baked context "
              "to compare", True))

    result = runner.invoke(cli.app, ["rebuild", "--no-ci"])

    compact = "".join(result.output.split())
    assert "!cc-agentcontext:skipped" in compact
    assert "✓cc-agentcontext" not in compact


def test_rebuild_announces_a_first_build_when_no_rollback_source_exists(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_verified returns no tag for an image that does not exist yet.
    Saying nothing there reads exactly like 'a rollback point was made'."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["rebuild", "--component", "cc-agent"])

    assert result.exit_code == 0, result.output
    compact = "".join(result.output.split())
    assert "dmac-assistant:poc" in compact
    assert "FIRSTBUILD" in compact
    assert "norollbackpoint" in compact


# ---------------------------------------------------------------------------
# disk preflight, the first step of both build commands
# ---------------------------------------------------------------------------

def _capture_preflight(monkeypatch, proceed: bool = True) -> list[dict]:
    from startup.steps import disk_preflight

    seen: list[dict] = []

    def fake(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(proceed=proceed, floor_gb=20, freed_gb=0.0, reason="t")

    monkeypatch.setattr(disk_preflight, "run_preflight", fake)
    return seen


def test_rebuild_measures_disk_before_it_builds_anything(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="prod")
    _mock_rebuild(monkeypatch)
    seen = _capture_preflight(monkeypatch)
    built: list[int] = []
    from startup.lib import docker_ops
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kw: built.append(1))
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert seen[0]["ci_profile"] == "prod"
    assert seen[0]["compose_project_name"] == "nextseek"
    assert built == [1]


def test_rebuild_stops_before_building_when_the_preflight_refuses(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short of disk is a refusal to start, not a warning to read afterwards."""
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    _capture_preflight(monkeypatch, proceed=False)
    built: list[int] = []
    from startup.lib import docker_ops
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kw: built.append(1))

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1
    assert built == []


def test_rebuild_no_disk_check_asks_the_preflight_to_skip(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    seen = _capture_preflight(monkeypatch)
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["rebuild", "--no-disk-check"])

    assert result.exit_code == 0, result.output
    assert seen[0]["skip"] is True


def test_rebuild_disk_floor_overrides_the_profile(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="prod")
    _mock_rebuild(monkeypatch)
    seen = _capture_preflight(monkeypatch)
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)

    result = runner.invoke(cli.app, ["rebuild", "--disk-floor", "8"])

    assert result.exit_code == 0, result.output
    assert seen[0]["floor_override"] == 8


# The three pre-NessieAI lines a box's rendered docker/nextseek.env carried on
# 2026-09-10. "/app" is joined on separately so the stale-path grep the move is
# verified with (over startup/) keeps returning nothing but real regressions.
_PRE = "/app"
_PRE_MOVE_ENV = (
    'SEEK_HOST="seek"\n'
    f'CATALOG_FILE="{_PRE}/chat_nextseek/agent_model_catalog.json"\n'
    f"DMAC_ROUTE_CAPABILITIES_FILE={_PRE}/dmac_assistant/build_context/route_capabilities.json\n"
    f"DMAC_ROUTER_MODEL_CLASS_MAP_FILE={_PRE}"
    "/dmac_assistant/build_context/router_model_class_map.json\n"
)


@pytest.mark.parametrize(
    "component", ["app", "cc-agent", "bedrock-proxy", "nextseek-sidecar", "custom-stack"]
)
def test_rebuild_refuses_a_nextseek_env_with_pre_move_paths(
    repo: Path, monkeypatch: pytest.MonkeyPatch, component: str,
) -> None:
    """rebuild never re-renders docker/nextseek.env, so it must refuse to
    recreate anything on top of one that names the pre-move layout, before the
    disk review, the rollback tags or the build."""
    from startup.lib import docker_ops
    from startup.steps import rollback_tags

    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    disk_seen = _capture_preflight(monkeypatch)
    tagged: list[int] = []
    monkeypatch.setattr(
        rollback_tags, "create_verified", lambda images, build_root: tagged.append(1) or ()
    )
    built: list[int] = []
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kw: built.append(1))
    (repo / "docker" / "nextseek.env").write_text(_PRE_MOVE_ENV)

    result = runner.invoke(cli.app, ["rebuild", "--component", component])

    assert result.exit_code == 1, result.output
    assert (disk_seen, tagged, built) == ([], [], [])
    flat = _flat(result.output)
    assert "stopped before building" in flat
    for line, key in ((2, "CATALOG_FILE"), (3, "DMAC_ROUTE_CAPABILITIES_FILE"),
                      (4, "DMAC_ROUTER_MODEL_CLASS_MAP_FILE")):
        assert f"line {line} {key}" in flat


def test_rebuild_proceeds_once_the_env_names_the_nessieai_paths(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _mock_rebuild(monkeypatch)
    _capture_preflight(monkeypatch)
    built: list[int] = []
    from startup.lib import docker_ops
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kw: built.append(1))
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: 0)
    (repo / "docker" / "nextseek.env").write_text(
        'SEEK_HOST="seek"\n'
        'CATALOG_FILE="/app/NessieAI/chat_nextseek/agent_model_catalog.json"\n'
    )

    result = runner.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert built == [1]


def test_install_measures_disk_too(repo: Path, steps) -> None:
    result = runner.invoke(cli.app, ["install", "--yes", "--ci-profile", "dev"])
    assert result.exit_code == 0, result.output
    steps.disk_preflight.run_preflight.assert_called_once()
    assert steps.disk_preflight.run_preflight.call_args.kwargs["ci_profile"] == "dev"


def test_install_stops_when_the_preflight_refuses(repo: Path, steps) -> None:
    steps.disk_preflight.run_preflight.return_value = SimpleNamespace(
        proceed=False, floor_gb=30, freed_gb=0.0, reason="still short"
    )
    result = runner.invoke(cli.app, ["install", "--yes"])
    assert result.exit_code == 1
    steps.build.build_and_start_nextseek.assert_not_called()
