"""The ops road switch (approach 1, piece 2): one Django setting, read in one place, default direct."""
from __future__ import annotations

import pytest
from django.conf import settings
from django.test import override_settings

from NessieAI.cc.ops_road import DIRECT, ROAD_ENV, SIDECAR, ops_road


def test_the_default_is_the_direct_road():
    """The boxes' env files are never re-rendered, so the default lives in code."""
    assert ROAD_ENV == "NEXTSEEK_CC_OPS_ROAD"
    assert settings.NEXTSEEK_CC_OPS_ROAD == "direct"
    assert ops_road() == DIRECT


@pytest.mark.parametrize("value, road", [("sidecar", SIDECAR), ("Sidecar ", SIDECAR), ("SIDECAR", SIDECAR),
                                         ("direct", DIRECT)])
def test_the_setting_picks_the_road(value, road):
    with override_settings(NEXTSEEK_CC_OPS_ROAD=value):
        assert ops_road() == road


@pytest.mark.parametrize("value", ["side-car", "websocket", "", None])
def test_anything_else_is_the_direct_road(value):
    with override_settings(NEXTSEEK_CC_OPS_ROAD=value):
        assert ops_road() == DIRECT
