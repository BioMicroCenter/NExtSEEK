"""The memory CLAUDE.md and every reader of another session's transcript never follow a link (step 1).

``cc-state/<session>/`` is the agent's ~/.claude. Django writes the memory CLAUDE.md there before each turn
(and now removes last turn's on a turn with none), checks it for a --resume store, and reads other sessions'
transcripts for the sync summarizer, the idle sweep and the staged copies mounted into later agents.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from NessieAI.cc import cc_engine, cc_memory, cc_memory_io, cc_session, cc_summary, safe_fs
from NessieAI.cc.cc_config import CCPaths
from NessieAI.tests.cc.agent_folder_canary import Canary


@pytest.fixture(autouse=True)
def _reset_roots(monkeypatch):
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})


@pytest.fixture
def canary(tmp_path):
    return Canary(tmp_path)


def _cc_state(tmp_path, session: str = "sess") -> Path:
    folder = tmp_path / "users" / "proj" / "alice" / "cc-state" / session
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _turn_until_spawn(tmp_path, monkeypatch, memory: bytes | None) -> Path:
    """Run everything run_cc_turn does before the spawn (the spawn raises); return the session's cc-state."""
    import docker as docker_mod
    from docker.errors import APIError

    class _SpawnStops:
        def __init__(self):
            self.containers = self

        def run(self, **_kwargs):
            raise APIError("spawn intercepted by test")

    monkeypatch.setattr(docker_mod, "from_env", lambda: _SpawnStops())
    merged = None
    if memory is not None:
        merged = tmp_path / "_memory-CLAUDE.md"
        merged.write_bytes(memory)
    cc_engine.run_cc_turn(
        query="q", model_id=None, send_event=lambda e, d: None,
        user_id="alice", project_dirname="proj", run_id="a1",
        paths=CCPaths(users_volume="dmac-cc-users", user_root_mount=str(tmp_path / "users")),
        cc_state_key="sess", memory_claude_md=str(merged) if merged else None,
    )
    return tmp_path / "users" / "proj" / "alice" / "cc-state" / "sess"


def _meta(session: str, path: Path) -> cc_memory.SessionMeta:
    return cc_memory.SessionMeta(session_id=session, updated_at=0.0, fingerprint=None, summary=None,
                                 transcript_path=str(path), changed=True)


# -- the memory file ---------------------------------------------------------------------------------------

def test_the_memory_file_is_never_written_through_a_link(tmp_path, canary, monkeypatch):
    canary.link_file(_cc_state(tmp_path) / "CLAUDE.md")
    cc_state = _turn_until_spawn(tmp_path, monkeypatch, b"# memory for this turn\n")
    staged = cc_state / "CLAUDE.md"
    assert not staged.is_symlink() and staged.read_bytes() == b"# memory for this turn\n"
    canary.assert_untouched()


def test_a_turn_without_memory_removes_the_last_memory_file(tmp_path, monkeypatch):
    (_cc_state(tmp_path) / "CLAUDE.md").write_bytes(b"# written on an earlier turn\n")
    cc_state = _turn_until_spawn(tmp_path, monkeypatch, None)
    assert not os.path.lexists(cc_state / "CLAUDE.md")


def test_removing_the_memory_file_never_follows_a_link(tmp_path, canary, monkeypatch):
    canary.link_file(_cc_state(tmp_path) / "CLAUDE.md")
    cc_state = _turn_until_spawn(tmp_path, monkeypatch, None)
    assert not os.path.lexists(cc_state / "CLAUDE.md")
    canary.assert_untouched()


def test_a_folder_at_the_memory_name_is_removed_on_a_turn_without_memory(tmp_path, monkeypatch):
    (_cc_state(tmp_path) / "CLAUDE.md" / "inner").mkdir(parents=True)
    cc_state = _turn_until_spawn(tmp_path, monkeypatch, None)
    assert not os.path.lexists(cc_state / "CLAUDE.md")


# -- the resume check and the transcript store's own reader ------------------------------------------------

def test_the_resume_check_never_follows_a_linked_store(tmp_path, canary):
    cc_state = _cc_state(tmp_path)
    canary.link_dir(cc_state / "projects")
    assert cc_session.store_has_transcripts(cc_state) is False
    canary.assert_untouched()


def test_the_store_is_under_the_session_folder_above_the_outermost_projects_folder(monkeypatch):
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})
    assert cc_session.split_store_path("/u/p/alice/cc-state/s/projects/-home-user/projects/x.jsonl") == (
        Path("/u/p/alice/cc-state/s"), "projects/-home-user/projects/x.jsonl")
    assert cc_session.split_store_path("/u/p/alice/x.jsonl") is None


