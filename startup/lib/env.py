"""Read/write .env files while preserving comments and key order."""
from __future__ import annotations

import re
from pathlib import Path

# Match KEY=VALUE with optional quoting on the value side.
_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _unquote(raw: str) -> str:
    raw = raw.rstrip()
    if len(raw) < 2 or raw[0] != raw[-1] or raw[0] not in ("'", '"'):
        return raw
    inner = raw[1:-1]
    if raw[0] != '"':
        return inner
    # Reverse _quote()'s escaping: undo \" and \\ in one left-to-right pass
    # so consecutive backslashes don't get double-handled.
    result: list[str] = []
    i = 0
    while i < len(inner):
        if inner[i] == "\\" and i + 1 < len(inner) and inner[i + 1] in ('\\', '"'):
            result.append(inner[i + 1])
            i += 2
        else:
            result.append(inner[i])
            i += 1
    return "".join(result)


def _quote(value: str) -> str:
    # Env values in this project are passwords, URLs, and paths — never
    # multi-line content. Refuse newlines so the file always round-trips
    # cleanly with a single key per line.
    if "\n" in value or "\r" in value:
        raise ValueError("env values cannot contain newlines or carriage returns")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_env(path: Path) -> dict[str, str]:
    """Return {key: value} from the env file. Comments and blanks are dropped."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if match:
            result[match.group(1)] = _unquote(match.group(2))
    return result


def write_env(path: Path, values: dict[str, str]) -> None:
    """Write a fresh env file from scratch. Loses any existing comments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={_quote(v)}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n")


def update_env(path: Path, updates: dict[str, str]) -> None:
    """Update or append keys in path without destroying comments/blanks.

    Keys already present get replaced in place. Keys not present get
    appended after a single blank line separator (if the file is non-empty).
    """
    if not path.exists():
        write_env(path, updates)
        return

    existing_lines = path.read_text().splitlines()
    seen: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        match = _LINE_RE.match(line)
        if match and match.group(1) in updates:
            key = match.group(1)
            new_lines.append(f"{key}={_quote(updates[key])}")
            seen.add(key)
        else:
            new_lines.append(line)

    to_append = [k for k in updates if k not in seen]
    if to_append:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        for key in to_append:
            new_lines.append(f"{key}={_quote(updates[key])}")

    path.write_text("\n".join(new_lines) + "\n")
