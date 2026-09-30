"""Keeps planting links in every folder an agent or the sidecar can write (step 1, CI).

Part A runs a whole Container-CC turn whose fake agent, mid-turn, plants links at the names Django reads,
writes, renames, chmods or lists in the chat's cc-state, the turn's scratch and the user's _staging folder, plus
a FIFO, and echoes the password into its transcript; the turn then runs for real (stop, sweep, publish, capture,
scrub). Part B is a source guard: the functions that work in those folders make no path-based file call, so a
later edit cannot quietly bring one back.

Blocking in CI (ci/blocking_lanes.py), with every other test_cc_agent_folders_ module.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import docker as docker_mod
import pytest

from NessieAI import paths
from NessieAI.cc import cc_engine
from NessieAI.cc.cc_config import CCPaths
from NessieAI.tests.cc.agent_folder_canary import MARK, Canary, FakeAgent, FakeContainer

RUN_ID = "c0ffee00-1111-2222-3333-444455556666"
CHAT = "chat-1"
SESSION = "5e55e55e-aaaa-bbbb-cccc-ddddeeeeffff"
API_USER = "alice-login"
API_PASS = MARK.decode()  # the canary holds the password, so a scrub through a link would rewrite it
REQ_OK = "22222222-2222-2222-2222-222222222222"
REQ_LINK = "33333333-3333-3333-3333-333333333333"
FRAMES = [
    json.dumps({"type": "system", "subtype": "init", "session_id": SESSION, "model": "opus"}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}),
    json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                "total_cost_usd": 0.01, "session_id": SESSION, "num_turns": 1, "duration_ms": 5}),
]


# -- Part A: a turn whose agent plants links everywhere -----------------------------------------------------

def _staging(users: Path) -> Path:
    return users / "_staging" / hashlib.sha256(API_USER.encode()).hexdigest()


def _agent_work(users: Path, canary: Canary):
    user = users / "proj" / "alice"
    cc_state = user / "cc-state" / CHAT
    scratch = user / "scratch" / RUN_ID

    def work():
        store = cc_state / "projects" / "-home-user"
        store.mkdir(parents=True, exist_ok=True)
        (store / f"{SESSION}.jsonl").write_bytes(
            b'{"type":"user","message":{"content":"echo ' + MARK + b'"}}\n')
        canary.link_file(store / f"{SESSION}.jsonl.scrub-tmp")
        canary.link_file(store / "leak.jsonl")
        canary.link_dir(store / "linked")
        canary.link_dir(cc_state / "projects" / "-elsewhere")
        (cc_state / "CLAUDE.md").unlink(missing_ok=True)
        canary.link_file(cc_state / "CLAUDE.md")
        (scratch / "report.csv").write_bytes(b"a,b\n1,2\n")
        (scratch / "raw").mkdir(exist_ok=True)
        (scratch / "raw" / "debug.log").write_bytes(b"debug\n")
        canary.link_file(scratch / "leak.csv")
        canary.link_file(scratch / "raw" / "leak.log")
        canary.link_dir(scratch / "linked")
        os.mkfifo(scratch / "pipe.csv")
        base = _staging(users)
        (base / REQ_OK).mkdir(parents=True, exist_ok=True)
        (base / REQ_OK / "staged.csv").write_bytes(b"s\n")
        (base / f"{REQ_OK}.complete").write_text("")
        canary.link_dir(base / REQ_LINK)
        (base / f"{REQ_LINK}.complete").write_text("")

    return work


def test_a_turn_whose_agent_plants_links_everywhere_leaves_the_canary_alone(tmp_path, monkeypatch):
    canary = Canary(tmp_path)
    users = tmp_path / "users"
    sibling = users / "proj" / "alice" / "cc-state" / "chat-2"
    sibling.mkdir(parents=True)
    canary.link_dir(sibling / "projects")
    memory = tmp_path / "memory-CLAUDE.md"
    memory.write_bytes(b"# memory\n")
    container = FakeContainer([])

    class _Client:
        class containers:
            @staticmethod
            def run(**_kwargs):
                return container

    class _Trace:
        def model_dump(self):
            return {"trace": True}

    monkeypatch.setattr(docker_mod, "from_env", lambda: _Client())
    monkeypatch.setattr(cc_engine, "BridgeAttachSocket",
                        lambda raw, stdout_stream=None: FakeAgent(_agent_work(users, canary), FRAMES))
    monkeypatch.setattr("NessieAI.cc.cc_trace.extract_trace", lambda *a, **k: _Trace())
    events: list = []
    payloads: list = []

    cc_engine.run_cc_turn(
        query="q", model_id="m", send_event=lambda e, d: events.append((e, dict(d))),
        user_id="alice", project_dirname="proj", run_id=RUN_ID,
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(users)),
        cc_state_key=CHAT, memory_claude_md=str(memory), api_user=API_USER, api_pass=API_PASS,
        chat_session=object(), user_query="q", on_turn_complete=payloads.append,
        chat_session_id=CHAT, turn_timeout=30,
    )

    assert [e for e, _ in events if e in ("query_complete", "query_error")] == ["query_complete"], events
    output = users / "proj" / "alice" / "output"
    canary.assert_untouched()
    canary.assert_unread(events, payloads, output)
    published = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    assert f"artifacts/{RUN_ID}/artifacts.zip" in published, published
    assert "raw/debug.log" in published, published
    assert not [p for p in published if "leak" in p or "pipe" in p], published
    assert (_staging(users) / f"{REQ_LINK}.complete").exists(), "a request that is a link is refused, its marker kept"


# -- Part B: no path-based file call in a function that works in an agent folder ----------------------------

AGENT_FOLDER_FUNCTIONS = {
    "NessieAI/cc/cc_engine.py": (
        "_read_scrub_manifest", "scrub_transcript_store", "scrub_sibling_transcript_stores",
        "_newest_jsonl_under", "_transcript_line_counts", "_read_turn_transcript", "_snapshot_tree",
        "_publish_artifacts", "_stage_memory_file", "_stop_and_confirm_exit"),
    "NessieAI/cc/cc_session.py": ("store_has_transcripts", "read_store_transcript"),
    "NessieAI/cc/cc_staging.py": (
        "_deliver_file_safely", "_request_dir_state", "_remove_request", "sweep_user_staging"),
    "NessieAI/cc/turn.py": ("_session_metas", "_summarize_sync_target"),
    "nextseek_api/cc_assistant/cc_sweep.py": ("_run_sweep",),
    "nextseek_api/management/commands/scrub_stored_sample_properties.py": ("_cc_files", "_cc_split", "_cc_dir_fd"),  # _replace_file and _read_regular also serve the NS outputs roots (path lstat, not agent-writable), which this rule cannot tell apart
}
# Calls that follow a link when handed a path. Allowed only on safe_fs, with a dir_fd keyword, with
# follow_symlinks=False, or on a folder fd (a positional name ending in "_fd").
PATH_CALLS = frozenset({
    "read_bytes", "read_text", "write_bytes", "write_text", "rglob", "glob", "iterdir", "is_file", "is_dir",
    "is_symlink", "exists", "stat", "lstat", "unlink", "rename", "replace", "chmod", "touch", "open", "walk",
    "listdir", "scandir", "copyfile", "copy", "copy2", "copytree", "move", "rmtree", "mkdir", "makedirs",
})


def _path_calls(fn: ast.AST) -> list[str]:
    bad = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name, owner = func.id, None
        elif isinstance(func, ast.Attribute):
            name, owner = func.attr, func.value
        else:
            continue
        if name not in PATH_CALLS:
            continue
        if isinstance(owner, ast.Name) and owner.id == "safe_fs":
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        if "dir_fd" in keywords:
            continue
        follow = keywords.get("follow_symlinks")
        if isinstance(follow, ast.Constant) and follow.value is False:
            continue
        if any(isinstance(arg, ast.Name) and arg.id.endswith("_fd") for arg in node.args):
            continue
        bad.append(f"line {node.lineno}: {ast.unparse(node)}")
    return bad


@pytest.mark.parametrize("module, function", [
    (module, function) for module, functions in AGENT_FOLDER_FUNCTIONS.items() for function in functions])
def test_no_path_based_file_call_in_an_agent_folder_function(module, function):
    tree = ast.parse((paths.REPO_ROOT / module).read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function), None)
    assert fn is not None, f"{module} has no {function}: update AGENT_FOLDER_FUNCTIONS with the code"
    assert _path_calls(fn) == [], (
        f"{module}:{function} makes a file call that follows links; go through NessieAI/cc/safe_fs.py")


def test_the_guard_itself_flags_a_path_call_and_passes_the_safe_forms():
    flagged = ast.parse("def f(p):\n    return Path(p).read_bytes()\n").body[0]
    safe = ast.parse(
        "def g(root, fd, e):\n"
        "    safe_fs.read_file(root, 'x')\n"
        "    os.unlink('x', dir_fd=fd)\n"
        "    os.scandir(root_fd)\n"
        "    e.is_dir(follow_symlinks=False)\n").body[0]
    assert _path_calls(flagged) == ["line 2: Path(p).read_bytes()"]
    assert _path_calls(safe) == []