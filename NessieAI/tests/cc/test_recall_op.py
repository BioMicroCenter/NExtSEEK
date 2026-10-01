"""§4.C: recall op — explicit-turn, no latest fallback, no residue on error."""
import json
import sys

import pytest

from NessieAI import paths

_BIN = paths.CC_PLUGIN_BIN
sys.path.insert(0, str(_BIN))

import _nextseek_runner as runner


class FakeClient:
    def __init__(self, turns, bundles):
        self._turns, self._bundles = turns, bundles
        self.calls = []

    def session_detail(self, session_id, *, include_turns=False):
        self.calls.append(("session_detail", session_id, include_turns))
        return {"session_id": session_id, "turns": self._turns}

    def download_bundle(self, session_id, bundle_id):
        self.calls.append(("download_bundle", session_id, bundle_id))
        return self._bundles[bundle_id]


def _args(turn):
    import argparse
    return argparse.Namespace(turn=turn)


def _install(monkeypatch, tmp_path, client, session="sess-1"):
    monkeypatch.setattr(runner, "_make_client", lambda: client)
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", session)
    monkeypatch.setenv("NEXTSEEK_SCRATCH_DIR", str(tmp_path))


def test_recall_resolves_turn_to_bundle_and_materializes(monkeypatch, tmp_path):
    rows = [{"uid": "D.SEQ-1", "sex": "F"}, {"uid": "D.SEQ-2", "sex": "M"}]
    client = FakeClient(
        turns=[{"turn_id": 3, "bundle_id": 2, "user_query": "q", "reply": "r",
                "mode": "search"}],
        bundles={2: {"id": 2, "api_result_full": {"ok": True,
                     "data": {"total": 139, "rows": rows}}}})
    _install(monkeypatch, tmp_path, client)
    manifest = runner._dispatch_recall(_args(3))
    dest = tmp_path / "recall" / "turn-3.json"
    assert json.loads(dest.read_bytes()) == rows
    assert manifest == {"turn_id": 3, "bundle_id": 2, "total": 139,
                        "row_count": 2, "columns": ["uid", "sex"],
                        "path": str(dest)}
    assert ("download_bundle", "sess-1", 2) in client.calls


def test_recall_unknown_turn_errors_no_fallback_no_residue(monkeypatch, tmp_path, capsys):
    client = FakeClient(turns=[{"turn_id": 1, "bundle_id": 1}], bundles={})
    _install(monkeypatch, tmp_path, client)
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_recall(_args(9))
    assert exc.value.code == 5
    assert "turn 9 not found" in capsys.readouterr().err
    assert list(tmp_path.rglob("*")) == []          # NO file written, no dirs
    assert not any(c[0] == "download_bundle" for c in client.calls)  # no latest fallback


def test_recall_missing_session_env_is_config_error(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, FakeClient([], {}))
    monkeypatch.delenv("NEXTSEEK_CHAT_SESSION_ID")
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_recall(_args(1))
    assert exc.value.code == 2


def test_recall_turn_without_bundle_errors(monkeypatch, tmp_path):
    client = FakeClient(turns=[{"turn_id": 2, "bundle_id": None, "mode": "cc"}],
                        bundles={})
    _install(monkeypatch, tmp_path, client)
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_recall(_args(2))
    assert exc.value.code == 5
    assert list(tmp_path.rglob("*")) == []


def test_recall_shim_arg_forms():
    """The shim accepts --turn N and --turn=N and execs the runner with
    --agent recall (text contract on the shim, mirroring the port tests)."""
    shim = (_BIN / "nextseek-recall").read_text()
    assert '--turn) TURN="$2"; shift 2 ;;' in shim
    assert '--turn=*) TURN="${1#--turn=}"; shift ;;' in shim
    assert '--agent recall --turn "$TURN"' in shim
    import os
    assert os.access(_BIN / "nextseek-recall", os.X_OK)


def test_recall_returns_a_graph_turns_rows(monkeypatch, tmp_path):
    """CC-RERUN-FINDINGS fix 6: a graph turn's rows are its graph_result.data, not an API result."""
    rows = [{"id": i, "uuid": f"TCGA-{i:04d}"} for i in range(585)]
    client = FakeClient(
        turns=[{"turn_id": 2, "bundle_id": 5, "user_query": "LUAD samples", "mode": "graph_query"}],
        bundles={5: {"id": 5, "mode": "graph_query",
                     "graph_result": {"ok": True, "count": 585, "total": 585, "truncated": False,
                                      "data": rows}}})
    _install(monkeypatch, tmp_path, client)
    manifest = runner._dispatch_recall(_args(2))
    dest = tmp_path / "recall" / "turn-2.json"
    assert json.loads(dest.read_bytes()) == rows
    assert manifest == {"turn_id": 2, "bundle_id": 5, "total": 585, "row_count": 585,
                        "columns": ["id", "uuid"], "path": str(dest)}


def test_recall_keeps_a_plan_bundles_rest_rows(monkeypatch, tmp_path):
    rows = [{"uid": "MUS-1"}, {"uid": "MUS-2"}]
    client = FakeClient(
        turns=[{"turn_id": 4, "bundle_id": 9, "mode": "plan"}],
        bundles={9: {"id": 9, "mode": "plan",
                     "graph_result": {"ok": False, "data": [], "count": 0, "error": "boom"},
                     "api_result_full": {"ok": True, "data": {"total": 2, "rows": rows}}}})
    _install(monkeypatch, tmp_path, client)
    manifest = runner._dispatch_recall(_args(4))
    assert manifest["row_count"] == 2 and manifest["total"] == 2


def test_recall_never_writes_the_hidden_parent_lists(monkeypatch, tmp_path):
    rows = [{"uuid": "A-1", "parent_titles": ["x"], "s": {"uuid": "A-1", "parent_title_hashes": ["h"]}}]
    client = FakeClient(turns=[{"turn_id": 1, "bundle_id": 1}],
                        bundles={1: {"id": 1, "graph_result": {"ok": True, "data": rows}}})
    _install(monkeypatch, tmp_path, client)
    runner._dispatch_recall(_args(1))
    assert json.loads((tmp_path / "recall" / "turn-1.json").read_bytes()) == [{"uuid": "A-1", "s": {"uuid": "A-1"}}]


def test_the_runner_hides_what_graph_scope_hides():
    """The container has no chat_nextseek, so the runner carries its own copy of the set."""
    from chat_nextseek.graph_scope import HIDDEN_SAMPLE_PROPERTIES

    assert set(HIDDEN_SAMPLE_PROPERTIES) <= set(runner._HIDDEN_SAMPLE_PROPERTIES)
