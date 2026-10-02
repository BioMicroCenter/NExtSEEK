"""The container side of the turn pass (spec piece 1): every direct nextseek-* tool authenticates with
NEXTSEEK_TURN_PASS, nothing in the container reads a password any more, and the entrypoint drops one a host still
sends. Runs the plugin's own modules from the source tree, like test_assistant_client_mirror_seam.py."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from NessieAI import paths

BIN = paths.CC_PLUGIN_BIN
CC_RUNTIME = paths.CC_RUNTIME_DIR
PASS = "T" * 43
SID = "11111111-1111-1111-1111-111111111111"
TASK = "22222222-2222-2222-2222-222222222222"
PASSWORD_NAMES = ("NEXTSEEK_PASSWORD", "API_PASS", "SEEK_PASSWORD")


@pytest.fixture(scope="module")
def bin_mods():
    sys.path.insert(0, str(BIN))
    try:
        yield {name: importlib.reload(importlib.import_module(name))
               for name in ("_turn_pass", "_assistant_models", "_assistant_client", "_nextseek_runner",
                            "_batch_upload_client")}
    finally:
        sys.path.remove(str(BIN))


def test_turn_pass_auth_sends_the_nextseekturn_scheme_and_hides_the_pass(bin_mods):
    tp = bin_mods["_turn_pass"]
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    with httpx.Client(auth=tp.TurnPassAuth(PASS), transport=httpx.MockTransport(handler)) as client:
        client.get("http://nextseek_nginx/nextseek_api/projects/")
    assert seen["auth"] == f"NextseekTurn {PASS}"
    assert PASS not in repr(tp.TurnPassAuth(PASS))
    for bad in ("", "two words"):
        with pytest.raises(ValueError):
            tp.TurnPassAuth(bad)


def _answer_like_the_assistant(seen):
    def handler(request):
        seen.setdefault("auth", set()).add(request.headers.get("authorization"))
        if request.url.path.endswith("/query/async/"):
            seen["body"] = json.loads(request.content)
            return httpx.Response(202, json={"task_id": TASK, "session_id": SID})
        return httpx.Response(200, json={
            "task_id": TASK, "session_id": SID, "status": "completed",
            "progress": [{"event": "query_complete", "data": {"reply": "ok", "debug": {}, "bundle_id": None}}],
            "result": None,
        })
    return handler


@pytest.fixture
def direct_road(bin_mods, monkeypatch):
    ac = bin_mods["_assistant_client"]
    seen = {}
    real_init = ac.AssistantClient.__init__

    def patched_init(self, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_answer_like_the_assistant(seen))
        real_init(self, **kwargs)

    monkeypatch.setattr(ac.AssistantClient, "__init__", patched_init)
    monkeypatch.setenv("NEXTSEEK_URL", "http://nextseek_nginx")
    monkeypatch.setenv("NEXTSEEK_TURN_PASS", PASS)
    monkeypatch.setenv("NEXTSEEK_CHAT_SESSION_ID", SID)
    monkeypatch.setenv("API_USER", "demo")
    monkeypatch.setenv("API_PASS", "a-password-from-an-old-host")
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)
    return seen


def test_the_direct_tools_send_the_pass_and_never_a_password(bin_mods, direct_road):
    bin_mods["_nextseek_runner"]._dispatch_query(SimpleNamespace(query="how many", planner=False))
    assert direct_road["auth"] == {f"NextseekTurn {PASS}"}


def test_nextseek_plan_runs_in_the_live_chat(bin_mods, direct_road):
    bin_mods["_nextseek_runner"]._dispatch_plan(SimpleNamespace(query="plan it"))
    assert direct_road["body"]["session_id"] == SID
    assert direct_road["body"]["mode"] == "plan"


def test_nextseek_plan_needs_the_chat_session(bin_mods, monkeypatch):
    monkeypatch.delenv("NEXTSEEK_CHAT_SESSION_ID", raising=False)
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)
    with pytest.raises(SystemExit) as exc:
        bin_mods["_nextseek_runner"]._dispatch_plan(SimpleNamespace(query="q"))
    assert exc.value.code == 2


def test_the_runner_will_not_start_without_a_pass_even_with_a_password(bin_mods, monkeypatch, capsys):
    runner = bin_mods["_nextseek_runner"]
    monkeypatch.delenv("NEXTSEEK_TURN_PASS", raising=False)
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)
    monkeypatch.setenv("API_USER", "demo")
    monkeypatch.setenv("API_PASS", "pw")
    monkeypatch.setattr(sys, "argv", ["nextseek-query", "--agent", "query", "--query", "q"])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2
    error = json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"]
    assert error["code"] == "CONFIG_MISSING" and "NEXTSEEK_TURN_PASS" in error["message"]


def test_the_batch_upload_client_uses_the_pass_and_ignores_passwords(bin_mods, monkeypatch):
    buc = bin_mods["_batch_upload_client"]
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    monkeypatch.setenv("NEXTSEEK_URL", "http://nextseek_nginx")
    monkeypatch.setenv("NEXTSEEK_TURN_PASS", PASS)
    monkeypatch.setenv("NEXTSEEK_USERNAME", "demo")
    monkeypatch.setenv("NEXTSEEK_PASSWORD", "old-password")
    buc.BatchUploadClient.from_env(transport=httpx.MockTransport(handler)).list_sample_types()
    assert seen["auth"] == f"NextseekTurn {PASS}"
    monkeypatch.delenv("NEXTSEEK_TURN_PASS")
    with pytest.raises(SystemExit) as exc:
        buc.BatchUploadClient.from_env()
    assert exc.value.code == 2


def test_runner_ns_builds_its_client_with_the_pass(monkeypatch):
    monkeypatch.setenv("DMAC_RUNNER_NS_NO_REMAP", "1")
    monkeypatch.setenv("NEXTSEEK_URL", "http://nextseek_nginx")
    monkeypatch.setenv("NEXTSEEK_TURN_PASS", PASS)
    spec = importlib.util.spec_from_file_location("runner_ns_under_test", CC_RUNTIME / "container" / "runner_ns.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module._build_assistant_client()
    assert type(client._auth).__name__ == "TurnPassAuth"


def test_the_image_puts_the_pass_helper_next_to_runner_ns():
    dockerfile = (CC_RUNTIME / "Dockerfile").read_text()
    assert "COPY build_context/plugins/nextseek/bin/_turn_pass.py /opt/dmac/_turn_pass.py" in dockerfile


def _container_code():
    plugin = CC_RUNTIME / "build_context" / "plugins" / "nextseek"
    files = [*(CC_RUNTIME / "container").glob("*.py"), CC_RUNTIME / "container" / "entrypoint.sh",
             *(plugin / "bin").glob("*.py"), *(plugin / "bin").glob("*.sh"), *(plugin / "bin").glob("nextseek-*"),
             *(plugin / "hooks").glob("*"), *(plugin / "scripts").glob("*")]
    return [path for path in files if path.is_file()]


def test_nothing_in_the_container_reads_a_password_except_the_sidecar_frame():
    """The one reader left is _nextseek_runner._api_pass, which fills the sidecar frame's ns_login until plan 03
    moves those ops to the direct road with the pass; plan 03 deletes it and drops the exception below."""
    offenders = []
    for path in _container_code():
        rel = path.relative_to(CC_RUNTIME).as_posix()
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if not any(name in line for name in PASSWORD_NAMES):
                continue
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("unset "):
                continue
            if rel.endswith("bin/_nextseek_runner.py") and 'os.environ.get("API_PASS", "")' in line:
                continue
            offenders.append(f"{rel}:{number}: {stripped}")
    assert offenders == []


def test_the_docs_the_agent_reads_name_the_pass_and_no_password():
    docs = [CC_RUNTIME / "container" / "CLAUDE.md",
            CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "skills" / "nextseek" / "SKILL.md"]
    for doc in docs:
        text = doc.read_text()
        assert [name for name in PASSWORD_NAMES if name in text] == [], doc.name
        assert "NEXTSEEK_TURN_PASS" in text, doc.name
