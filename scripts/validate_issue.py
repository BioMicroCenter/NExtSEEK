#!/usr/bin/env python3
"""Validate a NExtSEEK structured issue draft (YAML frontmatter + markdown body).

Conventions: docs/ISSUE-CONVENTIONS.md. This module is the SINGLE SOURCE OF
TRUTH for the issue taxonomy; the Issue Form, the conventions doc, and the
label seeder are drift-guarded against the constants below.

Draft format:

    ---
    title: "Observed behavior, not the action"
    type: bug                # exactly one of ISSUE_TYPES
    areas: [cc_assistant]    # >=1, lowercase, -/_ separators
    priority: medium         # optional: low|medium|high
    needs_ruling: false      # optional
    ---
    ## Summary ...           # seven sections, see SECTIONS

Exit codes: 0 valid, 1 invalid, 2 usage/parse error.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

ISSUE_TYPES: tuple[str, ...] = (
    "bug", "enhancement", "task", "docs", "performance",
    "security", "data-hygiene", "design-question", "ops",
)
PRIORITIES: tuple[str, ...] = ("low", "medium", "high")
EPISTEMIC_TAGS: tuple[str, ...] = (
    "test-proven", "reproduced-live", "code-reading", "inference",
)
ROOT_CAUSE_SENTINEL = "Not established — do not guess."
SEEDED_AREAS: tuple[str, ...] = (
    "cc_assistant", "chat_nextseek", "nextseek_api", "seek-proxy", "ui",
    "upload", "batch-upload", "sample-search", "project-search", "router",
    "schema-rag", "search-solr", "graph-neo4j", "deployment", "installer",
)

AREA_RE = re.compile(r"^[a-z0-9]+([_-][a-z0-9]+)*$")
_TITLE_BANNED = ("fix ", "fixes ", "fixed ", "implement ", "implements ", "todo")
_EVIDENCE_REF = re.compile(r"\w+\.\w+:\d+|`[^`]+`|\b[0-9a-f]{7,40}\b")

# canonical heading (lowercased) -> (model field, required)
SECTIONS: dict[str, tuple[str, bool]] = {
    "summary": ("summary", True),
    "evidence": ("evidence", True),
    "impact": ("impact", True),
    "root cause": ("root_cause", True),
    "suggested fix direction": ("fix_direction", False),
    "verification recipe": ("verification_recipe", True),
    "provenance": ("provenance", True),
}

_PLACEHOLDER = r"SET_IN_LOCAL_ENV|REDACTED|CHANGEME|changeme|<[^>]+>|\*\*\*|xxx"
_SECRET_PATTERNS: dict[str, re.Pattern] = {
    "GitHub classic token": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "GitHub fine-grained token": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "credential-looking assignment": re.compile(
        r"(?i)[A-Za-z0-9_]*(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*['\"]?"
        r"(?!(?:" + _PLACEHOLDER + r"))[A-Za-z0-9+/_\-]{12,}"
    ),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "OpenAI-style API key": re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ"),
}


class DraftError(Exception):
    """Structural problem: missing frontmatter, unparseable YAML, missing section."""


class IssueDraft(BaseModel):
    title: str = Field(min_length=15, max_length=120)
    type: Literal[
        "bug", "enhancement", "task", "docs", "performance",
        "security", "data-hygiene", "design-question", "ops",
    ]
    areas: list[str] = Field(min_length=1)
    priority: Optional[Literal["low", "medium", "high"]] = None
    needs_ruling: bool = False
    summary: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    root_cause: str
    fix_direction: Optional[str] = None
    verification_recipe: str = Field(min_length=1)
    provenance: str = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def _behavior_first(cls, v: str) -> str:
        if v.lower().startswith(_TITLE_BANNED):
            raise ValueError(
                "Title must state the observed behavior/need ('X does Y when Z'), "
                "never the action ('Fix X')"
            )
        return v

    @field_validator("areas")
    @classmethod
    def _area_names(cls, v: list[str]) -> list[str]:
        for a in v:
            if not AREA_RE.fullmatch(a):
                raise ValueError(
                    f"area '{a}' must match {AREA_RE.pattern} (lowercase, -/_ separators; "
                    "underscores only for code-path names like cc_assistant)"
                )
        return v

    @field_validator("evidence")
    @classmethod
    def _evidence_contract(cls, v: str) -> str:
        if not any(tag in v for tag in EPISTEMIC_TAGS):
            raise ValueError(
                "Every Evidence claim needs an epistemic tag: " + " | ".join(EPISTEMIC_TAGS)
            )
        if not _EVIDENCE_REF.search(v):
            raise ValueError("Evidence must cite at least one file:line, `command`, or commit ref")
        return v

    @field_validator("root_cause")
    @classmethod
    def _no_guessing(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                f"Root cause may not be empty — write the analysis or exactly: '{ROOT_CAUSE_SENTINEL}'"
            )
        return v


_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    matches = list(_HEADING.finditer(body))
    for i, m in enumerate(matches):
        name = m.group(1).strip().lower()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        if name in SECTIONS:
            found[name] = body[m.end():end].strip()
    return found


def parse_draft(text: str) -> IssueDraft:
    fm = _FRONTMATTER.match(text)
    if not fm:
        raise DraftError("Draft must start with a '---' YAML frontmatter block")
    try:
        meta = yaml.safe_load(fm.group(1)) or {}
    except yaml.YAMLError as e:
        raise DraftError(f"Unparseable YAML frontmatter: {e}") from e
    if not isinstance(meta, dict):
        raise DraftError("Frontmatter must be a YAML mapping")
    sections = _split_sections(text[fm.end():])
    missing = [h for h, (_f, req) in SECTIONS.items() if req and h not in sections]
    if missing:
        raise DraftError(
            "Missing required section heading(s): " + ", ".join(sorted(missing))
            + " (## Title-Case headings; see docs/ISSUE-CONVENTIONS.md)"
        )
    fields = {f: sections[h] for h, (f, _req) in SECTIONS.items() if h in sections}
    return IssueDraft(**meta, **fields)


def scan_secrets(text: str) -> list[str]:
    return [
        f"{name}: matches {pat.pattern[:40]}..."
        for name, pat in _SECRET_PATTERNS.items()
        if pat.search(text)
    ]


def labels_for(d: IssueDraft) -> list[str]:
    labels = [f"type: {d.type}"] + [f"area: {a}" for a in d.areas]
    if d.priority:
        labels.append(f"priority: {d.priority}")
    if d.needs_ruling:
        labels.append("needs-ruling")
    return labels


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("draft", help="path to the issue draft (.md with YAML frontmatter)")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    ap.add_argument("--labels", action="store_true", help="on success, print gh labels one per line")
    args = ap.parse_args(argv)
    path = Path(args.draft)
    if not path.is_file():
        print(f"ERROR: no such file: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    try:
        draft = parse_draft(text)
    except DraftError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except ValidationError as e:
        errors += [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        draft = None
    errors += [f"secret-scan: {hit} — remove it; the repo is PUBLIC" for hit in scan_secrets(text)]
    if args.json:
        print(json.dumps({"valid": not errors, "errors": errors,
                          "labels": labels_for(draft) if draft and not errors else []}))
    elif errors:
        for e in errors:
            print(f"INVALID: {e}", file=sys.stderr)
    else:
        print(f"VALID: {path.name} — title/type/areas/sections/secret-scan all pass")
        if args.labels:
            print("\n".join(labels_for(draft)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
