"""An op that fails inside the tool with no code of its own exits AGENT_FAILED, reason internal, with the approved
sentence (P03-T4+T12 item 6) and no exception text."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _nextseek_runner as runner  # noqa: E402


def test_an_unexpected_exception_exits_agent_failed_internal_without_its_text(monkeypatch, capsys):
    def boom(args):
        raise RuntimeError("secret-ish detail /data/scratch/x")

    monkeypatch.setitem(runner._DISPATCH, "graph-schema", boom)
    monkeypatch.setenv("NEXTSEEK_TURN_PASS", "pass-for-tests")
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)
    monkeypatch.setattr(sys, "argv", ["nextseek-graph-schema", "--agent", "graph-schema"])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 4
    err = capsys.readouterr().err
    assert json.loads(err.strip().splitlines()[-1]) == {
        "error": {"code": "AGENT_FAILED", "message": "The op failed inside NExtSEEK.", "reason": "internal"}}
    assert "RuntimeError" not in err and "secret-ish" not in err
