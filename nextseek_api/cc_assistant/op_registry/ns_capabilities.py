"""Strict bounded projection of canonical capabilities.md (Plan 005 Task 11)."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

NS_ROUTE_NAME = "nextseek_query"
REQUIRED_H2 = ("Overview", "What You Can Ask", "What the System Cannot Do")
MAX_PROJECTION_UTF8_BYTES = 2000
MAX_LABEL_CHARS = 120
STALE_PIPELINE_PHRASE = "cannot run pipelines"

BEST_FOR_PREFIX = "Requests supported by the NS capability authority: "
NOT_FOR_PREFIX = "Not intended for: "

_H2_RE = re.compile(r"^##\s+(\S.*)$")
_H3_RE = re.compile(r"^###\s+(\S.*)$")
_NUMBER_PREFIX_RE = re.compile(r"^\d+\.\s+")
_FENCE_RE = re.compile(r"^(```|~~~)(.*)$")
_TOP_BULLET_RE = re.compile(r"^- (\S.*)$")
_NESTED_BULLET_RE = re.compile(r"^[ \t]+- ")
_BOLD_LEAD_RE = re.compile(r"^\*\*(.+?)\*\*(?:\s+|$)(.*)$")


class NsCapabilitiesError(ValueError):
    """Raised when canonical Markdown cannot be projected without maintainer review."""


@dataclass(frozen=True)
class NsProjection:
    description: str
    tools: tuple[str, ...]
    negative_labels: tuple[str, ...]
    best_for: str
    not_for: str

    def route_level_object(self) -> dict[str, object]:
        return {
            "route_name": NS_ROUTE_NAME,
            "description": self.description,
            "tools": list(self.tools),
            "best_for": self.best_for,
            "not_for": self.not_for,
        }

    def route_level_utf8_bytes(self) -> bytes:
        return json.dumps(
            self.route_level_object(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")


def _fold_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _require_lf_utf8(text: str) -> None:
    if "\r" in text:
        raise NsCapabilitiesError("capabilities markdown must be UTF-8/LF (CR found)")


def _iter_unfenced_lines(text: str) -> list[tuple[int, str]]:
    lines = text.split("\n")
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for index, raw in enumerate(lines, start=1):
        fence_match = _FENCE_RE.match(raw.rstrip("\n"))
        if fence is None:
            if fence_match:
                fence = fence_match.group(1)
                continue
            out.append((index, raw))
            continue
        if fence_match and fence_match.group(1) == fence:
            fence = None
            continue
    if fence is not None:
        raise NsCapabilitiesError("unclosed fenced code block")
    return out


def _heading_title(match: re.Match[str]) -> str:
    title = match.group(1).strip()
    if not title:
        raise NsCapabilitiesError("malformed heading with empty title")
    return title


def _collect_h2(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for lineno, line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            if _H2_RE.match(stripped.lstrip("> ").lstrip()) or stripped.lstrip().startswith("##"):
                raise NsCapabilitiesError("nested heading substitute is not allowed")
        match = _H2_RE.match(line)
        if match:
            found.append((lineno, _heading_title(match)))
            continue
        if line.startswith("##") and not line.startswith("###"):
            raise NsCapabilitiesError(f"malformed H2 heading at line {lineno}")
    return found


def _required_h2_spans(
    h2s: list[tuple[int, str]],
    lines: list[tuple[int, str]],
) -> dict[str, tuple[int, int]]:
    required_hits = [item for item in h2s if item[1] in REQUIRED_H2]
    names = [name for _, name in required_hits]
    if len(names) != len(REQUIRED_H2) or set(names) != set(REQUIRED_H2):
        raise NsCapabilitiesError(
            "required H2 sections Overview, What You Can Ask, and "
            "What the System Cannot Do must each occur exactly once"
        )
    if tuple(names) != REQUIRED_H2:
        raise NsCapabilitiesError("required H2 sections occur out of order")

    last_lineno = lines[-1][0] + 1 if lines else 1
    by_name = {name: lineno for lineno, name in h2s}
    spans: dict[str, tuple[int, int]] = {}
    for index, (lineno, name) in enumerate(h2s):
        end = h2s[index + 1][0] if index + 1 < len(h2s) else last_lineno
        if name in REQUIRED_H2:
            spans[name] = (lineno, end)
    if by_name.keys() != {name for _, name in h2s}:
        raise NsCapabilitiesError("ambiguous section boundaries")
    return spans


def _section_body(
    lines: list[tuple[int, str]],
    span: tuple[int, int],
) -> list[tuple[int, str]]:
    start, end = span
    return [(lineno, line) for lineno, line in lines if start < lineno < end]


def _first_overview_paragraph(body: list[tuple[int, str]]) -> str:
    chunks: list[str] = []
    started = False
    for _, line in body:
        stripped = line.strip()
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith("#") or stripped.startswith("- ") or stripped.startswith(">"):
            if not started:
                raise NsCapabilitiesError("Overview is missing a leading paragraph")
            break
        chunks.append(stripped)
        started = True
    if not chunks:
        raise NsCapabilitiesError("Overview is missing a leading paragraph")
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()


def _unique_labels(labels: list[str], *, kind: str) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    ordered: list[str] = []
    for label in labels:
        if not label.strip():
            raise NsCapabilitiesError(f"empty {kind} label")
        key = _fold_key(label)
        if key in seen:
            raise NsCapabilitiesError(f"duplicate {kind} label after NFKC/casefold: {label!r}")
        seen[key] = label
        ordered.append(label)
    if not ordered:
        raise NsCapabilitiesError(f"missing {kind} labels")
    return tuple(ordered)


def _capability_labels(body: list[tuple[int, str]]) -> tuple[str, ...]:
    labels: list[str] = []
    for lineno, line in body:
        if _NESTED_BULLET_RE.match(line):
            continue
        if line.startswith("####"):
            raise NsCapabilitiesError(f"nested heading substitute at line {lineno}")
        match = _H3_RE.match(line)
        if match:
            raw = _heading_title(match)
            label = _NUMBER_PREFIX_RE.sub("", raw).strip()
            if not label:
                raise NsCapabilitiesError(f"empty capability label at line {lineno}")
            labels.append(label)
            continue
        if line.startswith("###"):
            raise NsCapabilitiesError(f"malformed H3 heading at line {lineno}")
    return _unique_labels(labels, kind="capability")


def _negative_labels(body: list[tuple[int, str]]) -> tuple[str, ...]:
    labels: list[str] = []
    for lineno, line in body:
        if _NESTED_BULLET_RE.match(line):
            if "**" in line:
                raise NsCapabilitiesError(
                    f"nested bold-lead substitute at line {lineno}"
                )
            continue
        bullet = _TOP_BULLET_RE.match(line)
        if not bullet:
            if line.lstrip().startswith("- **"):
                raise NsCapabilitiesError(f"malformed bold lead at line {lineno}")
            continue
        payload = bullet.group(1)
        if payload.count("**") % 2 == 1:
            raise NsCapabilitiesError(f"unclosed bold lead at line {lineno}")
        lead_match = _BOLD_LEAD_RE.match(payload)
        if not lead_match:
            raise NsCapabilitiesError(f"malformed bold lead at line {lineno}")
        lead = lead_match.group(1).strip()
        if not lead:
            raise NsCapabilitiesError(f"empty negative label at line {lineno}")
        labels.append(lead)
    return _unique_labels(labels, kind="negative")


def _reject_over_budget(projection: NsProjection) -> None:
    for label in (*projection.tools, *projection.negative_labels):
        if len(label) > MAX_LABEL_CHARS:
            raise NsCapabilitiesError(
                f"projected label exceeds {MAX_LABEL_CHARS} characters; "
                "failing for maintainer review (generator must not truncate)"
            )
    size = len(projection.route_level_utf8_bytes())
    if size > MAX_PROJECTION_UTF8_BYTES:
        raise NsCapabilitiesError(
            f"NS route-level projection is {size} UTF-8 bytes "
            f"(limit {MAX_PROJECTION_UTF8_BYTES}) before task families; "
            "failing for maintainer review (generator must not truncate)"
        )


def _reject_stale_phrase(projection: NsProjection) -> None:
    fields = (
        projection.description,
        *projection.tools,
        *projection.negative_labels,
        projection.best_for,
        projection.not_for,
    )
    if any(STALE_PIPELINE_PHRASE in field for field in fields):
        raise NsCapabilitiesError(
            f"stale phrase {STALE_PIPELINE_PHRASE!r} entered a selected heading "
            "or bold lead; failing for maintainer review rather than publishing it"
        )


def project_ns_capabilities(markdown: str) -> NsProjection:
    """Parse canonical capabilities Markdown and return the bounded NS projection."""
    _require_lf_utf8(markdown)
    lines = _iter_unfenced_lines(markdown)
    h2s = _collect_h2(lines)
    spans = _required_h2_spans(h2s, lines)
    description = _first_overview_paragraph(_section_body(lines, spans["Overview"]))
    tools = _capability_labels(_section_body(lines, spans["What You Can Ask"]))
    negatives = _negative_labels(
        _section_body(lines, spans["What the System Cannot Do"])
    )
    best_for = f"{BEST_FOR_PREFIX}{'; '.join(tools)}."
    not_for = f"{NOT_FOR_PREFIX}{'; '.join(negatives)}."
    projection = NsProjection(
        description=description,
        tools=tools,
        negative_labels=negatives,
        best_for=best_for,
        not_for=not_for,
    )
    _reject_over_budget(projection)
    _reject_stale_phrase(projection)
    return projection


def load_ns_projection(path: Path) -> NsProjection:
    if not path.is_file():
        raise NsCapabilitiesError(f"missing canonical capabilities markdown: {path}")
    return project_ns_capabilities(path.read_text(encoding="utf-8"))
