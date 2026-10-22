"""The sidecar road carries the turn pass, not the login (approach 1, piece 2).

For its one remaining release the sidecar keeps its 60 s wait, its drop folder and the sweep; its frame now carries
the turn pass and the username it keys its staging folder by, it sends NExtSEEK ``Authorization: NextseekTurn``, and
it hands NExtSEEK's own error code, reason and fixed message to the plugin unchanged. No network: httpx is patched.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from NessieAI import paths

REQUEST_ID = "6f1c1f6e-8a53-4c1b-9d8e-1a2b3c4d5e6f"
OP_URL = "http://nextseek/nextseek_api/assistant/graph/"


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    from NessieAI.cc import safe_fs
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})


@contextmanager
def _sidecar():
    """The sidecar package and the plugin's copy of the WS contract, loaded from the tree for the block."""
    with patch.dict(sys.modules):
        pkg = types.ModuleType("sidecar")
        pkg.__path__ = [str(paths.NS_SIDECAR_DIR)]
        sys.modules["sidecar"] = pkg
        spec = importlib.util.spec_from_file_location("_p03_plugin_ws_contract", paths.CC_PLUGIN_BIN / "_ws_contract.py")
        plugin_contract = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = plugin_contract
        spec.loader.exec_module(plugin_contract)
        yield SimpleNamespace(
            server=importlib.import_module("sidecar.app.server"),
            ns_client=importlib.import_module("sidecar.app.ns_client"),
            exceptions=importlib.import_module("sidecar.app.exceptions"),
            contract=importlib.import_module("sidecar.app.contract"),
            plugin_contract=plugin_contract,
        )


def _frame(**over) -> str:
    frame = {"op": "graph", "args": {"query": "How many mouse samples?"},
             "ns_turn": {"api_user": "u1", "turn_pass": "pass-1"}, "request_id": REQUEST_ID}
    frame.update(over)
    return json.dumps(frame)


def _reply(status, **kwargs) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", OP_URL), **kwargs)


def _answer(sc, raw_frame, reply):
    server = sc.server
    with patch.object(server, "_CFG", SimpleNamespace(nextseek_base_url="http://nextseek", staging_dir="/nonexistent")), \
         patch.object(server, "_build_write_gate", return_value=lambda *a, **k: None), \
         patch.object(server, "_build_stage", return_value=lambda op, result: result), \
         patch.object(server, "_build_stage_bytes", return_value=(lambda *a, **k: "/nonexistent", lambda: None)), \
         patch.object(sc.ns_client.httpx, "post", return_value=reply) as post:
        return json.loads(asyncio.run(server.handle_message(raw_frame))), post


def test_a_frame_carrying_the_login_is_refused():
    raw = json.dumps({"op": "graph", "args": {"query": "q"}, "ns_login": {"api_user": "u1", "api_pass": "pw"},
                      "request_id": REQUEST_ID})
    with _sidecar() as sc:
        out = json.loads(asyncio.run(sc.server.handle_message(raw)))
    assert (out["status"], out["error"]["code"]) == ("error", "VALIDATION")


def test_the_pass_goes_as_a_nextseek_turn_header_and_no_basic_auth():
    with _sidecar() as sc:
        out, post = _answer(sc, _frame(), _reply(200, json={"op": "graph", "result": {"plan": {}, "result": {}}}))
    assert out["status"] == "ok"
    kwargs = post.call_args.kwargs
    assert kwargs["headers"] == {"Authorization": "NextseekTurn pass-1"}
    assert "auth" not in kwargs
    assert kwargs["timeout"] == 60.0


def test_artifacts_are_fetched_with_the_pass_too():
    with _sidecar() as sc, patch.object(sc.ns_client.httpx, "get",
                                        return_value=_reply(200, content=b"xlsx")) as get:
        assert sc.ns_client.fetch_artifact("/nextseek_api/assistant/sessions/s/bundles/1/artifacts/k/",
                                           base_url="http://nextseek", turn_pass="pass-1") == b"xlsx"
    assert get.call_args.kwargs["headers"] == {"Authorization": "NextseekTurn pass-1"}
    assert "auth" not in get.call_args.kwargs


@pytest.mark.parametrize("code, status, exit_code", [("BUSY", 429, 10), ("TIME_UP", 408, 11),
                                                     ("PASS_NOT_ALLOWED", 403, 12), ("VALIDATION", 422, 3),
                                                     ("AUTH_FAILED", 401, 8), ("WRITE_BLOCKED", 403, 5)])
