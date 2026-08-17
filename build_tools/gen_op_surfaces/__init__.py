"""Deterministic generated-surface tooling for Plan 005."""
from __future__ import annotations

from build_tools.gen_op_surfaces.blocks import MarkerError, render_marked_file
from build_tools.gen_op_surfaces.constants import (
    BAKED_CAPABILITIES_REL,
    CANONICAL_CAPABILITIES_REL,
    EXIT_CHANGES_WRITTEN,
    EXIT_ERROR,
    EXIT_NO_CHANGE,
)
from build_tools.gen_op_surfaces.emit import (
    SurfaceTarget,
    capabilities_bytes,
    check_surfaces,
    surface_targets,
    write_surfaces,
)

__all__ = [
    "BAKED_CAPABILITIES_REL",
    "CANONICAL_CAPABILITIES_REL",
    "EXIT_CHANGES_WRITTEN",
    "EXIT_ERROR",
    "EXIT_NO_CHANGE",
    "MarkerError",
    "SurfaceTarget",
    "capabilities_bytes",
    "check_surfaces",
    "render_marked_file",
    "surface_targets",
    "write_surfaces",
]
