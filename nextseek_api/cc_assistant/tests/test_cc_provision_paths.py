"""Hermetic tests for the G7-10 volume-subpath path-builder."""
from nextseek_api.cc_assistant.cc_config import CCPaths
from nextseek_api.cc_assistant.cc_provision import build_user_dirs


def _paths() -> CCPaths:
    return CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")


def test_subpaths_are_volume_relative_tails():
    dirs = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id="S1")

    # shared is PROJECT-scoped (no user segment); the rest are per-user.
    assert dirs.shared_subpath == "42-liver-tox/shared"
    assert dirs.input_subpath == "42-liver-tox/alice/input"
    assert dirs.scratch_subpath == "42-liver-tox/alice/scratch"
    assert dirs.output_subpath == "42-liver-tox/alice/output"
    assert dirs.cc_state_subpath == "42-liver-tox/alice/cc-state/S1"
    assert dirs.memory_subpath == "42-liver-tox/alice/_memory/S1"
    assert dirs.transcripts_subpath == "42-liver-tox/alice/_memory/S1/transcripts"
    # never absolute — an absolute Subpath is rejected by the Engine.
    for s in (dirs.shared_subpath, dirs.input_subpath, dirs.scratch_subpath,
              dirs.output_subpath, dirs.cc_state_subpath, dirs.memory_subpath,
              dirs.transcripts_subpath):
        assert not s.startswith("/")


def test_mount_paths_use_the_container_mount_root():
    dirs = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id="S1")

    assert dirs.input_mnt == "/dmac/users/42-liver-tox/alice/input"
    assert dirs.scratch_mnt == "/dmac/users/42-liver-tox/alice/scratch"
    assert dirs.cc_state_mnt == "/dmac/users/42-liver-tox/alice/cc-state/S1"
    assert dirs.output_mnt == "/dmac/users/42-liver-tox/alice/output"
    assert dirs.memory_mnt == "/dmac/users/42-liver-tox/alice/_memory/S1"


def test_session_scoped_fields_are_none_without_session():
    dirs = build_user_dirs(_paths(), "42-liver-tox", "alice", session_id=None)

    assert dirs.cc_state_subpath is None
    assert dirs.memory_subpath is None
    assert dirs.transcripts_subpath is None
    assert dirs.cc_state_mnt is None
    assert dirs.memory_mnt is None
    # non-session fields still resolve
    assert dirs.scratch_subpath == "42-liver-tox/alice/scratch"


def test_missing_volume_or_root_fail_closed():
    for paths in (
        CCPaths(users_volume="", user_root_mount="/dmac/users"),
        CCPaths(users_volume="dmac-cc-users", user_root_mount=""),
    ):
        try:
            build_user_dirs(paths, "42-liver-tox", "alice", session_id="S1")
        except ValueError as exc:
            assert "users_volume" in str(exc)
        else:
            raise AssertionError("missing volume/root must fail closed")


def test_builder_rejects_malicious_segments():
    paths = _paths()

    for kwargs in (
        {"project_dirname": "../p", "user_id": "alice", "session_id": "S1"},
        {"project_dirname": "42-p", "user_id": "a/b", "session_id": "S1"},
        {"project_dirname": "42-p", "user_id": "alice", "session_id": "../S1"},
    ):
        try:
            build_user_dirs(paths, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"malicious segment accepted: {kwargs!r}")
