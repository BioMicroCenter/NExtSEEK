from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EntityItem(BaseModel):
    code: str
    name: str | None = None

    model_config = ConfigDict(extra="ignore")


class LabMatch(BaseModel):
    """One lab record a name or code in the turn resolved to, and how.

    Written by code after the LLM returns (``helpers/lab_code.py``), never by the LLM:
    the records come from SEEK's institution titles (``ChatConfig.LABS``).
    """

    text: str                # what matched, as the question or the LLM wrote it
    code: str                # the lab's three-letter UID code
    name: str                # the lab head's surname as SEEK spells it
    affiliation: str | None = None
    project_ids: list[int] = Field(default_factory=list)
    rule: str                # "code" | "lab_phrase" | "possessive" | "honorific" | "name"
    ambiguous: bool = False  # several records share the surname and nothing chose between them

    model_config = ConfigDict(extra="ignore")


class EntityAgentOutput(BaseModel):
    sampletypes: list[EntityItem] = Field(default_factory=list)
    assays: list[EntityItem] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    labs: list[str] = Field(default_factory=list)
    lab_codes: list[str] = Field(default_factory=list)
    #: People named as who made, collected or handled samples, and person names that
    #: matched no lab: the ``Scientist`` attribute, never a lab scope.
    scientists: list[str] = Field(default_factory=list)
    #: Overwritten after the LLM returns, like ``lab_codes``.
    lab_matches: list[LabMatch] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")
