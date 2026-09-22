"""Pure-function tests for startup/ci/runner.py: the argv the shim builds, and the
summary it reads back from the suite's junit file. No subprocess, no stack.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from startup.ci import runner
from startup.ci import runner as ci_runner
from startup.lib.instance import InstanceState


def _state(ci_profile: str = "local", port: int = 8000) -> InstanceState:
    return InstanceState(
        name="x", prefix="", ports={"nextseek": port}, compose_project_name="nextseek",
        created="2026-09-02T00:00:00", ci_profile=ci_profile,
    )


# --------------------------------------------------------------------------- #
# build_command
# --------------------------------------------------------------------------- #

def test_build_command_writes_the_junit_file_under_startup(tmp_path: Path) -> None:
    cmd = runner.build_command(tmp_path, _state(), wait_ready=False)
    assert f"--junitxml={tmp_path / 'startup' / '.ci-last-run.xml'}" in cmd


def test_junit_path_is_a_gitignored_file_under_startup(tmp_path: Path) -> None:
    assert runner.junit_path(tmp_path) == tmp_path / "startup" / ".ci-last-run.xml"


def test_build_command_keeps_the_suite_invocation_and_flags(tmp_path: Path) -> None:
    cmd = runner.build_command(tmp_path, _state(port=8123), wait_ready=True,
                               profile="prod", force_profile=None)
    assert cmd[:9] == ["uv", "run", "--no-project", "--with", "pytest", "--with",
                       "requests", "--with", runner.PLAYWRIGHT]
    assert cmd[9:11] == ["pytest", "ci/smoke/"]
    assert cmd[cmd.index("--base-url") + 1] == "http://127.0.0.1:8123"
    assert "--wait-ready" in cmd
    assert cmd[cmd.index("--profile") + 1] == "prod"
    assert "--force-profile" not in cmd


# --------------------------------------------------------------------------- #
# summarize_junit
# --------------------------------------------------------------------------- #

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="1" failures="2" skipped="4" tests="10" time="344.383">
<properties>
<property name="readiness_seconds" value="304"/>
<property name="readiness_floor" value="300"/>
</properties>
<testcase classname="a" name="p1" time="0.1"/>
<testcase classname="a" name="p2" time="0.1"/>
<testcase classname="a" name="p3" time="0.1"/>
<testcase classname="a" name="f1" time="0.1"><failure message="boom">tb</failure></testcase>
<testcase classname="a" name="f2" time="0.1"><failure message="boom">tb</failure></testcase>
<testcase classname="a" name="e1" time="0.1"><error message="setup">tb</error></testcase>
<testcase classname="a" name="s1" time="0.0"><skipped type="pytest.skip" message="opt-in"/></testcase>
<testcase classname="a" name="x1" time="0.0"><skipped type="pytest.xfail" message="known"/></testcase>
<testcase classname="a" name="x2" time="0.0"><skipped type="pytest.xfail" message="known"/></testcase>
<testcase classname="a" name="x3" time="0.0"><skipped type="pytest.xfail" message="known"/></testcase>
</testsuite>
</testsuites>
"""


def test_summarize_junit_counts_every_outcome_separately(tmp_path: Path) -> None:
    path = tmp_path / "r.xml"; path.write_text(JUNIT)
    s = runner.summarize_junit(path)
    assert (s.passed, s.failed, s.errors, s.skipped, s.xfailed) == (3, 2, 1, 1, 3)
    assert s.seconds == pytest.approx(344.383)
    assert s.readiness_seconds == 304


def test_summarize_junit_returns_none_when_the_file_is_missing(tmp_path: Path) -> None:
    assert runner.summarize_junit(tmp_path / "nope.xml") is None


def test_summarize_junit_returns_none_when_the_file_is_not_xml(tmp_path: Path) -> None:
    path = tmp_path / "r.xml"; path.write_text("not xml at all")
    assert runner.summarize_junit(path) is None


# --------------------------------------------------------------------------- #
# format_summary
# --------------------------------------------------------------------------- #

def test_format_summary_reads_like_pytest_plus_readiness() -> None:
    s = runner.Summary(passed=207, failed=0, errors=0, skipped=6, xfailed=13,
                       seconds=344.4, readiness_seconds=304)
    assert runner.format_summary(s) == "207 passed, 6 skipped, 13 xfailed in 5:44 (readiness 5:04)"


def test_format_summary_leads_with_failures_and_omits_zero_counts() -> None:
    s = runner.Summary(passed=200, failed=3, errors=1, skipped=0, xfailed=0,
                       seconds=61.0, readiness_seconds=None)
    assert runner.format_summary(s) == "3 failed, 1 error, 200 passed in 1:01"


# --------------------------------------------------------------------------- #
# the markdown run record
# --------------------------------------------------------------------------- #

