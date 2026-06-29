"""Hermetic tests for the CC bind-mount builder incl. the per-session .claude
store (Step 1b). No Docker, no Django."""
import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(
        host_dropbox_root="/host/dropbox",
        host_scratch_root="/host/scratch",
        host_output_root="/host/output",
        scratch_mount="/dmac/scratch",
        output_mount="/dmac/output",
        host_cc_state_root="/host/ccstate",
        cc_state_mount="/dmac/ccstate",
    )


def test_build_volumes_mounts_projects_ro_scratch_rw():
    vols = cc_engine._build_volumes(
        paths=_paths(), projects=["example-project"], user_id="demo", cc_state_key="S1",
    )
    assert vols["/host/dropbox/example-project"] == {
        "bind": "/data/projects/example-project", "mode": "ro"}
    assert vols["/host/scratch/demo"] == {"bind": "/data/scratch", "mode": "rw"}


def test_build_volumes_mounts_per_session_claude_state_rw():
    vols = cc_engine._build_volumes(
        paths=_paths(), projects=[], user_id="demo", cc_state_key="S1",
    )
    # source encodes BOTH identities; bind is the agent's HOME .claude
    assert vols["/host/ccstate/demo/S1"] == {"bind": "/home/user/.claude", "mode": "rw"}


def test_build_volumes_omits_claude_state_when_no_key():
    vols = cc_engine._build_volumes(
        paths=_paths(), projects=[], user_id="demo", cc_state_key=None,
    )
    assert not any(v["bind"] == "/home/user/.claude" for v in vols.values())


def test_claude_home_constant():
    assert cc_engine._CONTAINER_CLAUDE_HOME == "/home/user/.claude"


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "x" * 65])
def test_cc_state_key_uses_same_traversal_guard(bad):
    # cc_state_key (a chat-session UUID) must pass the same single-segment guard.
    with pytest.raises(ValueError):
        cc_engine._validate_user_id(bad)


def test_real_uuid_passes_the_guard():
    cc_engine._validate_user_id("b623a372-1c4e-4a9f-8d2b-0f1e2a3b4c5d")  # must not raise
