"""Read-only lookups reingest makes against NExtSEEK.

Kept in one module because the reingest plans need the same handful of answers,
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
``context_catalog.load_sample_type_strict``: it raises instead of swallowing
a catalog outage into an empty result, for the one caller that must not
mistake "the database is unreachable" for "genuinely not defined" -- see
``proposals.attribute_exists`` in ``NessieAI/ns/reingest/proposals.py``. There
is deliberately no strict twin for ``known_sample_types``: nothing needs one,
and an uncalled function is one more thing to keep true.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from nextseek_api.services.context_catalog import (
    load_sample_type,
    load_sample_type_strict,
    load_sample_types,
)

log = logging.getLogger(__name__)


def known_sample_types() -> set[str]:
    """Every SampleType code in the catalog. Empty on failure, never raises."""
    return {entry.code for entry in load_sample_types()}


def _attributes_from_entry(entry, sample_type: str) -> list[dict]:
    """[{"title", "required", "server_required"}] for one already-loaded
    entry, or [] for None.

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
    if entry is None:
        return []
    required = entry.required_metadata
    others = entry.standard_metadata + entry.possible_metadata_fields
    seek_required = _seek_required_map(sample_type)
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


def attributes_for(sample_type: str) -> list[dict]:
    """[{"title", "required", "server_required"}] for one sample type; []
    when it is unknown, and also [] on a catalog outage -- see
    `attributes_for_strict` for a caller that must tell those two apart.
    """
    st = str(sample_type or "").strip()
    entry = load_sample_type(st)
    return _attributes_from_entry(entry, st)


def attributes_for_strict(sample_type: str) -> list[dict]:
    """[{"title", "required", "server_required"}] for one sample type; []
    when it is genuinely unknown in a working catalog.

    Raises on a catalog outage instead of returning `[]` for it, so a caller
    that acts on absence (reingest's `attribute_exists`) cannot mistake a
    database outage for a genuine schema gap; see
    `context_catalog.load_sample_type_strict`.
    """
    st = str(sample_type or "").strip()
    entry = load_sample_type_strict(st)
    return _attributes_from_entry(entry, st)


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


def _parent_set(value) -> frozenset[str]:
    return frozenset(p.strip() for p in str(value or "").split(";") if p.strip())


def caller_project_scope(user):
    """The SEEK projects ``user``'s reingest lookups may see (SEC-001).

    ``None`` for a superuser (any project), else a tuple of project ids, and
    ``()`` when the caller has no SEEK person or the membership read fails: it
    fails closed, never widens. The batch upload's own definition
    (``_caller_project_scope`` in ``nextseek_api/batch_upload/views.py``), so a
    lookup sees exactly the projects the upload it prepares may write to.
    """
    from nextseek_api.batch_upload import views as batch_views

    return batch_views._caller_project_scope(user)


