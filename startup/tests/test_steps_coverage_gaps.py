"""Unit tests closing the coverage gaps across startup step/lib modules.

Companion to the per-module test files (pattern: test_cli_startup_gaps.py) —
each section below targets branches the original suites never exercised:
error fallbacks, retry loops, and the less-traveled subcommand paths of the
deployment-critical startup code.
"""
from __future__ import annotations

import gzip
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from startup.lib import env as env_lib
from startup.lib import ports, ui
from startup.lib.docker_ops import (
    DockerOpsError,
    compose_down,
    compose_exec,
    compose_port,
    compose_up,
)
from startup.steps import build, prereqs, schema_fixups, seed, seed_cleanup, seed_filestore, users
from startup.steps.schema_fixups import MissingColumn


# ---------------------------------------------------------------------------
# lib/docker_ops — remaining commands and branches
# ---------------------------------------------------------------------------

@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_up_build_flag(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(services=["nextseek"], project_dir="/repo", env={}, build=True)
    assert "--build" in mock_run.call_args.args[0]


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_down_with_and_without_volumes(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_down(project_dir="/repo", env={})
    assert "-v" not in mock_run.call_args.args[0]
    compose_down(project_dir="/repo", env={}, volumes=True)
    assert "-v" in mock_run.call_args.args[0]


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_down_raises_on_failure(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="down broke")
    with pytest.raises(DockerOpsError, match="down broke"):
        compose_down(project_dir="/repo", env={})


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_exec_stdin_branch_passes_bytes(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout=b"done", stderr=b"")
    out = compose_exec(
        service="db", command=["mysql"], project_dir="/repo", env={}, stdin=b"payload"
    )
    assert mock_run.call_args.kwargs["input"] == b"payload"
    assert out == "done"


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_port_parses_last_mapping(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        returncode=0, stdout="0.0.0.0:17687\n[::]:17687\n", stderr=""
    )
    assert compose_port("neo4j", 7687, project_dir="/repo", env={}) == 17687


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_port_no_mapping_raises(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="\n", stderr="")
    with pytest.raises(DockerOpsError, match="no published mapping"):
        compose_port("neo4j", 7687, project_dir="/repo", env={})


# ---------------------------------------------------------------------------
# lib/env, lib/ports, lib/ui — small uncovered branches
# ---------------------------------------------------------------------------

def test_update_env_on_missing_file_creates_it(tmp_path: Path) -> None:
    p = tmp_path / "new.env"
    env_lib.update_env(p, {"A": "1"})
    assert env_lib.read_env(p)["A"] == "1"


@patch("startup.lib.ports.is_port_free", return_value=False)
def test_find_free_port_exhaustion_raises(_mock: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="No free port found"):
        ports.find_free_port(9000, max_attempts=3)


@patch("startup.lib.ports.is_port_free", return_value=False)
def test_allocate_ports_exhaustion_raises(_mock: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="No free port for service"):
        ports.allocate_ports({"nextseek": 9000})


def test_ui_warn_and_fail_render() -> None:
    ui.warn("caution")
    ui.fail("broken")


# ---------------------------------------------------------------------------
# steps/prereqs — every failure shape
# ---------------------------------------------------------------------------

@patch("startup.steps.prereqs.subprocess.run", side_effect=FileNotFoundError)
def test_prereq_command_not_installed(_mock: MagicMock) -> None:
    r = prereqs.check_command_version("nonexistent", ["--version"])
    assert not r.ok and "not installed" in r.detail


@patch("startup.steps.prereqs.subprocess.run")
def test_prereq_command_timeout(mock_run: MagicMock) -> None:
    import subprocess as sp

    mock_run.side_effect = sp.TimeoutExpired(cmd="docker", timeout=5)
    r = prereqs.check_command_version("docker", ["--version"])
    assert not r.ok and "timed out" in r.detail


@patch("startup.steps.prereqs.subprocess.run")
def test_prereq_nonzero_exit_uses_stderr(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
    r = prereqs.check_command_version("docker", ["--version"])
    assert not r.ok and r.detail == "permission denied"


@patch("startup.steps.prereqs.subprocess.run")
def test_prereq_empty_stdout_gives_empty_detail(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    r = prereqs.check_command_version("docker", ["--version"])
    assert r.ok and r.detail == ""


@patch("startup.steps.prereqs.check_command_version")
def test_docker_compose_uv_checks_attach_remediation_on_failure(mock_ver: MagicMock) -> None:
    mock_ver.return_value = prereqs.PrereqResult(name="x", ok=False, detail="missing")
    for check in (prereqs.check_docker, prereqs.check_compose, prereqs.check_uv):
        r = check()
        assert not r.ok and r.remediation.startswith("Install")


@patch("startup.steps.prereqs.shutil.disk_usage")
def test_disk_space_low_and_ok(mock_usage: MagicMock) -> None:
    gb = 1024 ** 3
    mock_usage.return_value = (100 * gb, 98 * gb, 2 * gb)
    low = prereqs.check_disk_space("/", gb_required=5)
    assert not low.ok and "need 5" in low.detail
    mock_usage.return_value = (100 * gb, 50 * gb, 50 * gb)
    assert prereqs.check_disk_space("/", gb_required=5).ok


@patch("startup.steps.prereqs.check_disk_space")
@patch("startup.steps.prereqs.check_uv")
@patch("startup.steps.prereqs.check_compose")
@patch("startup.steps.prereqs.check_docker")
def test_run_all_returns_all_four_checks(md: MagicMock, mc: MagicMock, mu: MagicMock, mdisk: MagicMock) -> None:
    for m in (md, mc, mu, mdisk):
        m.return_value = prereqs.PrereqResult(name="x", ok=True, detail="")
    assert len(prereqs.run_all()) == 4


# ---------------------------------------------------------------------------
# steps/build — wait loops and stack phases
# ---------------------------------------------------------------------------

def _http_error(code: int):
    import urllib.error

    return urllib.error.HTTPError(url="http://x", code=code, msg="", hdrs=None, fp=None)


@patch("startup.steps.build.urllib.request.urlopen")
def test_wait_for_nextseek_returns_on_success_code(mock_open: MagicMock) -> None:
    resp = MagicMock()
    resp.getcode.return_value = 200
    mock_open.return_value.__enter__.return_value = resp
    build.wait_for_nextseek_http(8000, max_attempts=1, interval=0)


@patch("startup.steps.build.urllib.request.urlopen", side_effect=_http_error(404))
def test_wait_for_nextseek_treats_4xx_as_alive(_mock: MagicMock) -> None:
    build.wait_for_nextseek_http(8000, max_attempts=1, interval=0)


@patch("startup.steps.build.urllib.request.urlopen", side_effect=_http_error(502))
def test_wait_for_nextseek_times_out_on_502(_mock: MagicMock) -> None:
    with pytest.raises(DockerOpsError, match="did not respond"):
        build.wait_for_nextseek_http(8000, max_attempts=2, interval=0)


@patch("startup.steps.build.urllib.request.urlopen", side_effect=OSError("refused"))
def test_wait_for_nextseek_times_out_on_connection_refused(_mock: MagicMock) -> None:
    with pytest.raises(DockerOpsError, match="did not respond"):
        build.wait_for_nextseek_http(8000, max_attempts=1, interval=0)


@patch("startup.steps.build.compose_exec")
def test_wait_for_retries_until_check_passes(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = [DockerOpsError("not yet"), "ok"]
    build._wait_for(
        service="db", check_command=["true"], repo_root=Path("/r"), env={},
        label="MySQL", max_attempts=3, interval=0,
    )
    assert mock_exec.call_count == 2


@patch("startup.steps.build.compose_exec", side_effect=DockerOpsError("never"))
def test_wait_for_raises_after_max_attempts(_mock: MagicMock) -> None:
    with pytest.raises(DockerOpsError, match="did not become ready"):
        build._wait_for(
            service="db", check_command=["true"], repo_root=Path("/r"), env={},
            label="MySQL", max_attempts=2, interval=0,
        )


@patch("startup.steps.build._wait_for")
def test_wait_for_mysql_and_neo4j_poll_the_right_services(mock_wait: MagicMock) -> None:
    build.wait_for_mysql(Path("/r"), {})
    assert mock_wait.call_args.kwargs["service"] == "db"
    build.wait_for_neo4j(Path("/r"), {})
    assert mock_wait.call_args.kwargs["service"] == "neo4j"


@patch("startup.steps.build.wait_for_neo4j")
@patch("startup.steps.build.wait_for_mysql")
@patch("startup.steps.build.compose_up")
def test_start_databases_starts_then_waits(mock_up: MagicMock, mock_my: MagicMock, mock_neo: MagicMock) -> None:
    build.start_databases(Path("/r"), {})
    assert list(mock_up.call_args.kwargs["services"]) == ["db", "neo4j"]
    mock_my.assert_called_once()
    mock_neo.assert_called_once()


@patch("startup.steps.build.wait_for_seek_filestore")
@patch("startup.steps.build.compose_up")
def test_start_seek_side_waits_for_filestore_before_workers(mock_up: MagicMock, mock_wait: MagicMock) -> None:
    build.start_seek_side(Path("/r"), {})
    started = [list(c.kwargs["services"]) for c in mock_up.call_args_list]
    assert started == [["solr", "seek"], ["seek_workers"]]
    mock_wait.assert_called_once()


@patch("startup.steps.build.compose_build")
@patch("startup.steps.build.compose_up")
def test_build_and_start_nextseek_builds(mock_up: MagicMock, mock_build: MagicMock) -> None:
    build.build_and_start_nextseek(Path("/r"), {})
    assert list(mock_build.call_args.kwargs["services"]) == ["nextseek"]
    assert list(mock_up.call_args.kwargs["services"]) == [
        "nextseek",
        "attribute_mutation_worker",
        "attribute_mutation_dispatcher",
        "attribute_mutation_recovery_scheduler",
        "nextseek_nginx",
    ]
    assert mock_up.call_args.kwargs["no_deps"] is True


@patch("startup.steps.build.compose_up")
@patch("startup.steps.build.compose_build")
def test_start_cc_stack_builds_agent_then_recreates_services(mock_build: MagicMock, mock_up: MagicMock) -> None:
    build.start_cc_stack(Path("/r"), {})
    assert list(mock_build.call_args.kwargs["services"]) == ["cc-agent"]
    phases = [list(c.kwargs["services"]) for c in mock_up.call_args_list]
    assert phases == [["bedrock-proxy", "nextseek-sidecar"], ["nextseek_nginx"]]
    assert all(c.kwargs["force_recreate"] for c in mock_up.call_args_list)


@patch("startup.steps.build.start_cc_stack")
@patch("startup.steps.build.build_and_start_nextseek")
@patch("startup.steps.build.start_seek_side")
@patch("startup.steps.build.start_databases")
def test_start_full_stack_runs_phases_in_order(m1: MagicMock, m2: MagicMock, m3: MagicMock, m4: MagicMock) -> None:
    manager = MagicMock()
    for name, m in [("db", m1), ("seek", m2), ("nextseek", m3), ("cc", m4)]:
        manager.attach_mock(m, name)
    build.start_full_stack(Path("/r"), {})
    assert [c[0] for c in manager.mock_calls] == ["db", "seek", "nextseek", "cc"]


# ---------------------------------------------------------------------------
# steps/seed — error fallbacks and dump loading
# ---------------------------------------------------------------------------

@patch("startup.steps.seed.compose_exec", side_effect=DockerOpsError("db down"))
def test_mysql_db_is_populated_false_on_docker_error(_mock: MagicMock) -> None:
    assert seed.mysql_db_is_populated("dmac", Path("/r"), {}) is False


@patch("startup.steps.seed.compose_exec", return_value="not-a-number\n")
def test_mysql_db_is_populated_false_on_unparseable_output(_mock: MagicMock) -> None:
    assert seed.mysql_db_is_populated("dmac", Path("/r"), {}) is False


@patch("startup.steps.seed.compose_exec", side_effect=DockerOpsError("neo4j down"))
def test_neo4j_is_populated_false_on_docker_error(_mock: MagicMock) -> None:
    assert seed.neo4j_is_populated("pw", Path("/r"), {}) is False


@patch("startup.steps.seed.compose_exec")
def test_load_mysql_dump_streams_decompressed_sql(mock_exec: MagicMock, tmp_path: Path) -> None:
    gz = tmp_path / "dmac.sql.gz"
    gz.write_bytes(gzip.compress(b"CREATE TABLE t (id INT);"))
    seed.load_mysql_dump(gz, "dmac", Path("/r"), {})
    assert mock_exec.call_args.kwargs["stdin"] == b"CREATE TABLE t (id INT);"


def test_parse_neo4j_dump_rejects_unparseable_node() -> None:
    with pytest.raises(ValueError, match="unparseable node statement"):
        seed.parse_neo4j_cypher_dump("CREATE (n garbage")


def test_parse_neo4j_dump_rejects_unparseable_relationship() -> None:
    with pytest.raises(ValueError, match="unparseable relationship statement"):
        seed.parse_neo4j_cypher_dump("MATCH (a:_ImportRef garbage")


# ---------------------------------------------------------------------------
# steps/users — guards and fallbacks
# ---------------------------------------------------------------------------

def test_user_exists_rejects_unsafe_login() -> None:
    with pytest.raises(ValueError, match="unsafe login"):
        users.user_exists("demo'; DROP TABLE users;--", Path("/r"), {})


@patch("startup.steps.users.compose_exec", side_effect=DockerOpsError("db down"))
def test_user_exists_false_on_docker_error(_mock: MagicMock) -> None:
    assert users.user_exists("demo", Path("/r"), {}) is False


@patch("startup.steps.users.compose_exec", return_value="garbage\n")
def test_user_exists_false_on_unparseable_count(_mock: MagicMock) -> None:
    assert users.user_exists("demo", Path("/r"), {}) is False


# ---------------------------------------------------------------------------
# steps/schema_fixups — every status
# ---------------------------------------------------------------------------

FIX = MissingColumn(
    database="dmac", table="t", column="c",
    column_definition="JSON NULL",
    backfill_expression="JSON_OBJECT()",
    final_column_definition="JSON NOT NULL",
)


@patch("startup.steps.schema_fixups.compose_exec")
def test_fixup_table_missing(mock_exec: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schema_fixups, "KNOWN_FIXUPS", [FIX])
    mock_exec.return_value = "0\n"
    assert schema_fixups.apply_column_fixups(Path("/r"), {}) == [("dmac.t.c", "table missing")]


@patch("startup.steps.schema_fixups.compose_exec", side_effect=DockerOpsError("db down"))
def test_fixup_docker_error_propagates(_mock: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schema_fixups, "KNOWN_FIXUPS", [FIX])
    with pytest.raises(DockerOpsError, match="db down"):
        schema_fixups.apply_column_fixups(Path("/r"), {})


@patch("startup.steps.schema_fixups.compose_exec")
def test_fixup_applies_missing_column_with_backfill_and_tighten(
    mock_exec: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema_fixups, "KNOWN_FIXUPS", [FIX])
    mock_exec.side_effect = ["1\n", "0\n", "", ""]  # table yes, column no, ALTER, MODIFY
    assert schema_fixups.apply_column_fixups(Path("/r"), {}) == [("dmac.t.c", "applied")]
    alter_sql = mock_exec.call_args_list[2].kwargs["command"][-1]
    assert "ADD COLUMN c JSON NULL" in alter_sql
    assert "UPDATE t SET c = JSON_OBJECT()" in alter_sql
    modify_sql = mock_exec.call_args_list[3].kwargs["command"][-1]
    assert "MODIFY COLUMN c JSON NOT NULL" in modify_sql


@patch("startup.steps.schema_fixups.compose_exec")
def test_fixup_existing_column_gets_constraints_reset(
    mock_exec: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema_fixups, "KNOWN_FIXUPS", [FIX])
    mock_exec.side_effect = ["1\n", "1\n", ""]  # table yes, column yes, MODIFY
    assert schema_fixups.apply_column_fixups(Path("/r"), {}) == [("dmac.t.c", "constraints reset")]


@patch("startup.steps.schema_fixups.compose_exec")
def test_fixup_without_final_definition_reports_already_present(
    mock_exec: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    loose = MissingColumn(database="dmac", table="t", column="c", column_definition="JSON NULL")
    monkeypatch.setattr(schema_fixups, "KNOWN_FIXUPS", [loose])
    mock_exec.side_effect = ["1\n", "1\n"]  # no MODIFY call — tighten early-returns
    assert schema_fixups.apply_column_fixups(Path("/r"), {}) == [("dmac.t.c", "already present")]
    assert mock_exec.call_count == 2


# ---------------------------------------------------------------------------
# steps/seed_cleanup — the cascade delete state machine
# ---------------------------------------------------------------------------

@patch("startup.steps.seed_cleanup.compose_exec")
def test_cleanup_returns_0_when_table_absent(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "0\n"
    assert seed_cleanup.clear_stale_chat_sessions(Path("/r"), {}) == 0
    assert mock_exec.call_count == 1


@patch("startup.steps.seed_cleanup.compose_exec")
def test_cleanup_returns_0_when_no_stale_rows(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = ["1\n", "0\n"]
    assert seed_cleanup.clear_stale_chat_sessions(Path("/r"), {}) == 0


@patch("startup.steps.seed_cleanup.compose_exec")
def test_cleanup_returns_0_when_cascade_delete_fails(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = ["1\n", "5\n", DockerOpsError("fk error")]
    assert seed_cleanup.clear_stale_chat_sessions(Path("/r"), {}) == 0


@patch("startup.steps.seed_cleanup.compose_exec")
def test_cleanup_returns_0_when_parent_delete_fails(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = ["1\n", "5\n", "", DockerOpsError("locked")]
    assert seed_cleanup.clear_stale_chat_sessions(Path("/r"), {}) == 0


@patch("startup.steps.seed_cleanup.compose_exec")
def test_cleanup_deletes_children_before_parents_and_returns_count(mock_exec: MagicMock) -> None:
    mock_exec.side_effect = ["1\n", "5\n", "", ""]
    assert seed_cleanup.clear_stale_chat_sessions(Path("/r"), {}) == 5
    child_sql = mock_exec.call_args_list[2].kwargs["command"][-1]
    parent_sql = mock_exec.call_args_list[3].kwargs["command"][-1]
    assert "DELETE FROM assistant_query_task" in child_sql
    assert "DELETE FROM assistant_chat_session" in parent_sql


@patch("startup.steps.seed_cleanup.compose_exec", return_value="not-a-number\n")
def test_cleanup_query_count_unparseable_treated_as_0(_mock: MagicMock) -> None:
    assert seed_cleanup.clear_stale_chat_sessions(Path("/r"), {}) == 0


# ---------------------------------------------------------------------------
# steps/seed_filestore — download, populate check, load
# ---------------------------------------------------------------------------

def _fake_urlopen(payload: bytes):
    reader = io.BytesIO(payload)
    cm = MagicMock()
    cm.__enter__.return_value = reader
    cm.__exit__.return_value = False
    return cm


def test_archive_present(tmp_path: Path) -> None:
    assert seed_filestore.archive_present(tmp_path) is False
    archive = tmp_path / seed_filestore.FILESTORE_ARCHIVE
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"tar")
    assert seed_filestore.archive_present(tmp_path) is True


@patch("startup.steps.seed_filestore.urllib.request.urlopen")
def test_download_archive_verifies_checksum_and_installs(
    mock_open: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"filestore-bytes"
    monkeypatch.setattr(seed_filestore, "FILESTORE_SHA256", hashlib.sha256(payload).hexdigest())
    mock_open.return_value = _fake_urlopen(payload)
    seed_filestore.download_archive(tmp_path)
    dest = tmp_path / seed_filestore.FILESTORE_ARCHIVE
    assert dest.read_bytes() == payload
    assert not dest.with_suffix(dest.suffix + ".part").exists()


@patch("startup.steps.seed_filestore.urllib.request.urlopen")
def test_download_archive_checksum_mismatch_leaves_part_file(
    mock_open: MagicMock, tmp_path: Path
) -> None:
    mock_open.return_value = _fake_urlopen(b"corrupted")
    with pytest.raises(DockerOpsError, match="checksum mismatch"):
        seed_filestore.download_archive(tmp_path)
    dest = tmp_path / seed_filestore.FILESTORE_ARCHIVE
    assert not dest.exists()
    assert dest.with_suffix(dest.suffix + ".part").exists()


@patch("startup.steps.seed_filestore.compose_exec")
def test_filestore_is_populated_variants(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "/seek/filestore/assets/1/blob\n"
    assert seed_filestore.filestore_is_populated(Path("/r"), {}) is True
    mock_exec.return_value = "\n"
    assert seed_filestore.filestore_is_populated(Path("/r"), {}) is False
    mock_exec.side_effect = DockerOpsError("seek not running")
    assert seed_filestore.filestore_is_populated(Path("/r"), {}) is False


@patch("startup.steps.seed_filestore.compose_exec")
def test_load_filestore_streams_archive_and_chowns(mock_exec: MagicMock, tmp_path: Path) -> None:
    archive = tmp_path / seed_filestore.FILESTORE_ARCHIVE
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"targz-bytes")
    seed_filestore.load_filestore(tmp_path, {})
    assert mock_exec.call_args.kwargs["stdin"] == b"targz-bytes"
    shell = mock_exec.call_args.kwargs["command"][-1]
    assert "tar -C /seek/filestore -xzf -" in shell
    assert "chown -R www-data:www-data" in shell


# ---------------------------------------------------------------------------
# steps/doctor — instance-present path
# ---------------------------------------------------------------------------

@patch("startup.steps.doctor.validate")
@patch("startup.steps.doctor.load_instance")
@patch("startup.steps.doctor.prereqs")
def test_doctor_full_path_includes_health_checks(
    mock_prereqs: MagicMock, mock_load: MagicMock, mock_validate: MagicMock, tmp_path: Path
) -> None:
    from startup.steps.doctor import diagnose

    (tmp_path / "startup").mkdir()
    mock_prereqs.run_all.return_value = [prereqs.PrereqResult(name="docker", ok=True, detail="v27")]
    state = MagicMock()
    state.name, state.prefix, state.ports = "nextseek", "", {"nextseek": 8000}
    state.compose_env.return_value = {}
    mock_load.return_value = state
    mock_validate.run_all_health_checks.return_value = [
        SimpleNamespace(name="http", ok=True, detail="200")
    ]
    results = diagnose(tmp_path)
    names = [name for name, _, _ in results]
    assert names == ["docker", "off-box baseline", "instance state", "http"]