_JUNIT_ONE_OF_EACH = """<testsuites><testsuite name="p" time="12.5">
  <testcase name="test_ok"/>
  <testcase name="test_bad"><failure message="AssertionError: nope"/></testcase>
  <testcase name="test_skip"><skipped message="needs a stack"/></testcase>
  <testcase name="test_known"><skipped type="pytest.xfail" message="known defect"/></testcase>
</testsuite></testsuites>"""


def _junit(tmp_path, xml=_JUNIT_ONE_OF_EACH):
    path = ci_runner.junit_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml)
    return path


def test_report_is_named_for_the_label_with_the_colon_made_safe(tmp_path):
    """A rollback tag is the natural key and is not a legal-looking filename."""
    _junit(tmp_path)
    p = ci_runner.write_report(tmp_path, label="nextseek-nextseek:pre-20260904T091653-614b9ac1")
    assert p is not None
    assert p.name == "nextseek-nextseek-pre-20260904T091653-614b9ac1.md"
    assert p.parent == ci_runner.reports_dir(tmp_path)


def test_report_records_every_outcome_class_and_the_identity(tmp_path):
    _junit(tmp_path)
    p = ci_runner.write_report(tmp_path, label="run", image_ref="img:tag",
                               image_id="sha256:abc", profile="dev")
    body = p.read_text()
    assert "img:tag" in body and "sha256:abc" in body and "`dev`" in body
    assert "test_bad" in body and "AssertionError: nope" in body
    assert "test_skip" in body and "test_known" in body
    # Counts come from the junit, not from prose.
    assert "| passed | 1 |" in body
    assert "| failed | 1 |" in body
    assert "| xfailed | 1 |" in body


def test_report_records_the_stack_health_lines(tmp_path):
    _junit(tmp_path)
    p = ci_runner.write_report(tmp_path, label="run", health=[
        ("app + front door", True, "nextseek + nextseek_nginx running"),
        ("first-party images", False, "ABSENT: dmac-assistant:poc"),
    ])
    body = p.read_text()
    assert "## Stack health" in body
    assert "✓ **app + front door:** nextseek + nextseek_nginx running" in body
    assert "✗ **first-party images:** ABSENT: dmac-assistant:poc" in body
    # Health comes before the suite's own outcomes: it is step 1 of the run.
    assert body.index("## Stack health") < body.index("## Failures")


def test_no_junit_means_no_report_rather_than_an_empty_one(tmp_path):
    """A run that never produced a report has nothing to record."""
    assert ci_runner.write_report(tmp_path, label="run") is None
    assert not ci_runner.reports_dir(tmp_path).exists()


def test_records_accumulate_rather_than_overwrite(tmp_path):
    """The junit file is one slot; these are the history it does not keep."""
    _junit(tmp_path)
    ci_runner.write_report(tmp_path, label="first")
    ci_runner.write_report(tmp_path, label="second")
    names = sorted(p.name for p in ci_runner.reports_dir(tmp_path).glob("*.md"))
    assert names == ["first.md", "second.md"]


def test_a_missing_label_falls_back_to_the_image_and_a_timestamp(tmp_path):
    _junit(tmp_path)
    p = ci_runner.write_report(tmp_path, image_ref="nextseek-nextseek:latest")
    assert p.name.startswith("nextseek-nextseek-latest-")
    assert p.name.endswith(".md")


def test_running_image_survives_a_stub_without_stdout(tmp_path, monkeypatch):
    """Identity is decoration; it must never raise out of a finished CI run."""
    monkeypatch.setattr(ci_runner.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0))
    assert ci_runner.running_image() == (None, None)


def test_running_image_survives_no_docker_at_all(monkeypatch):
    def boom(*a, **k):
        raise OSError("no docker here")
    monkeypatch.setattr(ci_runner.subprocess, "run", boom)
    assert ci_runner.running_image() == (None, None)


# --------------------------------------------------------------------------- #
# the Nessie lane: its switch, its summary and evidence, its CI record section
# --------------------------------------------------------------------------- #

def test_build_command_runs_the_nessie_lane_by_default(tmp_path):
    assert "--no-nessie" not in runner.build_command(tmp_path, _state(), wait_ready=False)


def test_build_command_passes_no_nessie_when_off(tmp_path):
    cmd = runner.build_command(tmp_path, _state(), wait_ready=False, nessie=False)
    assert cmd[-1] == "--no-nessie"


