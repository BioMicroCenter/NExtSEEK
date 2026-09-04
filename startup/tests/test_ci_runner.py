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
                       "requests", "--with", "playwright"]
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