def existing_uids_for_primary_files_strict(sample_type: str,
                                           files: list[dict], *,
                                           project_ids=None) -> list[dict]:
    """For each ``{"link", "checksum", "parent"}``, the ``sample_type`` samples
    under that parent that already hold that file (``uids``) and those whose
    file at that path has changed since (``changed``).

    Only a sample whose ``Parent`` names the same set of UIDs is compared: a
    per-run output (one gene-count matrix for every sample) is the same file on
    every row. It holds the file when the checksums agree, or when the
    ``Link_PrimaryData`` paths agree and either side has no checksum to compare.
    The same path with a different checksum is a run redone in the same folder:
    new content, so ``changed``, never ``uids``. A reanalysis in its own run
    folder matches neither. The answer is aligned with ``files``.

    Reingest leaves out a new row that is already a sample, so an uploaded run
    is never uploaded twice. Strict: a failed fetch or metadata that cannot be
    parsed RAISES ``RuntimeError``, since "could not read" must never come back
    as "nothing there yet". A type this instance lacks holds no samples.

    ``project_ids`` is the caller's project scope, as ``caller_project_scope``
    gives it (SEC-001): only samples in one of those projects are compared, so
    the reply never names a sample the caller cannot see, nor drops a row
    because of one. ``None`` compares samples of every project, a superuser's
    scope and what a caller that passes no scope gets; an empty scope sees no
    sample and asks nothing.
    """
    if not files:
        return []
    wanted = [(str(f.get("link") or "").strip(), str(f.get("checksum") or "").strip(),
               _parent_set(f.get("parent"))) for f in files]
    needles = sorted({v for link, checksum, _ in wanted for v in (link, checksum) if v})
    scope = None if project_ids is None else sorted({int(p) for p in project_ids})
    if not needles or scope == []:
        return [{"uids": [], "changed": []} for _ in files]
    try:
        from django.db.models import Q

        from seek.models import Projects_samples, Sample_types, Samples

        type_ids = list(Sample_types.objects.filter(title=sample_type)
                        .values_list("id", flat=True))
        if not type_ids:
            return [{"uids": [], "changed": []} for _ in files]
        match_any = Q()
        for needle in needles:
            match_any |= Q(json_metadata__icontains=needle)
        held_query = Samples.objects.filter(sample_type_id__in=type_ids).filter(match_any)
        if scope is not None:
            held_query = held_query.filter(id__in=Projects_samples.objects.filter(
                project_id__in=scope).values("sample_id"))
        rows = list(held_query.values_list("uuid", "json_metadata"))
    except Exception as exc:
        raise RuntimeError(
            f"samples unreachable checking {len(files)} {sample_type} file(s): {exc}") from exc

    held: list[tuple[str, str, str, frozenset]] = []
    for uid, raw in rows:
        if not uid:
            continue
        try:
            meta = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise RuntimeError(f"unreadable metadata on sample {uid}") from exc
        if not isinstance(meta, dict):
            raise RuntimeError(f"unreadable metadata on sample {uid}")
        held.append((str(uid), str(meta.get("Link_PrimaryData") or "").strip(),
                     str(meta.get("Checksum_PrimaryData") or "").strip(),
                     _parent_set(meta.get("Parent"))))
    out: list[dict] = []
    for link, checksum, parents in wanted:
        same: set[str] = set()
        changed: set[str] = set()
        for uid, h_link, h_sum, h_parents in held:
            if h_parents != parents:
                continue
            if checksum and h_sum == checksum:
                same.add(uid)
            elif link and h_link == link:
                (changed if checksum and h_sum else same).add(uid)
        out.append({"uids": sorted(same), "changed": sorted(changed - same)})
    return out


def sample_types_for_uids(uids: list[str]) -> dict[str, str]:
    """The SampleType title per UID. A UID whose type cannot be determined is
    OMITTED.

    The omission is the safety property, same reasoning as `notes_for_uids`:
    reingest's QC backfill row (`NessieAI/ns/reingest/mapper.py`) uses this to
    find out what a resolved parent's REAL sample type is, because the UID
    alone cannot be trusted -- most UIDs start with their type code
    ("D.SEQ-...", "A.ALN-..."), but roughly 1.5% of real samples do not
    (free-text titles on CEL samples, measured against the live database),
    so parsing the prefix would be silently wrong for a small but real slice
    of every run. A UID absent from this result must never be guessed at
    (e.g. defaulted to "D.SEQ") -- that guess is exactly the class of bug the
    QC backfill exists to avoid: writing a measurement onto a row shaped for
    the wrong sample type. QA/mapper.py treats "absent from this map" as "do
    not build a QC row for that sample" -- which is why this must never
    return "" as a stand-in for an unresolved title.

    A UID that does not exist, or whose resolved type id names no known
    SampleType, is simply absent from the result; the whole lookup failing
    (for example, the samples or sample_types table being unreachable) omits
    every UID rather than raising.
    """
    if not uids:
        return {}
    try:
        from seek.models import Sample_types, Samples

        rows = list(
            Samples.objects.filter(uuid__in=uids).values_list("uuid", "sample_type_id")
        )

        type_ids = {type_id for _, type_id in rows if type_id is not None}
        titles_by_id: dict[int, str] = {}
        if type_ids:
            titles_by_id = dict(
                Sample_types.objects.filter(id__in=type_ids).values_list("id", "title")
            )
    except Exception:
        log.exception("sample_types_for_uids: lookup failed for %s", uids)
        return {}

    out: dict[str, str] = {}
    for uid, type_id in rows:
        if not uid:
            continue
        title = titles_by_id.get(type_id)
        if title:
            out[str(uid)] = str(title)
    return out


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


