#!/usr/bin/env python3
"""Validate NExtSEEK ViewSet conventions (endpoint descriptions + extend_schema).

This module is the SINGLE SOURCE OF TRUTH for mechanically enforceable ViewSet
conventions. The committed nextseek-viewset skill, hermetic tests, and the
SchemaGenerator guard import constants from here.

Exit codes: 0 clean, 1 violations, 2 usage/parse error.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

DESCRIPTION_FILES: tuple[str, ...] = (
    "nextseek_api/endpoint_descriptions.py",
    "nextseek_api/assistant/descriptions_evaluator.py",
    "nextseek_api/assistant/descriptions_cc.py",
)

# ViewSet modules scanned for @extend_schema(examples=...) and description=...
EXTEND_SCHEMA_SCAN_PATHS: tuple[str, ...] = (
    "nextseek_api/services",
    "nextseek_api/views.py",
    "nextseek_api/batch_upload/views.py",
)

# assistant.py is excluded from OpenAPI (dmac/openapi_hooks.py); skip AST scan there.
EXTEND_SCHEMA_SKIP_FILES: frozenset[str] = frozenset(
    {
        "nextseek_api/services/assistant.py",
    }
)

REQUIRED_DESC_HEADINGS: tuple[str, ...] = (
    "SUMMARY",
    "USE WHEN",
    "ACCEPTS",
    "RETURNS",
    "TRIGGER PHRASES",
    "EXAMPLES",
)

OPTIONAL_DESC_HEADINGS: frozenset[str] = frozenset({"DO NOT USE WHEN"})

HEADING_RE = re.compile(r"\*\*([^*]+):\*\*")
EXAMPLES_BULLET_RE = re.compile(r"^\s*-\s+", re.MULTILINE)


@dataclass(frozen=True)
class GrandfatherOp:
    """Single grandfather entry for AST and/or SchemaGenerator example guards."""

    rel_path: Optional[str] = None
    func_name: Optional[str] = None
    operation_ids: tuple[str, ...] = ()
    ast_missing_examples: bool = False
    inline_description: bool = False


# SoT for legacy ops missing OpenApiExample lists and/or inline descriptions.
# Do not extend — fix the ViewSet instead.
GRANDFATHER_OPS: tuple[GrandfatherOp, ...] = (
    GrandfatherOp(
        rel_path="nextseek_api/batch_upload/views.py",
        func_name="cancel",
        operation_ids=("batch_upload_cancel_destroy",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/batch_upload/views.py",
        func_name="job_status",
        operation_ids=("batch_upload_status_retrieve",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/batch_upload/views.py",
        func_name="list",
        operation_ids=("batch_upload_list",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/batch_upload/views.py",
        func_name="start",
        operation_ids=("batch_upload_start_create",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/batch_upload/views.py",
        func_name="summary",
        operation_ids=("batch_upload_summary_retrieve",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/batch_upload/views.py",
        func_name="validate",
        operation_ids=("batch_upload_validate_create",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/services/evaluator.py",
        func_name="retry_context_by_bundle",
        operation_ids=("Evaluator: Retry Context by Bundle",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/services/evaluator.py",
        func_name="retry_context_by_task",
        operation_ids=("Evaluator: Retry Context by Task",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/services/evaluator.py",
        func_name="retry_execute",
        operation_ids=("Evaluator: Execute Retry",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/services/evaluator.py",
        func_name="runs_list",
        operation_ids=("Evaluator: List Runs",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/services/sample_types.py",
        func_name="create",
        operation_ids=("Create a SampleType",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/services/sample_types.py",
        func_name="partial_update",
        operation_ids=("Update a SampleType",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/services/sample_types.py",
        func_name="retrieve",
        operation_ids=("Fetch a SampleType",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/services/samples.py",
        func_name="destroy",
        operation_ids=("Delete a Sample",),
        ast_missing_examples=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/views.py",
        func_name="download",
        operation_ids=("Download NHP Data",),
        ast_missing_examples=True,
        inline_description=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/views.py",
        func_name="events",
        inline_description=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/views.py",
        func_name="info",
        inline_description=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/views.py",
        func_name="retrieve_samples",
        inline_description=True,
    ),
    GrandfatherOp(
        rel_path="nextseek_api/views.py",
        func_name="timeline",
        inline_description=True,
    ),
    GrandfatherOp(operation_ids=("cc_assistant_artifacts_download_retrieve",)),
    GrandfatherOp(operation_ids=("cc_assistant_transcript_retrieve",)),
    GrandfatherOp(operation_ids=("cc_assistant_upload_create",)),
    GrandfatherOp(operation_ids=("cc_assistant_upload_list_retrieve",)),
    GrandfatherOp(operation_ids=("cc_assistant_upload_status_retrieve",)),
)


def _derive_grandfather_allowlists() -> tuple[
    frozenset[tuple[str, str]],
    frozenset[tuple[str, str]],
    frozenset[str],
]:
    ast_examples: set[tuple[str, str]] = set()
    inline: set[tuple[str, str]] = set()
    schema_ops: set[str] = set()
    for entry in GRANDFATHER_OPS:
        schema_ops.update(entry.operation_ids)
        if entry.rel_path is None or entry.func_name is None:
            continue
        key = (entry.rel_path, entry.func_name)
        if entry.ast_missing_examples:
            ast_examples.add(key)
        if entry.inline_description:
            inline.add(key)
    return frozenset(ast_examples), frozenset(inline), frozenset(schema_ops)


(
    EXTEND_SCHEMA_EXAMPLES_ALLOWLIST,
    INLINE_DESCRIPTION_ALLOWLIST,
    SCHEMA_EXAMPLES_OPERATION_ID_ALLOWLIST,
) = _derive_grandfather_allowlists()


@dataclass(frozen=True)
class Violation:
    kind: str
    location: str
    message: str


def _heading_positions(text: str) -> List[Tuple[str, int, int]]:
    """Return (heading, start, end) for each **HEADING:** block."""
    matches = list(HEADING_RE.finditer(text))
    blocks: List[Tuple[str, int, int]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append((match.group(1).strip(), start, end))
    return blocks


def validate_desc_text(text: str, *, location: str = "<description>") -> List[Violation]:
    """Validate one endpoint description constant body."""
    violations: List[Violation] = []
    blocks = _heading_positions(text)
    if not blocks:
        return [
            Violation(
                "description",
                location,
                "no **HEADING:** sections found",
            )
        ]

    headings = [name for name, _, _ in blocks]
    cursor = 0

    def _require(name: str) -> bool:
        nonlocal cursor
        if cursor >= len(headings):
            violations.append(
                Violation("description", location, f"missing required heading {name!r}")
            )
            return False
        if headings[cursor] != name:
            violations.append(
                Violation(
                    "description",
                    location,
                    f"expected heading {name!r}, got {headings[cursor]!r} "
                    f"(headings seen: {headings})",
                )
            )
            return False
        body = text[blocks[cursor][1] : blocks[cursor][2]].strip()
        if not body:
            violations.append(
                Violation("description", location, f"section {name!r} is empty")
            )
        cursor += 1
        return True

    if not _require("SUMMARY"):
        return violations
    if not _require("USE WHEN"):
        return violations

    if cursor < len(headings) and headings[cursor] == "DO NOT USE WHEN":
        body = text[blocks[cursor][1] : blocks[cursor][2]].strip()
        if not body:
            violations.append(
                Violation("description", location, "DO NOT USE WHEN section is empty")
            )
        cursor += 1

    if not _require("ACCEPTS"):
        return violations
    if not _require("RETURNS"):
        return violations

    # Optional extra **HEADING:** blocks (FILE SIZE LIMIT, ERROR CODES, PROGRESS, NOTE, …)
    while cursor < len(headings) and headings[cursor] not in ("TRIGGER PHRASES", "EXAMPLES"):
        cursor += 1

    if not _require("TRIGGER PHRASES"):
        return violations

    if cursor >= len(headings) or headings[cursor] != "EXAMPLES":
        violations.append(
            Violation(
                "description",
                location,
                f"expected heading 'EXAMPLES', got {headings[cursor] if cursor < len(headings) else 'end'} "
                f"(headings seen: {headings})",
            )
        )
        return violations

    examples_body = text[blocks[cursor][1] : blocks[cursor][2]].strip()
    if not examples_body:
        violations.append(
            Violation("description", location, "section 'EXAMPLES' is empty")
        )
    elif not EXAMPLES_BULLET_RE.search(examples_body):
        violations.append(
            Violation(
                "description",
                location,
                "EXAMPLES section must contain at least one markdown bullet (- ...)",
            )
        )

    return violations


def _iter_desc_constants(path: Path) -> Iterator[Tuple[str, str]]:
    """Yield (constant_name, string_value) from a descriptions module."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or not target.id.endswith("_DESC"):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                yield target.id, value.value
            elif isinstance(value, (ast.Tuple, ast.JoinedStr)):
                try:
                    compiled = ast.literal_eval(value) if isinstance(value, ast.Tuple) else None
                except (ValueError, SyntaxError):
                    compiled = None
                if isinstance(compiled, str):
                    yield target.id, compiled
                else:
                    parts: List[str] = []
                    for piece in ast.walk(value):
                        if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                            parts.append(piece.value)
                    if parts:
                        yield target.id, "".join(parts)


