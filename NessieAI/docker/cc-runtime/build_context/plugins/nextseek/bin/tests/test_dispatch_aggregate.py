"""nextseek-aggregate's runner dispatch: what it sends the sidecar, and its dry run.

The op takes the whole question and, optionally, its parts as a JSON array sent as text. The runner forwards both
unchanged (the server validates the parts) and sends no ``parts`` key when none were given.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _nextseek_runner as runner  # noqa: E402
import _sidecar_client as sc  # noqa: E402


class _Args:
    def __init__(self, query, parts=None):
        self.query = query
        self.parts = parts


class _Socket:
    def __init__(self):
        self.sent = None

    def send(self, payload):
        self.sent = json.loads(payload)

    def recv(self, timeout=None):
        return json.dumps({"status": "ok", "result": {"question": self.sent["args"]["query"], "parts": []}})

    def close(self):
        pass


@pytest.fixture
def socket(monkeypatch):
    sock = _Socket()
    monkeypatch.setattr(sc, "_connect", lambda url: sock)
    monkeypatch.setenv("API_USER", "u")
    monkeypatch.setenv("API_PASS", "p")
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)
    monkeypatch.delenv("NEXTSEEK_CC_TURN_DEADLINE_EPOCH", raising=False)
    return sock


def test_the_question_and_its_parts_go_to_the_sidecar(socket):
    parts = json.dumps(["How many samples have no parent?", "How many have no children?"])

    out = runner._dispatch_aggregate(_Args("Roots and leaves", parts))

    assert socket.sent["op"] == "aggregate"
    assert socket.sent["args"] == {"query": "Roots and leaves", "parts": parts}
    assert out["question"] == "Roots and leaves"


def test_no_parts_sends_no_parts_key(socket):
    runner._dispatch_aggregate(_Args("How many TIS samples?", ""))

    assert socket.sent["args"] == {"query": "How many TIS samples?"}


def test_a_missing_query_is_a_validation_error(socket):
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_aggregate(_Args(""))
    assert exc.value.code == 3
    assert socket.sent is None


def test_dry_run_answers_without_the_sidecar(monkeypatch, socket):
    monkeypatch.setenv("NEXTSEEK_DRY_RUN", "1")

    out = runner._dispatch_aggregate(_Args("How many TIS samples?"))

    assert socket.sent is None
    assert out["question"] == "How many TIS samples?"
    assert out["complete"] is True and out["parts"] == []


def test_aggregate_is_a_known_sidecar_op():
    assert "aggregate" in runner._DISPATCH
    assert "aggregate" in sc.SIDECAR_OPS
