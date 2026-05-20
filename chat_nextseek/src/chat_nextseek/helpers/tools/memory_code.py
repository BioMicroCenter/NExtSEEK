"""Memory-code sandbox and JSON data profiling helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import ast
import json
import re
import signal
from typing import Any


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _compact_json_value(value: Any, *, max_string: int = 160, max_keys: int = 40, max_items: int = 5) -> Any:
    """Trim a JSON-like value for prompts while preserving shape and representative values."""
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "...[truncated]"
    if isinstance(value, dict):
        return {
            str(k): _compact_json_value(v, max_string=max_string, max_keys=max_keys, max_items=max_items)
            for k, v in list(value.items())[:max_keys]
        }
    if isinstance(value, list):
        return [_compact_json_value(v, max_string=max_string, max_keys=max_keys, max_items=max_items) for v in value[:max_items]]
    return value


def _merge_skeletons(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Merge two compact skeleton nodes produced by `_build_skeleton_node`."""
    a_types = set(a.get("types") or ([a.get("type")] if a.get("type") else []))
    b_types = set(b.get("types") or ([b.get("type")] if b.get("type") else []))
    merged: dict[str, Any] = {"types": sorted(t for t in (a_types | b_types) if t)}

    examples = []
    for ex in (a.get("examples") or []) + (b.get("examples") or []):
        if ex not in examples and len(examples) < 3:
            examples.append(ex)
    if examples:
        merged["examples"] = examples

    if "keys" in a or "keys" in b:
        keys: dict[str, Any] = {}
        for source in (a.get("keys") or {}, b.get("keys") or {}):
            for key, node in source.items():
                keys[key] = _merge_skeletons(keys[key], node) if key in keys else node
        merged["keys"] = keys

    if "items" in a or "items" in b:
        if "items" in a and "items" in b:
            merged["items"] = _merge_skeletons(a["items"], b["items"])
        else:
            merged["items"] = a.get("items") or b.get("items")

    if "length" in a or "length" in b:
        merged["length"] = max(a.get("length") or 0, b.get("length") or 0)

    return merged


def _build_skeleton_node(
    value: Any,
    *,
    depth: int,
    max_depth: int,
    sample_limit: int,
    max_keys: int,
) -> dict[str, Any]:
    value_type = _json_type_name(value)
    if depth >= max_depth:
        return {"type": value_type}

    if isinstance(value, dict):
        items = list(value.items())
        node: dict[str, Any] = {
            "type": "object",
            "keys": {
                str(k): _build_skeleton_node(
                    v,
                    depth=depth + 1,
                    max_depth=max_depth,
                    sample_limit=sample_limit,
                    max_keys=max_keys,
                )
                for k, v in items[:max_keys]
            },
        }
        if len(items) > max_keys:
            node["truncated_keys"] = len(items) - max_keys
        return node

    if isinstance(value, list):
        sampled = value[:sample_limit]
        item_node: dict[str, Any] | None = None
        for item in sampled:
            current = _build_skeleton_node(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                sample_limit=sample_limit,
                max_keys=max_keys,
            )
            item_node = current if item_node is None else _merge_skeletons(item_node, current)
        return {"type": "array", "length": len(value), "items": item_node or {"type": "unknown"}}

    node = {"type": value_type}
    if value is not None:
        node["examples"] = [_compact_json_value(value)]
    return node


def _find_record_arrays(value: Any, *, path: str = "$", sample_limit: int = 5, max_arrays: int = 12) -> list[dict[str, Any]]:
    """Find likely result-row arrays: JSON lists whose first non-null item is an object."""
    found: list[dict[str, Any]] = []

    def visit(obj: Any, current_path: str) -> None:
        if len(found) >= max_arrays:
            return
        if isinstance(obj, list):
            first_dict = next((item for item in obj if isinstance(item, dict)), None)
            if first_dict is not None:
                found.append(
                    {
                        "path": current_path,
                        "length": len(obj),
                        "examples": [_compact_json_value(item) for item in obj[:sample_limit] if isinstance(item, dict)],
                    }
                )
            for i, item in enumerate(obj[:sample_limit]):
                visit(item, f"{current_path}[{i}]")
        elif isinstance(obj, dict):
            for key, child in obj.items():
                visit(child, f"{current_path}.{key}")

    visit(value, path)
    return found


