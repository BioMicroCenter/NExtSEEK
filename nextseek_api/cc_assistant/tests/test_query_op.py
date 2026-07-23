"""§4.D + PD-5: nextseek-query runs the deterministic NS path IN THE LIVE CHAT
SESSION (user ruling PD-5) and materializes the result like recall."""
import json
import sys
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parents[3] / "docker" / "cc-runtime" / \
    "build_context" / "plugins" / "nextseek" / "bin"
sys.path.insert(0, str(_BIN))

import _nextseek_runner as runner


class FakeQueryClient:
    def __init__(self, terminal, bundle=None):
        self._terminal, self._bundle = terminal, bundle
        self.calls = []

    def run_query(self, query, *, mode, session_id=None, force_new=False,
                  on_event=None):
        self.calls.append(("run_query", query, mode, session_id, force_new))
        return self._terminal, []

    def download_bundle(self, session_id, bundle_id):
        self.calls.append(("download_bundle", session_id, bundle_id))
        return self._bundle


def _args(query, planner=False):
    import argparse
    return argparse.Namespace(query=query, planner=planner)


def test_query_runs_in_live_session_and_materializes(monkeypatch, tmp_path):
    rows = [{"uid": "MUS-1"}]
    client = FakeQueryClient(
        terminal={"reply": "found 1", "bundle_id": 4, "session_id": "live-1",
                  "debug": {}},
        bundle={"id": 4, "api_result_full": {"ok": True,
                "data": {"total": 1, "rows": rows}}})
    monkeypatch.setattr(runner, "_make_client", lambda: client)
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", "live-1")
    monkeypatch.setenv("NEXTSEEK_SCRATCH_DIR", str(tmp_path))
    manifest = runner._dispatch_query(_args("mice with NDMA"))
    # PD-5: the op passes the LIVE session id — never None, never force_new.
    assert ("run_query", "mice with NDMA", "standard", "live-1", False) in client.calls
    dest = tmp_path / "query" / "result-4.json"
    assert json.loads(dest.read_bytes()) == rows
    assert manifest == {"turn_id": None, "bundle_id": 4, "total": 1,
                        "row_count": 1, "columns": ["uid"],
                        "path": str(dest), "reply": "found 1"}


def test_query_without_bundle_returns_reply_only_manifest(monkeypatch, tmp_path):
    client = FakeQueryClient(
        terminal={"reply": "chatter-only answer", "bundle_id": None,
                  "session_id": "live-1", "debug": {}})
    monkeypatch.setattr(runner, "_make_client", lambda: client)
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", "live-1")
    monkeypatch.setenv("NEXTSEEK_SCRATCH_DIR", str(tmp_path))
    manifest = runner._dispatch_query(_args("hello"))
    assert manifest["reply"] == "chatter-only answer"
    assert manifest["bundle_id"] is None and manifest["path"] is None
    assert not any(c[0] == "download_bundle" for c in client.calls)
    assert list(tmp_path.rglob("*")) == []


def test_query_missing_session_env_is_config_error(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_make_client", lambda: FakeQueryClient({}))
    monkeypatch.delenv("NEXTSEEK_CHAT_SESSION_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_query(_args("q"))
    assert exc.value.code == 2


def test_query_planner_mode(monkeypatch, tmp_path):
    client = FakeQueryClient(
        terminal={"reply": "plan", "bundle_id": None, "session_id": "live-1",
                  "debug": {}})
    monkeypatch.setattr(runner, "_make_client", lambda: client)
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", "live-1")
    monkeypatch.setenv("NEXTSEEK_SCRATCH_DIR", str(tmp_path))
    runner._dispatch_query(_args("q", planner=True))
    assert client.calls[0][2] == "plan"


def test_query_shim_and_port_pin_flipped():
    shim = (_BIN / "nextseek-query").read_text()
    assert '--agent query' in shim and '--planner' in shim
    import os
    assert os.access(_BIN / "nextseek-query", os.X_OK)
