"""The env registry: one declaration per env var and Django setting (#103).

`startup/env_registry.toml` holds one table per key. The templates, examples, compose
interpolation and code defaults are checked against it by
`startup/tests/test_env_registry_drift.py`. Standard library only.

Fields of a table (all but `files` optional):
- `files`: where the value is set, any of FILES. `local_settings` is a Django setting in
  `dmac/local_settings.py`, not env; `image` is set by the Dockerfile, compose
  `environment:` or the entrypoint; `internal` is set by code for a child process or by a
  test harness, never by an operator.
- `default`: the code's default, as a string. Never a real value for a `secret`.
- `secret`: the value is a credential and is never printed.
- `required`: the box does not work without it; it must appear in each of its files'
  template and example.
- `alias_of`: the code treats this key and the named one as one value.
- `forbidden`: must not be set in a box env file.
- `dynamic`: read through a name built at run time, which the drift scanner cannot see;
  `doc` names the reading file.
- `doc`: what it does.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "env_registry.toml"

FILES = frozenset({
    "nextseek.env", "db.env", "root.env", "proxy-secret.env", "laya.env",
    "local_settings", "image", "internal",
})

# Values that hold no real credential: a template placeholder or nothing.
PLACEHOLDERS = frozenset({"", "SET_IN_LOCAL_ENV", "CHANGE_ME"})

_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True)
class Entry:
    files: tuple[str, ...]
    default: str | None = None
    secret: bool = False
    required: bool = False
    alias_of: str | None = None
    forbidden: bool = False
    dynamic: bool = False
    doc: str = ""


_FIELDS = {f.name for f in fields(Entry)}


def _entry(key: str, table: dict) -> Entry:
    unknown = set(table) - _FIELDS
    if unknown:
        raise ValueError(f"{key}: unknown field {sorted(unknown)}")
    files = table.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        raise ValueError(f"{key}: files must be a non-empty list of names")
    if set(files) - FILES:
        raise ValueError(f"{key}: unknown files {sorted(set(files) - FILES)}")
    for name in ("default", "alias_of", "doc"):
        if name in table and not isinstance(table[name], str):
            raise ValueError(f"{key}: {name} must be a string")
    for name in ("secret", "required", "forbidden", "dynamic"):
        if name in table and not isinstance(table[name], bool):
            raise ValueError(f"{key}: {name} must be true or false")
    entry = Entry(**{**table, "files": tuple(files)})
    if entry.secret and entry.default is not None and entry.default not in PLACEHOLDERS:
        raise ValueError(f"{key}: a secret has no default but a placeholder")
    if entry.forbidden and entry.required:
        raise ValueError(f"{key}: a forbidden key cannot be required")
    return entry


def load(path: Path = REGISTRY_PATH) -> dict[str, Entry]:
    """Return {key: Entry}; raise ValueError on any entry that breaks the schema."""
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    entries = {}
    for key, table in raw.items():
        if not _KEY_RE.match(key) or not isinstance(table, dict):
            raise ValueError(f"{key}: a key is a table named in capitals")
        entries[key] = _entry(key, table)
    for key, entry in entries.items():
        if entry.alias_of is not None and entry.alias_of not in entries:
            raise ValueError(f"{key}: alias_of {entry.alias_of} has no entry")
    return entries
