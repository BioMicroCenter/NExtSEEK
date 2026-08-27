"""CliRunner-driven tests for every startup CLI command.

These run the real typer app end-to-end — real argument parsing, real
`.instance.json` round-trips through save_instance/load_instance, real prompt
handling — with the step modules mocked at the cli namespace boundary. They
exist to cover the orchestration in cli.py itself: phase ordering, branch
selection (populated-vs-load, --no-seed, filestore fallback), failure exits,
and the interactive confirm loop.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from startup import cli
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
        ports={"nextseek": 8000, "seek": 3000, "neo4j_http": 7474, "neo4j_bolt": 7687},
        compose_project_name="nextseek",
        created="2026-08-06T00:00:00",
        seek_public_url=overrides.pop("seek_public_url", "https://seek.example.org"),
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
