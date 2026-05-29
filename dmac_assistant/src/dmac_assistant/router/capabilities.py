"""Loader for the router's capability registry.

The registry lives at build_context/route_capabilities.json and is read by the
bridge process on the host at startup. The parsed objects are passed to the
BAML RouteQuery function via RouterInput.routes. The registry is NOT mounted
into any container.

Env override: DMAC_ROUTE_CAPABILITIES_FILE.

Failures (missing file, invalid JSON, schema validation) raise ConfigError so
the bridge fails fast at boot (per locked spec § "Error handling" line 537).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from dmac_assistant.config import ConfigError
from dmac_assistant.router.baml_client.types import RouteCapability


# capabilities.py lives at src/dmac_assistant/router/capabilities.py.
# parent x4 -> repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_PATH = _REPO_ROOT / "build_context" / "route_capabilities.json"
_ENV_VAR = "DMAC_ROUTE_CAPABILITIES_FILE"


def _resolve_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    raw = os.environ.get(_ENV_VAR)
    if raw is not None and raw.strip():
        return Path(raw)
    return _DEFAULT_PATH


def load_capabilities(path: Path | None = None) -> list[RouteCapability]:
    """Read and validate the route capability registry.

    Precedence: explicit `path` argument > DMAC_ROUTE_CAPABILITIES_FILE env >
    repo-root default `build_context/route_capabilities.json`.

    Raises ConfigError on any of: missing file, unreadable file, invalid JSON,
    top-level not a dict, missing or non-list `routes`, empty `routes`, or any
    item failing RouteCapability validation.
    """
    resolved = _resolve_path(path)
    if not resolved.is_file():
        raise ConfigError(f"route_capabilities file does not exist: {resolved}")
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"route_capabilities file is not readable: {resolved}"
        ) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"route_capabilities file is not valid JSON: {resolved} ({exc})"
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"route_capabilities file must decode to an object: {resolved}"
        )
    if "routes" not in parsed:
        raise ConfigError(
            f"route_capabilities file missing 'routes' key: {resolved}"
        )
    routes_raw = parsed["routes"]
    if not isinstance(routes_raw, list):
        raise ConfigError(
            f"route_capabilities 'routes' must be a list: {resolved}"
        )
    if not routes_raw:
        raise ConfigError(
            f"route_capabilities 'routes' must not be empty: {resolved}"
        )
    try:
        return [RouteCapability.model_validate(item) for item in routes_raw]
    except ValidationError as exc:
        raise ConfigError(
            f"route_capabilities item failed validation: {resolved} ({exc})"
        ) from exc
