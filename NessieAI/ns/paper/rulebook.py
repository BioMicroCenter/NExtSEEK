"""The paper curation rulebook (spec §8): rulebook.json beside this file, reviewed like the reingest maps.

A rule's ``check`` names a function in curation_check.CONSTRAINT_CHECKS, never code inline (the reingest maps'
registered-token pattern). A ``constraint`` must have one; an ``exclusion`` may. The version a run used is the
file's sha256, recorded in context.json; approved rules from piece 5 add their highest id to it.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from NessieAI.ns.paper.models import _Strict

RULEBOOK_PATH = Path(__file__).with_name("rulebook.json")
_ID = re.compile(r"^R-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_SCOPE = re.compile(r"^(?:global|project:\d+|technology:\S.*|sample_type:[A-Z][A-Za-z0-9.]*)$")


class Rule(_Strict):
    id: str
    scope: str
    kind: Literal["constraint", "default", "precedence", "exclusion"]
    statement: str
    check: str | None = None
    origin: str

    @model_validator(mode="after")
    def _well_formed(self) -> "Rule":
        if not _ID.fullmatch(self.id):
            raise ValueError(f"rule id {self.id!r} is not R-<WORDS>")
        if not _SCOPE.fullmatch(self.scope):
            raise ValueError(f"rule {self.id} has a bad scope {self.scope!r}")
        if self.kind == "constraint" and not self.check:
            raise ValueError(f"constraint rule {self.id} names no check")
        return self


class Rulebook(_Strict):
    rulebook_version: int
    rules: list[Rule]

    @model_validator(mode="after")
    def _unique(self) -> "Rulebook":
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids are not unique")
        return self


def load_rulebook(path: Path | None = None) -> Rulebook:
    return Rulebook.model_validate_json((path or RULEBOOK_PATH).read_bytes())


def rulebook_version(path: Path | None = None) -> str:
    return hashlib.sha256((path or RULEBOOK_PATH).read_bytes()).hexdigest()


def applicable(rule: Rule, *, project_id: int | None, sample_types: set[str], assays: set[str]) -> bool:
    kind, _, value = rule.scope.partition(":")
    if kind == "global":
        return True
    if kind == "project":
        return project_id is not None and int(value) == project_id
    if kind == "sample_type":
        return value in sample_types
    family = value.casefold()
    return any(family in assay.casefold() for assay in assays)
