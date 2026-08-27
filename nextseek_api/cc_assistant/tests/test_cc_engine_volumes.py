"""Hermetic tests for the G7-10 nested CC volume-subpath mount builder.

Pre-G7-10 these asserted host bind-source dicts (``{src: {"bind":…, "mode":…}}``);
post-cutover ``_build_volumes`` emits Engine-API ``Mount`` payloads addressing
subpaths of the ``dmac-cc-users`` named volume. Exact per-mount Subpath values
are pinned in ``test_cc_volume_subpath.py``; this file keeps the builder-shape
and traversal-guard regressions.
"""
import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")


def _by_target(mounts):
    return {m["Target"]: m for m in mounts}


def test_input_and_shared_mounted_ro():
    mounts = _by_target(cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1", run_id="R1",
    ))

    inp = mounts["/data/input"]
    assert inp["Source"] == "dmac-cc-users"
    assert inp["VolumeOptions"]["Subpath"] == "42-px/alice/input"
    assert inp["ReadOnly"] is True

    shared = mounts["/data/shared"]
    assert shared["VolumeOptions"]["Subpath"] == "42-px/shared"  # project-scoped
    assert shared["ReadOnly"] is True


def test_scratch_rw_and_cc_state_rw():
    mounts = _by_target(cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1", run_id="R1",
    ))

    scratch = mounts["/data/scratch"]
    assert scratch["VolumeOptions"]["Subpath"] == "42-px/alice/scratch/R1"  # #70/#36 per-turn
    assert scratch["ReadOnly"] is False

    state = mounts["/home/user/.claude"]
    assert state["VolumeOptions"]["Subpath"] == "42-px/alice/cc-state/S1"
    assert state["ReadOnly"] is False


def test_build_volumes_omits_claude_state_when_no_key():
    mounts = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key=None, run_id="R1",
    )

    assert not any(m["Target"] == "/home/user/.claude" for m in mounts)
    assert any(m["Target"] == "/data/scratch" for m in mounts)


def test_claude_home_constant():
    assert cc_engine._CONTAINER_CLAUDE_HOME == "/home/user/.claude"


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "x" * 65])
def test_cc_state_key_uses_same_traversal_guard(bad):
    # cc_state_key (a chat-session UUID) must pass the same single-segment guard.
    with pytest.raises(ValueError):
        cc_engine._validate_user_id(bad)


def test_real_uuid_passes_the_guard():
    cc_engine._validate_user_id("b623a372-1c4e-4a9f-8d2b-0f1e2a3b4c5d")  # must not raise


def test_transcripts_mount_rides_along_ro():
    mounts = _by_target(cc_engine._build_volumes(
        paths=_paths(),
        project_dirname="42-px",
        user_id="alice",
        cc_state_key="S1",
        run_id="R1",
        transcripts_subpath="42-px/alice/_memory/S1/transcripts",
    ))

    tr = mounts["/home/user/.cc-memory/transcripts"]
    assert tr["Source"] == "dmac-cc-users"
    assert tr["VolumeOptions"]["Subpath"] == "42-px/alice/_memory/S1/transcripts"
    assert tr["ReadOnly"] is True


def test_no_legacy_flat_or_host_sources_emitted():
    mounts = cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-px", user_id="alice", cc_state_key="S1", run_id="R1",
    )

    for m in mounts:
        assert m["Source"] == "dmac-cc-users"       # never a host path source
        assert not m["Source"].startswith("/legacy")
        assert "/data/projects/" not in m["Target"]
