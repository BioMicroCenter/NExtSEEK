"""Hermetic: the two Step-1c RO memory mounts. No Docker/Django."""
from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(
        host_dropbox_root="/host/dropbox", host_scratch_root="/host/scratch",
        host_output_root="/host/output", scratch_mount="/dmac/scratch",
        output_mount="/dmac/output", host_cc_state_root="/host/ccstate",
        cc_state_mount="/dmac/ccstate")


def test_user_memory_mounted_ro_nested_over_session_claude():
    vols = cc_engine._build_volumes(
        paths=_paths(), projects=[], user_id="demo", cc_state_key="S1",
        user_memory_file="/host/ccstate/demo/_memory/S1/CLAUDE.md")
    assert vols["/host/ccstate/demo/_memory/S1/CLAUDE.md"] == {
        "bind": "/home/user/.claude/CLAUDE.md", "mode": "ro"}
    assert vols["/host/ccstate/demo/S1"] == {"bind": "/home/user/.claude", "mode": "rw"}


def test_transcripts_dir_mounted_ro():
    vols = cc_engine._build_volumes(
        paths=_paths(), projects=[], user_id="demo", cc_state_key="S1",
        transcripts_dir="/host/ccstate/demo/_memory/S1/transcripts")
    assert vols["/host/ccstate/demo/_memory/S1/transcripts"] == {
        "bind": "/home/user/.cc-memory/transcripts", "mode": "ro"}


def test_no_memory_mounts_when_none():
    vols = cc_engine._build_volumes(
        paths=_paths(), projects=[], user_id="demo", cc_state_key="S1")
    assert not any(v["bind"] == "/home/user/.claude/CLAUDE.md" for v in vols.values())
    assert not any(v["bind"] == "/home/user/.cc-memory/transcripts" for v in vols.values())


def test_existing_volume_shape_unchanged():
    vols = cc_engine._build_volumes(
        paths=_paths(), projects=["example-project"], user_id="demo", cc_state_key="S1")
    assert vols["/host/dropbox/example-project"]["mode"] == "ro"
    assert vols["/host/scratch/demo"]["mode"] == "rw"
