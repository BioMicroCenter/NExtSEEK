"""Pin the in-repo docs source contract: README.md lists pages, each starts with an H1."""
from __future__ import annotations

from pathlib import Path

from NessieAI import paths
from NessieAI.build_tools.ingest_nextseek_docs import fetch as fetch_module
from NessieAI.build_tools.ingest_nextseek_docs.split import split_by_h1


def test_loader_corpus_splits_into_one_section_per_page(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("## A\n\n- [Alpha](alpha.md)\n- [Beta](beta.md)\n")
    (tmp_path / "alpha.md").write_text("# Alpha\n\nAlpha body.\n")
    (tmp_path / "beta.md").write_text("# Beta\n\nBeta body.\n")

    sections = split_by_h1(fetch_module.load_repo_docs_corpus(str(tmp_path)))

    assert [s.title for s in sections] == ["Alpha", "Beta"]


def test_default_source_exists_and_has_a_readme() -> None:
    from NessieAI.build_tools.ingest_nextseek_docs.constants import DEFAULT_SOURCE

    assert (paths.REPO_ROOT / DEFAULT_SOURCE / "README.md").is_file()
