"""Which road the Container-CC op tools take (approach 1, piece 2): ``direct`` or ``sidecar``.

One reader for Django's ``NEXTSEEK_CC_OPS_ROAD`` setting, so the agent's env, the in-turn staging sweep and the op
view's sidecar cap always agree. ``sidecar`` (any case, spaces trimmed) is the sidecar road; anything else is
``direct``, the default. The sidecar road, its sweep and this switch are removed in the release after approach 1 is
proven on dev and prod.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ROAD_ENV = "NEXTSEEK_CC_OPS_ROAD"
DIRECT = "direct"
SIDECAR = "sidecar"


def ops_road() -> str:
    """``SIDECAR`` or ``DIRECT``, from the setting."""
    from django.conf import settings

    value = str(getattr(settings, ROAD_ENV, DIRECT) or "").strip().lower()
    if value == SIDECAR:
        return SIDECAR
    if value not in ("", DIRECT):
        logger.warning("%s=%r is neither direct nor sidecar; using direct", ROAD_ENV, value)
    return DIRECT
