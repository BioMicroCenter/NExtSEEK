"""Central constants for the NExtSEEK ingestion tool."""
from __future__ import annotations

from pathlib import Path

from NessieAI import paths

BEGIN_MARKER = "<!-- BEGIN NEXTSEEK-DOCS (auto-generated) -->"
END_MARKER = "<!-- END NEXTSEEK-DOCS (auto-generated) -->"

DEFAULT_SOURCE = paths.repo_relative(paths.REPO_ROOT / "themes" / "NextSeek" / "docs")

# The source and outputs are repo-relative, so they resolve against the working directory exactly as
# before the move: run the CLI from the checkout root.
DEFAULT_DOCS_DIR = Path(paths.repo_relative(paths.CC_RUNTIME_DIR / "docs" / "nextseek"))
DEFAULT_CLAUDE_MD_PATH = Path(
    paths.repo_relative(paths.CC_RUNTIME_DIR / "container" / "CLAUDE.md")
)
