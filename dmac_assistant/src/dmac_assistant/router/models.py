"""Loader for the router's ModelClass -> Bedrock-qualified model-id map.

The map lives at build_context/router_model_class_map.json and is read by the
bridge process at startup. The parsed dict is consumed by exec_cc_turn (T3.1)
to construct the `claude --model <id>` argv on the CC route.

DD-08 (locked): model IDs NEVER appear in BAML, Python literals, or docker
command templates - only in this file. This is the single edit point when the
model knowledge cutoff advances.

Env override: DMAC_ROUTER_MODEL_CLASS_MAP_FILE.

Failures raise ConfigError so the bridge fails fast at boot
(per locked spec section "Error handling" line 538).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dmac_assistant.config import ConfigError
from dmac_assistant.router.baml_client.types import ModelClass


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_PATH = _REPO_ROOT / "build_context" / "router_model_class_map.json"
_ENV_VAR = "DMAC_ROUTER_MODEL_CLASS_MAP_FILE"
_BEDROCK_ID_RE = re.compile(r"^us\.anthropic\.[a-z0-9.\-:]+$")

_cache: dict[str, str] | None = None


def _resolve_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    raw = os.environ.get(_ENV_VAR)
    if raw is not None and raw.strip():
        return Path(raw)
    return _DEFAULT_PATH


def _validate_map(raw_map: dict[str, str], source: Path) -> dict[str, str]:
    for member in ModelClass:
        key = member.name.lower()
        value = raw_map.get(key)
        if value is None or not isinstance(value, str) or not value:
            raise ConfigError(
                f"router_model_class_map missing or empty entry for {key!r}: {source}"
            )
        if not _BEDROCK_ID_RE.match(value):
            raise ConfigError(
                f"router_model_class_map entry for {key!r} is not a "
                f"Bedrock-qualified id (must match {_BEDROCK_ID_RE.pattern!r}): "
                f"{value!r} at {source}"
            )
    return raw_map


def load_model_class_map(path: Path | None = None) -> dict[str, str]:
    """Read, validate, and return the model-class map.

    Precedence: explicit `path` argument > DMAC_ROUTER_MODEL_CLASS_MAP_FILE env >
    repo-root default `build_context/router_model_class_map.json`.

    Raises ConfigError on any failure. Does not update the module-level cache
    used by resolve(); pass path= when injecting a custom map in tests.
    """
    resolved = _resolve_path(path)
    if not resolved.is_file():
        raise ConfigError(f"router_model_class_map file does not exist: {resolved}")
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"router_model_class_map file is not readable: {resolved}"
        ) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"router_model_class_map file is not valid JSON: {resolved} ({exc})"
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"router_model_class_map file must decode to an object: {resolved}"
        )
    return _validate_map(parsed, resolved)


def _ensure_cache() -> dict[str, str]:
    global _cache
    if _cache is None:
        _cache = load_model_class_map()
    return _cache


def resolve(model_class: ModelClass) -> str:
    """Return the Bedrock-qualified model id for a ModelClass enum value."""
    return _ensure_cache()[model_class.name.lower()]


def resolve_cc_model() -> str:
    """Return the fixed Bedrock-qualified model id for every container_cc turn.

    The router no longer selects a model class for the CC route (OI-5): CC
    always runs on the single auto-mode-capable Opus tier. The id lives ONLY
    in build_context/router_model_class_map.json (DD-08) under the "opus"
    key; this reads it rather than hardcoding the literal.
    """
    return _ensure_cache()["opus"]
