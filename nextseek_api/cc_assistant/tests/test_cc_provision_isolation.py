"""Cross-user isolation invariants for Step 2 provisioning."""
import pytest

from nextseek_api.cc_assistant import cc_engine
from nextseek_api.cc_assistant.cc_config import CCPaths


def _paths() -> CCPaths:
    return CCPaths(host_user_root="/host/users", user_root_mount="/dmac/users")


def _volumes(project_dirname: str, user_id: str):
    return cc_engine._build_volumes(
        paths=_paths(),
        project_dirname=project_dirname,
        user_id=user_id,
        cc_state_key="S1",
    )


def test_same_project_shares_shared_but_not_private_sources():
    alice = _volumes("42-liver", "alice")
    bob = _volumes("42-liver", "bob")

    assert "/host/users/42-liver/shared" in alice
    assert "/host/users/42-liver/shared" in bob
    assert "/host/users/42-liver/alice/input" in alice
    assert "/host/users/42-liver/alice/input" not in bob
    assert "/host/users/42-liver/bob/scratch" in bob
    assert "/host/users/42-liver/bob/scratch" not in alice
    assert "/host/users/42-liver/alice/cc-state/S1" in alice
    assert "/host/users/42-liver/alice/cc-state/S1" not in bob


def test_different_projects_are_fully_disjoint_including_shared():
    project_a = _volumes("42-liver", "alice")
    project_b = _volumes("99-heart", "alice")

    assert set(project_a).isdisjoint(project_b)


def test_private_and_shared_are_readonly_and_only_scratch_is_rw():
    volumes = _volumes("42-liver", "alice")

    assert volumes["/host/users/42-liver/alice/input"]["mode"] == "ro"
    assert volumes["/host/users/42-liver/shared"]["mode"] == "ro"
    assert volumes["/host/users/42-liver/alice/scratch"]["mode"] == "rw"
    assert volumes["/host/users/42-liver/alice/cc-state/S1"]["mode"] == "rw"


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "", "a\x00b"])
def test_malicious_project_rejected_before_interpolation(bad):
    with pytest.raises(ValueError):
        cc_engine._validate_project(bad)


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", ".", "x" * 65])
def test_malicious_user_rejected(bad):
    with pytest.raises(ValueError):
        cc_engine._validate_user_id(bad)
