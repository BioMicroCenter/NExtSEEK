"""Unit tests for NessieAI.build_tools.ingest_nextseek_docs.constants."""
from __future__ import annotations

from pathlib import Path

from NessieAI import paths
from NessieAI.build_tools.ingest_nextseek_docs import constants as C

RAW_DEFAULT_DOCS_DIR = C.DEFAULT_DOCS_DIR
RAW_DEFAULT_CLAUDE_MD_PATH = C.DEFAULT_CLAUDE_MD_PATH


def test_begin_marker_exact_string() -> None:
    assert C.BEGIN_MARKER == "<!-- BEGIN NEXTSEEK-DOCS (auto-generated) -->"


def test_end_marker_exact_string() -> None:
    assert C.END_MARKER == "<!-- END NEXTSEEK-DOCS (auto-generated) -->"


def test_default_source_is_repo_docs_dir() -> None:
    assert C.DEFAULT_SOURCE == "themes/NextSeek/docs"


def test_default_docs_dir_is_pathlib_path() -> None:
    assert isinstance(RAW_DEFAULT_DOCS_DIR, Path)
    assert str(RAW_DEFAULT_DOCS_DIR) == "NessieAI/docker/cc-runtime/docs/nextseek"


def test_default_claude_md_path_is_container_file() -> None:
    assert isinstance(RAW_DEFAULT_CLAUDE_MD_PATH, Path)
    assert str(RAW_DEFAULT_CLAUDE_MD_PATH) == "NessieAI/docker/cc-runtime/container/CLAUDE.md"


def test_markers_match_container_claude_md_file() -> None:
    """Constants must match the strings baked into container/CLAUDE.md exactly."""
    md = (paths.CC_RUNTIME_DIR / "container" / "CLAUDE.md").read_text()
    assert C.BEGIN_MARKER in md
    assert C.END_MARKER in md