def test_nextseeks_code_reaches_the_plugin(code, status, exit_code):
    errors = ([{"field": "parts", "type": "invalid_json"}] if code == "VALIDATION"
              else [{"title": code, "detail": "A fixed sentence."}])
    body = {"code": code, "reason": None, "message": "A fixed sentence.", "errors": errors}
    with _sidecar() as sc:
        out, _ = _answer(sc, _frame(), _reply(status, json=body))
        for contract in (sc.contract, sc.plugin_contract):
            parsed = contract.SidecarResponse.model_validate(out)
            assert parsed.error.code == code
            assert contract.ERROR_EXIT[code] == exit_code
    assert out["error"]["message"].startswith("A fixed sentence.")
    if code == "VALIDATION":
        assert "parts (invalid_json)" in out["error"]["message"]


def test_an_agent_failure_keeps_its_reason():
    body = {"code": "AGENT_FAILED", "reason": "deadline", "message": "The op ran out of time before it could finish.",
            "errors": [{"title": "AGENT_FAILED", "detail": "The op ran out of time before it could finish."}]}
    with _sidecar() as sc:
        out, _ = _answer(sc, _frame(), _reply(502, json=body))
    assert (out["error"]["code"], out["error"]["reason"]) == ("AGENT_FAILED", "deadline")


def test_an_unknown_reason_is_not_passed_on():
    body = {"code": "AGENT_FAILED", "reason": "weird", "message": "m", "errors": []}
    with _sidecar() as sc:
        out, _ = _answer(sc, _frame(), _reply(502, json=body))
    assert out["error"]["reason"] is None


@pytest.mark.parametrize("status, kwargs, code", [
    (401, {"json": {"detail": "Authentication credentials were not provided."}}, "AUTH_FAILED"),
    (502, {"text": "<html>bad gateway</html>"}, "AGENT_FAILED"),
])
def test_a_reply_without_a_code_keeps_todays_mapping(status, kwargs, code):
    with _sidecar() as sc:
        out, _ = _answer(sc, _frame(), _reply(status, **kwargs))
    assert out["error"]["code"] == code


def test_the_config_repr_hides_the_pass():
    with _sidecar() as sc:
        assert "pass-1" not in repr(sc.server.NsHttpConfig(base_url="http://x", turn_pass="pass-1"))


def test_the_frame_repr_hides_the_pass():
    with _sidecar() as sc:
        for contract in (sc.contract, sc.plugin_contract):
            assert "pass-mine" not in repr(contract.NsTurn(api_user="u", turn_pass="pass-mine"))

# ---- Ruling R1: the drop folder is named after the pass, never after the frame's username ----

def _folder_for(sc, turn_pass):
    import hashlib
    return hashlib.sha256(turn_pass.encode("utf-8")).hexdigest()


def test_the_drop_folder_is_named_after_the_pass_not_the_username(tmp_path):
    src = tmp_path / "report.xlsx"
    src.write_bytes(b"x")
    with _sidecar() as sc:
        staging = importlib.import_module("sidecar.app.staging")
        cfg = SimpleNamespace(staging_dir=str(tmp_path / "staging"))
        # A frame naming ANOTHER user, with its own pass, files under its own pass's folder.
        turn = sc.contract.NsTurn(api_user="other-user", turn_pass="pass-mine")
        staging.make_stage(cfg, turn, REQUEST_ID)("report", {"saved_files": {"k": str(src)}})
    own = tmp_path / "staging" / _folder_for(None, "pass-mine")
    assert (own / f"{REQUEST_ID}.complete").is_file()
    import hashlib
    assert not (tmp_path / "staging" / hashlib.sha256(b"other-user").hexdigest()).exists()


def test_a_turn_with_another_pass_never_sees_the_file(tmp_path):
    from NessieAI.cc import cc_staging
    src = tmp_path / "report.xlsx"
    src.write_bytes(b"x")
    users = tmp_path / "users"
    (users / "_staging").mkdir(parents=True)
    with _sidecar() as sc:
        staging = importlib.import_module("sidecar.app.staging")
        cfg = SimpleNamespace(staging_dir=str(users / "_staging"))
        staging.make_stage(cfg, sc.contract.NsTurn(api_user="u1", turn_pass="pass-mine"), REQUEST_ID)(
            "report", {"saved_files": {"k": str(src)}})
    assert cc_staging.staging_folder_for("pass-mine") == _folder_for(None, "pass-mine")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for pass_, expect in (("pass-other", 0), ("pass-mine", 1)):
        out = cc_staging.sweep_user_staging(
            user_root_mount=str(users), scratch_dir=str(scratch),
            staging_folder=cc_staging.staging_folder_for(pass_), user_id="alice", project_dirname="1-p")
        assert len(out.delivered) == expect

