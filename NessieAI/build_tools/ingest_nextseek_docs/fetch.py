"""Read the in-repo NExtSEEK user docs (themes/NextSeek/docs) as one Markdown corpus."""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Same rule as seek/views/pages.py:_TOC_PAGE.
_TOC_PAGE = re.compile(r"^- \[(.+?)\]\(([\w-]+)\.md\)")


def load_repo_docs_corpus(docs_dir: str) -> str:
    """Join the pages README.md lists, in order, into one corpus.

    Each page already starts with its own H1. A listed page with no file raises.
    """
    root = Path(docs_dir)
    slugs = [
        m[2]
        for line in (root / "README.md").read_text().splitlines()
        if (m := _TOC_PAGE.match(line))
    ]
    pages = [(root / f"{slug}.md").read_text().strip() for slug in slugs]
    if not pages:
        raise ValueError(f"no docs pages found under {docs_dir}")
    logger.info("Loaded %d docs pages from %s", len(pages), docs_dir)
    return "\n\n".join(pages) + "\n"
