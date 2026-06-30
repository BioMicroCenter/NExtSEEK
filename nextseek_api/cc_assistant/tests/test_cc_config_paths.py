"""Hermetic tests for the Step-2 CCPaths single user root."""
from nextseek_api.cc_assistant import cc_config


def test_ccpaths_has_user_root_defaults(monkeypatch):
    for var in ("DMAC_USER_ROOT", "DMAC_USER_ROOT_MOUNT"):
        monkeypatch.delenv(var, raising=False)

    paths = cc_config.CCPaths.from_env()

    assert paths.host_user_root
    assert paths.user_root_mount


def test_ccpaths_user_root_overridable(monkeypatch):
    monkeypatch.setenv("DMAC_USER_ROOT", "/host/users")
    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", "/dmac/users")

    paths = cc_config.CCPaths.from_env()

    assert paths.host_user_root == "/host/users"
    assert paths.user_root_mount == "/dmac/users"
