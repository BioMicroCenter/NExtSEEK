"""Shared plumbing for the review forms: exit codes, errors, atomic writes."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

# The same codes the handoff script and launch.py use, so a caller reads them one way.
EXIT_OK = 0
EXIT_INVALID = 2   # the form is wrong: the message names every field
EXIT_EXISTS = 3    # the output exists; pass --force


class FormError(Exception):
    """A form failed. Carries every problem found, not only the first."""

    def __init__(self, problems: list[str], code: int = EXIT_INVALID):
        super().__init__("\n".join(problems))
        self.problems = list(problems)
        self.code = code


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def validate(model, data, what: str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        lines = [f"{what} failed schema validation:"]
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "(top)"
            lines.append(f"  {loc}: {err['msg']}")
        raise FormError(lines) from None


def read_json(path, what: str):
    p = Path(path)
    if not p.is_file():
        raise FormError([f"{what} not found: {p}"])
    try:
        return json.loads(p.read_bytes().decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise FormError([f"{what} is not valid JSON ({p}): {e}"]) from None


def atomic_write(path, text: str, *, force: bool = True) -> None:
    p = Path(path)
    if p.exists() and not force:
        raise FormError([f"{p} exists; pass --force to overwrite it"], EXIT_EXISTS)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-form-", suffix=p.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dump(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def md(s) -> str:
    """A table cell: no pipes, no newlines."""
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")
