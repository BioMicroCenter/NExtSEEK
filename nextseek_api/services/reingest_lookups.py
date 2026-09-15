"""Read-only lookups reingest makes against NExtSEEK.

Kept in one module because all three reingest plans need the same three answers,
and because each is a query someone could otherwise re-invent slightly
differently -- which is how a required-attribute check ends up disagreeing with
the server that enforces it.

Everything here is read-only. Nothing in this module writes to SEEK, to the
sample-type catalog, or to Neo4j.

The catalog behind ``known_sample_types``/``attributes_for`` is the bundled
export ``sampletypes_db.json`` (see ``_CATALOG_PATH``), not a live query
against ``sample_types_context``. Two reasons: the export is what
``chat_nextseek`` already ships and keeps refreshed from that same table
(NessieAI/chat_nextseek/src/chat_nextseek/config.py), and unlike a live query it
does not depend on a per-instance database actually being reachable and
populated -- which the standard Django test lane's in-memory database is not.
The export spells its metadata-field columns as COMMA-SEPARATED STRINGS under
"Required Metadata", "Standard Metadata" and "Possible Metadata Fields" (not as
JSON lists), which is what ``_split`` is for.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# NessieAI/chat_nextseek/src/chat_nextseek/context/sampletypes_db.json, relative
# to this file's own location, so this works the same whether the caller's
# cwd is the repo root, a container's /app, or a test runner's tmp dir. See the
# module docstring for why this is the export and not a live catalog query.
_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "NessieAI" / "chat_nextseek" / "src" / "chat_nextseek" / "context"
    / "sampletypes_db.json"
)

# The catalog spells these as comma-separated strings, not lists.
_REQUIRED_KEY = "Required Metadata"
_STANDARD_KEY = "Standard Metadata"
_POSSIBLE_KEY = "Possible Metadata Fields"
_CODE_KEY = "SampleType"


def _catalog() -> list[dict]:
    """The sampletypes export, or [] when it cannot be read.

    Same house rule as context_catalog.py: a missing or unreadable file costs
    the caller an empty catalog, never an exception.
    """
    try:
        raw = _CATALOG_PATH.read_text()
    except OSError:
        log.exception("reingest_lookups: cannot read %s", _CATALOG_PATH)
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        log.exception("reingest_lookups: %s is not valid JSON", _CATALOG_PATH)
        return []
    return data if isinstance(data, list) else []


def _split(value) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def known_sample_types() -> set[str]:
    """Every SampleType code in the catalog."""
    return {str(row.get(_CODE_KEY) or "").strip()
            for row in _catalog() if row.get(_CODE_KEY)}


def attributes_for(sample_type: str) -> list[dict]:
    """[{"title", "required"}] for one sample type; [] when it is unknown.

    Required comes from the catalog's "Required Metadata"; standard and possible
    fields are returned too, flagged not-required, so a caller can ask both
    "does this attribute exist?" and "must it be filled?" from one call.
    """
    for row in _catalog():
        if str(row.get(_CODE_KEY) or "").strip() != sample_type:
            continue
        required = _split(row.get(_REQUIRED_KEY))
        others = _split(row.get(_STANDARD_KEY)) + _split(row.get(_POSSIBLE_KEY))
        seen: set[str] = set()
        out: list[dict] = []
        for title in required + others:
            if title in seen:
                continue
            seen.add(title)
            out.append({"title": title, "required": title in required})
        return out
    return []


def uids_by_primary_data(path: str) -> list[str]:
    """D.SEQ UIDs whose File_PrimaryData or Link_PrimaryData mentions ``path``.

    The fallback UID join for a run Nessie did not launch. Returns every match:
    the caller decides what to do with more than one, and must never pick.

    Read-only: a plain filtered SELECT against the seek-mirrored ``samples``
    table (`seek.models.Samples`), scoped to the D.SEQ sample type. Any failure
    -- the table unreachable, D.SEQ unresolvable on this instance, malformed
    metadata -- costs this one lookup, never raises to the caller.
    """
    if not path:
        return []
    try:
        from seek.models import Sample_types, Samples

        type_ids = list(
            Sample_types.objects.filter(title="D.SEQ").values_list("id", flat=True)
        )
        if not type_ids:
            return []

        matches: set[str] = set()
        rows = list(
            Samples.objects.filter(
                sample_type_id__in=type_ids, json_metadata__icontains=path
            ).values_list("uuid", "json_metadata")
        )
        for uid, raw in rows:
            if not uid:
                continue
            try:
                meta = json.loads(raw) if raw else {}
            except ValueError:
                continue
            if not isinstance(meta, dict):
                continue
            if path in str(meta.get("File_PrimaryData") or "") or \
                    path in str(meta.get("Link_PrimaryData") or ""):
                matches.add(str(uid))
        return sorted(matches)
    except Exception:
        log.exception("uids_by_primary_data: lookup failed for %s", path)
        return []


def notes_for_uids(uids: list[str]) -> dict[str, str]:
    """Current ``Notes`` per UID. A UID whose fetch failed is OMITTED.

    The omission is the safety property. A later write overwrites Notes
    wholesale, so writing a composed value without having read the existing one
    destroys it. QA treats "absent from this map" as "do not write Notes for
    that sample" -- which is why this must never return "" as a stand-in.

    A UID that does not exist, or whose row cannot be parsed, is simply absent
    from the result; the whole lookup failing (for example, the samples table
    being unreachable) omits every UID rather than raising.
    """
    if not uids:
        return {}
    try:
        from seek.models import Samples

        rows = list(
            Samples.objects.filter(uuid__in=uids).values_list("uuid", "json_metadata")
        )
    except Exception:
        log.exception("notes_for_uids: fetch failed for %s", uids)
        return {}

    out: dict[str, str] = {}
    for uid, raw in rows:
        if not uid:
            continue
        try:
            meta = json.loads(raw) if raw else {}
        except ValueError:
            continue
        if not isinstance(meta, dict):
            continue
        out[str(uid)] = str(meta.get("Notes") or "")
    return out