def build_memory_data_profile(data: Any, *, sample_limit: int = 5) -> dict[str, Any]:
    """
    Build a generic JSON skeleton and representative examples for memory-code prompts.
    This is schema-agnostic: it summarizes object/list structure and highlights any list of dicts.

    Now also includes a `field_index` summarizing categorical fields (small fixed value sets)
    so memory_coder can prefer exact equality on those fields rather than substring scans.
    """
    return {
        "root_type": _json_type_name(data),
        "skeleton": _build_skeleton_node(
            data,
            depth=0,
            max_depth=8,
            sample_limit=sample_limit,
            max_keys=80,
        ),
        "record_arrays": _find_record_arrays(data, sample_limit=sample_limit),
        "field_index": _build_field_index(data),
    }


def _build_field_index(data: Any, *, max_rows: int = 200, max_distinct: int = 15) -> dict[str, Any]:
    """Walk the first N rows of any record-array under `data` and categorize each
    field by whether it's categorical (few distinct values) or free-text. Used by
    the memory_coder prompt to choose exact-match over substring-scan.
    """
    if not isinstance(data, dict):
        return {}
    rows: list[Any] = []
    d = data.get("data")
    if isinstance(d, dict):
        for key in ("rows", "nodes", "data"):
            val = d.get(key)
            if isinstance(val, list):
                rows = val
                break
    elif isinstance(d, list):
        rows = d
    if not rows:
        return {}

    import re as _re
    tag_pat = _re.compile(r"<[^>]+>")

    def _clean(v: Any) -> str:
        if v is None:
            return ""
        return tag_pat.sub("", str(v)).strip()

    field_values: dict[str, set] = {}
    field_seen: dict[str, int] = {}
    nested_meta_fields: dict[str, set] = {}

    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if not isinstance(k, str) or k.startswith("_"):
                continue
            field_seen[k] = field_seen.get(k, 0) + 1
            cleaned = _clean(v)
            if cleaned:
                field_values.setdefault(k, set()).add(cleaned[:80])
        md = row.get("json_metadata")
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except Exception:
                md = None
        if isinstance(md, dict):
            for mk, mv in md.items():
                if not isinstance(mk, str) or mk.startswith("_"):
                    continue
                cleaned = _clean(mv)
                if cleaned:
                    nested_meta_fields.setdefault(mk, set()).add(cleaned[:80])

    def _classify(name: str, values: set, populated: int) -> dict[str, Any]:
        examples = sorted(list(values))[:6]
        n_distinct = len(values)
        kind = "categorical" if n_distinct <= max_distinct else "free_text"
        return {
            "kind": kind,
            "n_distinct": n_distinct,
            "n_populated": populated,
            "examples": examples,
        }

    top_index = {
        name: _classify(name, vals, field_seen.get(name, 0))
        for name, vals in field_values.items()
    }
    meta_index = {
        name: _classify(name, vals, len(vals))
        for name, vals in nested_meta_fields.items()
    }
    return {
        "row_top_level": top_index,
        "row_json_metadata": meta_index,
        "rows_scanned": min(len(rows), max_rows),
        "note": (
            "Prefer json_metadata fields for biology filters (Lab, Sex, Treatment1, Tissue, etc). "
            "Use exact `(meta.get('FIELD') or '').strip().upper() == VALUE.upper()` — never substring."
        ),
    }


class MemoryCodeSafetyError(ValueError):
    """Raised when generated memory code uses syntax outside the allowed subset."""


class MemoryCodeTimeoutError(TimeoutError):
    """Raised when generated memory code exceeds the execution timeout."""


_MEMORY_ALLOWED_BUILTINS = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "sorted": sorted,
    "sum": sum,
    "min": min,
    "max": max,
    "any": any,
    "all": all,
    "enumerate": enumerate,
    "range": range,
    "isinstance": isinstance,
    # Exception classes — exposed so `try/except <Exc>: ...` patterns work
    # (the coder needs these to safely parse optional/malformed metadata).
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "AttributeError": AttributeError,
}

_MEMORY_ALLOWED_METHODS = {
    "get",
    "items",
    "keys",
    "values",
    "append",
    "extend",
    "add",
    "update",
    "lower",
    "upper",
    "strip",
    "split",
    "replace",
    "startswith",
    "endswith",
    "isdigit",
    "join",
}

