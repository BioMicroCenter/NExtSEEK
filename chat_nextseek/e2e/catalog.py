"""Catalog Pydantic models + loader."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class PassCriterion(BaseModel):
    field: str
    op: Literal["eq", "contains", "nonempty", "true", "gte", "lte", "mentions", "matches_re"]
    value: Any = None


class Turn(BaseModel):
    label: str
    query: str
    pass_criteria: list[PassCriterion] = Field(default_factory=list)


class Variant(BaseModel):
    family: str
    id: str
    name: str
    tags: list[str] = Field(default_factory=list)
    requires_env: list[str] = Field(default_factory=list)
    turns: list[Turn]


class Family(BaseModel):
    description: str = ""
    variants: list[Variant]


class Catalog(BaseModel):
    version: str = "1.0"
    families: dict[str, Family]


def load_catalog(path: Path) -> Catalog:
    """Read catalog.json from disk and validate via Pydantic."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return Catalog.model_validate(payload)
