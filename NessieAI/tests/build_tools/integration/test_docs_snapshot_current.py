"""Nessie's committed docs snapshot is what the ingester makes from today's user docs.

The site serves themes/NextSeek/docs/ and Nessie's Claude Code container reads the
snapshot under NessieAI/docker/cc-runtime/, so an edit to one page without a fresh
snapshot leaves Nessie answering from the old text. Blocking in CI (ci/blocking_lanes.py).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from NessieAI import paths
from NessieAI.build_tools.ingest_nextseek_docs.__main__ import EXIT_CHANGES_WRITTEN, ingest
from NessieAI.build_tools.ingest_nextseek_docs.constants import (
    DEFAULT_CLAUDE_MD_PATH,
    DEFAULT_DOCS_DIR,
    DEFAULT_SOURCE,
)

REFRESH = (
    "Nessie's docs snapshot is stale. From the repo root run "
    "`python -m NessieAI.build_tools.ingest_nextseek_docs` and commit what it changes under "
    "NessieAI/docker/cc-runtime/ (a Nessie brain change: show the operator the diff)."
)


def test_committed_snapshot_matches_the_user_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = paths.REPO_ROOT
    monkeypatch.chdir(root)  # the ingester's paths are repo-relative, as on the command line
    claude_md = tmp_path / "CLAUDE.md"
    shutil.copyfile(root / DEFAULT_CLAUDE_MD_PATH, claude_md)

    rc = ingest(docs_dir=tmp_path / "docs", claude_md_path=claude_md, source=DEFAULT_SOURCE, force=True)

    assert rc == EXIT_CHANGES_WRITTEN
    fresh = {p.name: p.read_text() for p in (tmp_path / "docs").iterdir()}
    committed = {p.name: p.read_text() for p in (root / DEFAULT_DOCS_DIR).iterdir() if p.is_file()}
    assert fresh == committed, REFRESH
    assert claude_md.read_text() == (root / DEFAULT_CLAUDE_MD_PATH).read_text(), REFRESH