_MEMORY_ALLOWED_RE_METHODS = {"search", "match", "fullmatch", "findall", "sub"}
_MEMORY_ALLOWED_JSON_METHODS = {"loads", "dumps"}
_MEMORY_ALLOWED_RUNTIME_HELPERS = {"strip_html"}
_MEMORY_BLOCKED_NAMES = {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "dir", "help", "input"}


def _validate_memory_code(tree: ast.AST) -> None:
    # Allow `try/except` — natural for safely parsing optional / malformed metadata
    # fields (e.g. json_metadata as string vs dict). Body of the try and handlers
    # are walked normally by the rest of this validator, so dunder access, blocked
    # names, etc. inside a try still get caught.
    blocked_nodes = (
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Lambda,
        ast.With,
        ast.AsyncWith,
        ast.Delete,
        ast.Global,
        ast.Nonlocal,
        ast.Raise,
        ast.While,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
    )
    for node in ast.walk(tree):
        if isinstance(node, blocked_nodes):
            raise MemoryCodeSafetyError(f"Disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id in _MEMORY_BLOCKED_NAMES:
            raise MemoryCodeSafetyError(f"Disallowed name: {node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise MemoryCodeSafetyError("Dunder attribute access is not allowed")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in _MEMORY_ALLOWED_RUNTIME_HELPERS:
                    continue
                if func.id not in _MEMORY_ALLOWED_BUILTINS:
                    raise MemoryCodeSafetyError(f"Disallowed function call: {func.id}")
            elif isinstance(func, ast.Attribute):
                if func.attr.startswith("__"):
                    raise MemoryCodeSafetyError("Dunder method calls are not allowed")
                if isinstance(func.value, ast.Name) and func.value.id == "re":
                    if func.attr not in _MEMORY_ALLOWED_RE_METHODS:
                        raise MemoryCodeSafetyError(f"Disallowed re method: {func.attr}")
                elif isinstance(func.value, ast.Name) and func.value.id == "json":
                    if func.attr not in _MEMORY_ALLOWED_JSON_METHODS:
                        raise MemoryCodeSafetyError(f"Disallowed json method: {func.attr}")
                elif func.attr not in _MEMORY_ALLOWED_METHODS:
                    raise MemoryCodeSafetyError(f"Disallowed method call: {func.attr}")
            else:
                raise MemoryCodeSafetyError("Dynamic calls are not allowed")

    if not any(isinstance(node, ast.Name) and node.id == "result" and isinstance(node.ctx, ast.Store) for node in ast.walk(tree)):
        raise MemoryCodeSafetyError("Code must assign the final answer data to `result`")


def execute_memory_code(code: str, data: Any, *, timeout_seconds: int = 3) -> dict[str, Any]:
    """
    Execute LLM-generated memory extraction code against local JSON with a narrow Python subset.
    The code must assign JSON-serializable output to `result`.
    """
    tree = ast.parse(code, mode="exec")
    _validate_memory_code(tree)

    rows: list[Any] = []
    if isinstance(data, dict):
        current: Any = data
        for key in ("data", "rows"):
            current = current.get(key) if isinstance(current, dict) else None
        if isinstance(current, list):
            rows = current

    def _timeout_handler(signum, frame):
        raise MemoryCodeTimeoutError(f"Memory code exceeded {timeout_seconds}s timeout")

    old_handler = None
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_seconds)
    except Exception:
        old_handler = None

    def _strip_html_helper(value: Any) -> str:
        """Strip HTML tags from a value — bound into memory-coder runtime so it
        can clean UID/title/uuid strings that may be wrapped in `<a href=...>` tags."""
        if value is None:
            return ""
        text = str(value)
        return re.sub(r"<[^>]+>", "", text).strip()

    exec_scope: dict[str, Any] = {
        "__builtins__": _MEMORY_ALLOWED_BUILTINS,
        "data": data,
        "rows": rows,
        "re": re,
        "json": json,
        "strip_html": _strip_html_helper,
        "result": {},
    }
    try:
        exec(compile(tree, "<memory_coder>", "exec"), exec_scope)  # noqa: S102
    finally:
        try:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        except Exception:
            pass

    result = exec_scope.get("result", {})
    if not isinstance(result, dict):
        result = {"value": result}
    json.dumps(result, default=str)
    return result