def test_the_store_root_is_the_registered_session_folder(monkeypatch):
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})
    # A project folder that happens to be named "projects" would mislead the name rule; the registry does not.
    safe_fs.register_agent_root("/u/projects/alice/cc-state/s")
    assert cc_session.split_store_path("/u/projects/alice/cc-state/s/projects/-home-user/x.jsonl") == (
        Path("/u/projects/alice/cc-state/s"), "projects/-home-user/x.jsonl")
    assert cc_session.split_store_path("/u/projects/alice/cc-state/s/CLAUDE.md") is None


def test_a_store_transcript_is_never_read_through_a_linked_folder(tmp_path, canary):
    projects = _cc_state(tmp_path) / "projects"
    projects.mkdir()
    canary.link_dir(projects / "-home-user")
    with pytest.raises(OSError):
        cc_session.read_store_transcript(projects / "-home-user" / "secret.jsonl")
    canary.assert_untouched()


# -- the readers of other sessions -------------------------------------------------------------------------

@pytest.mark.django_db
def test_session_metas_never_list_or_read_through_a_link(tmp_path, canary):
    from django.contrib.auth import get_user_model

    import NessieAI.cc.turn as cc_turn
    from NessieAI.cc import cc_config
    from nextseek_api.assistant.models_db import ChatSession

    user = get_user_model().objects.create_user("alice", password="x")
    linked = ChatSession.objects.create(user=user, extra_state={"cc_project_dirname": "proj"})
    nested = ChatSession.objects.create(user=user, extra_state={"cc_project_dirname": "proj"})
    canary.link_dir(_cc_state(tmp_path, str(linked.session_id)) / "projects")
    canary.link_dir(_cc_state(tmp_path, str(nested.session_id)) / "projects" / "-home-user")
    paths = dataclasses.replace(cc_config.CCPaths.from_env(), user_root_mount=str(tmp_path / "users"))

    metas = cc_turn._session_metas(user, None, paths, cc_config.CCMemoryConfig.from_env(),
                                   project_dirname="proj")

    assert {m.session_id: m.transcript_path for m in metas} == {
        str(linked.session_id): None, str(nested.session_id): None}
    canary.assert_untouched()


def test_the_sync_summarizer_never_reads_through_a_link(tmp_path, canary, monkeypatch):
    import NessieAI.cc.turn as cc_turn

    handed: list[bytes] = []
    monkeypatch.setattr(cc_summary, "summarize_transcript", lambda raw, *a, **k: handed.append(raw))
    projects = _cc_state(tmp_path, "sess-b") / "projects"
    projects.mkdir()
    canary.link_dir(projects / "-home-user")

    ok = cc_turn._summarize_sync_target(None, _meta("sess-b", projects / "-home-user" / "secret.jsonl"),
                                        None, None)

    assert ok is False
    assert handed == []
    canary.assert_untouched()


def test_the_idle_sweep_never_reads_through_a_link(tmp_path, canary, monkeypatch):
    from nextseek_api.cc_assistant import cc_sweep
    from NessieAI.router import router as cc_router

    handed: list[bytes] = []

    class _User:
        username = "alice"

    class _Users:
        class objects:
            @staticmethod
            def all():
                return [_User()]

    monkeypatch.setattr("django.contrib.auth.models.User", _Users)
    monkeypatch.setattr(cc_engine, "transcript_is_verified_scrubbed",
                        lambda path, raw: handed.append(raw) or True)
    monkeypatch.setattr(cc_summary, "summarize_transcript", lambda raw, *a, **k: handed.append(raw))
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "model-x")
    projects = _cc_state(tmp_path, "sess-b") / "projects"
    projects.mkdir()
    canary.link_dir(projects / "-home-user")
    meta = _meta("sess-b", projects / "-home-user" / "secret.jsonl")
    monkeypatch.setattr("NessieAI.cc.turn._session_metas", lambda *a, **k: [meta])

    assert cc_sweep._run_sweep() == 0
    assert handed == []
    canary.assert_untouched()


def test_staged_transcripts_never_copy_through_a_link(tmp_path, canary):
    linked = _cc_state(tmp_path, "sess-b") / "projects"
    linked.mkdir()
    canary.link_dir(linked / "-home-user")
    real = _cc_state(tmp_path, "sess-c") / "projects" / "-home-user" / "c.jsonl"
    real.parent.mkdir(parents=True)
    real.write_bytes(b'{"ok":1}\n')
    staging = tmp_path / "users" / "proj" / "alice" / "_memory" / "sess-a" / "transcripts"
    window = [_meta("sess-b", linked / "-home-user" / "secret.jsonl"), _meta("sess-c", real)]

    assert cc_memory_io.stage_transcripts(window, staging) == staging

    assert sorted(p.name for p in staging.iterdir()) == ["sess-c.jsonl"]
    canary.assert_untouched()
    canary.assert_unread(staging)
