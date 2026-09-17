"""Read-only lookups reingest makes against NExtSEEK.

Kept in one module because all three reingest plans need the same three answers,
and because each is a query someone could otherwise re-invent slightly
differently -- which is how a required-attribute check ends up disagreeing with
the server that enforces it.

Everything here is read-only. Nothing in this module writes to SEEK, to the
sample-type catalog, or to Neo4j.

The catalog behind ``known_sample_types``/``attributes_for`` is
``nextseek_api.services.context_catalog``, the same loader the sample type
catalog pages use -- not the ``chat_nextseek`` bundled export this module used
to read directly. That export lives under ``NessieAI/``, which is AI-owned
territory (see the repo root ``CLAUDE.md``: "the API surface stays in
``nextseek_api/``"), so ``nextseek_api`` must not depend on it. Going through
``context_catalog.load_sample_type``/``load_sample_types`` also means this
module inherits that loader's house rule for free: a missing table or row
costs the caller an empty catalog, never an exception.

``attributes_for_strict`` is the exception to that house rule, built on
``context_catalog.load_sample_types_strict``: it raises instead of swallowing
a catalog outage into an empty result, for the one caller that must not
mistake "the database is unreachable" for "genuinely not defined" -- see
``proposals.attribute_exists`` in ``NessieAI/ns/reingest/proposals.py``. There
is deliberately no strict twin for ``known_sample_types``: nothing needs one,
and an uncalled function is one more thing to keep true.
"""
from __future__ import annotations

import json
import logging

from nextseek_api.services.context_catalog import (
    load_sample_type,
    load_sample_type_strict,
    load_sample_types,
)

log = logging.getLogger(__name__)


def known_sample_types() -> set[str]:
    """Every SampleType code in the catalog. Empty on failure, never raises."""
    return {entry.code for entry in load_sample_types()}


def _attributes_from_entry(entry) -> list[dict]:
    """[{"title", "required"}] for one already-loaded entry, or [] for None.

    Required comes from the catalog's required metadata; standard and possible
    fields are returned too, flagged not-required, so a caller can ask both
    "does this attribute exist?" and "must it be filled?" from one call.
    """
    if entry is None:
        return []
    required = entry.required_metadata
    others = entry.standard_metadata + entry.possible_metadata_fields
    seen: set[str] = set()
    out: list[dict] = []
    for title in required + others:
        if title in seen:
            continue
        seen.add(title)
        out.append({"title": title, "required": title in required})
    return out


def attributes_for(sample_type: str) -> list[dict]:
    """[{"title", "required"}] for one sample type; [] when it is unknown,
    and also [] on a catalog outage -- see `attributes_for_strict` for a
    caller that must tell those two apart.
    """
    entry = load_sample_type(str(sample_type or "").strip())
    return _attributes_from_entry(entry)


def attributes_for_strict(sample_type: str) -> list[dict]:
    """[{"title", "required"}] for one sample type; [] when it is genuinely
    unknown in a working catalog.

    Raises on a catalog outage instead of returning `[]` for it, so a caller
    that acts on absence (reingest's `attribute_exists`) cannot mistake a
    database outage for a genuine schema gap; see
    `context_catalog.load_sample_types_strict`.
    """
    entry = load_sample_type_strict(str(sample_type or "").strip())
    return _attributes_from_entry(entry)


def _matches_path(value, path: str) -> bool:
    """True if ``path`` appears in ``value`` as a whole path segment.

    A plain ``path in value`` substring test also matches "a.fastq.gz" inside
    "aa.fastq.gz", which would silently fold two different files' UIDs
    together. This requires a boundary (start/end of string, or one of
    ``/,; ``) on both sides of the match, so a shorter filename can never match
    merely because it is a suffix/prefix of a longer one.

    Contract this exists to protect: ``uids_by_primary_data`` returns every
    UID that genuinely matches and the caller must never auto-pick among them
    -- this function only rules out matches that were never real to begin
    with, it does not change that multiple real matches can still come back.
    """
    value = str(value or "")
    if not path:
        return False
    start = 0
    length = len(path)
    boundary = "/,; "
    while True:
        idx = value.find(path, start)
        if idx == -1:
            return False
        before_ok = idx == 0 or value[idx - 1] in boundary
        after = idx + length
        after_ok = after == len(value) or value[after] in boundary
        if before_ok and after_ok:
            return True
        start = idx + 1


def uids_by_primary_data(path: str, types=("D.SEQ",)) -> list[str]:
    """UIDs of a sample type in ``types`` whose File_PrimaryData or
    Link_PrimaryData mentions ``path``.

    The fallback UID join for a run Nessie did not launch. Returns every match:
    the caller decides what to do with more than one, and must never pick.

    ``types`` defaults to ``("D.SEQ",)`` -- this lookup's original, sole
    scope -- so every existing caller that does not pass it keeps searching
    exactly what it always searched. A caller that knows its pipeline's map
    declares a wider ``accepts_parent_types`` (see
    ``NessieAI/ns/reingest/maps.py``) may pass that instead, to find a parent
    that is itself an already-analysed A.* sample rather than raw D.SEQ. This
    function does not know about maps or reingest at all -- it only searches
    whatever type titles it is given -- so scoping decisions stay entirely
    with the caller, matching the rest of this module's read-only, caller-
    decides contract.

    Read-only: a plain filtered SELECT against the seek-mirrored ``samples``
    table (`seek.models.Samples`), scoped to ``types``. Any failure -- the
    table unreachable, a named type unresolvable on this instance, malformed
    metadata -- costs this one lookup, never raises to the caller.
    """
    if not path or not types:
        return []
    try:
        from seek.models import Sample_types, Samples

        type_ids = list(
            Sample_types.objects.filter(title__in=list(types)).values_list("id", flat=True)
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
            if _matches_path(meta.get("File_PrimaryData"), path) or \
                    _matches_path(meta.get("Link_PrimaryData"), path):
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
