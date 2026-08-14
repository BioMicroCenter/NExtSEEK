"""Hermetic: Step-1c memory in G7-10 volume mode.

Old→new mapping: ``test_user_memory_mounted_ro_nested_over_session_claude``
asserted the pre-cutover RO ``user_memory_file`` host-file bind nested over the
session ``.claude`` mount; G7-10 drops that bind entirely (Docker volume
subpaths mount directories, not file overlays) — the merged CLAUDE.md is now
byte-copied into the cc-state subpath before spawn (covered in
``test_cc_volume_subpath.py``). This file keeps the transcripts-mount and
no-file-bind contracts at the ``_build_volumes`` level.
"""
from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")


def _by_target(mounts):
    return {m["Target"]: m for m in mounts}


def test_no_user_memory_file_bind_in_volume_mode():
    # The RO CLAUDE.md file bind is GONE: cc-state (RW) is the only .claude
    # mount, and no mount targets /home/user/.claude/CLAUDE.md.
    mounts = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="demo", cc_state_key="S1", run_id="R1",
        transcripts_subpath="42-px/demo/_memory/S1/transcripts",
    )
    by_target = _by_target(mounts)
    assert "/home/user/.claude/CLAUDE.md" not in by_target
    state = by_target["/home/user/.claude"]
    assert state["ReadOnly"] is False
    assert state["VolumeOptions"]["Subpath"] == "42-px/demo/cc-state/S1"


def test_transcripts_dir_mounted_ro():
    mounts = _by_target(cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="demo", cc_state_key="S1", run_id="R1",
        transcripts_subpath="42-px/demo/_memory/S1/transcripts",
    ))
    tr = mounts["/home/user/.cc-memory/transcripts"]
    assert tr["VolumeOptions"]["Subpath"] == "42-px/demo/_memory/S1/transcripts"
    assert tr["ReadOnly"] is True


def test_no_memory_mounts_when_none():
    mounts = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="demo", cc_state_key="S1", run_id="R1")
    assert not any(m["Target"] == "/home/user/.claude/CLAUDE.md" for m in mounts)
    assert not any(m["Target"] == "/home/user/.cc-memory/transcripts" for m in mounts)


def test_existing_volume_shape_unchanged():
    mounts = _by_target(cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="demo", cc_state_key="S1", run_id="R1"))
    assert mounts["/data/input"]["ReadOnly"] is True
    assert mounts["/data/shared"]["ReadOnly"] is True
    assert mounts["/data/scratch"]["ReadOnly"] is False
