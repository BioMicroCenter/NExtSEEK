from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EntityItem(BaseModel):
    code: str
    name: str | None = None

    model_config = ConfigDict(extra="ignore")


class EntityAgentOutput(BaseModel):
    sampletypes: list[EntityItem] = Field(default_factory=list)
    assays: list[EntityItem] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