def attribute_values_for_uids_strict(uids: list[str], attribute: str) -> dict[str, object]:
    """Current non-empty ``attribute`` value per UID, from ``json_metadata``.

    The strict twin of ``notes_for_uids``, for a caller that must not overwrite
    a curated value (a placed metric in reingest). A UID that is absent, or
    whose value is empty, is simply omitted. But a failed fetch or a row whose
    metadata cannot be parsed RAISES ``RuntimeError``: "could not read" must
    never come back looking like "nothing held".
    """
    clean = sorted({str(u).strip() for u in uids if str(u or "").strip()})
    if not clean:
        return {}
    try:
        from seek.models import Samples

        rows = list(
            Samples.objects.filter(uuid__in=clean).values_list("uuid", "json_metadata")
        )
    except Exception as exc:
        raise RuntimeError(
            f"samples unreachable reading {attribute!r} for {len(clean)} UID(s): {exc}"
        ) from exc

    out: dict[str, object] = {}
    for uid, raw in rows:
        if not uid:
            continue
        try:
            meta = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise RuntimeError(f"unreadable metadata on sample {uid}") from exc
        if not isinstance(meta, dict):
            raise RuntimeError(f"unreadable metadata on sample {uid}")
        value = meta.get(attribute)
        if value is None or (isinstance(value, str) and not value.strip()) \
                or (isinstance(value, (list, dict)) and not value):
            continue
        out[str(uid)] = value
    return out


def _seek_schema() -> str:
    """SEEK's schema name, backquoted, for qualifying its tables in raw SQL.

    SEEK's tables are reached through the ``seek`` alias. The ``default``
    (dmac) schema holds an empty ``samples`` table of the same name, made by
    migration 0001 because the mirror models are managed, so an unqualified
    query on ``default`` finds nothing and fails quietly. The name comes from
    settings, never from a caller.
    """
    from django.conf import settings

    name = settings.DATABASES[settings.SEEK_DATABASE]["NAME"]
    return f"`{name}`"


@dataclass(frozen=True)
class StudyAssayPlan:
    """Per study the parents' assays sit in: that study's assays of one house assay.

    ``studies`` maps a study id to its matching SEEK ``assays.id`` values, an
    empty list when the study has none. ``parents_without_study`` names the
    parents that are in no study assay at all, a UID with no sample included.
    """
    studies: dict
    parents_by_study: dict
    parents_without_study: list
    study_titles: dict


def study_assays_for_parents_strict(parent_uids: list[str],
                                    internal_assay_title: str) -> StudyAssayPlan:
    """Where the parents sit, and each of those studies' assays of the house assay.

    One query on ``default``, SEEK's tables named by schema (see
    ``_seek_schema``). An outage raises ``RuntimeError``.
    """
    uids = sorted({str(u).strip() for u in parent_uids or () if str(u or "").strip()})
    if not uids or not internal_assay_title:
        return StudyAssayPlan({}, {}, uids, {})
    from django.db import connections

    seek = _seek_schema()
    uid_ph = ", ".join(["%s"] * len(uids))
    sql = f"""
        SELECT s.uuid, pa.study_id, st.title, t.id
          FROM {seek}.samples AS s
          JOIN {seek}.assay_assets AS aa
            ON aa.asset_id = s.id AND aa.asset_type = 'Sample'
          JOIN {seek}.assays AS pa
            ON pa.id = aa.assay_id AND pa.study_id IS NOT NULL
          JOIN {seek}.studies AS st
            ON st.id = pa.study_id
          LEFT JOIN ({seek}.assays AS t
                     JOIN assays_internal_assays AS j ON j.assay_id = t.id
                     JOIN internal_assays AS ia ON ia.id = j.internal_assay_id)
            ON t.study_id = pa.study_id AND ia.internal_assay_title = %s
         WHERE s.uuid IN ({uid_ph})
    """
    try:
        with connections["default"].cursor() as cur:
            cur.execute(sql, [internal_assay_title, *uids])
            rows = cur.fetchall()
    except Exception as exc:
        raise RuntimeError(
            f"assay catalog unreachable while placing {len(uids)} parent UID(s) "
            f"for {internal_assay_title!r}: {exc}") from exc
    studies: dict[int, set[int]] = {}
    parents: dict[int, set[str]] = {}
    titles: dict[int, str] = {}
    for uid, study_id, study_title, assay_id in rows:
        study_id = int(study_id)
        studies.setdefault(study_id, set())
        if assay_id is not None:
            studies[study_id].add(int(assay_id))
        parents.setdefault(study_id, set()).add(str(uid))
        titles[study_id] = str(study_title or "")
    placed = {uid for members in parents.values() for uid in members}
    return StudyAssayPlan(
        studies={k: sorted(v) for k, v in sorted(studies.items())},
        parents_by_study={k: sorted(v) for k, v in sorted(parents.items())},
        parents_without_study=[uid for uid in uids if uid not in placed],
        study_titles=dict(sorted(titles.items())))


