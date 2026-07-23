"""R-2: client↔mirror seam for session_detail / recall (plan-010 post-review).

Hermetic FakeClient tests and Django APIClient Lane C tests both bypass the
in-container mirror. Production recall goes:

  _dispatch_recall → AssistantClient.session_detail → SessionDetailResponse(**data)

If the server Turn gains fields the container Turn (extra=forbid) lacks,
session_detail raises and recall never resolves a turn. These tests drive that
real client path with an httpx MockTransport returning a server-shaped payload.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

_BIN = Path(__file__).resolve().parents[3] / "docker" / "cc-runtime" / \
    "build_context" / "plugins" / "nextseek" / "bin"


@pytest.fixture(scope="module")
def bin_mods():
    sys.path.insert(0, str(_BIN))
    try:
        models = importlib.import_module("_assistant_models")
        client_mod = importlib.import_module("_assistant_client")
        runner = importlib.import_module("_nextseek_runner")
        importlib.reload(models)
        importlib.reload(client_mod)
        importlib.reload(runner)
        yield models, client_mod, runner
    finally:
        if str(_BIN) in sys.path:
            sys.path.remove(str(_BIN))


def _server_shaped_session_detail(*, turn_id=5, bundle_id=2, with_cc_traces=True):
    """Payload shaped like Django SessionDetailResponse.model_dump(mode='json')."""
    turn = {
        "bundle_id": bundle_id,
        "turn_id": turn_id,
        "user_query": "Find me mice treated with NDMA",
        "reply": "42 mice",
        "mode": "search",
        "ts": "2026-07-22T12:00:00+00:00",
        "artifacts": None,
        "cc_traces": [{"total_cost_usd": 0.01}] if with_cc_traces else None,
    }
    return {
        "session_id": "00000000-0000-0000-0000-0000000000aa",
        "created_at": "2026-07-22T11:00:00Z",
        "query_count": 1,
        "has_results": True,
        "title": "test",
        "turns": [turn],
    }


def test_mirror_turn_accepts_server_turn_id_and_cc_traces(bin_mods):
    models, _, _ = bin_mods
    payload = _server_shaped_session_detail()
    parsed = models.SessionDetailResponse.model_validate(payload)
    assert parsed.turns[0].turn_id == 5
    assert parsed.turns[0].cc_traces == [{"total_cost_usd": 0.01}]


def test_mirror_turn_still_forbids_unknown_keys(bin_mods):
    models, _, _ = bin_mods
    payload = _server_shaped_session_detail()
    payload["turns"][0]["invented_field"] = "nope"
    with pytest.raises(Exception) as exc:
        models.SessionDetailResponse.model_validate(payload)
    assert "invented_field" in str(exc.value) or "extra" in str(exc.value).lower()


def test_assistant_client_session_detail_validates_server_shaped_turns(bin_mods):
    """Real AssistantClient.session_detail — the production validation seam."""
    _, client_mod, _ = bin_mods
    body = _server_shaped_session_detail()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "include=turns" in str(request.url)
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    client = client_mod.AssistantClient(
        base_url="http://test",
        assistant_prefix="nextseek_api/assistant",
        auth=("u", "p"),
        transport=transport,
    )
    detail = client.session_detail("00000000-0000-0000-0000-0000000000aa",
                                   include_turns=True)
    assert detail["turns"][0]["turn_id"] == 5
    assert detail["turns"][0]["cc_traces"] is not None


def test_dispatch_recall_through_real_client_and_mirror(bin_mods, monkeypatch, tmp_path):
    """_dispatch_recall end-to-end via AssistantClient + mirror (not FakeClient)."""
    models, client_mod, runner = bin_mods
    sid = "00000000-0000-0000-0000-0000000000aa"
    detail = _server_shaped_session_detail(turn_id=3, bundle_id=2)
    rows = [{"uid": "D.SEQ-1", "sex": "F"}, {"uid": "D.SEQ-2", "sex": "M"}]
    bundle = {
        "id": 2,
        "api_result_full": {"ok": True, "data": {"total": 2, "rows": rows}},
        "terminal_reply": "found",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/sessions/{sid}/") or f"/sessions/{sid}/" in path:
            if "bundles" not in path:
                return httpx.Response(200, json=detail)
        if f"/sessions/{sid}/bundles/2/" in path or path.endswith("/bundles/2/"):
            return httpx.Response(200, json=bundle)
        return httpx.Response(404, json={"error": f"unexpected {path}"})

    transport = httpx.MockTransport(handler)

    def _make():
        return client_mod.AssistantClient(
            base_url="http://test",
            assistant_prefix="nextseek_api/assistant",
            auth=("u", "p"),
            transport=transport,
        )

    monkeypatch.setattr(runner, "_make_client", _make)
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", sid)
    monkeypatch.setenv("NEXTSEEK_SCRATCH_DIR", str(tmp_path))

    import argparse
    manifest = runner._dispatch_recall(argparse.Namespace(turn=3))
    dest = tmp_path / "recall" / "turn-3.json"
    assert json.loads(dest.read_bytes()) == rows
    assert manifest["turn_id"] == 3
    assert manifest["bundle_id"] == 2
    assert manifest["row_count"] == 2
