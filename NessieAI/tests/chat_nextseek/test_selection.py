"""selection.decide: map a model reply onto one of four verdicts, and never raise."""
import pytest

from chat_nextseek.pipeline.selection import (
    SELECTION_SECTIONS,
    Verdict,
    decide,
    out_of_scope,
)

ATLAS_KEYS = {"rnaseq", "rnasplice", "scrnaseq", "differentialabundance"}
# differentialabundance is in the atlas but not in the build catalog, as in production.
LAUNCHABLE_KEYS = {"rnaseq", "rnasplice", "scrnaseq"}


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
                     question="Q", atlas_keys=ATLAS_KEYS, launchable_keys=LAUNCHABLE_KEYS)
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


def test_empty_list_on_a_data_basis_is_a_refusal():
    verdict, _ = _decide(
        '{"pipelines": [], "reason": "amplicon data cannot answer this", "basis": "data"}')
    assert verdict.kind == "refused"
    assert "amplicon" in verdict.reason


def test_empty_list_on_an_atlas_basis_steps_aside():
    # Measured live 2026-09-30: "Call variants" on duplex DNA came back refused because
    # no ATLAS pipeline calls DNA variants, though sarek is in the build catalog. The
    # atlas covers RNA only, so "nothing in the atlas does this" is not a refusal of
    # the question: it hands the choice back to the agent's full catalog.
    verdict, _ = _decide(
        '{"pipelines": [], "reason": "no atlas pipeline calls DNA variants", "basis": "atlas"}')
    assert verdict.kind == "out_of_scope"
    assert verdict.pipelines == []
    assert "DNA variants" in verdict.reason


@pytest.mark.parametrize("basis", ['', ', "basis": ""', ', "basis": "vibes"', ', "basis": null'])
def test_empty_list_without_a_known_basis_steps_aside(basis):
    # A refusal blocks the build, so it must say it is about the data. Anything
    # less degrades to out_of_scope rather than blocking a build that works today.
    verdict, _ = _decide('{"pipelines": [], "reason": "cannot answer this"%s}' % basis)
    assert verdict.kind == "out_of_scope"
    assert "cannot answer this" in verdict.reason


def test_empty_list_without_a_reason_is_out_of_scope():
    # A refusal with no reason is not a decision anyone can act on or relay.
    verdict, _ = _decide('{"pipelines": [], "reason": "", "basis": "data"}')
    assert verdict.kind == "out_of_scope"


def test_an_atlas_key_that_cannot_be_launched_is_dropped_not_fatal():
    # Measured live 2026-09-30: the model answered rnaseq + differentialabundance, an
    # answer the eval set accepts, and the whole verdict was thrown away because
    # differentialabundance is atlas-only. The launchable half must survive.
    verdict, _ = _decide(
        '{"pipelines": ["rnaseq", "differentialabundance"], "reason": "counts then DE"}')
    assert verdict.kind == "chosen"
    assert verdict.pipelines == ["rnaseq"]
    assert verdict.dropped == ["differentialabundance"]


def test_a_fork_keeps_its_launchable_members():
    verdict, _ = _decide(
        '{"pipelines": ["rnaseq", "rnasplice", "differentialabundance"], "reason": "either"}')
    assert verdict.kind == "fork"
    assert verdict.pipelines == ["rnaseq", "rnasplice"]
    assert verdict.dropped == ["differentialabundance"]


def test_only_unlaunchable_atlas_keys_steps_aside_and_says_why():
    verdict, _ = _decide('{"pipelines": ["differentialabundance"], "reason": "has counts"}')
    assert verdict.kind == "out_of_scope"
    assert "differentialabundance" in verdict.reason
    assert "not in the atlas" not in verdict.reason
    assert "cannot be launched" in verdict.reason


def test_launchable_keys_default_to_the_atlas():
    client = _StubClient(content='{"pipelines": ["differentialabundance"], "reason": "x"}')
    verdict = decide(client=client, model="m", budget=None, payload="P",
                     question="Q", atlas_keys=ATLAS_KEYS)
    assert verdict.kind == "chosen"
    assert verdict.dropped == []


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
