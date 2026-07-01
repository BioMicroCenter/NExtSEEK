"""Path configuration for the Container-CC route.

Step 2 consolidates all per-project/per-user CC paths under one host root:
``DMAC_USER_ROOT/<project>/<user>/...``. The nextseek container sees that same
tree at ``DMAC_USER_ROOT_MOUNT`` so it can mkdir scratch/cc-state dirs, render
memory, and publish artifacts before/after spawning the sibling CC container.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# Dev-instance defaults. Overridable via docker/nextseek.env.
_DEFAULT_HOST_USER_ROOT = "/srv/dmac/users"
_DEFAULT_USER_ROOT_MOUNT = "/dmac/users"


@dataclass(frozen=True)
class CCPaths:
    """Single Step-2 host root + its nextseek-container mount point."""

    host_user_root: str  # host: consolidated per-project/per-user CC bind sources
    user_root_mount: str # nextseek-container path where host_user_root is mounted

    @classmethod
    def from_env(cls) -> "CCPaths":
        return cls(
            host_user_root=os.environ.get("DMAC_USER_ROOT", _DEFAULT_HOST_USER_ROOT),
            user_root_mount=os.environ.get("DMAC_USER_ROOT_MOUNT", _DEFAULT_USER_ROOT_MOUNT),
        )


def _env_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class CCMemoryConfig:
    """Step 1c knobs (env-overridable). Window size IS the size cap (no byte budget)."""

    window_size: int          # most-recent OTHER sessions merged into memory
    max_items: int            # cap on MemoryItem count per SessionSummary
    truncate_chars: int       # per tool-output truncation in the actions view
    sweep_idle_seconds: int   # Celery sweep idle threshold
    summary_model: str        # recorded label for the Gemini summary model

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> "CCMemoryConfig":
        src = os.environ if source is None else source
        return cls(
            window_size=_env_int(src, "DMAC_CC_MEMORY_WINDOW", 10),
            max_items=_env_int(src, "DMAC_CC_MEMORY_MAX_ITEMS", 8),
            truncate_chars=_env_int(src, "DMAC_CC_MEMORY_TRUNCATE_CHARS", 500),
            sweep_idle_seconds=_env_int(src, "DMAC_CC_MEMORY_SWEEP_IDLE_SECONDS", 900),
            summary_model=src.get("DMAC_CC_MEMORY_SUMMARY_MODEL", "gemini-flash"),
        )