def test_run_ci_names_the_nessie_summary_and_evidence_paths(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, cwd, env):
        seen.update(env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.nessie_summary_path(tmp_path).parent.mkdir(parents=True)
    runner.nessie_summary_path(tmp_path).write_text("{}")      # a stale one
    runner.run_ci(tmp_path, _state(), wait_ready=False)
    assert seen["CI_NESSIE_SUMMARY"] == str(runner.nessie_summary_path(tmp_path))
    assert seen["CI_NESSIE_EVIDENCE_DIR"] == str(runner.nessie_evidence_path(tmp_path))
    assert not runner.nessie_summary_path(tmp_path).exists(), "a stale summary survived"


def test_run_ci_clears_stale_nessie_evidence(tmp_path, monkeypatch):
    """A previous failure's trace must not be filed under this run's record."""
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda cmd, cwd, env: SimpleNamespace(returncode=0))
    stale = runner.nessie_evidence_path(tmp_path)
    stale.mkdir(parents=True)
    (stale / "trace.zip").write_bytes(b"old")
    runner.run_ci(tmp_path, _state(), wait_ready=False)
    assert not stale.exists()


def test_nessie_summary_and_evidence_live_under_startup(tmp_path):
    assert runner.nessie_summary_path(tmp_path) == tmp_path / "startup" / ".ci-nessie-last.json"
    assert runner.nessie_evidence_path(tmp_path) == tmp_path / "startup" / ".ci-nessie-evidence"


def test_read_nessie_summary_is_none_when_absent_or_unreadable(tmp_path):
    assert runner.read_nessie_summary(tmp_path) is None
    path = runner.nessie_summary_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    assert runner.read_nessie_summary(tmp_path) is None
    path.write_text('{"posts": 4}')
    assert runner.read_nessie_summary(tmp_path) == {"posts": 4}


SUMMARY = {
    "questions": [
        {"key": "capabilities", "text": "What can you do?", "expected_route": "nextseek_query",
         "route": "nextseek_query", "source": "baml", "path": "system", "task_id": "t1",
         "session_id": "s", "status": "completed", "seconds": 31.5, "cost_usd": None,
         "error": None},
        {"key": "nhp_graph", "text": "Make me a graph of NHP species",
         "expected_route": "container_cc", "route": "container_cc", "source": "baml",
         "path": "cc", "task_id": "t4", "session_id": "s", "status": "completed",
         "seconds": 88.0, "cost_usd": 0.24, "error": None},
    ],
    "posts": 2, "refused_posts": 0, "spent_usd": 0.24, "ceiling_usd": 1.0,
    "kept_session": {"session_id": "s", "debug_url": "http://127.0.0.1:8000/nextseek_api/nessie/sessions/s/debug/"},
    "evidence_dir": None,
}


def test_render_nessie_section():
    text = "\n".join(runner.render_nessie_section(SUMMARY))
    assert text.startswith("## Nessie")
    assert "| capabilities | nextseek_query | baml | system | 31.5 | unmeasured | completed |" in text
    assert "| nhp_graph | container_cc | baml | cc | 88.0 | $0.24 | completed |" in text
    assert "$0.24 of $1.00" in text
    assert "/nessie/sessions/s/debug/" in text
    assert "\u2014" not in text


def test_render_nessie_section_names_each_question_s_path():
    """Spec 3.4: route, source, path, task_id, duration and cost per question."""
    text = "\n".join(runner.render_nessie_section(SUMMARY))
    assert "| question | route | source | path | seconds | cost | status | task |" in text
    # A summary from before the lane recorded a path still renders, with a dash.
    old = dict(SUMMARY, questions=[{k: v for k, v in SUMMARY["questions"][0].items()
                                    if k != "path"}])
    assert "| capabilities | nextseek_query | baml | - | 31.5 |" in "\n".join(
        runner.render_nessie_section(old))


def test_render_nessie_section_names_each_turn_s_task_id():
    """The spec's record carries the task_id, the handle /debug/ resolves a turn by."""
    text = "\n".join(runner.render_nessie_section(SUMMARY))
    assert ("| capabilities | nextseek_query | baml | system | 31.5 | unmeasured | completed "
            "| `t1` |") in text
    assert "| nhp_graph | container_cc | baml | cc | 88.0 | $0.24 | completed | `t4` |" in text


def test_render_nessie_section_carries_a_question_s_error_and_a_turn_never_asked():
    summary = dict(SUMMARY, kept_session=None, questions=[
        dict(SUMMARY["questions"][0], status="failed", error="HTTP 502"),
        {"key": "ndma_mice", "route": None, "source": None, "task_id": None,
         "status": None, "seconds": None, "cost_usd": None,
         "error": "not asked: the lane passed its 720 s deadline"},
    ])
    text = "\n".join(runner.render_nessie_section(summary))
    assert "| failed: HTTP 502 |" in text
    assert "| ndma_mice | - | - | - | - | unmeasured | not run: not asked:" in text
    assert "Kept session" not in text


