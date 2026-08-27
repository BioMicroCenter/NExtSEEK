"""Unit tests for build_tools.ingest_nextseek_docs.constants."""
from __future__ import annotations

from pathlib import Path

from build_tools.ingest_nextseek_docs import constants as C

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DEFAULT_DOCS_DIR = C.DEFAULT_DOCS_DIR
RAW_DEFAULT_CLAUDE_MD_PATH = C.DEFAULT_CLAUDE_MD_PATH


def test_begin_marker_exact_string() -> None:
    assert C.BEGIN_MARKER == "<!-- BEGIN NEXTSEEK-DOCS (auto-generated) -->"


def test_end_marker_exact_string() -> None:
    assert C.END_MARKER == "<!-- END NEXTSEEK-DOCS (auto-generated) -->"


def test_default_doc_url_is_gitbook_site_index() -> None:
    assert C.DEFAULT_DOC_URL == (
        "https://koch-institute-mit.gitbook.io/mit-data-management-analysis-core/"
        "~gitbook/site-index"
    )


def test_default_docs_dir_is_pathlib_path() -> None:
    assert isinstance(RAW_DEFAULT_DOCS_DIR, Path)
    assert str(RAW_DEFAULT_DOCS_DIR) == "docker/cc-runtime/docs/nextseek"


def test_default_claude_md_path_is_container_file() -> None:
    assert isinstance(RAW_DEFAULT_CLAUDE_MD_PATH, Path)
    assert str(RAW_DEFAULT_CLAUDE_MD_PATH) == "docker/cc-runtime/container/CLAUDE.md"


def test_markers_match_container_claude_md_file() -> None:
    """Constants must match the strings baked into container/CLAUDE.md exactly."""
    md = (REPO_ROOT / "docker" / "cc-runtime" / "container" / "CLAUDE.md").read_text()
    assert C.BEGIN_MARKER in md
    assert C.END_MARKER in md
