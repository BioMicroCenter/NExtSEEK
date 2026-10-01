"""Every sample UID a Nessie reply names links to that sample's page.

The embedded chat UI already linkifies bare UIDs (``remark-uid-links.ts``), but it
walks markdown text nodes only, so a UID the model wraps in backticks, which the
chatter does for most of its examples (``**`TIS-200901ENG-1`**``), reaches the user
as plain code. The rewrite runs on the reply text server side, so it holds for every
UID and does not depend on the shipped bundle.
"""
from __future__ import annotations

import pytest

from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan
from chat_nextseek.uid_links import link_sample_uids, sample_url

OPERATOR_EXAMPLE = (
    "Representative examples include NHP-220630FLY-1, NHP-220630FLY-2, and "
    "NHP-220630FLY-3 (all Macaca fascicularis). The full list of 73 records is "
    "available in the attached table and downloadable file."
)


def _link(uid: str, text: str | None = None) -> str:
    return f"[{text or uid}](/seek/sampletree/uid={uid}/)"


def test_the_sample_page_is_the_uid_addressed_route():
    assert sample_url("NHP-220630FLY-1") == "/seek/sampletree/uid=NHP-220630FLY-1/"


def test_the_operators_example_links_all_three_uids_and_nothing_else():
    out = link_sample_uids(OPERATOR_EXAMPLE)
    assert out == (
        f"Representative examples include {_link('NHP-220630FLY-1')}, "
        f"{_link('NHP-220630FLY-2')}, and {_link('NHP-220630FLY-3')} "
        "(all Macaca fascicularis). The full list of 73 records is available in the "
        "attached table and downloadable file."
    )


def test_a_backticked_uid_becomes_a_link_that_keeps_its_code_styling():
    out = link_sample_uids("* **`TIS-200901ENG-1`** (liver)")
    assert out == f"* **{_link('TIS-200901ENG-1', '`TIS-200901ENG-1`')}** (liver)"


def test_other_inline_code_is_left_alone():
    text = "Run `MATCH (s {uid: 'TIS-200901ENG-1'})` to see it."
    assert link_sample_uids(text) == text


@pytest.mark.parametrize("uid", [
    "D.SEQ-240910LAU-4", "A.GEX-220630FLY-2", "D.IMG-230201KAM-76",
    "NHP-220630FLY-1-PUB", "TIS-230324BOO-39-PUB2", "AB-230522GRI-1",
])
def test_dotted_types_and_pub_suffixes_link_whole(uid):
    assert link_sample_uids(f"See {uid}.") == f"See {_link(uid)}."


def test_a_uid_inside_a_longer_token_or_path_is_not_rewritten():
    for text in (
        "file fooNHP-220630FLY-1 here",
        "NHP-220630FLY-12a is not a UID",
        "NHP-220630FLY-1_R1.fastq.gz",
        "path/NHP-220630FLY-1/x",
        "NHP-2206FLY-1 has a short date",
        "https://example.org/NHP-220630FLY-1",
    ):
        assert link_sample_uids(text) == text, text


def test_a_fenced_code_block_is_left_alone():
    text = "Found NHP-220630FLY-1.\n\n```\nNHP-220630FLY-2\n```\n\nAlso NHP-220630FLY-3."
    assert link_sample_uids(text) == (
        f"Found {_link('NHP-220630FLY-1')}.\n\n```\nNHP-220630FLY-2\n```\n\n"
        f"Also {_link('NHP-220630FLY-3')}."
    )


def test_the_debug_info_json_block_is_left_alone():
    debug = (
        "**Debug info**\n\n```json\n"
        '{\n  "rows": [{"uid": "NHP-220630FLY-1"}, "D.SEQ-240910LAU-4"]\n}\n```'
    )
    text = "One sample, NHP-220630FLY-1.\n\n" + debug
    assert link_sample_uids(text) == f"One sample, {_link('NHP-220630FLY-1')}.\n\n" + debug


def test_an_unclosed_fence_protects_the_rest_of_the_reply():
    text = "Before NHP-220630FLY-1.\n```json\n{\"uid\": \"NHP-220630FLY-2\"}"
    assert link_sample_uids(text) == (
        f"Before {_link('NHP-220630FLY-1')}.\n```json\n{{\"uid\": \"NHP-220630FLY-2\"}}"
    )


def test_an_existing_markdown_link_is_left_alone():
    for text in (
        "[NHP-220630FLY-1](/seek/sample/id=5/)",
        "[the monkey NHP-220630FLY-1](https://example.org/x)",
        "[`TIS-200901ENG-1`](/seek/sampletree/uid=TIS-200901ENG-1/)",
    ):
        assert link_sample_uids(text) == text, text


def test_running_twice_changes_nothing():
    text = OPERATOR_EXAMPLE + "\n* **`TIS-200901ENG-1`**\n| D.SEQ-240910LAU-4 | 3 |"
    once = link_sample_uids(text)
    assert once != text
    assert link_sample_uids(once) == once


@pytest.mark.parametrize("text", [
    "", "No samples matched.", "There are 1,084,754 samples across 12 projects.",
    "The date 2022-06-30 and code ABC-123 are not UIDs.",
])
def test_text_without_uids_is_unchanged(text):
    assert link_sample_uids(text) == text


def test_none_passes_through():
    assert link_sample_uids(None) is None


# ---------------------------------------------------------------------------
# Wired into the chatter.
# ---------------------------------------------------------------------------

class _StubConfig:
    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


def test_the_chatter_reply_links_uids_and_leaves_its_debug_block_alone(monkeypatch):
    def _fake(config, **kw):
        return "Examples: `NHP-220630FLY-1` and NHP-220630FLY-2."

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake)
    rows = {"ok": True, "data": {"total": 2, "rows": [{"uid": "NHP-220630FLY-1"}]}}
    out = chatter_mod.chatter_agent_answer(
        _StubConfig(), "which monkeys?",
        EntityAgentOutput().model_dump(),
        ParserPlan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/").model_dump(),
        {"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST", "requestBody": {}},
        rows, rows, None, log_dir="",
    )
    answer, debug = out.split("**Debug info**", 1)
    assert answer.strip() == (
        f"Examples: {_link('NHP-220630FLY-1', '`NHP-220630FLY-1`')} and {_link('NHP-220630FLY-2')}."
    )
    assert "/seek/sampletree/" not in debug
