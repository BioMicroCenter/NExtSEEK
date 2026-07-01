"""input_mnt is the container-mount path Django writes uploads to. Hermetic."""
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_provision import build_user_dirs


def _paths() -> CCPaths:
    return CCPaths(host_user_root="/host/users", user_root_mount="/dmac/users")


def test_input_mnt_uses_mount_root_and_matches_input_src_shape():
    d = build_user_dirs(_paths(), "42-px", "alice", session_id="S1")
    assert d.input_mnt == "/dmac/users/42-px/alice/input"
    assert d.input_src == "/host/users/42-px/alice/input"   # unchanged
