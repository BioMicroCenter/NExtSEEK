"""The graph sync health line (SPEC-ci-health D12 to D16): `manage.py graph_sync_health` asked of the app
container on every box, production included, by `rebuild` and `ci`.

The command is never run. The subprocess is answered by a fake that records it, and the one test that lets the real
function reach its guard gives it a tree with no compose file, where it must ask docker nothing. The CLI tests stub
the check itself, as the drift tests stub theirs.
"""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from startup import cli
from startup.ci import runner as ci_runner
from startup.lib.instance import InstanceState, save_instance
from startup.steps import validate

cli_runner_ = CliRunner()

LOST = "OperationalError: (2006, 'Server has gone away')"
QUIET = "failing outbox rows: 0 (0 overdue); failed runs: 0 (0 overdue); latest drift check: run 6 ok"
ONE_WAITING = "failing outbox rows: 1 (0 overdue); failed runs: 0 (0 overdue); latest drift check: run 6 ok"
ONE_EACH = "failing outbox rows: 1 (1 overdue); failed runs: 1 (1 overdue); latest drift check: run 6 ok"
ROW_OVERDUE = (f"catalog *: failing for 100 min against 90 min, attempts 0, next retry "
               f"2026-09-29T19:00:00+00:00: {LOST}")
RUN_OVERDUE = f"catalog run 7 failed (trigger loop, finished 2026-09-29T17:00:00+00:00, 118 min ago): {LOST}"
ROW_WAITING = (f"row catalog *: failing for 38 min against 90 min, attempts 0, next retry "
               f"2026-09-29T18:00:00+00:00: {LOST}")
UNREADABLE = ("The graph_sync_outbox and graph_sync_run tables could not be read. An instance that has not applied "
              "migration 0021 does not have them yet.")
INDENT = "\n" + " " * 11

HEALTHY = {"verdict": "ok", "summary": QUIET, "problems": [], "warnings": []}
WAITING = {"verdict": "ok", "summary": ONE_WAITING, "problems": [], "warnings": [ROW_WAITING]}
FAILING = {"verdict": "problems", "summary": ONE_EACH, "problems": [ROW_OVERDUE, RUN_OVERDUE], "warnings": []}
UNAVAILABLE = {"verdict": "unavailable", "summary": UNREADABLE, "problems": [UNREADABLE], "warnings": []}
NOT_READY = {"verdict": "not_ready", "problems": [], "warnings": [],
             "summary": "1 migrations of nextseek_api not applied yet, first 0023_graph_sync_outbox_failing_since"}
UNKNOWN_COMMAND = b"Unknown command: 'graph_sync_health'\nType 'manage.py help' for usage.\n"


def _repo_with_compose(tmp_path: Path) -> Path:
    """A tree the check will actually ask docker about."""
    (tmp_path / "docker-compose.yml").write_text("services:\n  nextseek: {}\n")
    return tmp_path


def _answers(monkeypatch: pytest.MonkeyPatch, *replies) -> list[SimpleNamespace]:
    """Answer the command once per (returncode, stdout, stderr) reply, in order; return the calls it made."""
    calls: list[SimpleNamespace] = []
    queue = list(replies)

    def fake_run(cmd, **kwargs):
        calls.append(SimpleNamespace(cmd=list(cmd), kwargs=kwargs))
        returncode, stdout, stderr = queue.pop(0)
        out = json.dumps(stdout).encode() if isinstance(stdout, dict) else stdout
        return SimpleNamespace(returncode=returncode, stdout=out, stderr=stderr)

    monkeypatch.setattr(validate.subprocess, "run", fake_run)
    return calls


def _check(tmp_path: Path, env: dict | None = None, **kwargs) -> validate.HealthResult:
    kwargs.setdefault("sleep", lambda seconds: None)
    return validate.check_graph_sync_health(_repo_with_compose(tmp_path), env or {}, **kwargs)


# --------------------------------------------------------------------------- #
# what the command answers with
# --------------------------------------------------------------------------- #

def test_a_healthy_box_is_one_green_line(tmp_path, monkeypatch):
    _answers(monkeypatch, (0, HEALTHY, b""))

    result = _check(tmp_path)

    assert (result.name, result.ok, result.warn, result.detail) == ("graph sync health", True, False, QUIET)


