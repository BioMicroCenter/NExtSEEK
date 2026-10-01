"""The tool loops keep an always-thinking model's reasoning and hand it back unchanged (run 2's model switch).

Claude Opus 5.5 always thinks. In a tool loop (the follow-up and pipeline agents) the reasoning of the step that
called a tool must go back, unchanged and with its signature, in the assistant turn the next step replays, and the
history must stay append-only: an edited earlier turn invalidates every later reasoning block (claude-api
reference, "Migrating to Claude Opus 5.5", breaking change 3). ``chat_with_tools`` used to drop those blocks.

After a move (a 503, a stall, or a model that failed earlier in the turn), the loop's later steps go to Sonnet 4.6,
which runs without thinking; its request must not carry Opus 5.5's reasoning blocks, so they are stripped from that
request only. No live model is called: the Converse endpoint is a fake that refuses, like Bedrock, a step 2 whose
tool call lost its reasoning, and a non-thinking request that carries some.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from chat_nextseek import call_scope
from chat_nextseek.llm_clients import BedrockClient, without_reasoning_blocks
from chat_nextseek.tool_loop import call_tools

OPUS55 = "us.anthropic.claude-opus-5-5"
SONNET = "us.anthropic.claude-sonnet-4-6"
SONNET55 = "global.anthropic.claude-sonnet-5-5"
TOOLS = [{"name": "run_new_query", "description": "Run a query.",
          "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}}},
         {"name": "answer", "description": "Answer.",
          "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}}]


def _refuse(message: str) -> ClientError:
    return ClientError({"Error": {"Code": "ValidationException", "Message": message}}, "Converse")


def _reasoning(sig: str) -> dict:
    return {"reasoningContent": {"reasoningText": {"text": "", "signature": sig}}}


class FakeConverse:
    """Bedrock's Converse, as far as these rules go. Scripted replies per model; every request is kept."""

    def __init__(self, script: dict[str, list]):
        self.script = {m: list(v) for m, v in script.items()}
        self.requests: list[dict] = []
        self.issued: dict[str, dict] = {}  # signature -> the reasoning block as it was sent out

    def __call__(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=lambda b: {"bytes": list(b)})))
        self._validate(kwargs)
        reply = self.script[kwargs["modelId"]].pop(0)
        if isinstance(reply, BaseException):
            raise reply
        for block in reply:
            sig = ((block.get("reasoningContent") or {}).get("reasoningText") or {}).get("signature")
            if sig:
                self.issued[sig] = block
        stop = "tool_use" if any("toolUse" in b for b in reply) else "end_turn"
        return {"stopReason": stop, "output": {"message": {"role": "assistant", "content": reply}},
                "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}}

    def _validate(self, kwargs):
        thinking = (kwargs.get("additionalModelRequestFields") or {}).get("thinking")
        blocks = [(i, b) for i, m in enumerate(kwargs["messages"]) for b in m["content"]]
        if thinking is None:
            if any("reasoningContent" in b for _, b in blocks):
                raise _refuse("reasoningContent sent to a request without thinking")
            return
        if kwargs["modelId"] == OPUS55:
            if "temperature" in kwargs["inferenceConfig"] or thinking != {"type": "adaptive"}:
                raise _refuse("temperature or thinking.type is not supported for this model")
            if set((kwargs.get("toolConfig") or {}).get("toolChoice") or {}) & {"any", "tool"}:
                raise _refuse('tool_choice: type "tool" and "any" are not supported for this model.')
        # The last assistant turn that called a tool must carry its reasoning first, exactly as it was issued.
        for message in reversed(kwargs["messages"]):
            if message["role"] == "assistant" and any("toolUse" in b for b in message["content"]):
                first = message["content"][0]
                sig = ((first.get("reasoningContent") or {}).get("reasoningText") or {}).get("signature")
                if sig is None or self.issued.get(sig) != first:
                    raise _refuse("Expected a thinking block before tool_use, unchanged")
                break


def _bedrock(fake: FakeConverse) -> BedrockClient:
    client = BedrockClient.__new__(BedrockClient)
    client.client = MagicMock()
    client.client.converse = fake
    client.max_output_tokens = 4096
    return client


class _Cfg:
    LOG_DIR = None
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None, "low": 4000}
    LLM_MODEL = OPUS55

    def __init__(self, client):
        self.LLM_CLIENT = client
        self.LLM_CLIENTS = {"anth": client}
        self.AGENT_MODEL_CATALOG = {
            "_fallback": {"followup": {"provider": "anth", "model": SONNET, "thinking_level": None}},
        }


