from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MemoryCoderOutput(BaseModel):
    extraction_code: str = Field(
        description="Python snippet that reads `data`/`rows` and assigns final JSON-serializable output to `result`."
    )
    result_description: str = Field(
        default="",
        description="Brief description of the intended computed result.",
    )
    fields_used: list[str] = Field(
        default_factory=list,
        description="JSON paths or field names the code relies on.",
    )
    notes: str = ""

    model_config = ConfigDict(extra="ignore")