def test_failures_inside_their_window_are_a_warning_listing_each(tmp_path, monkeypatch):
    _answers(monkeypatch, (0, WAITING, b""))

    result = _check(tmp_path)

    assert result.ok is True and result.warn is True
    assert result.detail == f"{ONE_WAITING}; not yet overdue:{INDENT}{ROW_WAITING}"


def test_problems_fail_the_line_one_indented_line_each(tmp_path, monkeypatch):
    _answers(monkeypatch, (1, FAILING, b"CommandError: graph sync health: problems\n"))

    result = _check(tmp_path)

    assert result.ok is False
    assert result.detail == f"{ONE_EACH}{INDENT}{ROW_OVERDUE}{INDENT}{RUN_OVERDUE}"


def test_problems_also_list_what_is_still_waiting(tmp_path, monkeypatch):
    _answers(monkeypatch, (1, {**FAILING, "warnings": [ROW_WAITING]}, b""))

    result = _check(tmp_path)

    assert result.detail.endswith(f"{INDENT}not yet overdue: {ROW_WAITING}")


def test_unreadable_tables_fail_the_line_with_the_fixed_prose(tmp_path, monkeypatch):
    _answers(monkeypatch, (3, UNAVAILABLE, b""))

    result = _check(tmp_path)

    assert (result.ok, result.detail) == (False, UNREADABLE)


def test_a_container_still_migrating_is_asked_again_until_it_answers(tmp_path, monkeypatch):
    calls = _answers(monkeypatch, (4, NOT_READY, b""), (0, HEALTHY, b""))
    slept: list[float] = []

    result = _check(tmp_path, sleep=slept.append, clock=iter([0.0, 5.0]).__next__)

    assert len(calls) == 2
    assert slept == [validate.GRAPH_SYNC_HEALTH_POLL_S]
    assert result.ok is True and result.detail == QUIET


def test_a_container_that_never_finishes_migrating_is_a_warning_after_the_wait(tmp_path, monkeypatch):
    calls = _answers(monkeypatch, *[(4, NOT_READY, b"")] * 3)

    result = _check(tmp_path, wait_s=300, clock=iter([0.0, 100.0, 200.0, 300.0]).__next__)

    assert len(calls) == 3
    assert result.ok is True and result.warn is True
    assert result.detail.startswith("skipped: the app container had not applied its migrations after 300s")
    assert "0023_graph_sync_outbox_failing_since" in result.detail


def test_an_app_image_without_the_command_is_a_warning_to_rebuild(tmp_path, monkeypatch):
    _answers(monkeypatch, (1, b"", UNKNOWN_COMMAND))

    result = _check(tmp_path)

    assert result.ok is True and result.warn is True
    assert result.detail == ("skipped: the running app image has no graph_sync_health command, so it predates "
                             "this check; rebuild the app")


def test_a_crash_fails_the_line_with_the_last_line_of_stderr(tmp_path, monkeypatch):
    _answers(monkeypatch, (1, b"", b"Traceback (most recent call last):\n  ...\nOperationalError: boom\n"))

    result = _check(tmp_path)

    assert result.ok is False
    assert result.detail == "graph_sync_health could not complete (exit 1): OperationalError: boom"


def test_an_answer_with_no_verdict_fails_the_line(tmp_path, monkeypatch):
    _answers(monkeypatch, (0, b"starting\n", b""))

    result = _check(tmp_path)

    assert result.ok is False
    assert result.detail == "graph_sync_health could not complete (exit 0): starting"


def test_the_json_line_is_found_among_other_output(tmp_path, monkeypatch):
    noisy = b"starting\n" + json.dumps(HEALTHY).encode() + b"\n{not json\n"
    _answers(monkeypatch, (0, noisy, b""))

    assert _check(tmp_path).detail == QUIET


# --------------------------------------------------------------------------- #
# how it asks
# --------------------------------------------------------------------------- #

def test_the_command_line_is_the_app_containers_own_manage_py(tmp_path, monkeypatch):
    calls = _answers(monkeypatch, (0, HEALTHY, b""))

    _check(tmp_path, {"COMPOSE_PROJECT_NAME": "nextseek-v2"})

    assert calls[0].cmd == ["docker", "compose", "exec", "-T", "nextseek",
                            "uv", "run", "--no-sync", "python", "manage.py", "graph_sync_health", "--json"]
    assert calls[0].kwargs["cwd"] == str(tmp_path)
    assert calls[0].kwargs["env"]["COMPOSE_PROJECT_NAME"] == "nextseek-v2"
    assert "PATH" in calls[0].kwargs["env"]
    assert calls[0].kwargs["timeout"] == validate.GRAPH_SYNC_HEALTH_TIMEOUT_S


