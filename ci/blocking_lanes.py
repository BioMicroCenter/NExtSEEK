#!/usr/bin/env python3
"""The unit tests whose failure fails ci-pytest.yml.

    python3 ci/blocking_lanes.py      # from the repo root: one test path per line

The rest of the application suite is informational: it is scored against
ci/pytest-baseline.txt and never fails the job. The modules these globs expand
to run a second time, in the workflow's "Blocking unit tests
(ci/blocking_lanes.py)" step, where any failure fails it. A new test module joins by its name, with no
edit here: the graph_sync and graph_search tests under nextseek_api/tests/, the
studies tool's tests under nextseek_api/studies/tests/, the Sample Search page's view and
JavaScript tests and the user docs tests under seek/tests/, and the check that Nessie's docs
snapshot matches the user docs. Two modules are named one by one: the entity_tree view tests and
their read-routing test.

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
    # The studies tool's own suite: it writes SEEK only through stubs and runs on SQLite, so it is as safe to block
    # on as the graph_sync tests.
    "nextseek_api/studies/tests/test_*.py",
    # The Sample Search page, whose two search boxes send their searches to
    # graph_search: its view tests and its JavaScript cases, which skip where node
    # is missing (the workflow step checks for node first). It replaced the
    # glob seek/tests/test_graph_search_*.py when the separate Graph Search page
    # was retired and its tests went with it.
    "seek/tests/test_sample_search_*.py",
    # The entity_tree endpoints, whose type-pair statements read every assay an edge carries; no glob covers them.
    "nextseek_api/tests/test_services_entity_tree.py",
    "nextseek_api/tests/test_entity_tree_read_routing.py",
    # The user docs at /docs/: every page renders and its links, anchors and images
    # resolve; and Nessie's docs snapshot is regenerated whenever a page changes.
    "seek/tests/test_docs_*.py",
    "NessieAI/tests/build_tools/integration/test_docs_snapshot_*.py",
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
