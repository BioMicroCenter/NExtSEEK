from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIRequestPlan(BaseModel):
    endpoint: str | None = None
    method: str | None = None
    requestBody: dict[str, Any] = Field(default_factory=dict)
    queryParameters: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    model_config = ConfigDict(extra="ignore")