def validate_description_files(repo_root: Path = REPO_ROOT) -> List[Violation]:
    violations: List[Violation] = []
    for rel in DESCRIPTION_FILES:
        path = repo_root / rel
        if not path.is_file():
            violations.append(Violation("description", rel, "description file missing"))
            continue
        for const_name, text in _iter_desc_constants(path):
            loc = f"{rel}:{const_name}"
            violations.extend(validate_desc_text(text, location=loc))
    return violations


def _extend_schema_keyword(call: ast.Call, name: str) -> Optional[ast.AST]:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _examples_nonempty(node: Optional[ast.AST]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.List):
        return len(node.elts) > 0
    return True


def _description_is_constant(node: Optional[ast.AST]) -> bool:
    if node is None:
        return True
    return isinstance(node, ast.Name) and node.id.endswith("_DESC")


def _iter_extend_schema_files(repo_root: Path) -> Iterator[Path]:
    for rel in EXTEND_SCHEMA_SCAN_PATHS:
        path = repo_root / rel
        if path.is_file():
            if str(path.relative_to(repo_root)).replace("\\", "/") not in EXTEND_SCHEMA_SKIP_FILES:
                yield path
        elif path.is_dir():
            for fp in sorted(path.glob("*.py")):
                rel_path = str(fp.relative_to(repo_root)).replace("\\", "/")
                if rel_path in EXTEND_SCHEMA_SKIP_FILES:
                    continue
                yield fp


