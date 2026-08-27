"""selection.decide: map a model reply onto one of four verdicts, and never raise."""
import pytest

from chat_nextseek.pipeline.selection import (
    SELECTION_SECTIONS,
    Verdict,
    decide,
    out_of_scope,
)

ATLAS_KEYS = {"rnaseq", "rnasplice", "scrnaseq"}


class _StubClient:
    """Returns a canned body, or raises, in place of a real model call."""

    def __init__(self, content=None, raises=None):
        self._content = content
        self._raises = raises
        self.calls = []

    def chat(self, *, messages, model, temperature=0, thinking_budget=None):
        self.calls.append({"messages": messages, "model": model,
                           "temperature": temperature, "thinking_budget": thinking_budget})
        if self._raises is not None:
            raise self._raises
        return type("R", (), {"content": self._content})()


def _decide(content=None, raises=None):
    client = _StubClient(content=content, raises=raises)
    verdict = decide(client=client, model="m", budget=None, payload="P",
                     question="Q", atlas_keys=ATLAS_KEYS)
    return verdict, client


def test_sections_exclude_docs():
    assert SELECTION_SECTIONS == ("atlas", "digest", "schemas")


def test_single_pipeline_is_chosen():
    verdict, _ = _decide('{"pipelines": ["rnaseq"], "reason": "bulk polyA reads"}')
    assert verdict.kind == "chosen"
    assert verdict.pipelines == ["rnaseq"]
    assert verdict.reason == "bulk polyA reads"


def test_two_pipelines_is_a_fork():
    verdict, _ = _decide('{"pipelines": ["rnaseq", "rnasplice"], "reason": "either reading"}')
    assert verdict.kind == "fork"
    assert verdict.pipelines == ["rnaseq", "rnasplice"]


def test_empty_list_with_a_reason_is_a_refusal():
    verdict, _ = _decide('{"pipelines": [], "reason": "amplicon data cannot answer this"}')
    assert verdict.kind == "refused"
    assert "amplicon" in verdict.reason


def test_empty_list_without_a_reason_is_out_of_scope():
    # A refusal with no reason is not a decision anyone can act on or relay.
    verdict, _ = _decide('{"pipelines": [], "reason": ""}')
    assert verdict.kind == "out_of_scope"


def test_key_outside_the_atlas_is_out_of_scope():
    verdict, _ = _decide('{"pipelines": ["sarek"], "reason": "variants"}')
    assert verdict.kind == "out_of_scope"
    assert "sarek" in verdict.reason


def test_partially_invented_fork_is_out_of_scope():
    verdict, _ = _decide('{"pipelines": ["rnaseq", "sarek"], "reason": "either"}')
    assert verdict.kind == "out_of_scope"


def test_more_than_three_pipelines_is_out_of_scope():
    verdict, _ = _decide(
        '{"pipelines": ["rnaseq", "rnasplice", "scrnaseq", "rnaseq"], "reason": "all"}')
    assert verdict.kind == "out_of_scope"


def test_unparseable_json_is_out_of_scope_and_keeps_the_raw():
    verdict, _ = _decide("I think you want rnaseq.")
    assert verdict.kind == "out_of_scope"
    assert verdict.raw == "I think you want rnaseq."


def test_pipelines_not_a_list_of_strings_is_out_of_scope():
    verdict, _ = _decide('{"pipelines": [1, 2], "reason": "x"}')
    assert verdict.kind == "out_of_scope"


def test_json_inside_a_markdown_fence_still_parses():
    verdict, _ = _decide('```json\n{"pipelines": ["rnaseq"], "reason": "ok"}\n```')
    assert verdict.kind == "chosen"


def test_a_raising_client_is_out_of_scope_not_an_exception():
    verdict, _ = _decide(raises=RuntimeError("bedrock throttled"))
    assert verdict.kind == "out_of_scope"
    assert "bedrock throttled" in verdict.reason


def test_empty_content_is_out_of_scope():
    verdict, _ = _decide(None)
    assert verdict.kind == "out_of_scope"


def test_the_call_is_deterministic_and_carries_both_messages():
    _, client = _decide('{"pipelines": ["rnaseq"], "reason": "ok"}')
    call = client.calls[0]
    assert call["temperature"] == 0
    assert [m["role"] for m in call["messages"]] == ["system", "user"]
    assert "P" in call["messages"][1]["content"]
    assert "Q" in call["messages"][1]["content"]


def test_out_of_scope_helper_shape():
    verdict = out_of_scope("no evidence")
    assert isinstance(verdict, Verdict)
    assert verdict.kind == "out_of_scope"
    assert verdict.pipelines == []
    assert verdict.reason == "no evidence"
