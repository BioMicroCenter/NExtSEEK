"""Hermetic tests for the Step-2 nested CC bind-mount builder."""
import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(host_user_root="/host/users", user_root_mount="/dmac/users")


def test_input_and_shared_mounted_ro():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1",
    )

    assert vols["/host/users/42-px/alice/input"] == {
        "bind": "/data/input",
        "mode": "ro",
    }
    assert vols["/host/users/42-px/shared"] == {
        "bind": "/data/shared",
        "mode": "ro",
    }


def test_scratch_rw_and_cc_state_rw():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1",
    )

    assert vols["/host/users/42-px/alice/scratch"] == {
        "bind": "/data/scratch",
        "mode": "rw",
    }
    assert vols["/host/users/42-px/alice/cc-state/S1"] == {
        "bind": "/home/user/.claude",
        "mode": "rw",
    }


def test_build_volumes_omits_claude_state_when_no_key():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key=None,
    )

    assert not any(v["bind"] == "/home/user/.claude" for v in vols.values())
    assert "/host/users/42-px/alice/scratch" in vols


def test_claude_home_constant():
    assert cc_engine._CONTAINER_CLAUDE_HOME == "/home/user/.claude"


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "x" * 65])
def test_cc_state_key_uses_same_traversal_guard(bad):
    # cc_state_key (a chat-session UUID) must pass the same single-segment guard.
    with pytest.raises(ValueError):
        cc_engine._validate_user_id(bad)


def test_real_uuid_passes_the_guard():
    cc_engine._validate_user_id("b623a372-1c4e-4a9f-8d2b-0f1e2a3b4c5d")  # must not raise


def test_memory_and_transcripts_mounts_ride_along_ro():
    vols = cc_engine._build_volumes(
        paths=_paths(),
        project_dirname="42-px",
        user_id="alice",
        cc_state_key="S1",
        user_memory_file="/host/users/42-px/alice/_memory/S1/CLAUDE.md",
        transcripts_dir="/host/users/42-px/alice/_memory/S1/transcripts",
    )

    assert vols["/host/users/42-px/alice/_memory/S1/CLAUDE.md"] == {
        "bind": "/home/user/.claude/CLAUDE.md",
        "mode": "ro",
    }
    assert vols["/host/users/42-px/alice/_memory/S1/transcripts"] == {
        "bind": "/home/user/.cc-memory/transcripts",
        "mode": "ro",
    }


def test_no_legacy_flat_sources_emitted():
    vols = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1",
    )

    assert "/legacy/scratch/alice" not in vols
    assert "/legacy/ccstate/alice/S1" not in vols
    assert not any("/data/projects/" in v["bind"] for v in vols.values())