def house_assay_title_strict(name: str) -> str | None:
    """The ``internal_assays`` title a map's assay name stands for, or None.

    The name matches itself or, through ``assay_context``, any name in its
    catalog row's ``assay_name`` and ``Alternative_Assay_Names``. Exact,
    case-sensitive titles only, and never ``assay_context.internal_assay_id``,
    whose ids are production's. More than one matching title raises
    ``ValueError``; an outage raises ``RuntimeError``.
    """
    wanted = str(name or "").strip()
    if not wanted:
        return None
    from django.db import connections

    try:
        with connections["default"].cursor() as cur:
            cur.execute("SELECT assay_name, Alternative_Assay_Names FROM assay_context")
            catalog = cur.fetchall()
            cur.execute("SELECT internal_assay_title FROM internal_assays")
            titles = {str(row[0]) for row in cur.fetchall() if row[0]}
    except Exception as exc:
        raise RuntimeError(f"house assays unreachable while resolving {wanted!r}: {exc}") from exc
    names = {wanted}
    for assay_name, alternatives in catalog:
        group = {n.strip() for n in [str(assay_name or ""), *str(alternatives or "").split(",")]
                 if n.strip()}
        if wanted in group:
            names |= group
    hits = sorted(names & titles)
    if len(hits) > 1:
        raise ValueError(f"{wanted!r} names more than one house assay: {hits}")
    return hits[0] if hits else None


def project_ids_for_uids_strict(uids: list[str]) -> list[int]:
    """Distinct SEEK project ids of the samples with these UIDs, sorted.

    An upload needs exactly one project; the caller decides what zero or
    several mean. An outage RAISES rather than returning [], for the same
    reason the other ``_strict`` lookups do.
    """
    clean = sorted({str(u).strip() for u in uids if str(u or "").strip()})
    if not clean:
        return []
    from django.conf import settings
    from django.db import connections

    placeholders = ", ".join(["%s"] * len(clean))
    sql = f"""
        SELECT DISTINCT ps.project_id
          FROM samples AS s
          JOIN projects_samples AS ps ON ps.sample_id = s.id
         WHERE s.uuid IN ({placeholders})
         ORDER BY ps.project_id
    """
    try:
        # Every table here is SEEK's: read them through the SEEK alias.
        with connections[settings.SEEK_DATABASE].cursor() as cur:
            cur.execute(sql, clean)
            return [int(r[0]) for r in cur.fetchall()]
    except Exception as exc:
        raise RuntimeError(
            f"project catalog unreachable for {len(clean)} UID(s): {exc}") from exc


def next_name_ordinal_strict(sample_type: str, prefix: str) -> int:
    """1 + the highest N among existing ``<prefix>_<N>`` Names on ``sample_type``.

    A per_run analysis row cannot inherit a parent's Name (it has as many
    parents as the run had samples), so it is named for what it is plus when it
    was made -- which collides the moment two runs land on one day. This is the
    disambiguator: the first A.GEX named ``gex_2026-01-26`` gets ``_1``, the
    next ``_2``.

    Only ``<prefix>_<digits>`` counts. A Name that merely starts with the prefix
    (``gex_2026-01-26_final``) is ignored rather than guessed at, so a
    hand-edited Name can never be read as an ordinal and silently reserve a
    number.

    RAISES on an unreachable catalog rather than returning 1. Same reasoning as
    ``attribute_exists``: 1 is a real answer meaning "nothing exists yet", and
    handing it back for "the database blinked" is precisely how two samples end
    up sharing a Name -- the thing this function exists to prevent.
    """
    if not prefix:
        return 1
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
    try:
        from seek.models import Sample_types, Samples

        type_id = (Sample_types.objects.filter(title=sample_type)
                   .values_list("id", flat=True).first())
        if type_id is None:
            return 1
        rows = Samples.objects.filter(sample_type_id=type_id).values_list(
            "json_metadata", flat=True)
        highest = 0
        for raw in rows:
            try:
                meta = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except ValueError:
                continue
            if not isinstance(meta, dict):
                continue
            hit = pattern.match(str(meta.get("Name") or "").strip())
            if hit:
                highest = max(highest, int(hit.group(1)))
        return highest + 1
    except Exception as exc:
        raise RuntimeError(
            f"sample catalog unreachable while numbering {prefix!r} "
            f"on {sample_type}: {exc}"
        ) from exc


