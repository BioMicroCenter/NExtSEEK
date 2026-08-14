"""cc_engine per-turn wall-clock clamp + env-configurable hard ceiling (#6).

The Debug panel's max-turn-length control posts an admin-only override; the
server clamps it via clamp_turn_timeout to [_TIMEOUT_FLOOR, _TIMEOUT_HARD_MAX].
The hard ceiling is env-configurable (NEXTSEEK_CC_TIMEOUT_HARD_MAX, default 180)
so a deployment can raise it while a UI override can never exceed it.
"""
import importlib

from nextseek_api.cc_assistant import cc_engine


def test_default_hard_max_is_180_when_env_unset():
    assert cc_engine._TIMEOUT_HARD_MAX == 180


def test_none_or_nonpositive_returns_default():
    d = cc_engine._DEFAULT_TURN_TIMEOUT
    assert cc_engine.clamp_turn_timeout(None) == d
    assert cc_engine.clamp_turn_timeout(0) == d
    assert cc_engine.clamp_turn_timeout(-5) == d


def test_value_below_floor_is_raised_to_floor():
    assert cc_engine.clamp_turn_timeout(1) == cc_engine._TIMEOUT_FLOOR


def test_value_above_ceiling_is_capped():
    assert cc_engine.clamp_turn_timeout(10_000) == cc_engine._TIMEOUT_HARD_MAX


def test_value_within_range_is_honored():
    v = cc_engine._TIMEOUT_FLOOR + 30
    assert v <= cc_engine._TIMEOUT_HARD_MAX  # holds at the 180 default
    assert cc_engine.clamp_turn_timeout(v) == v


def test_hard_max_is_env_configurable(monkeypatch):
    # Raise the ceiling via env and reload; a formerly-clamped value is now honored.
    monkeypatch.setenv("NEXTSEEK_CC_TIMEOUT_HARD_MAX", "600")
    try:
        importlib.reload(cc_engine)
        assert cc_engine._TIMEOUT_HARD_MAX == 600
        assert cc_engine.clamp_turn_timeout(500) == 500      # now allowed
        assert cc_engine.clamp_turn_timeout(9_999) == 600    # capped to the new ceiling
    finally:
        monkeypatch.delenv("NEXTSEEK_CC_TIMEOUT_HARD_MAX", raising=False)
        importlib.reload(cc_engine)  # restore the 180 default for the rest of the suite
