"""Hermetic: the two Step-1c RO memory mounts. No Docker/Django."""
from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(host_user_root="/host/users", user_root_mount="/dmac/users")


def test_user_memory_mounted_ro_nested_over_session_claude():
    vols = cc_engine._build_volumes(
        paths=_paths(),
        project_dirname="42-px",
        user_id="demo",
        cc_state_key="S1",
        user_memory_file="/host/users/42-px/demo/_memory/S1/CLAUDE.md",
    )
    assert vols["/host/users/42-px/demo/_memory/S1/CLAUDE.md"] == {
        "bind": "/home/user/.claude/CLAUDE.md", "mode": "ro"}
    assert vols["/host/users/42-px/demo/cc-state/S1"] == {
        "bind": "/home/user/.claude", "mode": "rw"}


def test_transcripts_dir_mounted_ro():
    vols = cc_engine._build_volumes(
        paths=_paths(),
        project_dirname="42-px",
        user_id="demo",
        cc_state_key="S1",
        transcripts_dir="/host/users/42-px/demo/_memory/S1/transcripts",
    )
    assert vols["/host/users/42-px/demo/_memory/S1/transcripts"] == {
        "bind": "/home/user/.cc-memory/transcripts", "mode": "ro"}


def test_no_memory_mounts_when_none():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="demo", cc_state_key="S1")
    assert not any(v["bind"] == "/home/user/.claude/CLAUDE.md" for v in vols.values())
    assert not any(v["bind"] == "/home/user/.cc-memory/transcripts" for v in vols.values())


def test_existing_volume_shape_unchanged():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="demo", cc_state_key="S1")
    assert vols["/host/users/42-px/demo/input"]["mode"] == "ro"
    assert vols["/host/users/42-px/shared"]["mode"] == "ro"
    assert vols["/host/users/42-px/demo/scratch"]["mode"] == "rw"
