"""Pydantic input models for batch-upload CLI JSON files.

Required fields only; unused API fields are preserved via extra="allow" so
producer shapes can evolve without breaking consumers.
"""
from __future__ import annotations

from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    model_validator,
)


class SampleAttributeDef(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    required: bool = False
    is_title: bool = False


class SampleTypeSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    sample_type: str = Field(
        validation_alias=AliasChoices("sample_type", "SampleType"),
    )
    attributes: list[SampleAttributeDef] = Field(
        validation_alias=AliasChoices("attributes", "sample_attributes"),
        min_length=1,
    )


class SchemaFile(RootModel[list[SampleTypeSchema]]):
    """Accept either a single schema dict or a list of them."""

    @model_validator(mode="before")
    @classmethod
    def coerce_single(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return [data]
        return data

    def as_dicts(self) -> list[dict[str, Any]]:
        return [item.model_dump(by_alias=False) for item in self.root]
