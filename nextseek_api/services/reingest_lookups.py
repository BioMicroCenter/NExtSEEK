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
"""
from __future__ import annotations

import json
import logging

from nextseek_api.services.context_catalog import load_sample_type, load_sample_types

log = logging.getLogger(__name__)


def known_sample_types() -> set[str]:
    """Every SampleType code in the catalog."""
    return {entry.code for entry in load_sample_types()}


def attributes_for(sample_type: str) -> list[dict]:
    """[{"title", "required", "server_required"}] for one sample type; []
    when it is unknown.

    Two independent answers to "is this attribute required?", because two
    stores disagree and each is the authority for a different question:

    - ``required`` -- the catalog's ``required_metadata`` policy
      (``sample_types_context``). Unchanged from before this flag existed:
      a curation expectation, not necessarily something the server enforces.
    - ``server_required`` -- SEEK's own ``sample_attributes.required`` flag,
      the thing that actually rejects a row at upload
      (``seek/dbtable_sampleattribute.py``, ``seek/sample/core.py``'s
      ``_verifyRequiredFields``). This is the HARD blocker; ``required`` on
      its own must never be treated as one.

    Standard and possible fields are returned too, flagged not-required by
    either measure, so a caller can ask both "does this attribute exist?"
    and "must it be filled?" from one call.

    Fail-safe direction: when SEEK's attribute table is unreachable, or has
    no row for a title the catalog knows, that is an outage, not an answer
    -- and the safe direction here is STRICTER, not looser. So an unknown
    ``server_required`` falls back to the catalog's own ``required`` flag
    for that title, never to ``False``. This mirrors this module's own house
    rule (see the module docstring: a missing table costs the caller an
    empty catalog, never an exception) applied to the new flag specifically,
    so a caller that never learns SEEK's answer keeps today's HARD-blocking
    behaviour instead of silently letting a row through.
    """
    st = str(sample_type or "").strip()
    entry = load_sample_type(st)
    if entry is None:
        return []
    required = entry.required_metadata
    others = entry.standard_metadata + entry.possible_metadata_fields
    seek_required = _seek_required_map(st)
    seen: set[str] = set()
    out: list[dict] = []
    for title in required + others:
        if title in seen:
            continue
        seen.add(title)
        is_required = title in required
        # .get(title, is_required): only an EXPLICIT SEEK answer overrides
        # the catalog's own flag -- an outage or a title SEEK never heard of
        # falls back to `is_required`, the stricter of the two possible
        # defaults ("not required" would let a row through SEEK might
        # actually reject; "required" merely keeps today's behaviour).
        server_required = seek_required.get(title, is_required)
        out.append({"title": title, "required": is_required,
                    "server_required": server_required})
    return out


def _seek_required_map(sample_type: str) -> dict[str, bool]:
    """{attribute title: bool(required)} from SEEK's own ``sample_attributes``
    table for ``sample_type``, or ``{}`` on any failure -- table unreachable,
    the type unresolvable on this instance, or no attribute rows at all.

    ``{}`` is read by ``attributes_for`` as "we don't know", never as "SEEK
    requires nothing here": that distinction is the whole point of the
    fail-safe fallback documented on ``attributes_for``. Follows the same
    lazy, guarded shape as ``uids_by_primary_data`` above.
    """
    try:
        from seek.models import Sample_attributes, Sample_types

        type_ids = list(
            Sample_types.objects.filter(title=sample_type).values_list("id", flat=True))
        if not type_ids:
            return {}
        rows = list(
            Sample_attributes.objects.filter(
                sample_type_id__in=type_ids).values_list("title", "required"))
        return {str(title): bool(required) for title, required in rows}
    except Exception:
        log.exception("attributes_for: SEEK required-attribute lookup failed for %s", sample_type)
        return {}


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
