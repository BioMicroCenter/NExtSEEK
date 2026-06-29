"""Hermetic tests for the CCPaths claude-state roots (Step 1b)."""
from nextseek_api.cc_assistant import cc_config


def test_ccpaths_has_cc_state_defaults(monkeypatch):
    for var in ("DMAC_HOST_CC_STATE_ROOT", "DMAC_CC_STATE_MOUNT"):
        monkeypatch.delenv(var, raising=False)
    p = cc_config.CCPaths.from_env()
    assert p.host_cc_state_root  # non-empty default
    assert p.cc_state_mount      # non-empty default
    # distinct from scratch so transcripts never land in published artifacts
    assert p.host_cc_state_root != p.host_scratch_root
    assert p.cc_state_mount != p.scratch_mount


def test_ccpaths_cc_state_overridable(monkeypatch):
    monkeypatch.setenv("DMAC_HOST_CC_STATE_ROOT", "/host/ccstate")
    monkeypatch.setenv("DMAC_CC_STATE_MOUNT", "/dmac/ccstate")
    p = cc_config.CCPaths.from_env()
    assert p.host_cc_state_root == "/host/ccstate"
    assert p.cc_state_mount == "/dmac/ccstate"
