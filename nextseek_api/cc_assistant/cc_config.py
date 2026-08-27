"""Path configuration for the Container-CC route.

G7-10 (Step 7, compose-native): all per-project/per-user CC trees live in the
external named Docker volume ``dmac-cc-users`` (same pattern as
``seek-filestore``) — never a host bind directory. The volume is
mounted once into the nextseek container at ``DMAC_USER_ROOT_MOUNT``
(``/dmac/users``) so Django can mkdir scratch/cc-state dirs, render memory, and
publish artifacts; each per-turn CC sibling mounts a *subpath* of that same
volume (``VolumeOptions.Subpath``), so bind SOURCES are no longer host paths.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# Dev-instance defaults. Overridable via docker/nextseek.env.
_DEFAULT_USERS_VOLUME = "dmac-cc-users"
_DEFAULT_USER_ROOT_MOUNT = "/dmac/users"


@dataclass(frozen=True)
class CCPaths:
    """The named CC user-tree volume + its nextseek-container mount point.

    ``users_volume`` is the external Docker volume that persists every
    per-project/per-user CC tree; ``user_root_mount`` is where that volume is
    mounted inside the nextseek container. Per-CC-sibling mounts address
    subpaths of ``users_volume`` (there is no host bind source root).
    """

    users_volume: str    # external named Docker volume (e.g. dmac-cc-users)
    user_root_mount: str  # nextseek-container path where users_volume is mounted

    @classmethod
    def from_env(cls) -> "CCPaths":
        return cls(
            users_volume=os.environ.get("DMAC_CC_USERS_VOLUME", _DEFAULT_USERS_VOLUME),
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