#: A UID's lab segment: "D.SEQ-230512ABC-287-PUB" -> "ABC". Anchored on the
#: <YYMMDD><LETTERS> shape rather than split on "-", so a UID that does not
#: follow the convention yields nothing instead of a wrong slice. Roughly 1.5%
#: of real samples do not follow it (see `sample_types_for_uids`), which is
#: exactly why every caller here omits rather than guesses.
_UID_LAB_SEGMENT = re.compile(r"^[^-]+-\d{6}([A-Za-z]{2,5})-")


def lab_code_from_uid(uid: str) -> str | None:
    """"ABC" from "D.SEQ-230512ABC-287-PUB"; None when the UID is not that shape."""
    hit = _UID_LAB_SEGMENT.match(str(uid or "").strip())
    return hit.group(1).upper() if hit else None


#: How long one read of the catalog's Lab spellings is reused. A build resolves a
#: Lab per row (`_computed_lab` in `NessieAI/ns/reingest/pipelines/common.py`),
#: and before this every call read every sample in SEEK; this window covers one
#: build (its op limit is 60 s) and lets the next build read the catalog afresh.
#: A spelling a curator adds during that window is picked up by the next build.
_LAB_SPELLINGS_TTL_S = 120.0

#: (monotonic time read, spelling -> sample count), or None before the first read.
_lab_spellings_cache: tuple[float, dict[str, int]] | None = None

#: Selects only the Lab value of each sample that names a Lab key, never the
#: whole metadata. ``LIKE`` is a superset filter (case-insensitive under SEEK's
#: collation); ``JSON_VALID`` keeps ``JSON_EXTRACT`` from failing the query on
#: one malformed row, which the Python loop it replaced skipped. ``JSON_EXTRACT``
#: answers SQL NULL when the top-level object has no ``Lab`` key (or is not an
#: object), and the value as JSON text otherwise, on MySQL 8 and MariaDB alike.
_LAB_SPELLINGS_SQL = """
    SELECT JSON_EXTRACT(s.json_metadata, '$.Lab')
      FROM samples AS s
     WHERE s.json_metadata LIKE %s
       AND JSON_VALID(s.json_metadata)
"""


def _read_lab_spellings_strict() -> dict[str, int]:
    """Every Lab value samples carry, stripped, with how many samples carry it.

    One query on the SEEK alias that returns the Lab fragment of the rows that
    have one, so no metadata blob leaves the database. Each fragment is decoded
    and normalised exactly as the per-sample loop this replaced did
    (``str(value or "").strip()``, blanks dropped), so the counts are the same.
    Known, deliberate differences, none of which SEEK writes: a key spelled with
    a JSON escape (``"L\\u0061b"``) misses the ``LIKE`` filter, and a row that
    only Python's parser accepts (``NaN``) fails ``JSON_VALID``. Raises on any
    database error; the caller wraps it.
    """
    from django.conf import settings
    from django.db import connections

    with connections[settings.SEEK_DATABASE].cursor() as cur:
        cur.execute(_LAB_SPELLINGS_SQL, ['%"Lab"%'])
        rows = cur.fetchall()
    counts: dict[str, int] = {}
    for (fragment,) in rows:
        if fragment is None:
            continue
        try:
            value = json.loads(fragment) if isinstance(fragment, (str, bytes, bytearray)) \
                else fragment
        except ValueError:
            continue
        value = str(value or "").strip()
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _lab_spellings_strict() -> dict[str, int]:
    """`_read_lab_spellings_strict`, reused for `_LAB_SPELLINGS_TTL_S` seconds."""
    global _lab_spellings_cache
    import time

    now = time.monotonic()
    cached = _lab_spellings_cache
    if cached is not None and now - cached[0] < _LAB_SPELLINGS_TTL_S:
        return cached[1]
    counts = _read_lab_spellings_strict()
    _lab_spellings_cache = (now, counts)
    return counts


