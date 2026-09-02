"""Invoke the CI smoke suite.

SUBPROCESSES, never imports. startup/ is pinned to typer, rich, neo4j, orjson and
PyMySQL so ./startup.sh stays bootstrappable on a host with no C toolchain;
importing the suite would drag requests and playwright into it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from startup.lib.instance import InstanceState


JUNIT_NAME = ".ci-last-run.xml"


def junit_path(repo_root: Path) -> Path:
    """Where the suite writes its junit report for the shim to summarise.

    Under startup/ next to .instance.json, gitignored, overwritten every run.
    """
    return repo_root / "startup" / JUNIT_NAME


def build_command(repo_root: Path, state: InstanceState, *, wait_ready: bool,
                  profile: str | None = None,
                  force_profile: str | None = None) -> list[str]:
    """The exact argv the shim runs. Pure, so the CLI can print it before running."""
    port = state.ports.get("nextseek", 8000)
    cmd = [
        "uv", "run", "--no-project",
        "--with", "pytest", "--with", "requests", "--with", "playwright",
        "pytest", "ci/smoke/",
        "--base-url", f"http://127.0.0.1:{port}",
        f"--junitxml={junit_path(repo_root)}",
    ]
    if wait_ready:
        cmd.append("--wait-ready")
    if profile:
        cmd += ["--profile", profile]
    if force_profile:
        cmd += ["--force-profile", force_profile]
    return cmd


def run_ci(repo_root: Path, state: InstanceState, *, wait_ready: bool,
           profile: str | None = None, force_profile: str | None = None,
           confirm_force: bool = False) -> int:
    box_profile = state.ci_profile or "prod"      # fail closed
    cmd = build_command(repo_root, state, wait_ready=wait_ready,
                        profile=profile, force_profile=force_profile)
    env = {
        **os.environ,
        # PYTHONDONTWRITEBYTECODE: a repo-root pytest run must never leave
        # __pycache__ behind in the working tree it is testing.
        "PYTHONDONTWRITEBYTECODE": "1",
        "CI_BOX_PROFILE": box_profile,
    }
    if confirm_force:
        # Set for this invocation only, and only after the operator answered the
        # prompt in cli.ci. Never persisted, never exported to a workflow file.
        env["CI_FORCE_PROFILE_CONFIRM"] = "yes"
    # A run that exits before the first test (a readiness failure, a refused
    # profile) writes no report; the previous run's must not be read in its place.
    junit_path(repo_root).unlink(missing_ok=True)
    try:
        return subprocess.run(cmd, cwd=repo_root, env=env).returncode
    except FileNotFoundError:
        # This module stays free of the UI layer -- startup.lib.ui and the rich
        # console behind it -- so that it can be called from anywhere: the CLI, a
        # test, or a future hook that has no terminal. That is why it writes its own
        # stderr here rather than calling ui.fail. A traceback would read as a
        # harness fault rather than as the missing tool it is.
        print("cannot run CI: 'uv' is not on PATH. Install it (see DEPLOYMENT.md) "
              "or rerun with --no-ci.", file=sys.stderr)
        return 127


@dataclass(frozen=True)
class Summary:
    """One run's outcome, read back from the suite's junit file."""
    passed: int
    failed: int
    errors: int
    skipped: int
    xfailed: int
    seconds: float
    readiness_seconds: int | None


def summarize_junit(path: Path) -> Summary | None:
    """Read pytest's junit file. None when there is no usable report."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    passed = failed = errors = skipped = xfailed = 0
    seconds = 0.0
    readiness: int | None = None
    for suite in suites:
        seconds += float(suite.get("time") or 0)
        for prop in suite.iter("property"):
            if prop.get("name") == "readiness_seconds" and readiness is None:
                try:
                    readiness = int(prop.get("value", ""))
                except ValueError:
                    readiness = None
        for case in suite.iter("testcase"):
            if case.find("failure") is not None:
                failed += 1
            elif case.find("error") is not None:
                errors += 1
            elif (skip := case.find("skipped")) is not None:
                if skip.get("type") == "pytest.xfail":
                    xfailed += 1
                else:
                    skipped += 1
            else:
                passed += 1
    return Summary(passed, failed, errors, skipped, xfailed, seconds, readiness)


def _mmss(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def format_summary(s: Summary) -> str:
    """pytest's own summary vocabulary, failures first, zero counts omitted."""
    parts: list[str] = []
    if s.failed:
        parts.append(f"{s.failed} failed")
    if s.errors:
        parts.append(f"{s.errors} error" + ("" if s.errors == 1 else "s"))
    parts.append(f"{s.passed} passed")
    if s.skipped:
        parts.append(f"{s.skipped} skipped")
    if s.xfailed:
        parts.append(f"{s.xfailed} xfailed")
    text = ", ".join(parts) + f" in {_mmss(s.seconds)}"
    if s.readiness_seconds is not None:
        text += f" (readiness {_mmss(s.readiness_seconds)})"
    return text
