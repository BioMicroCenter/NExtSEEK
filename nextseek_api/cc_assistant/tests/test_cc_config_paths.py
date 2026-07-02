"""Hermetic tests for the G7-10 CCPaths named-volume config."""
from nextseek_api.cc_assistant import cc_config


def test_ccpaths_has_volume_defaults(monkeypatch):
    for var in ("DMAC_CC_USERS_VOLUME", "DMAC_USER_ROOT_MOUNT"):
        monkeypatch.delenv(var, raising=False)

    paths = cc_config.CCPaths.from_env()

    assert paths.users_volume == "dmac-cc-users"
    assert paths.user_root_mount == "/dmac/users"


def test_ccpaths_overridable(monkeypatch):
    monkeypatch.setenv("DMAC_CC_USERS_VOLUME", "my-cc-vol")
    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", "/mnt/cc")

    paths = cc_config.CCPaths.from_env()

    assert paths.users_volume == "my-cc-vol"
    assert paths.user_root_mount == "/mnt/cc"


def test_ccpaths_retires_host_user_root():
    # The /srv/dmac/users host-bind root model is retired; no host_user_root
    # attribute survives on CCPaths.
    paths = cc_config.CCPaths(users_volume="dmac-cc-users", user_root_mount="/dmac/users")
    assert not hasattr(paths, "host_user_root")