def lab_for_code_strict(code: str) -> str | None:
    """The Lab string the database ALREADY uses for this UID lab code.

    Returns an existing value verbatim rather than composing one, because the
    catalog has no single convention to compose to: the same lab is spelled
    several ways (bare surname, "<Surname> Lab", "<Surname> lab",
    "<SURNAME>_lab"), and which spelling dominates differs per lab. Inventing
    "<Surname> Lab" would add one more; copying the most-used existing value at
    least lands on a string already in use. Count the spellings for yourself
    with a Counter over `_read_lab_spellings_strict()` -- deliberately not
    quoted here, since it changes as the catalog is curated.

    Matching is on the first three letters of the value with non-alphabetic
    characters stripped, so "ABCDEF_lab" and "Abcdef Lab" both match "ABC".
    Among matches the most frequent wins; ties break alphabetically so the
    answer is deterministic run to run.

    None when no existing value matches. Real lab codes do exist for which no
    sample anywhere carries a Lab value, so this is an ordinary outcome, not an
    edge case: there is nothing to copy and the cell stays blank for a curator.

    The spellings come from one filtered query, reused for the span of a build
    (`_lab_spellings_strict`): a build calls this once per row, and SEEK holds
    about a million samples.

    RAISES on an unreachable catalog, never returns None for it: a blank Lab
    that means "we could not reach the database" is indistinguishable from one
    that means "this lab has no recorded name", and only the second is safe to
    hand a curator.
    """
    code = str(code or "").strip().upper()
    if not code:
        return None
    try:
        counts = _lab_spellings_strict()
    except Exception as exc:
        raise RuntimeError(
            f"sample catalog unreachable while resolving lab code {code!r}: {exc}"
        ) from exc

    matches = [(v, n) for v, n in counts.items()
               if re.sub(r"[^A-Za-z]", "", v)[:3].upper() == code]
    if not matches:
        return None
    matches.sort(key=lambda vn: (-vn[1], vn[0]))
    return matches[0][0]


def assay_ids_in_study_strict(study_id: int, internal_assay_title: str) -> list[int]:
    """The study's SEEK assays tagged with the house assay, sorted. Outage raises."""
    from django.db import connections

    seek = _seek_schema()
    sql = f"""
        SELECT a.id FROM {seek}.assays AS a
          JOIN assays_internal_assays AS j ON j.assay_id = a.id
          JOIN internal_assays AS ia ON ia.id = j.internal_assay_id
         WHERE a.study_id = %s AND ia.internal_assay_title = %s
         ORDER BY a.id
    """
    try:
        with connections["default"].cursor() as cur:
            cur.execute(sql, [int(study_id), internal_assay_title])
            return [int(r[0]) for r in cur.fetchall()]
    except Exception as exc:
        raise RuntimeError(f"assays of study {study_id} unreachable: {exc}") from exc


def internal_assay_id_strict(title: str) -> int | None:
    """``internal_assays.id`` for an exact title, None when absent. Outage raises."""
    from django.db import connections

    try:
        with connections["default"].cursor() as cur:
            cur.execute("SELECT MIN(id) FROM internal_assays WHERE internal_assay_title = %s",
                        [title])
            row = cur.fetchone()
    except Exception as exc:
        raise RuntimeError(f"house assays unreachable: {exc}") from exc
    return int(row[0]) if row and row[0] is not None else None


def untagged_assay_ids_in_study_strict(study_id: int, title: str) -> list[int]:
    """The study's SEEK assays titled exactly ``title`` with no house-assay tag, sorted.

    What a SEEK create that answered with a timeout or a 5xx may have left
    behind. The title is compared in Python, exactly: the database's own
    comparison ignores case and trailing blanks. An outage raises.
    """
    from django.db import connections

    seek = _seek_schema()
    sql = f"""
        SELECT a.id, a.title FROM {seek}.assays AS a
          LEFT JOIN assays_internal_assays AS j ON j.assay_id = a.id
         WHERE a.study_id = %s AND a.title = %s AND j.assay_id IS NULL
         ORDER BY a.id
    """
    try:
        with connections["default"].cursor() as cur:
            cur.execute(sql, [int(study_id), title])
            rows = cur.fetchall()
    except Exception as exc:
        raise RuntimeError(f"assays of study {study_id} unreachable: {exc}") from exc
    return sorted(int(r[0]) for r in rows if r[1] == title)
