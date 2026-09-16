#!/usr/bin/env python3
"""The unit tests whose failure fails ci-pytest.yml.

    python3 ci/blocking_lanes.py      # from the repo root: one test path per line

The rest of the application suite is informational: it is scored against
ci/pytest-baseline.txt and never fails the job. The modules these globs expand
to run a second time, in the workflow's "Blocking unit tests
(ci/blocking_lanes.py)" step, where any failure fails it. A new test module joins by its name, with no
edit here: the graph_sync and graph_search tests under nextseek_api/tests/, and
the Graph Search page's view and JavaScript tests under seek/tests/.

Exit 1, printing nothing on stdout, when a glob matches no file: the workflow
passes the output to pytest as its paths, and pytest given no path walks the
whole tree. ci/gate/test_blocking_lanes.py holds the same rule in the gate.

Standard library only: the workflow runs this with the runner's bare python,
before and outside the application's environment.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLOCKING_GLOBS = (
    "nextseek_api/tests/test_graph_sync_*.py",
    "nextseek_api/tests/test_graph_search_*.py",
    "nextseek_api/tests/test_services_graph_*.py",
    # The Graph Search page: its view tests and its JavaScript cases, which skip
    # where node is missing (the workflow step checks for node first).
    "seek/tests/test_graph_search_*.py",
)


def expand(root: Path = ROOT, globs: tuple[str, ...] = BLOCKING_GLOBS) -> list[str]:
    """Every file the globs match, as sorted repo-relative posix paths."""
    found = {p.relative_to(root).as_posix() for g in globs for p in root.glob(g) if p.is_file()}
    return sorted(found)


def unmatched(root: Path = ROOT, globs: tuple[str, ...] = BLOCKING_GLOBS) -> list[str]:
    """The globs that match no file."""
    return [g for g in globs if not any(p.is_file() for p in root.glob(g))]


def main(root: Path = ROOT) -> int:
    missing = unmatched(root)
    if missing:
        for g in missing:
            print(f"ci/blocking_lanes.py: {g} matches no file", file=sys.stderr)
        return 1
    for path in expand(root):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
