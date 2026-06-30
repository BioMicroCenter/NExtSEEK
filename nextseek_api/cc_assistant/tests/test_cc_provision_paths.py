"""Hermetic tests for the Step-2 single path-builder."""
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_provision import build_user_dirs


def _paths() -> CCPaths:
    return CCPaths(
        host_dropbox_root="/legacy/dropbox",
        host_scratch_root="/legacy/scratch",
        host_output_root="/legacy/output",
        scratch_mount="/legacy/m/scratch",
        output_mount="/legacy/m/output",
        host_cc_state_root="/legacy/ccstate",
        cc_state_mount="/legacy/m/ccstate",
        host_user_root="/host/users",
        user_root_mount="/dmac/users",
    )


def test_host_sources_are_nested_under_project_and_user():
    dirs = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id="S1")

    assert dirs.shared_src == "/host/users/42-liver-tox/shared"
    assert dirs.input_src == "/host/users/42-liver-tox/alice/input"
    assert dirs.scratch_src == "/host/users/42-liver-tox/alice/scratch"
    assert dirs.cc_state_src == "/host/users/42-liver-tox/alice/cc-state/S1"
    assert dirs.output_src == "/host/users/42-liver-tox/alice/output"


def test_mount_paths_use_the_container_mount_root():
    dirs = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id="S1")

    assert dirs.scratch_mnt == "/dmac/users/42-liver-tox/alice/scratch"
    assert dirs.cc_state_mnt == "/dmac/users/42-liver-tox/alice/cc-state/S1"
    assert dirs.output_mnt == "/dmac/users/42-liver-tox/alice/output"
    assert dirs.memory_mnt == "/dmac/users/42-liver-tox/alice/_memory/S1"


def test_cc_state_and_memory_are_none_without_session():
    dirs = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id=None)

    assert dirs.cc_state_src is None
    assert dirs.cc_state_mnt is None
    assert dirs.memory_mnt is None
    assert dirs.scratch_src == "/host/users/42-liver-tox/alice/scratch"
