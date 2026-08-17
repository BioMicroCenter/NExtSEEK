"""Strict stdlib validation for Claude Code plugin identity manifests (Plan 005)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_TOP_LEVEL = frozenset({"name", "version", "description", "author", "keywords"})
ALLOWED_AUTHOR_KEYS = frozenset({"name", "email", "url"})
INVENTORY_LIKE_FIELDS = frozenset(
    {
        "commands",
        "families",
        "inventory",
        "operations",
        "ops",
        "plugins",
        "routes",
        "skills",
        "task_families",
        "tools",
    }
)
_ENUMERATING_DESCRIPTION_RE = re.compile(
    r"\b(entity-extract|parse|plan|query|recall|api-read|api-write|graph|report|"
    r"generate-submission|nextseek-)\b",
    re.IGNORECASE,
)


class PluginIdentityError(ValueError):
    """Raised when a plugin.json identity manifest fails local validation."""


@dataclass(frozen=True)
class PluginIdentity:
    name: str
    version: str
    description: str
    author: dict[str, str]
    keywords: tuple[str, ...]


def validate_plugin_identity(payload: Any) -> PluginIdentity:
    """Accept only supported identity fields; reject inventory-like extensions."""
    if not isinstance(payload, dict):
        raise PluginIdentityError("manifest must be a JSON object")

    keys = set(payload)
    inventory = keys & INVENTORY_LIKE_FIELDS
    if inventory:
        raise PluginIdentityError(
            "inventory-like fields are forbidden: " + ", ".join(sorted(inventory))
        )
    unknown = keys - ALLOWED_TOP_LEVEL
    if unknown:
        raise PluginIdentityError(
            "unknown manifest fields: " + ", ".join(sorted(unknown))
        )

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PluginIdentityError("name must be a non-empty string")

    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise PluginIdentityError("version must be a non-empty string")

    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise PluginIdentityError("description must be a non-empty string")
    if _ENUMERATING_DESCRIPTION_RE.search(description):
        raise PluginIdentityError(
            "description must not enumerate operations, skills, or routes"
        )

    author_raw = payload.get("author")
    if not isinstance(author_raw, dict):
        raise PluginIdentityError("author must be an object")
    author_unknown = set(author_raw) - ALLOWED_AUTHOR_KEYS
    if author_unknown:
        raise PluginIdentityError(
            "unknown author fields: " + ", ".join(sorted(author_unknown))
        )
    author_name = author_raw.get("name")
    if not isinstance(author_name, str) or not author_name.strip():
        raise PluginIdentityError("author.name must be a non-empty string")
    author = {key: value for key, value in author_raw.items() if isinstance(value, str)}

    keywords_raw = payload.get("keywords", [])
    if keywords_raw is None:
        keywords_raw = []
    if not isinstance(keywords_raw, list):
        raise PluginIdentityError("keywords must be an array")
    keywords: list[str] = []
    for item in keywords_raw:
        if not isinstance(item, str) or not item.strip():
            raise PluginIdentityError("keywords entries must be non-empty strings")
        keywords.append(item)

    return PluginIdentity(
        name=name,
        version=version,
        description=description,
        author=author,
        keywords=tuple(keywords),
    )


def load_and_validate_manifest(path: Path) -> PluginIdentity:
    """Load plugin.json from disk and validate the identity schema."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    identity = validate_plugin_identity(payload)
    plugin_dir = path.parent.parent.name
    if identity.name != plugin_dir:
        raise PluginIdentityError(
            f"manifest name must match plugin directory: {identity.name!r} != {plugin_dir!r}"
        )
    return identity
