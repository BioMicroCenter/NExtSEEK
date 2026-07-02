"""Cross-user isolation invariants for CC provisioning (G7-10 volume mode).

Isolation is now enforced by per-mount ``VolumeOptions.Subpath`` tails of the
single dmac-cc-users volume (not disjoint host bind sources): same-project
users share ONLY the project-scoped ``shared`` subpath; different projects are
fully disjoint.
"""
import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")


def _subpaths(project_dirname: str, user_id: str) -> dict[str, str]:
    mounts = cc_engine._build_volumes(
        paths=_paths(),
        project_dirname=project_dirname,
        user_id=user_id,
        cc_state_key="S1",
    )
    return {m["Target"]: m["VolumeOptions"]["Subpath"] for m in mounts}


def test_same_project_shares_shared_but_not_private_subpaths():
    alice = _subpaths("42-liver", "alice")
    bob = _subpaths("42-liver", "bob")

    assert alice["/data/shared"] == bob["/data/shared"] == "42-liver/shared"
    assert alice["/data/input"] == "42-liver/alice/input"
    assert bob["/data/input"] == "42-liver/bob/input"
    assert alice["/data/scratch"] == "42-liver/alice/scratch"
    assert bob["/data/scratch"] == "42-liver/bob/scratch"
    assert alice["/home/user/.claude"] == "42-liver/alice/cc-state/S1"
    assert bob["/home/user/.claude"] == "42-liver/bob/cc-state/S1"
    # only shared overlaps between the two users
    assert set(alice.values()) & set(bob.values()) == {"42-liver/shared"}


def test_different_projects_are_fully_disjoint_including_shared():
    project_a = set(_subpaths("42-liver", "alice").values())
    project_b = set(_subpaths("99-heart", "alice").values())

    assert project_a.isdisjoint(project_b)


def test_private_and_shared_are_readonly_and_only_scratch_and_state_rw():
    mounts = {m["Target"]: m for m in cc_engine._build_volumes(
        paths=_paths(), project_dirname="42-liver", user_id="alice", cc_state_key="S1",
    )}

    assert mounts["/data/input"]["ReadOnly"] is True
    assert mounts["/data/shared"]["ReadOnly"] is True
    assert mounts["/data/scratch"]["ReadOnly"] is False
    assert mounts["/home/user/.claude"]["ReadOnly"] is False


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "", "a\x00b"])
def test_malicious_project_rejected_before_interpolation(bad):
    with pytest.raises(ValueError):
        cc_engine._validate_project(bad)


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", ".", "x" * 65])
def test_malicious_user_rejected(bad):
    with pytest.raises(ValueError):
        cc_engine._validate_user_id(bad)
