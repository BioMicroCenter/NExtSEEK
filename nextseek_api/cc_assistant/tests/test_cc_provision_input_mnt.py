"""input_mnt is the container-mount path Django writes uploads to. Hermetic.

Post-G7-10 there is no ``input_src`` host path — the CC sibling mounts the
``input_subpath`` tail of the dmac-cc-users volume; Django (uploads, list)
resolves paths via ``input_mnt`` only.
"""
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_provision import build_user_dirs


def _paths() -> CCPaths:
    return CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")


def test_input_mnt_uses_mount_root_and_matches_input_subpath_tail():
    d = build_user_dirs(_paths(), "42-px", "alice", session_id="S1")
    assert d.input_mnt == "/dmac/users/42-px/alice/input"
    assert d.input_subpath == "42-px/alice/input"
    # the mount path is exactly the subpath rooted at user_root_mount
    assert d.input_mnt == f"/dmac/users/{d.input_subpath}"


def test_no_host_source_field_survives():
    d = build_user_dirs(_paths(), "42-px", "alice", session_id="S1")
    assert not hasattr(d, "input_src")
    assert not hasattr(d, "shared_src")
    assert not hasattr(d, "scratch_src")
    assert not hasattr(d, "output_src")
    assert not hasattr(d, "cc_state_src")
