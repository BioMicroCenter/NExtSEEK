"""The drift check that runs after every app rebuild (spec CI-4).

`graph_sync --drift` answers one question no smoke test can: does the graph the
site searches still equal MySQL? It is asked of the app container, so these
tests never start one. The subprocess is recorded, never run, and the one test
that lets the real function through gives it a tree with no compose file, where
it must refuse to ask docker anything at all.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from startup import cli
from startup.ci import runner as ci_runner
from startup.lib.instance import InstanceState, save_instance
from startup.steps import validate

cli_runner_ = CliRunner()


# --------------------------------------------------------------------------- #
# what the command answers with
# --------------------------------------------------------------------------- #

def _checks(*failing: str) -> list[dict]:
    names = ["samples.missing_in_graph", "samples.not_in_mysql",
             "samples.source_hash_mismatch", "freshness.full", "8.catalog.hash"]
    return [{"name": n, "pass": n not in failing, "expected": 0, "actual": 0}
            for n in names]


CLEAN = {
    "status": "ok", "pass": True, "checks": _checks(),
    "stats": {"detection": {"mysql_samples": 1084754, "graph_samples": 1084754,
                            "changed": 0, "missing_in_graph": 0, "not_in_mysql": 0}},
}
DRIFTED = {
    "status": "drift", "pass": False,
    "checks": _checks("samples.source_hash_mismatch", "freshness.full"),
    "stats": {"detection": {"mysql_samples": 1084754, "graph_samples": 1084700,
                            "changed": 12, "missing_in_graph": 54, "not_in_mysql": 0}},
}
REFUSED = {
    "status": "refused", "pass": False, "checks": [], "stats": {},
    "reason": "the graph's GraphMeta reads schema version '1.1', not the writer's '1.2'; "
              "only a graph that graph_sync --full wrote at 1.2 is compared",
}


def _repo_with_compose(tmp_path: Path) -> Path:
    """A tree the check will actually ask docker about."""
    (tmp_path / "docker-compose.yml").write_text("services:\n  nextseek: {}\n")
    return tmp_path


def _answer(monkeypatch: pytest.MonkeyPatch, *, returncode: int,
            stdout: object = b"", stderr: bytes = b"") -> list[SimpleNamespace]:
    """Answer the drift command without running it; return the calls it made."""
    calls: list[SimpleNamespace] = []
    payload = (json.dumps(stdout).encode() if isinstance(stdout, dict) else stdout)

    def fake_run(cmd, **kwargs):
        calls.append(SimpleNamespace(cmd=list(cmd), kwargs=kwargs))
        return SimpleNamespace(returncode=returncode, stdout=payload, stderr=stderr)

    monkeypatch.setattr(validate.subprocess, "run", fake_run)
    return calls


def test_a_clean_graph_passes_and_says_what_it_compared(tmp_path, monkeypatch):
    _answer(monkeypatch, returncode=0, stdout=CLEAN)

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert result.name == "graph drift"
    assert result.ok is True and result.warn is False
    assert "no drift" in result.detail
    assert "1084754" in result.detail


def test_drift_fails_with_the_failing_check_names(tmp_path, monkeypatch):
    """The names are the whole point: they say which part of the graph moved."""
    _answer(monkeypatch, returncode=1, stdout=DRIFTED)

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert result.ok is False and result.warn is False
    assert "samples.source_hash_mismatch" in result.detail
    assert "freshness.full" in result.detail


def test_a_graph_not_at_the_writers_version_is_skipped_not_failed(tmp_path, monkeypatch):
    """Exit 2 is a refusal, and until the operator's first --full every box is
    on the older schema. A red rebuild on every box would be noise, so the
    printed reason is carried and the deploy stays green."""
    _answer(monkeypatch, returncode=2, stdout=REFUSED)

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert result.ok is True and result.warn is True
    assert result.detail.startswith("skipped: ")
    assert "schema version '1.1'" in result.detail


def test_a_command_that_could_not_complete_fails(tmp_path, monkeypatch):
    _answer(monkeypatch, returncode=3,
            stderr=b"reading the graph\nneo4j.exceptions.ServiceUnavailable: refused\n")

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert result.ok is False
    assert "exit 3" in result.detail
    assert "ServiceUnavailable" in result.detail


def test_an_unparseable_answer_still_reports_the_exit_status(tmp_path, monkeypatch):
    """A drift exit with no JSON (an older image, a crash mid-write) is still a
    red line, not a crash in the deploy command."""
    _answer(monkeypatch, returncode=1, stdout=b"not json at all",
            stderr=b"Unknown option --drift\n")

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert result.ok is False
    assert "Unknown option --drift" in result.detail


def test_json_mixed_with_progress_lines_is_still_read(tmp_path, monkeypatch):
    """--json puts only JSON on stdout, but a stray line must not lose the names."""
    noisy = b"starting\n" + json.dumps(DRIFTED).encode() + b"\n"
    _answer(monkeypatch, returncode=1, stdout=noisy)

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert "samples.source_hash_mismatch" in result.detail


# --------------------------------------------------------------------------- #
# how it asks
# --------------------------------------------------------------------------- #

def test_the_command_line_is_the_app_containers_own_manage_py(tmp_path, monkeypatch):
    """The app image carries no bare `python` on PATH; `uv run --no-sync` runs
    in /app/.venv without modifying it, exactly as the CC runner check does."""
    repo = _repo_with_compose(tmp_path)
    calls = _answer(monkeypatch, returncode=0, stdout=CLEAN)

    validate.check_graph_drift(repo, {"COMPOSE_PROJECT_NAME": "nextseek-v2"})

    assert calls[0].cmd == [
        "docker", "compose", "exec", "-T", "nextseek",
        "uv", "run", "--no-sync", "python", "manage.py", "graph_sync", "--drift", "--json",
    ]
    assert calls[0].kwargs["cwd"] == str(repo)
    assert calls[0].kwargs["env"]["COMPOSE_PROJECT_NAME"] == "nextseek-v2"
    # The ambient environment still reaches docker (DOCKER_HOST, PATH).
    assert "PATH" in calls[0].kwargs["env"]


def test_the_check_feeds_the_command_empty_stdin(tmp_path, monkeypatch):
    """`docker compose exec -T` inherits the caller's stdin and swallows what it
    finds there, which over ssh has eaten the rest of a piped script. A rebuild
    is often run from a pipe or a hook, so the command is given nothing."""
    calls = _answer(monkeypatch, returncode=0, stdout=CLEAN)

    validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert calls[0].kwargs["input"] == b""
    assert calls[0].kwargs.get("text") is not True


def test_the_check_never_goes_through_compose_exec(tmp_path, monkeypatch):
    """compose_exec raises DockerOpsError on a non-zero exit and keeps only the
    message, so exit 1 would become an exception and the JSON naming the drifted
    checks would be thrown away."""
    def forbidden(*args, **kwargs):
        raise AssertionError("check_graph_drift must not use compose_exec")

    monkeypatch.setattr(validate, "compose_exec", forbidden)
    _answer(monkeypatch, returncode=1, stdout=DRIFTED)

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert result.ok is False


def test_a_timeout_fails_rather_than_hanging_the_deploy(tmp_path, monkeypatch):
    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(validate.subprocess, "run", timeout)

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert result.ok is False
    assert "timed out" in result.detail


def test_no_docker_at_all_is_a_failure_not_a_traceback(tmp_path, monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    monkeypatch.setattr(validate.subprocess, "run", boom)

    result = validate.check_graph_drift(_repo_with_compose(tmp_path), {})

    assert result.ok is False
    assert "docker" in result.detail


def test_a_tree_with_no_compose_file_is_skipped_without_asking_docker(tmp_path, monkeypatch):
    """No compose file means no `nextseek` service to exec into. This is what
    keeps the check inert in every test tree, including the CLI tests that stub
    docker at a different seam."""
    def forbidden(*args, **kwargs):
        raise AssertionError("no subprocess may start without a compose file")

    monkeypatch.setattr(validate.subprocess, "run", forbidden)

    result = validate.check_graph_drift(tmp_path, {})

    assert result.ok is True and result.warn is True
    assert result.detail.startswith("skipped: ")


# --------------------------------------------------------------------------- #
# the CI record
# --------------------------------------------------------------------------- #

_JUNIT = """<testsuites><testsuite name="p" time="1.0">
  <testcase name="test_ok"/>
  <testcase name="test_bad"><failure message="AssertionError: nope"/></testcase>
