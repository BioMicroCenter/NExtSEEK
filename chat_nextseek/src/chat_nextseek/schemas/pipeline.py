"""Pipeline-agent structured-output schemas.

Each LLM step in pipeline_agent returns one of these. The agent's session
state stores them as dicts (Pydantic `.model_dump()`); the per-turn
dispatcher re-instantiates them when reading.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Edit-by-reprompt
# ---------------------------------------------------------------------------


class EditDiffOutput(BaseModel):
    """Edit step: apply a user edit message to the current samplesheet rows."""

    action: Literal["apply", "ask", "reject"]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    ask_reply: str | None = None
    reject_reason: str | None = None
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


__all__ = [
    "EditDiffOutput",
]
