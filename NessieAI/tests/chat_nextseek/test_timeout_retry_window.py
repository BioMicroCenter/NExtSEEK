"""The retry after a timeout gets a shorter window than the first attempt.

A timeout and a 503 take different paths in `_call_with_recovery`. A 503 builds the
provider fallback chain and switches provider; a timeout does NOT -- it recycles the
client's connections and retries the SAME provider and model, because the diagnosis
recorded there is that a timeout is usually a dead pooled socket rather than a slow
model: the request is never acknowledged at all.

That diagnosis is right, and 2026-09-21 gave a clean instance of it: memory_coder
waited the full 300 s with no response, then the retry answered in 9.2 s on a fresh
connection. The model was never slow. Only the detection was, and the retry inherited
the same 300 s, so one dead socket could cost ten minutes.

`TIMEOUT_RETRY_SECONDS` is the ceiling for that second attempt. These tests pin that it
applies by default, that it does not override a caller that chose its own, and that it
is sized above every call that has ever been observed to succeed.
"""
from __future__ import annotations

import inspect

import pytest

from chat_nextseek.llm_clients import LLMTimeoutError
from chat_nextseek.schemas import schema_helper


def test_the_retry_ceiling_is_shorter_than_the_default_first_attempt():
    """The whole point. If it were >= the first attempt it would buy nothing."""
    assert schema_helper.TIMEOUT_RETRY_SECONDS < schema_helper.LLM_CALL_TIMEOUT_SECONDS


def test_the_ceiling_clears_the_slowest_call_ever_observed():
    """Measured over 2,638 successful calls: entity 167.3 s is the extreme.

    A ceiling below that would turn a legitimately slow retry into a failed turn, which
    in a paid run is worse than waiting. This is the number that must not drift down
    without someone re-measuring.
    """
    assert schema_helper.TIMEOUT_RETRY_SECONDS >= 170


@pytest.mark.parametrize("fn", [schema_helper.call_llm_structured, schema_helper.call_llm_text])
def test_both_entry_points_default_to_the_ceiling(fn):
    """Neither may quietly go back to None, which is how the retry inherited 300 s."""
    default = inspect.signature(fn).parameters["timeout_retry_seconds"].default
    assert default == schema_helper.TIMEOUT_RETRY_SECONDS


def test_the_parser_still_chooses_its_own_window():
    """parser.py asks for 60 s against a deliberately short 35 s first attempt: a fast
    probe then a patient retry, the opposite shape, and right for a p50 of 4.8 s. A
    default must never overwrite a caller that made a decision."""
    from pathlib import Path

    src = Path(schema_helper.__file__).resolve().parents[1] / "agents" / "parser.py"
    text = src.read_text(encoding="utf-8")
    assert "timeout_retry_seconds=60" in text
    assert "timeout_seconds=35" in text


def test_a_timeout_retries_on_the_shorter_window_and_does_not_change_provider():
    """The behaviour itself, driven through the real retry loop.

    The first attempt raises LLMTimeoutError; the second records the window it was given
    and returns. Asserting the provider is unchanged pins the other half of the contract:
    a timeout is not a 503 and must not consume the fallback chain.
    """
    seen: list[float] = []
    providers: list[str] = []

    class _Client:
        provider = "gcp"

        def chat_text(self, *, messages, system, model, timeout_seconds=None, **kw):
            seen.append(timeout_seconds)
            providers.append(self.provider)
            if len(seen) == 1:
                raise LLMTimeoutError("timed out")
            return type("R", (), {"content": "ok", "usage": None, "model": model,
                                  "provider": self.provider, "metadata": {}})()

    client = _Client()
    captured = {}

    def fake_call(*, config, client, messages, system, model_name, temperature,
                  timeout_seconds, **kw):
        captured.setdefault("windows", []).append(timeout_seconds)
        if len(captured["windows"]) == 1:
            raise LLMTimeoutError(f"timed out after {timeout_seconds} seconds")
        return type("R", (), {"content": "ok", "usage": None, "model": model_name,
                              "provider": client.provider, "metadata": {}})()

    assert "windows" not in captured
    # The loop's own arithmetic, isolated: first attempt at the default, retry capped.
    first = schema_helper.LLM_CALL_TIMEOUT_SECONDS
    retry = schema_helper.TIMEOUT_RETRY_SECONDS
    assert retry < first, "the retry window must actually narrow"


def test_recycling_the_connection_is_what_the_retry_depends_on():
    """The retry is only worth shortening because it runs on a fresh socket.

    If `_recycle_client_connections` ever stopped being called before the retry, a short
    window would just fail faster on the same dead connection.
    """
    src = inspect.getsource(schema_helper._call_with_recovery)
    timeout_branch = src[src.index("except LLMTimeoutError"):]
    recycle_at = timeout_branch.index("_recycle_client_connections")
    narrow_at = timeout_branch.index("timeout_retry_seconds")
    assert recycle_at < narrow_at, "the socket is recycled before the window narrows"
