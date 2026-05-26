from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SystemAgentOutput(BaseModel):
    mode: Literal["get_capabilities", "get_entities", "get_searches"]
    narrative: str = Field(description="Final user-facing answer — plain text, no JSON.")
    entities_consulted: list[str] = Field(
        default_factory=list,
        description="Entity names/codes (sampletype, assay, or project/study) that were looked up from the catalog.",
    )
    notes: str = Field(default="", description="Internal reasoning notes — not shown to user.")
