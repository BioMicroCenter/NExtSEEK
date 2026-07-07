"""Pin the GitBook site-index plus Markdown-page source contract."""
from __future__ import annotations

import json

import pytest

from build_tools.ingest_nextseek_docs import fetch as fetch_module

SITE_INDEX_URL = (
    "https://koch-institute-mit.gitbook.io/mit-data-management-analysis-core/"
    "~gitbook/site-index"
)


def test_markdown_source_loader_uses_dynamic_site_index_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_index = json.dumps(
        {
            "version": 1,
            "pages": [
                {
                    "title": "Dynamic Landing",
                    "pathname": "/mit-data-management-analysis-core",
                },
                {
                    "title": "New Child Page",
                    "pathname": "/mit-data-management-analysis-core/new-child-page",
                },
            ],
        }
    ).encode("utf-8")
    responses = {
        SITE_INDEX_URL: site_index,
        "https://koch-institute-mit.gitbook.io/mit-data-management-analysis-core/dynamic-landing.md": (
            b"# Dynamic Landing\n\nRoot page body.\n"
        ),
        "https://koch-institute-mit.gitbook.io/mit-data-management-analysis-core/new-child-page.md": (
            b"# New Child Page\n\nChild page body.\n"
        ),
    }

    monkeypatch.setattr(fetch_module, "fetch_source_bytes", lambda url: responses[url])

    corpus = fetch_module.load_gitbook_markdown_corpus(SITE_INDEX_URL)

    assert "# Dynamic Landing" in corpus
    assert "# New Child Page" in corpus
    assert corpus.index("# Dynamic Landing") < corpus.index("# New Child Page")
