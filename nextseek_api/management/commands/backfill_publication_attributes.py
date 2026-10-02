"""Write DOI and PMID into each sample's json_metadata.

    # local / dev: the study-level DOIs already recorded there
    uv run manage.py backfill_publication_attributes --from-studies
    uv run manage.py backfill_publication_attributes --from-studies --apply

    # production: a mapping file, because prod's studies are project-level
    uv run manage.py backfill_publication_attributes --from-file pairs.tsv --apply

Dry-run by default; --apply writes.

Why Python and not one UPDATE ... JSON_SET: json_metadata is a TEXT column
holding JSON whose key order mirrors the sample type's column order. MySQL's
JSON functions round-trip through its internal JSON type, which sorts keys
alphabetically -- silently reordering every sample's metadata. Reading and
writing the text here preserves insertion order and appends the two new keys at
the end, matching where DOI and PMID sit in the attribute list.

A sample can appear in more than one paper. Multiple values are joined with
'; ' and PMIDs align positionally with DOIs, blank where a paper has no PubMed
record.

The graph: this command bumps no updated_at, so nothing else would tell the
sample graph that the metadata moved. Once --apply has committed, the ids it
wrote are queued for a graph sync in batches of 5,000 (kind 'samples', key
'batch:backfill:<n>', the ids as the payload). The row goes in after the
updates, never inside them, and it is only a note to the drain, which reads the
samples back and writes the graph itself.

The studies tool calls ``write_publication_attributes`` with one row per sample for its whole run and its own key
prefix, journaling each batch's old and new text through ``on_batch`` before the batch is written, and rolls back
through ``restore_publication_text``.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.conf import settings

from nextseek_api.graph_sync import hooks

SEPARATOR = "; "
GRAPH_SYNC_BATCH = 5_000

_FROM_STUDIES_SQL = """
    SELECT sample_id,
           GROUP_CONCAT(doi  ORDER BY study_id SEPARATOR %s) AS dois,
           GROUP_CONCAT(pmid ORDER BY study_id SEPARATOR %s) AS pmids
      FROM (
        SELECT DISTINCT aa.asset_id AS sample_id, st.id AS study_id,
               st.doi AS doi, COALESCE(CAST(st.pmid AS CHAR), '') AS pmid
          FROM assay_assets aa
          JOIN assays a  ON a.id = aa.assay_id
          JOIN studies st ON st.id = a.study_id
         WHERE aa.asset_type = 'Sample' AND st.doi IS NOT NULL
      ) d
     GROUP BY sample_id
"""


def _cursor():
    return connections[settings.SEEK_DATABASE].cursor()


def pairs_from_studies() -> dict[int, tuple[str, str]]:
    """sample_id -> (doi string, pmid string), both possibly multi-valued."""
    with _cursor() as c:
        c.execute(_FROM_STUDIES_SQL, [SEPARATOR, SEPARATOR])
        return {r[0]: (r[1] or "", r[2] or "") for r in c.fetchall()}


def pairs_from_file(path: str) -> dict[int, tuple[str, str]]:
    """A TSV of sample_id, doi, pmid. Repeated sample_ids accumulate."""
    acc: dict[int, tuple[list[str], list[str]]] = {}
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                raise CommandError(f"{path}:{n}: expected sample_id<TAB>doi[<TAB>pmid]")
            sid = int(parts[0])
            doi = parts[1].strip()
            pmid = parts[2].strip() if len(parts) > 2 else ""
            d, p = acc.setdefault(sid, ([], []))
            d.append(doi)
            p.append(pmid)
    return {sid: (SEPARATOR.join(d), SEPARATOR.join(p)) for sid, (d, p) in acc.items()}


def updated_metadata(raw: str | None, doi: str, pmid: str) -> str:
    """Set DOI and PMID, preserving existing key order and appending if new."""
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["DOI"] = doi
    data["PMID"] = pmid
    return json.dumps(data)


def enqueue_graph_sync(ids: list[int], batch: int | None = None, *, prefix: str = "batch:backfill") -> int:
    """Queue the samples this run updated for a graph sync. Returns how many ids were queued.

    Called after the updates are committed, never inside them. hooks.enqueue never raises, so a batch that cannot be
    queued costs the backfill nothing: those samples wait for the nightly targeted sync instead. Keys are
    ``<prefix>:<n>``, so two callers with their own prefixes never overwrite each other's ids before a drain.
    """
    size = batch or GRAPH_SYNC_BATCH
    ordered = sorted(ids)
    queued = 0
    for n, start in enumerate(range(0, len(ordered), size)):
        chunk = ordered[start:start + size]
        if hooks.enqueue("samples", f"{prefix}:{n}", chunk):
            queued += len(chunk)
    return queued


def _metadata_object(raw: str | None) -> dict | None:
    """The metadata as a dict, {} for none at all, or None when it is not a JSON object (never written over)."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def publication_pairs(doi, pmid) -> tuple[tuple[str, str], ...]:
    """The (DOI, PMID) pairs a DOI and a PMID text hold: both split on ';' and stripped, PMIDs aligned with DOIs by
    position (blank where a paper has none), a pair whose DOI is blank dropped. Values, not text: what two metadata
    texts are compared by."""
    dois = [part.strip() for part in str(doi or "").split(";")]
    pmids = [part.strip() for part in str(pmid or "").split(";")]
    pmids += [""] * max(0, len(dois) - len(pmids))
    return tuple((d, p) for d, p in zip(dois, pmids) if d)


