"""Shared pure helpers for the laya router (JevLevROUTING). Owner: unit U3.

Stage 0 skeleton: signatures fixed by PLAN section 0; bodies land in U3.
No torch, no laya import in this file (SPEC test 13).
"""
from __future__ import annotations

import pathlib

from .router_context import HistoryTurn


def condense(query: str, history: list[HistoryTurn]) -> str:
    """SPEC s4 exact format, deterministic."""
    raise NotImplementedError


def options_hash(options: dict) -> str:
    """sha256 of the rendered option texts (SPEC s5)."""
    raise NotImplementedError


def prompt_hash(root: pathlib.Path | None = None) -> str:
    """sha256 of router.baml, route_capabilities.json, clients.baml, plus followup_rule_text() under split."""
    raise NotImplementedError


def apply_temperature(probs: dict[str, float], T: float) -> dict[str, float]:
    """p_i^(1/T), renormalised (SPEC s8 Calibration)."""
    raise NotImplementedError


def norm_text_hash(text: str) -> str:
    """Lowercase, collapse whitespace, sha256 hex."""
    raise NotImplementedError
