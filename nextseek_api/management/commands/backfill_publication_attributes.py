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


def enqueue_graph_sync(ids: list[int], batch: int | None = None) -> int:
    """Queue the samples this run updated for a graph sync. Returns how many ids were queued.

    Called after the updates are committed, never inside them. hooks.enqueue never raises, so a batch that cannot be
    queued costs the backfill nothing: those samples wait for the nightly targeted sync instead.
    """
    size = batch or GRAPH_SYNC_BATCH
    ordered = sorted(ids)
    queued = 0
    for n, start in enumerate(range(0, len(ordered), size)):
        chunk = ordered[start:start + size]
        if hooks.enqueue("samples", f"batch:backfill:{n}", chunk):
            queued += len(chunk)
    return queued


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

        ids = sorted(pairs)
        missing = 0
        changed = 0
        updated: list[int] = []
        with _cursor() as c:
            for i in range(0, len(ids), options["batch"]):
                chunk = ids[i:i + options["batch"]]
                placeholders = ",".join(["%s"] * len(chunk))
                c.execute(
                    f"SELECT id, json_metadata FROM samples WHERE id IN ({placeholders})",
                    chunk,
                )
                rows = c.fetchall()
                found = {r[0] for r in rows}
                missing += len(set(chunk) - found)
                for sample_id, raw in rows:
                    doi, pmid = pairs[sample_id]
                    new = updated_metadata(raw, doi, pmid)
                    if new == (raw or ""):
                        continue
                    changed += 1
                    if options["apply"]:
                        c.execute(
                            "UPDATE samples SET json_metadata = %s WHERE id = %s",
                            [new, sample_id],
                        )
                        updated.append(sample_id)

        self.stdout.write(f"{changed} sample(s) would change" if not options["apply"]
                          else f"{changed} sample(s) updated")
        if missing:
            self.stdout.write(self.style.WARNING(
                f"{missing} sample id(s) in the source do not exist here"))
        if updated:
            queued = enqueue_graph_sync(updated)
            self.stdout.write(f"{queued} sample(s) queued for a graph sync")
            if queued < len(updated):
                self.stdout.write(self.style.WARNING(
                    f"{len(updated) - queued} sample(s) could not be queued for a graph sync; "
                    "the nightly targeted sync will find them"))
        if not options["apply"]:
            self.stdout.write("Dry run. Re-run with --apply to write.")
