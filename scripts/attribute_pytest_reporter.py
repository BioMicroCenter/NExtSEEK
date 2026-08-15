from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

_collected: set[str] = set()
_deselected: set[str] = set()
_outcomes: dict[str, str] = {}


def pytest_collection_finish(session):
    _collected.update(item.nodeid for item in session.items)


def pytest_deselected(items):
    _deselected.update(item.nodeid for item in items)


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.skipped and report.when in {"setup", "teardown"}):
        if report.skipped:
            outcome = "xfailed" if getattr(report, "wasxfail", False) else "skipped"
        else:
            outcome = "passed" if report.passed else "failed"
        previous = _outcomes.get(report.nodeid)
        if previous is not None and previous != outcome:
            raise RuntimeError(f"conflicting terminal outcomes for {report.nodeid}")
        _outcomes[report.nodeid] = outcome


def pytest_sessionfinish(session, exitstatus):
    all_nodes = _collected | _deselected
    rows = []
    for nodeid in sorted(all_nodes):
        outcome = "deselected" if nodeid in _deselected else _outcomes.get(nodeid)
        if outcome is None:
            session.exitstatus = 1
            outcome = "failed"
        rows.append({"nodeid": nodeid, "outcome": outcome})
    root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
    fd, temporary = tempfile.mkstemp(prefix=".node-results.", dir=root)
    with os.fdopen(fd, "w") as stream:
        json.dump(rows, stream, indent=2, sort_keys=True); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    os.link(temporary, root / "node-results.json")
    Path(temporary).unlink()