def test_the_check_feeds_the_command_empty_stdin(tmp_path, monkeypatch):
    calls = _answers(monkeypatch, (0, HEALTHY, b""))

    _check(tmp_path)

    assert calls[0].kwargs["input"] == b""


def test_a_timeout_fails_rather_than_hanging_the_deploy(tmp_path, monkeypatch):
    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(validate.subprocess, "run", timeout)

    result = _check(tmp_path)

    assert result.ok is False
    assert "timed out after 120s" in result.detail


def test_no_docker_at_all_is_a_failure_not_a_traceback(tmp_path, monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    monkeypatch.setattr(validate.subprocess, "run", boom)

    result = _check(tmp_path)

    assert result.ok is False
    assert result.detail.startswith("cannot run docker: ")


def test_a_tree_with_no_compose_file_is_skipped_without_asking_docker(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no subprocess may start without a compose file")

    monkeypatch.setattr(validate.subprocess, "run", forbidden)

    result = validate.check_graph_sync_health(tmp_path, {})

    assert result.ok is True and result.warn is True
    assert result.detail.startswith("skipped: ")


def test_a_detail_with_bracketed_text_prints_rather_than_raising(monkeypatch):
    """rich reads "[/tmp]" as a closing tag and raises MarkupError, and an error excerpt can hold exactly that."""
    out = io.StringIO()
    monkeypatch.setattr(cli.ui, "console", Console(file=out, width=200, color_system=None))

    cli._print_health_results([validate.HealthResult("graph sync health", False, "OSError: [/tmp] is full")])

    assert "graph sync health: OSError: [/tmp] is full" in out.getvalue()


# --------------------------------------------------------------------------- #
# the rebuild and ci wiring
# --------------------------------------------------------------------------- #

@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "startup").mkdir()
    (tmp_path / "docker").mkdir()
    (tmp_path / "NessieAI" / "chat_nextseek").mkdir(parents=True)
    (tmp_path / "NessieAI" / "chat_nextseek" / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    return tmp_path


def _saved_state(repo: Path, ci_profile: str = "prod") -> InstanceState:
    state = InstanceState(
        name="nextseek", prefix="", ports={"nextseek": 8000, "seek": 3000},
        compose_project_name="nextseek", created="2026-09-15T00:00:00",
        seek_public_url="https://seek.example.org", ci_profile=ci_profile,
    )
    save_instance(repo, state)
    return state


@pytest.fixture()
def stack(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Everything a rebuild or a ci run touches, answered without docker."""
    from startup.lib import docker_ops
    from startup.steps import disk_preflight, registry_push, rollback_tags

    seen = SimpleNamespace(health=[], report=[], ci=[])
    monkeypatch.setattr(rollback_tags, "create_verified", lambda images, build_root: ())
    monkeypatch.setattr(docker_ops, "compose_build", lambda **kwargs: None)
    monkeypatch.setattr(docker_ops, "compose_up", lambda **kwargs: None)
    monkeypatch.setattr(registry_push, "push_baselines", lambda *a, **k: ())
    monkeypatch.setattr(
        disk_preflight, "run_preflight",
        lambda **k: SimpleNamespace(proceed=True, floor_gb=20, freed_gb=0.0, reason="ok"),
    )
    monkeypatch.setattr(
        validate, "nessie_prerequisites",
        lambda repo_root, env, compose_project_name: (
            validate.HealthResult("bedrock proxy token", True, "token present"),
        ),
    )
    monkeypatch.setattr(ci_runner, "run_ci", lambda *a, **k: seen.ci.append(k) or 0)
    monkeypatch.setattr(ci_runner, "running_image", lambda *a, **k: (None, None))
    monkeypatch.setattr(ci_runner, "write_report",
                        lambda *a, **k: seen.report.append(k) or None)
    return seen


def _stack_is_up(monkeypatch: pytest.MonkeyPatch, *, runtimes_ok: bool = True) -> None:
    monkeypatch.setattr(
        validate, "stack_health",
        lambda repo_root, env, compose_project_name, **kwargs: validate.StackHealth(
            blocking=(validate.HealthResult(
                "app + front door", runtimes_ok,
                "nextseek + nextseek_nginx running" if runtimes_ok
                else "not running: nextseek_nginx"),),
            advisory=(validate.HealthResult("cc services", True, "running"),),
        ),
    )


def _health_answer(monkeypatch: pytest.MonkeyPatch, seen: SimpleNamespace,
                   result: validate.HealthResult) -> None:
    monkeypatch.setattr(
        validate, "check_graph_sync_health",
        lambda repo_root, env: seen.health.append((repo_root, env)) or result,
    )


_RED = validate.HealthResult("graph sync health", False, f"{ONE_EACH}{INDENT}{ROW_OVERDUE}{INDENT}{RUN_OVERDUE}")
_GREEN = validate.HealthResult("graph sync health", True, QUIET)
_WAITING_LINE = validate.HealthResult("graph sync health", True,
                                      f"{ONE_WAITING}; not yet overdue:{INDENT}{ROW_WAITING}", warn=True)


def test_ci_on_a_prod_box_fails_on_a_red_line_after_the_suite_passes(repo, monkeypatch, stack):
    """On production nothing else checks the sync, so `ci` cannot say "passed" over a red line (D14)."""
    _saved_state(repo, "prod")
    _stack_is_up(monkeypatch)
    _health_answer(monkeypatch, stack, _RED)

    result = cli_runner_.invoke(cli.app, ["ci"])

    assert result.exit_code == 1, result.output
    assert len(stack.ci) == 1, "the suite must still run"
    compact = "".join(result.output.split())
    assert "catalogrun7failed" in compact
    assert "CIfailed:thesuitepassed" in compact


@pytest.mark.parametrize("line", [_GREEN, _WAITING_LINE], ids=["green", "waiting"])
def test_ci_on_a_prod_box_passes_on_a_green_or_waiting_line(repo, monkeypatch, stack, line):
    _saved_state(repo, "prod")
    _stack_is_up(monkeypatch)
    _health_answer(monkeypatch, stack, line)

    result = cli_runner_.invoke(cli.app, ["ci"])

    assert result.exit_code == 0, result.output
    assert "CIpassed" in "".join(result.output.split())


def test_ci_records_the_line_as_a_stack_health_row(repo, monkeypatch, stack):
    _saved_state(repo, "prod")
    _stack_is_up(monkeypatch)
    _health_answer(monkeypatch, stack, _RED)

    cli_runner_.invoke(cli.app, ["ci"])

    assert stack.report[0]["health"][-1] == ("graph sync health", False, _RED.detail)


def test_rebuild_on_a_prod_box_exits_1_at_the_end_on_a_red_line(repo, monkeypatch, stack):
    _saved_state(repo, "prod")
    _stack_is_up(monkeypatch)
    _health_answer(monkeypatch, stack, _RED)

    result = cli_runner_.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1, result.output
    assert len(stack.ci) == 1, "the suite must still run"
    compact = "".join(result.output.split())
    assert "CIpassed" in compact and "catalogrun7failed" in compact
    assert stack.report[0]["health"][-1] == ("graph sync health", False, _RED.detail)


def test_rebuild_asks_after_the_drift_line_and_before_the_suite(repo, monkeypatch, stack):
    _saved_state(repo, "dev")
    _stack_is_up(monkeypatch)
    monkeypatch.setattr(validate, "check_graph_drift",
                        lambda repo_root, env: validate.HealthResult("graph drift", True, "no drift"))
    _health_answer(monkeypatch, stack, _GREEN)

    result = cli_runner_.invoke(cli.app, ["rebuild"])
    out = result.output

    assert result.exit_code == 0, out
    assert out.index("graph drift") < out.index("graph sync health") < out.index("running CI after rebuild")


def test_a_component_rebuild_still_asks(repo, monkeypatch, stack):
    """It is about the box, not the image, and costs two table reads: a cc-agent rebuild asks too."""
    _saved_state(repo, "prod")
    _stack_is_up(monkeypatch)
    _health_answer(monkeypatch, stack, _GREEN)

    result = cli_runner_.invoke(cli.app, ["rebuild", "--component", "cc-agent"])

    assert result.exit_code == 0, result.output
    assert len(stack.health) == 1


def test_a_stack_that_is_down_is_not_asked(repo, monkeypatch, stack):
    _saved_state(repo, "prod")
    _stack_is_up(monkeypatch, runtimes_ok=False)
    _health_answer(monkeypatch, stack, _RED)

    result = cli_runner_.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1
    assert stack.health == []
