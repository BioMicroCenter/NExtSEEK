"""Discover executable nextseek plugin bin ops from the on-disk inventory (BIN-2).

Catalog modules bind ``BIN_OPS`` via ``discover_ops("query")`` rather than
hardcoded name lists so new shims are picked up automatically.
"""
from __future__ import annotations

import os
from pathlib import Path

_QUERY_RUNNER = "_nextseek_runner.py"
_BATCH_RUNNER = "_batch_upload_runner.py"
_OP_PREFIX = "nextseek-"

VIEWSET_SUFFIXES = frozenset({"query", "plan", "recall"})
PUBLISHED_PATH_SUFFIXES = frozenset({"report", "generate-submission"})


def default_bin_dir() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "docker/cc-runtime/build_context/plugins/nextseek/bin"
    )


def _runner_for_shim(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if _QUERY_RUNNER in text:
        return _QUERY_RUNNER
    if _BATCH_RUNNER in text:
        return _BATCH_RUNNER
    return None


def discover_ops(family: str | None = None, *, bin_dir: Path | None = None) -> tuple[str, ...]:
    """Return sorted executable ``nextseek-*`` bin names under ``bin_dir``.

    ``family`` filters by shim runner reference: ``"query"`` →
    ``_nextseek_runner.py``, ``"batch-upload"`` → ``_batch_upload_runner.py``,
    ``None`` → all families.
    """
    root = bin_dir or default_bin_dir()
    if not root.is_dir():
        return ()
    ops: list[str] = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or not path.name.startswith(_OP_PREFIX):
            continue
        if not os.access(path, os.X_OK):
            continue
        runner = _runner_for_shim(path)
        if runner is None:
            continue
        if family == "query" and runner != _QUERY_RUNNER:
            continue
        if family == "batch-upload" and runner != _BATCH_RUNNER:
            continue
        ops.append(path.name)
    return tuple(ops)


def op_suffix(op: str) -> str:
    if not op.startswith(_OP_PREFIX):
        raise ValueError(f"not a bin op: {op!r}")
    return op[len(_OP_PREFIX) :]


def wire_op_name(op: str) -> str:
    suf = op_suffix(op)
    if suf == "entity-extract":
        return "entity"
    return suf


def is_viewset_op(op: str) -> bool:
    return op_suffix(op) in VIEWSET_SUFFIXES


def transport_for_op(op: str) -> str:
    return "viewset" if is_viewset_op(op) else "sidecar"


def assistant_endpoint_for_op(op: str) -> str:
    suf = op_suffix(op)
    if suf in ("query", "plan"):
        return "/nextseek_api/assistant/query/async/"
    if suf == "recall":
        return "/nextseek_api/assistant/sessions/"
    return f"/nextseek_api/assistant/{wire_op_name(op)}/"


def excerpt_allowed_fields_for_op(op: str) -> frozenset[str]:
    suf = op_suffix(op)
    if suf in ("query", "plan"):
        return frozenset({"reply", "debug", "bundle_id"})
    if suf == "recall":
        return frozenset({"turn_id", "bundle_id", "total", "row_count", "columns", "path"})
    base = frozenset({"op", "result"})
    if suf in PUBLISHED_PATH_SUFFIXES:
        return base | frozenset({"download"})
    return base


def published_path_ops(ops: tuple[str, ...] | None = None) -> tuple[str, ...]:
    pool = ops or discover_ops("query")
    return tuple(op for op in pool if op_suffix(op) in PUBLISHED_PATH_SUFFIXES)


def write_gated_op(ops: tuple[str, ...] | None = None) -> str:
    for op in ops or discover_ops("query"):
        if op_suffix(op) == "api-write":
            return op
    raise RuntimeError("no api-write op in inventory")