</testsuite></testsuites>"""


def _junit(tmp_path: Path) -> None:
    path = ci_runner.junit_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_JUNIT)


def test_the_ci_record_carries_the_graph_drift_section(tmp_path):
    _junit(tmp_path)

    body = ci_runner.write_report(
        tmp_path, label="run",
        health=[("app + front door", True, "nextseek + nextseek_nginx running")],
        graph_drift=("graph drift", False, "DRIFT: 1 of 41 checks failed: samples.not_in_mysql"),
    ).read_text()

    assert "## Graph drift" in body
    assert "✗ **graph drift:** DRIFT: 1 of 41 checks failed: samples.not_in_mysql" in body
    # Both are steps of the run that happened before the suite.
    assert body.index("## Stack health") < body.index("## Graph drift")
    assert body.index("## Graph drift") < body.index("## Failures")


def test_a_record_without_a_drift_result_has_no_section(tmp_path):
    """A prod box and a component rebuild never ask, so there is nothing to say."""
    _junit(tmp_path)

    body = ci_runner.write_report(tmp_path, label="run").read_text()

    assert "## Graph drift" not in body


def test_a_skipped_drift_check_is_recorded_as_the_pass_it_is(tmp_path):
    _junit(tmp_path)

    body = ci_runner.write_report(
        tmp_path, label="run",
        graph_drift=("graph drift", True, "skipped: the graph is not at schema 1.2"),
    ).read_text()

    assert "✓ **graph drift:** skipped: the graph is not at schema 1.2" in body


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


def _saved_state(repo: Path, ci_profile: str = "dev") -> InstanceState:
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

    seen = SimpleNamespace(drift=[], report=[], ci=[])
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
        lambda repo_root, env, compose_project_name: validate.StackHealth(
            blocking=(validate.HealthResult(
                "app + front door", runtimes_ok,
                "nextseek + nextseek_nginx running" if runtimes_ok
                else "not running: nextseek_nginx"),),
            advisory=(validate.HealthResult("cc services", True, "running"),),
        ),
    )


def _drift_answer(monkeypatch: pytest.MonkeyPatch, seen: SimpleNamespace,
                  result: validate.HealthResult) -> None:
    monkeypatch.setattr(
        validate, "check_graph_drift",
        lambda repo_root, env: seen.drift.append((repo_root, env)) or result,
    )


_DRIFTED = validate.HealthResult("graph drift", False,
                                 "DRIFT: 2 of 41 checks failed: samples.not_in_mysql")
_CLEAN = validate.HealthResult("graph drift", True, "no drift: 41 checks passed")


def test_rebuild_exits_non_zero_on_drift_after_the_suite_has_run(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace,
) -> None:
    """Advisory, exactly like the CC image checks: the suite still runs and its
    result still stands, and the rebuild ends red so the drift is not lost."""
    _saved_state(repo, ci_profile="dev")
    _stack_is_up(monkeypatch)
    _drift_answer(monkeypatch, stack, _DRIFTED)

    result = cli_runner_.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1, result.output
    assert len(stack.ci) == 1, "the suite must still run"
    compact = "".join(result.output.split())
    assert "samples.not_in_mysql" in compact
    assert "CIpassed" in compact


def test_rebuild_asks_the_graph_after_the_health_report_and_before_the_suite(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _stack_is_up(monkeypatch)
    _drift_answer(monkeypatch, stack, _CLEAN)

    out = cli_runner_.invoke(cli.app, ["rebuild"]).output

    assert out.index("app + front door") < out.index("graph drift")
    assert out.index("graph drift") < out.index("running CI after rebuild")


def test_rebuild_passes_the_instances_own_compose_environment(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _stack_is_up(monkeypatch)
    _drift_answer(monkeypatch, stack, _CLEAN)

    assert cli_runner_.invoke(cli.app, ["rebuild"]).exit_code == 0
    assert len(stack.drift) == 1
    repo_root, env = stack.drift[0]
    assert repo_root == repo
    assert env["COMPOSE_PROJECT_NAME"] == "nextseek"


def test_rebuild_records_the_drift_line_in_the_ci_record(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace,
) -> None:
    _saved_state(repo, ci_profile="dev")
    _stack_is_up(monkeypatch)
    _drift_answer(monkeypatch, stack, _DRIFTED)

    cli_runner_.invoke(cli.app, ["rebuild"])

    assert stack.report[0]["graph_drift"] == (
        "graph drift", False, "DRIFT: 2 of 41 checks failed: samples.not_in_mysql")


def test_a_clean_graph_leaves_the_rebuild_green(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace,
) -> None:
    _saved_state(repo, ci_profile="local")
    _stack_is_up(monkeypatch)
    _drift_answer(monkeypatch, stack, _CLEAN)

    result = cli_runner_.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("profile", ["prod", ""])
def test_a_prod_or_undeclared_box_is_never_asked_about_the_graph(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace, profile: str,
) -> None:
    """Production runs a v1.0 graph without migration 0021, so the command there
    reports a refusal on every deploy. An absent profile is prod, as everywhere."""
    _saved_state(repo, ci_profile=profile)
    _stack_is_up(monkeypatch)
    _drift_answer(monkeypatch, stack, _DRIFTED)

    result = cli_runner_.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 0, result.output
    assert stack.drift == []
    assert stack.report[0]["graph_drift"] is None


def test_a_component_rebuild_is_never_asked_about_the_graph(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace,
) -> None:
    """cc-agent's image says nothing about the graph, and the check costs a full
    read of it."""
    _saved_state(repo, ci_profile="dev")
    _stack_is_up(monkeypatch)
    _drift_answer(monkeypatch, stack, _DRIFTED)

    result = cli_runner_.invoke(cli.app, ["rebuild", "--component", "cc-agent"])

    assert result.exit_code == 0, result.output
    assert stack.drift == []


def test_a_stack_that_is_down_is_not_asked_about_the_graph(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace,
) -> None:
    """With the app container down the exec cannot work, and the reason is
    already on the screen."""
    _saved_state(repo, ci_profile="dev")
    _stack_is_up(monkeypatch, runtimes_ok=False)
    _drift_answer(monkeypatch, stack, _DRIFTED)

    result = cli_runner_.invoke(cli.app, ["rebuild"])

    assert result.exit_code == 1
    assert stack.drift == []


def test_ci_reports_the_drift_but_still_answers_what_the_suite_says(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace,
) -> None:
    """`ci` answers one question. A drifted graph is printed and recorded there;
    `rebuild` is the command that exits red on it."""
    _saved_state(repo, ci_profile="local")
    _stack_is_up(monkeypatch)
    _drift_answer(monkeypatch, stack, _DRIFTED)

    result = cli_runner_.invoke(cli.app, ["ci"])

    assert result.exit_code == 0, result.output
    assert len(stack.drift) == 1
    assert "samples.not_in_mysql" in "".join(result.output.split())
    assert stack.report[0]["graph_drift"][1] is False
