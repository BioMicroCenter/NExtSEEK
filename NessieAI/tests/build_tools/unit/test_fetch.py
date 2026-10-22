"""Unit tests for NessieAI.build_tools.ingest_nextseek_docs.fetch."""
from __future__ import annotations

from pathlib import Path

import pytest

from NessieAI.build_tools.ingest_nextseek_docs import fetch as fetch_module


def _docs(tmp_path: Path, readme: str, pages: dict[str, str]) -> Path:
    (tmp_path / "README.md").write_text(readme)
    for slug, body in pages.items():
        (tmp_path / f"{slug}.md").write_text(body)
    return tmp_path


def test_corpus_follows_readme_order(tmp_path: Path) -> None:
    root = _docs(
        tmp_path,
        "# Docs\n\n## Start\n\n- [Second](second.md)\n- [First](first.md)\n",
        {"first": "# First\n\nOne.\n", "second": "# Second\n\nTwo.\n", "unlisted": "# Nope\n"},
    )

    corpus = fetch_module.load_repo_docs_corpus(str(root))

    assert corpus == "# Second\n\nTwo.\n\n# First\n\nOne.\n"


def test_corpus_raises_when_a_listed_page_is_missing(tmp_path: Path) -> None:
    root = _docs(tmp_path, "- [Here](here.md)\n- [Gone](gone.md)\n", {"here": "# Here\n"})

    with pytest.raises(FileNotFoundError):
        fetch_module.load_repo_docs_corpus(str(root))
