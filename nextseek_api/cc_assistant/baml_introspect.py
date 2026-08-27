"""Introspection helpers for runtime ClassifiedFamily enum members."""
from __future__ import annotations

from typing import Any, Iterable

__all__ = ["declared_family_members"]


def declared_family_members(type_builder_recipe: dict[str, Any]) -> set[str]:
    members = type_builder_recipe.get("members") or []
    return {str(m["name"]) for m in members if isinstance(m, dict) and m.get("name")}


def validate_member(label: str | None, allowed: Iterable[str]) -> bool:
    if label is None:
        return False
    return label in set(allowed)
