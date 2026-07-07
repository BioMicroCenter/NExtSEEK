"""Central constants for the NExtSEEK ingestion tool."""
from __future__ import annotations

from pathlib import Path

BEGIN_MARKER = "<!-- BEGIN NEXTSEEK-DOCS (auto-generated) -->"
END_MARKER = "<!-- END NEXTSEEK-DOCS (auto-generated) -->"

DEFAULT_DOC_URL = (
    "https://koch-institute-mit.gitbook.io/mit-data-management-analysis-core/"
    "~gitbook/site-index"
)

DEFAULT_DOCS_DIR = Path("docker/cc-runtime/docs/nextseek")
DEFAULT_CLAUDE_MD_PATH = Path("docker/cc-runtime/container/CLAUDE.md")