def _loop(config, client, steps, *, thinking_budget=4000):
    """Drive ``steps`` steps of a follow-up style loop through ``call_tools``: append, run, append."""
    messages = [{"role": "user", "content": "which of those have RNA?"}]
    for _ in range(steps):
        resp = call_tools(config, messages=messages, tools=TOOLS, system="SYSTEM", model_name=OPUS55,
                          client=client, agent_label="followup", thinking_budget=thinking_budget)
        messages.append({"role": "assistant", "content": resp["content"]})
        uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
        if not uses:
            break
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": u["id"], "content": "{\"ok\": true, \"count\": 2}"} for u in uses]})
    return messages


def _step(sig, tool_id, name="run_new_query"):
    return [_reasoning(sig), {"toolUse": {"toolUseId": tool_id, "name": name, "input": {"question": "q"}}}]


# ---------------------------------------------------------------------------- kept, and replayed unchanged


def test_the_reasoning_block_is_kept_first_and_unchanged():
    fake = FakeConverse({OPUS55: [_step("sig-1", "t1")]})
    result = _bedrock(fake).chat_with_tools(messages=[{"role": "user", "content": "Q"}], tools=TOOLS,
                                            system="S", model=OPUS55, thinking_budget=4000)
    assert result["content"][0] == _reasoning("sig-1")
    assert result["content"][1]["type"] == "tool_use"
    assert result["metadata"]["reasoning_blocks"] == 1


def test_step_two_of_an_opus_5_5_loop_is_accepted():
    fake = FakeConverse({OPUS55: [_step("sig-1", "t1"), _step("sig-2", "t2"),
                                  [_reasoning("sig-3"), {"text": "Two have RNA."}]]})
    client = _bedrock(fake)
    with call_scope.scope():
        _loop(_Cfg(client), client, 3)

    assert len(fake.requests) == 3
    step2 = fake.requests[1]["messages"]
    assert step2[1] == {"role": "assistant", "content": [
        _reasoning("sig-1"),
        {"toolUse": {"toolUseId": "t1", "name": "run_new_query", "input": {"question": "q"}}}]}
    # append-only: step 3 re-sends step 2's messages as they were, then its own
    assert fake.requests[2]["messages"][:len(step2)] == step2
    assert all(r["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"},
                                                     "output_config": {"effort": "low"}} for r in fake.requests)


