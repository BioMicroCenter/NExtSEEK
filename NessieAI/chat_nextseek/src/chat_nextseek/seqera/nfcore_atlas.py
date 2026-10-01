"""Load and validate the curated nf-core RNA atlas.

The atlas holds the judgement that no machine-readable schema carries: which
pipeline to reach for when several accept the same reads, and what to ask a
human when the data genuinely cannot decide.

Structural failures raise — a malformed atlas should fail fast rather than
degrade quietly. Drift warnings only warn.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG

ATLAS_PATH = Path(__file__).resolve().parent.parent / "context" / "nfcore_rna_atlas.json"


class AtlasError(Exception):
    """The atlas file is structurally invalid."""


def load_atlas(path: str | Path | None = None) -> dict[str, Any]:
    """Read the atlas, validate it, and return it.

    Raises AtlasError when an entry lacks a revision or a `versus` key names a
    pipeline that has no entry. Warns on one-sided `versus` pairs and on a
    revision that disagrees with seqera/catalog.py.
    """
    target = Path(path) if path is not None else ATLAS_PATH
    try:
        payload = json.loads(target.read_text())
    except FileNotFoundError as exc:
        raise AtlasError(f"Atlas file not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise AtlasError(f"Atlas file is not valid JSON: {target}: {exc}") from exc

    if not isinstance(payload, dict) or "pipelines" not in payload:
        raise AtlasError(f"Atlas must be an object with a 'pipelines' key: {target}")

    pipelines = payload["pipelines"]
    if not isinstance(pipelines, dict) or not pipelines:
        raise AtlasError("Atlas 'pipelines' must be a non-empty object")

    for key, entry in pipelines.items():
        if not isinstance(entry, dict):
            raise AtlasError(f"Atlas entry {key!r} is not an object")
        if not entry.get("revision"):
            raise AtlasError(f"Atlas entry {key!r} has no revision")
        for neighbour in (entry.get("versus") or {}):
            if neighbour not in pipelines:
                raise AtlasError(
                    f"Atlas entry {key!r} names {neighbour!r} in its versus map, "
                    f"but {neighbour!r} has no entry"
                )

    for key, entry in pipelines.items():
        for neighbour in (entry.get("versus") or {}):
            back = (pipelines[neighbour].get("versus") or {})
            if key not in back:
                warnings.warn(
                    f"one-sided versus pair: {key!r} points at {neighbour!r} "
                    f"but {neighbour!r} does not point back",
                    stacklevel=2,
                )

    for key, entry in pipelines.items():
        catalogued = NFCORE_PIPELINE_CATALOG.get(key)
        if not catalogued:
            continue
        pinned = catalogued.get("default_revision")
        if pinned and pinned != entry["revision"]:
            warnings.warn(
                f"atlas revision drift for {key!r}: atlas says {entry['revision']}, "
                f"seqera/catalog.py default_revision says {pinned}",
                stacklevel=2,
            )

    return payload
