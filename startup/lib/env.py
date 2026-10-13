"""Read/write .env files while preserving comments and key order."""
from __future__ import annotations

import re
from pathlib import Path

# Match KEY=VALUE with optional quoting on the value side.
_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _unescape(inner: str) -> str:
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


def _closing_quote(rest: str) -> int | None:
    """Index of the quote that closes rest[0], skipping \\-escapes inside double quotes."""
    i = 1
    while i < len(rest):
        if rest[0] == '"' and rest[i] == "\\":
            i += 2
        elif rest[i] == rest[0]:
            return i
        else:
            i += 1
    return None


def parse_line(line: str) -> tuple[str, str, str] | None:
    """(key, value, quote) for one KEY=VALUE line, read the way compose reads an env_file.

    The one parser for the rendered env files (#103): read_env, install's
    read-back and its keep-what-is-on-disk render all go through it.
    ``quote`` is the value's quote character, or "" when it is unquoted.
    Single quotes are literal; double quotes undo ``\\"`` and ``\\\\``; text after
    the closing quote, and an unquoted value's `` #`` comment, are dropped.
    ``$`` is left as written: compose's interpolation is not resolved here.
    Comments, blanks and anything that is not an assignment give None.
    """
    match = _LINE_RE.match(line.strip())
    if not match:
        return None
    key, rest = match.groups()
    if rest[:1] in ("'", '"'):
        end = _closing_quote(rest)
        if end is not None:
            inner = rest[1:end]
            return key, (_unescape(inner) if rest[0] == '"' else inner), rest[0]
    return key, re.split(r"\s+#", rest, maxsplit=1)[0].rstrip(), ""


def read_env_text(text: str) -> dict[str, str]:
    """Return {key: value} from env-file text; the last line for a key wins, as in compose."""
    return {p[0]: p[1] for p in map(parse_line, text.splitlines()) if p}


def read_env(path: Path) -> dict[str, str]:
    """Return {key: value} from the env file. Comments and blanks are dropped."""
    return read_env_text(path.read_text()) if path.exists() else {}


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