def test_a_redacted_block_survives_json_and_goes_back_as_the_same_bytes():
    redacted = {"reasoningContent": {"redactedContent": b"\x00\x01opaque"}}
    fake = FakeConverse({OPUS55: [[redacted, {"toolUse": {"toolUseId": "t1", "name": "answer", "input": {}}}],
                                  [{"text": "done"}]]})
    fake._validate = lambda kwargs: None
    client = _bedrock(fake)
    first = client.chat_with_tools(messages=[{"role": "user", "content": "Q"}], tools=TOOLS, system="S",
                                   model=OPUS55)
    kept = json.loads(json.dumps(first["content"]))  # a pipeline build stores its history in the JSON session
    client.chat_with_tools(messages=[{"role": "user", "content": "Q"}, {"role": "assistant", "content": kept},
                                     {"role": "user", "content": [
                                         {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}],
                           tools=TOOLS, system="S", model=OPUS55)
    replayed = fake.requests[1]["messages"][1]["content"][0]
    assert replayed == {"reasoningContent": {"redactedContent": {"bytes": list(b"\x00\x01opaque")}}}


# ---------------------------------------------------------------------------- stripped for a model not thinking


def test_a_request_without_thinking_is_sent_the_history_without_reasoning():
    fake = FakeConverse({SONNET: [[{"text": "fine"}]]})
    history = [{"role": "user", "content": "Q"},
               {"role": "assistant", "content": [_reasoning("sig-1"),
                                                 {"type": "tool_use", "id": "t1", "name": "answer", "input": {}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]
    before = json.dumps(history)
    _bedrock(fake).chat_with_tools(messages=history, tools=TOOLS, system="S", model=SONNET)

    sent = fake.requests[0]["messages"][1]["content"]
    assert sent == [{"toolUse": {"toolUseId": "t1", "name": "answer", "input": {}}}]
    assert json.dumps(history) == before  # the loop's own history keeps its blocks


def test_after_a_move_every_later_step_goes_to_sonnet_without_opus_reasoning():
    down = ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "503"}}, "Converse")
    fake = FakeConverse({OPUS55: [_step("sig-1", "t1"), down],
                         SONNET: [[{"toolUse": {"toolUseId": "t2", "name": "run_new_query", "input": {}}}],
                                  [{"text": "Two have RNA."}]]})
    client = _bedrock(fake)
    with call_scope.scope():
        _loop(_Cfg(client), client, 3)

    assert [r["modelId"] for r in fake.requests] == [OPUS55, OPUS55, SONNET, SONNET]
    moved, remembered = fake.requests[2], fake.requests[3]
    for request in (moved, remembered):
        assert "additionalModelRequestFields" not in request
        assert not any("reasoningContent" in b for m in request["messages"] for b in m["content"])
    # the tool call Opus made is still in the history Sonnet gets, only its reasoning is gone
    assert moved["messages"][1]["content"] == [
        {"toolUse": {"toolUseId": "t1", "name": "run_new_query", "input": {"question": "q"}}}]


def test_a_step_moved_to_sonnet_5_5_thinks_and_keeps_opus_reasoning_unchanged():
    """Sonnet 5.5 always thinks, so the strip does not run: the step's request carries adaptive thinking and the
    Opus 5.5 block as it was issued. Whether Bedrock's Converse drops a block Sonnet 5.5 cannot read (Anthropic's
    documentation says the API does) is proven on dev, not here."""
    fake = FakeConverse({OPUS55: [_step("sig-1", "t1")],
                         SONNET55: [[_reasoning("sig-2"), {"text": "Two have RNA."}]]})
    client = _bedrock(fake)
    first = client.chat_with_tools(messages=[{"role": "user", "content": "Q"}], tools=TOOLS, system="S",
                                   model=OPUS55, thinking_budget=4000)
    history = [{"role": "user", "content": "Q"}, {"role": "assistant", "content": first["content"]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]
    client.chat_with_tools(messages=history, tools=TOOLS, system="S", model=SONNET55)

    moved = fake.requests[1]
    assert moved["modelId"] == SONNET55
    assert moved["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"},
                                                     "output_config": {"effort": "low"}}
    assert moved["messages"][1]["content"][0] == _reasoning("sig-1")


def test_without_reasoning_blocks_leaves_other_turns_and_never_an_empty_one():
    messages = [{"role": "user", "content": "Q"},
                {"role": "assistant", "content": [_reasoning("s")]},
                {"role": "assistant", "content": [{"type": "text", "text": "a"}]}]
    out = without_reasoning_blocks(messages)
    assert out[0] is messages[0] and out[2] is messages[2]
    assert out[1] == {"role": "assistant", "content": [{"text": "(no content)"}]}
    assert messages[1]["content"] == [_reasoning("s")]


# ---------------------------------------------------------------------------- the follow-up's last pass


def test_the_follow_ups_answer_only_pass_is_sent_without_reasoning():
    """The last pass offers only ``answer``: its tools differ from the ones the earlier steps' reasoning was
    written under, so replaying those blocks would be an edited history. They are left out of that pass."""
    from chat_nextseek.agents.followup import FOLLOWUP_AGENT_KEY, MAX_ITER, run_followup

    replies = [_step(f"sig-{i}", f"t{i}") for i in range(MAX_ITER)]
    replies.append([{"toolUse": {"toolUseId": "tz", "name": "answer", "input": {"text": "Two."}}}])
    fake = FakeConverse({OPUS55: replies})
    fake._validate = lambda kwargs: None
    client = _bedrock(fake)

    class Cfg(_Cfg):
        def get_agent_model(self, label):
            assert label == FOLLOWUP_AGENT_KEY
            return client, OPUS55, 4000

        def _load_prompt(self, name):
            return "SYSTEM PROMPT"

    bundle = {"id": 7, "user_query": "mouse samples", "mode": "graph_query",
              "graph_result": {"ok": True, "count": 3, "total": 3, "data": [{"uid": "MUS-1"}]}}
    with call_scope.scope():
        out = run_followup(Cfg(client), user_text="which have RNA?", bundle=bundle,
                           run_query=lambda **kw: {"ok": True, "count": 2})

    assert out["reply"] == "Two."
    working, last = fake.requests[MAX_ITER - 1], fake.requests[MAX_ITER]
    assert any("reasoningContent" in b for m in working["messages"] for b in m["content"])
    assert not any("reasoningContent" in b for m in last["messages"] for b in m["content"])
    assert [t["toolSpec"]["name"] for t in last["toolConfig"]["tools"] if "toolSpec" in t] == ["answer"]