def validate_extend_schema_decorators(repo_root: Path = REPO_ROOT) -> List[Violation]:
    violations: List[Violation] = []
    for fp in _iter_extend_schema_files(repo_root):
        rel_path = str(fp.relative_to(repo_root)).replace("\\", "/")
        source = fp.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(fp))
        except SyntaxError as exc:
            violations.append(
                Violation("parse", rel_path, f"syntax error: {exc.msg}")
            )
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id == "extend_schema"
                ):
                    continue
                key = (rel_path, node.name)
                examples = _extend_schema_keyword(dec, "examples")
                if not _examples_nonempty(examples) and key not in EXTEND_SCHEMA_EXAMPLES_ALLOWLIST:
                    violations.append(
                        Violation(
                            "extend_schema",
                            f"{rel_path}:{node.name}",
                            "missing or empty examples= on @extend_schema "
                            "(add OpenApiExample entries or get an explicit grandfather entry)",
                        )
                    )
                description = _extend_schema_keyword(dec, "description")
                if (
                    description is not None
                    and not _description_is_constant(description)
                    and key not in INLINE_DESCRIPTION_ALLOWLIST
                ):
                    violations.append(
                        Violation(
                            "extend_schema",
                            f"{rel_path}:{node.name}",
                            "description= must reference a *_DESC constant, not an inline string",
                        )
                    )
    return violations


def validate_repo(repo_root: Path = REPO_ROOT) -> List[Violation]:
    violations: List[Violation] = []
    violations.extend(validate_description_files(repo_root))
    violations.extend(validate_extend_schema_decorators(repo_root))
    return violations


def _format_violations(violations: Sequence[Violation]) -> str:
    lines = []
    for v in violations:
        lines.append(f"[{v.kind}] {v.location}: {v.message}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate NExtSEEK ViewSet endpoint descriptions and extend_schema conventions."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root (default: parent of scripts/)",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if not (repo_root / "nextseek_api").is_dir():
        print(f"error: {repo_root} does not look like the NExtSEEK repo root", file=sys.stderr)
        return 2

    violations = validate_repo(repo_root)
    if violations:
        print(_format_violations(violations), file=sys.stderr)
        return 1
    print("OK: ViewSet conventions validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
