"""A turn that is only a NExtSEEK page name is never searched as data.

Production 2026-11-16: after a zero for a named subject, the user typed only "Sampletree". The parser read it with
the previous question (filters.keywords held the subject) but kept "Sampletree" as a keyword, and the graph step
searched every record for the word: 0. The same word after an answer with no subject in the filters went to the
system agent as a how-to about the page and was right. One rule now: the page name leaves the keywords; with a
subject left the turn is that subject's graph question, with none it is a how-to.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chat_nextseek.agents import parser as p
from chat_nextseek.schemas.router import ParserPlan

DOCS = Path(__file__).resolve().parents[3] / "themes" / "NextSeek" / "docs"


def _plan(mode, keywords=(), uids=(), resolved_keywords=None):
    plan = ParserPlan(mode=mode, intent_summary="x")
    plan.filters.keywords = list(keywords)
    plan.filters.uids = list(uids)
    plan.resolved.keywords = list(resolved_keywords if resolved_keywords is not None else keywords)
    return plan


def test_a_page_word_read_with_the_previous_subject_searches_the_subject_not_the_word():
    plan = _plan("graph_query", ["Sampletree", "QX-12"], resolved_keywords=["Sampletree"])
    out = p._route_by_kind("Sampletree", plan, None)
    assert out.mode == "graph_query"
    assert out.filters.keywords == ["QX-12"] and out.resolved.keywords == []


def test_a_page_word_with_no_subject_is_a_how_to():
    out = p._route_by_kind("Sampletree", _plan("graph_query", ["Sampletree"]), None)
    assert out.mode == "system_question" and out.filters.keywords == []


@pytest.mark.parametrize("q", ["sample search", "Sample Tree?", "sample-tree"])
def test_other_spellings_and_pages(q):
    assert p._route_by_kind(q, _plan("graph_query", [q]), None).mode == "system_question"


@pytest.mark.parametrize("q", ["Projects", "Home", "sample tree for QX-12", "lung"])
def test_a_sidebar_word_or_a_longer_question_is_unchanged(q):
    plan = _plan("graph_query", [q])
    assert p._route_by_kind(q, plan, None) is plan


def test_every_page_name_is_a_name_the_user_docs_use():
    text = " ".join(f.read_text(encoding="utf-8") for f in DOCS.glob("*.md")).lower()
    for name in p.PAGE_NAMES:
        assert name in text, name
