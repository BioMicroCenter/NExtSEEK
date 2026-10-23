"""Load and validate the curated nf-core atlas: the RNA and DNA files, merged.

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

ATLAS_PATH = Path(__file__).resolve().parent / "nfcore_rna_atlas.json"
DNA_ATLAS_PATH = Path(__file__).resolve().parent / "nfcore_dna_atlas.json"
#: Every file the default load merges. A pipeline key may appear in only one of them.
ATLAS_PATHS: tuple[Path, ...] = (ATLAS_PATH, DNA_ATLAS_PATH)


class AtlasError(Exception):
    """The atlas file is structurally invalid."""


def _read(target: Path) -> dict[str, Any]:
    try:
        payload = json.loads(target.read_text())
    except FileNotFoundError as exc:
        raise AtlasError(f"Atlas file not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise AtlasError(f"Atlas file is not valid JSON: {target}: {exc}") from exc
    if not isinstance(payload, dict) or "pipelines" not in payload:
        raise AtlasError(f"Atlas must be an object with a 'pipelines' key: {target}")
    if not isinstance(payload["pipelines"], dict) or not payload["pipelines"]:
        raise AtlasError("Atlas 'pipelines' must be a non-empty object")
    return payload


def _merge(paths) -> dict[str, Any]:
    merged: dict[str, Any] = {"guidance": {}, "pipelines": {}}
    owner: dict[str, Path] = {}
    for target in map(Path, paths):
        payload = _read(target)
        for key, entry in payload["pipelines"].items():
            if key in owner:
                raise AtlasError(f"Atlas entry {key!r} is in both {owner[key].name} and {target.name}")
            owner[key] = target
            merged["pipelines"][key] = entry
        guidance = payload.get("guidance") or {}
        notes = list(merged["guidance"].get("notes") or []) + list(guidance.get("notes") or [])
        merged["guidance"] = {**guidance, **merged["guidance"], "notes": notes}
    return merged


def _validate(pipelines: dict[str, Any], known: set[str] | None = None) -> None:
    known = set(pipelines) | (known or set())
    for key, entry in pipelines.items():
        if not isinstance(entry, dict):
            raise AtlasError(f"Atlas entry {key!r} is not an object")
        if not entry.get("revision"):
            raise AtlasError(f"Atlas entry {key!r} has no revision")
        for neighbour in (entry.get("versus") or {}):
            if neighbour not in known:
                raise AtlasError(
                    f"Atlas entry {key!r} names {neighbour!r} in its versus map, "
                    f"but {neighbour!r} has no entry"
                )

    for key, entry in pipelines.items():
        for neighbour in (entry.get("versus") or {}):
            if neighbour not in pipelines:
                continue  # lives in another atlas file; the merged load checks the pair
            back = (pipelines[neighbour].get("versus") or {})
            if key not in back:
                warnings.warn(
                    f"one-sided versus pair: {key!r} points at {neighbour!r} "
                    f"but {neighbour!r} does not point back",
                    stacklevel=3,
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
                stacklevel=3,
            )


def load_atlas(path: str | Path | None = None) -> dict[str, Any]:
    """Read the atlas, validate it, and return it.

    With no `path`, every file in ATLAS_PATHS is merged (a key in two files raises). With
    one, only that file's pipelines are returned; the sibling files in ATLAS_PATHS are still
    read, for the pipeline names its `versus` keys may use, so a broken sibling raises too.
    Raises AtlasError when an entry lacks a revision or a `versus` key names a pipeline that
    has no entry. Warns on one-sided `versus` pairs and
    on a revision that disagrees with seqera/catalog.py.
    """
    if path is None:
        payload = _merge(ATLAS_PATHS)
        _validate(payload["pipelines"])
        return payload
    # One file alone: a `versus` key may name a pipeline that lives in a sibling file.
    payload = _read(Path(path))
    siblings: set[str] = set()
    for sibling in ATLAS_PATHS:
        # A broken shipped atlas must fail loudly here too, not leave its names unknown.
        siblings |= set(_read(sibling)["pipelines"])
    _validate(payload["pipelines"], siblings)
    return payload