def test_render_nessie_section_names_a_chat_the_lane_could_not_delete():
    """Decision 8 deletes the chat on a pass. A DELETE that did not answer 204
    leaves it behind, and the record says so instead of staying silent."""
    error = "the chat s was not deleted: DELETE answered 500: boom"
    text = "\n".join(runner.render_nessie_section(
        dict(SUMMARY, kept_session=None, cleanup_error=error)))
    assert f"- **Cleanup failed:** {error}; delete it by hand." in text
    # A summary with no cleanup_error key (every lane before this field) renders
    # no cleanup line.
    assert "Cleanup failed" not in "\n".join(runner.render_nessie_section(SUMMARY))


def test_write_report_includes_the_nessie_section_and_moves_the_evidence(tmp_path):
    runner.junit_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    runner.junit_path(tmp_path).write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0" time="1">'
        '<testcase classname="c" name="n" time="1"/></testsuite></testsuites>')
    evidence = runner.nessie_evidence_path(tmp_path)
    evidence.mkdir(parents=True)
    (evidence / "page.png").write_bytes(b"png")
    path = runner.write_report(tmp_path, label="run1", nessie_summary=dict(SUMMARY))
    text = path.read_text()
    assert "## Nessie" in text
    moved = runner.reports_dir(tmp_path) / "run1-nessie"
    assert (moved / "page.png").is_file() and not evidence.exists()
    assert str(moved) in text


def test_write_report_without_a_nessie_summary_has_no_nessie_section(tmp_path):
    _junit(tmp_path)
    evidence = runner.nessie_evidence_path(tmp_path)
    evidence.mkdir(parents=True)
    (evidence / "page.png").write_bytes(b"png")
    text = runner.write_report(tmp_path, label="run2").read_text()
    assert "## Nessie" not in text
    # Not the lane's run, so not its evidence to file.
    assert (evidence / "page.png").is_file()
    assert not (runner.reports_dir(tmp_path) / "run2-nessie").exists()


def test_write_report_files_the_evidence_of_a_lane_that_wrote_no_summary(tmp_path):
    """Spec 3.4. The lane writes its summary in chat_run's teardown, so a lane that
    died earlier (the chat page never loaded, the write account could not log in)
    leaves a trace and no summary. That trace is the evidence a failed rebuild most
    needs, and the next run clears the slot it sits in."""
    _junit(tmp_path)
    evidence = runner.nessie_evidence_path(tmp_path)
    evidence.mkdir(parents=True)
    (evidence / "trace.zip").write_bytes(b"zip")
    path = runner.write_report(tmp_path, label="run4", nessie_ran=True, nessie_summary=None)
    text = path.read_text()
    moved = runner.reports_dir(tmp_path) / "run4-nessie"
    assert (moved / "trace.zip").is_file() and not evidence.exists()
    assert "## Nessie" in text
    assert "stopped before it wrote its summary" in text
    assert str(moved) in text
    assert "\u2014" not in text.split("## Nessie", 1)[1].split("\n## ", 1)[0]


def test_write_report_says_a_lane_without_a_summary_left_no_evidence(tmp_path):
    _junit(tmp_path)
    text = runner.write_report(tmp_path, label="run5", nessie_ran=True).read_text()
    assert "stopped before it wrote its summary" in text
    assert "**Evidence:** none written" in text
    assert not (runner.reports_dir(tmp_path) / "run5-nessie").exists()


def test_write_report_puts_the_nessie_section_after_stack_health(tmp_path):
    _junit(tmp_path)
    text = runner.write_report(tmp_path, label="run3", nessie_summary=dict(SUMMARY),
                               health=[("app + front door", True, "running")]).read_text()
    assert text.index("## Stack health") < text.index("## Nessie")


# --------------------------------------------------------------------------- #
# the browser: pinned, and installed by the run itself
# --------------------------------------------------------------------------- #

def test_playwright_is_pinned_to_one_exact_version():
    assert runner.PLAYWRIGHT.startswith("playwright==") and runner.PLAYWRIGHT.count(".") == 2


def test_the_suite_and_the_browser_install_use_the_same_pin(tmp_path):
    assert runner.PLAYWRIGHT in runner.build_command(tmp_path, _state(), wait_ready=False)
    assert runner.browser_install_command()[:5] == ["uv", "run", "--no-project", "--with", runner.PLAYWRIGHT]
    assert runner.browser_install_command()[-2:] == ["install", "chromium"]


def test_run_ci_installs_the_browser_before_the_suite(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, cwd, env):
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.run_ci(tmp_path, _state(), wait_ready=False) == 0
    assert calls[0] == runner.browser_install_command()
    assert calls[1][9:11] == ["pytest", "ci/smoke/"]


def test_a_failed_browser_install_still_runs_the_suite(tmp_path, monkeypatch, capsys):
    codes = iter([1, 0])
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda cmd, cwd, env: SimpleNamespace(returncode=next(codes)))
    assert runner.run_ci(tmp_path, _state(), wait_ready=False) == 0
    assert "may fail at setup" in capsys.readouterr().err