def write_publication_attributes(pairs: dict[int, tuple[str, str]], *, apply: bool, batch: int = 500,
                                 on_batch=None, enqueue_prefix: str = "batch:backfill", also_enqueue=()) -> dict:
    """Set DOI and PMID on each sample of ``pairs`` (sample id to its full DOI and PMID text), in batches of ``batch``.

    A sample is written only when its parsed values differ from the new ones (``publication_pairs``); a sample whose
    ``json_metadata`` is not a JSON object is skipped and listed in ``unreadable``, never replaced. With ``apply``,
    ``on_batch(rows)`` gets ``[(sample_id, old_text, new_text), ...]`` for one batch before any of its rows is
    written, and after the last batch the updated ids (plus ``also_enqueue``) are queued for a graph sync under
    ``<enqueue_prefix>:<n>``.
    """
    ids = sorted(pairs)
    missing = changed = 0
    updated: list[int] = []
    unreadable: list[int] = []
    with _cursor() as c:
        for i in range(0, len(ids), batch):
            chunk = ids[i:i + batch]
            placeholders = ",".join(["%s"] * len(chunk))
            c.execute(f"SELECT id, json_metadata FROM samples WHERE id IN ({placeholders})", chunk)
            rows = c.fetchall()
            missing += len(set(chunk) - {r[0] for r in rows})
            writes: list[tuple[int, str, str]] = []
            for sample_id, raw in rows:
                data = _metadata_object(raw)
                if data is None:
                    unreadable.append(sample_id)
                    continue
                doi, pmid = pairs[sample_id]
                if publication_pairs(data.get("DOI"), data.get("PMID")) == publication_pairs(doi, pmid):
                    continue
                changed += 1
                data["DOI"] = doi
                data["PMID"] = pmid
                writes.append((sample_id, raw or "", json.dumps(data)))
            if not apply or not writes:
                continue
            if on_batch is not None:
                on_batch(list(writes))
            for sample_id, _old, new in writes:
                c.execute("UPDATE samples SET json_metadata = %s WHERE id = %s", [new, sample_id])
                updated.append(sample_id)
    to_queue = sorted(set(updated) | {int(i) for i in also_enqueue})
    queued = enqueue_graph_sync(to_queue, prefix=enqueue_prefix) if (apply and to_queue) else 0
    return {"pairs": len(ids), "missing": missing, "changed": changed, "updated": updated,
            "unreadable": sorted(unreadable), "queued": queued, "to_queue": len(to_queue)}


def restore_publication_text(rows, *, enqueue_prefix: str) -> dict:
    """Rollback of ``write_publication_attributes``: for each ``(sample_id, old_text, new_text)``, write ``old_text``
    back where the current text still equals ``new_text``. A row already at ``old_text`` is left; a row changed since
    is reported, never overwritten; a sample gone is reported. The restored ids are queued under ``enqueue_prefix``."""
    wanted = {int(sid): (old, new) for sid, old, new in rows}
    ids = sorted(wanted)
    restored: list[int] = []
    already: list[int] = []
    changed_since: list[int] = []
    found: set[int] = set()
    with _cursor() as c:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ",".join(["%s"] * len(chunk))
            c.execute(f"SELECT id, json_metadata FROM samples WHERE id IN ({placeholders})", chunk)
            for sample_id, raw in c.fetchall():
                found.add(sample_id)
                old, new = wanted[sample_id]
                current = raw or ""
                if current == old:
                    already.append(sample_id)
                elif current == new:
                    c.execute("UPDATE samples SET json_metadata = %s WHERE id = %s", [old, sample_id])
                    restored.append(sample_id)
                else:
                    changed_since.append(sample_id)
    queued = enqueue_graph_sync(restored, prefix=enqueue_prefix) if restored else 0
    return {"restored": sorted(restored), "already_old": sorted(already), "changed_since": sorted(changed_since),
            "missing": sorted(set(ids) - found), "queued": queued}


class Command(BaseCommand):
    help = "Backfill DOI/PMID into sample json_metadata. Dry-run unless --apply."

    def add_arguments(self, parser):
        src = parser.add_mutually_exclusive_group(required=True)
        src.add_argument("--from-studies", action="store_true",
                         help="Derive pairs from studies.doi (local and dev only).")
        src.add_argument("--from-file",
                         help="TSV of sample_id, doi, pmid (production).")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--batch", type=int, default=500)

    def handle(self, *args, **options):
        pairs = (pairs_from_studies() if options["from_studies"]
                 else pairs_from_file(options["from_file"]))
        if not pairs:
            self.stdout.write("no sample/publication pairs found")
            return

        multi = sum(1 for d, _ in pairs.values() if SEPARATOR in d)
        self.stdout.write(
            f"{len(pairs)} sample(s) to update; {multi} appear in more than one paper"
        )

        report = write_publication_attributes(pairs, apply=options["apply"], batch=options["batch"])
        changed, updated = report["changed"], report["updated"]
        self.stdout.write(f"{changed} sample(s) would change" if not options["apply"]
                          else f"{changed} sample(s) updated")
        if report["missing"]:
            self.stdout.write(self.style.WARNING(
                f"{report['missing']} sample id(s) in the source do not exist here"))
        if report["unreadable"]:
            self.stdout.write(self.style.WARNING(
                f"{len(report['unreadable'])} sample(s) skipped: json_metadata is not a JSON object "
                f"(ids {report['unreadable'][:20]})"))
        if updated:
            queued = report["queued"]
            self.stdout.write(f"{queued} sample(s) queued for a graph sync")
            if queued < len(updated):
                self.stdout.write(self.style.WARNING(
                    f"{len(updated) - queued} sample(s) could not be queued for a graph sync; "
                    "the nightly targeted sync will find them"))
        if not options["apply"]:
            self.stdout.write("Dry run. Re-run with --apply to write.")
