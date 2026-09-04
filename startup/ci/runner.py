"""Invoke the CI smoke suite.

SUBPROCESSES, never imports. startup/ is pinned to typer, rich, neo4j, orjson and
PyMySQL so ./startup.sh stays bootstrappable on a host with no C toolchain;
importing the suite would drag requests and playwright into it.
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from startup.lib.instance import InstanceState


JUNIT_NAME = ".ci-last-run.xml"

# Markdown run records, one file per run, keyed on the image identity. Unlike
# the junit file above -- which is a single slot overwritten every run -- these
# accumulate, so "what did CI say the last time we shipped this image" has an
# answer after the next run has already happened.
REPORTS_DIRNAME = "ci-reports"


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


def reports_dir(repo_root: Path) -> Path:
    return repo_root / "startup" / REPORTS_DIRNAME


def _safe_name(text: str) -> str:
    """A tag as a filename. `:` and `/` are legal on Linux but hostile in a path."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-") or "ci-run"


def running_image(container: str = "nextseek") -> tuple[str | None, str | None]:
    """(image ref, image id) of a running container, or (None, None).

    Soft on every failure: no docker, no container, or a malformed answer costs
    the report its identity line, never the CI run's exit code.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.Config.Image}}\t{{.Image}}", container],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    # getattr, not attribute access: this module's own tests stub subprocess.run
    # with a namespace carrying only `returncode`, and more to the point an
    # identity line is decoration -- it must never be the thing that raises out
    # of a CI run that has already finished.
    if getattr(result, "returncode", 1) != 0:
        return None, None
    parts = str(getattr(result, "stdout", "") or "").strip().split("\t")
    if len(parts) != 2 or not parts[0]:
        return None, None
    return parts[0], parts[1]


def _outcomes(path: Path) -> list[tuple[str, str, str]]:
    """(kind, test id, first line of the message) for everything that is not a pass."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    out: list[tuple[str, str, str]] = []
    for suite in suites:
        for case in suite.iter("testcase"):
            name = case.get("name") or "?"
            for kind, tag in (("FAILED", "failure"), ("ERROR", "error")):
                node = case.find(tag)
                if node is not None:
                    msg = (node.get("message") or "").strip().splitlines()
                    out.append((kind, name, msg[0] if msg else ""))
                    break
            else:
                skip = case.find("skipped")
                if skip is not None:
                    kind = "XFAIL" if skip.get("type") == "pytest.xfail" else "SKIPPED"
                    msg = (skip.get("message") or "").strip().splitlines()
                    out.append((kind, name, msg[0] if msg else ""))
    return out


def write_report(repo_root: Path, *, label: str | None = None,
                 image_ref: str | None = None, image_id: str | None = None,
                 profile: str | None = None, command: list[str] | None = None,
                 now: datetime.datetime | None = None) -> Path | None:
    """Write one markdown record of the run the junit file describes.

    `label` names the file. A rebuild passes its rollback tag, which ties the
    record to the deploy that produced it; a standalone `startup ci` has no tag,
    so the running image's ref plus a timestamp is used instead.

    Returns the path written, or None when there is no usable junit report --
    a run that never produced one (an unreachable stack, a refused profile) has
    nothing to record, and inventing a file for it would be worse than silence.

    Soft throughout: this is a record of the run, never a gate on it. Any failure
    to write returns None rather than changing what CI decided.
    """
    summary = summarize_junit(junit_path(repo_root))
    if summary is None:
        return None

    stamp = now or datetime.datetime.now()
    if not label:
        base = image_ref or "nextseek"
        label = f"{base}-{stamp.strftime('%Y%m%dT%H%M%S')}"

    lines = [
        f"# CI run — {label}",
        "",
        f"- **When:** {stamp.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if image_ref:
        lines.append(f"- **Image:** `{image_ref}`")
    if image_id:
        lines.append(f"- **Image ID:** `{image_id}`")
    if profile:
        lines.append(f"- **Profile:** `{profile}`")
    lines += [
        f"- **Result:** {format_summary(summary)}",
        "",
        "| | count |",
        "|---|---|",
        f"| passed | {summary.passed} |",
        f"| failed | {summary.failed} |",
        f"| errors | {summary.errors} |",
        f"| skipped | {summary.skipped} |",
        f"| xfailed | {summary.xfailed} |",
        "",
    ]

    outcomes = _outcomes(junit_path(repo_root))
    for kind, heading in (("FAILED", "Failures"), ("ERROR", "Errors"),
                          ("SKIPPED", "Skipped"), ("XFAIL", "Expected failures")):
        rows = [(n, m) for k, n, m in outcomes if k == kind]
        if not rows:
            continue
        lines += [f"## {heading}", ""]
        lines += [f"- `{n}`" + (f" — {m}" if m else "") for n, m in rows]
        lines.append("")

    if command:
        lines += ["## Command", "", "```", " ".join(command), "```", ""]

    path = reports_dir(repo_root) / f"{_safe_name(label)}.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        return None
    return path


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
