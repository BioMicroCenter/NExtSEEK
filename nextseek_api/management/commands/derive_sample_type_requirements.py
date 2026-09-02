"""Fill dmac.sample_type_requirements from Neo4j's DERIVED_FROM edges.

Run on demand, not per request: the query walks ~522k edges, and the Download
Templates page deliberately keeps Neo4j off the request path. The relation only
changes when samples are uploaded.

    manage.py derive_sample_type_requirements [--dry-run]
"""

import json
import logging
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from neo4j import GraphDatabase

from nextseek_api.services.type_requirements import classify, classify_companions
from nextseek_api.services.sample_workbook import ASSAY_TITLE_SUFFIXES
from seek.models import NEXTSEEK_DATABASE, Sample_type_requirements

logger = logging.getLogger(__name__)

# One row per (child type, parent type). The joining assay lives on the edge,
# so it comes back with the pair rather than needing a second lookup.
#
# The type is read from the Sample node rather than joined through OF_TYPE:
# every one of the 51,374 sample nodes carries `.type`, both projections give
# byte-identical results (verified: 129 pairs, no differences), and the join
# form made Neo4j warn about a cartesian product on every run.
CYPHER = """
MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample)
WHERE c.type IS NOT NULL AND p.type IS NOT NULL AND c.type <> p.type
WITH c.type AS child, p.type AS parent, c,
     // #118: an edge used to keep only the lowest-id shared assay and drop the
     // rest, so an assay that lost the tie never appeared at all -- Cell
     // Isolation loses to Flow Cytometry on 998 production edges and was
     // invisible. Edges written or backfilled since that fix carry the whole
     // set in the plural property; coalesce falls back to the singular winner,
     // so an un-backfilled edge reports exactly what it did before. Same
     // handling as nextseek_api/services/sampletype_connections.py.
     CASE WHEN size(coalesce(r.internal_assay_titles, [])) > 0
          THEN r.internal_assay_titles
          ELSE [r.internal_assay_title] END AS titles
RETURN child, parent,
       count(DISTINCT c)        AS n,
       collect(DISTINCT titles) AS title_lists
"""


def _strip_suffix(title):
    """'Patient Visit - Metadata' -> 'Patient Visit'.

    SEEK suffixes an assay title by how the data is attached. That is SEEK
    bookkeeping, not a different experimental step, so the variants collapse.
    """
    if not title:
        return None
    for suffix in ASSAY_TITLE_SUFFIXES:
        if title.endswith(suffix):
            return title[: -len(suffix)]
    return title


class Command(BaseCommand):
    help = "Derive sample type upload requirements from the sample graph."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would be written and leave the table alone.",
        )

    def handle(self, *args, **options):
        try:
            config = settings.NEO4J_DATABASE
            with GraphDatabase.driver(config["URI"], auth=config["AUTH"]) as driver:
                with driver.session() as session:
                    rows = list(session.run(CYPHER))
        except Exception:
            # Exit before touching the table. A stale set of requirements is
            # worth more than none: the page keeps working either way, but an
            # emptied table silently drops every requirement.
            logger.exception("graph unavailable; sample_type_requirements untouched")
            self.stderr.write("graph unavailable; table left untouched")
            sys.exit(1)

        # One row per (child, parent), so there is nothing to merge: the count
        # is already a distinct-sample count for the pair. The previous shape
        # returned a row per assay variant and summed them, counting a child
        # once per assay its edge carried and inflating the support the rule
        # divides by.
        #
        # `title_lists` is a list of per-edge title lists. Strip the SEEK
        # suffix before de-duplicating, or "Patient Visit" and "Patient Visit -
        # Metadata" survive as two assays. classify() de-dupes again over the
        # parents it selects, so a wrong order here is absorbed downstream and
        # this file's own tests cannot see it.
        pairs = []
        for row in rows:
            titles = []
            for edge_titles in row["title_lists"] or []:
                for raw in edge_titles or []:
                    title = _strip_suffix(raw)
                    if title and title not in titles:
                        titles.append(title)
            pairs.append((row["child"], row["parent"], int(row["n"]), titles))
        # Same rows, read from both ends. classify() asks which parents a child
        # cannot be uploaded without; classify_companions() asks which child a
        # parent almost always goes on to produce.
        requirements = classify(pairs)
        companions = classify_companions(pairs)

        if options["dry_run"]:
            for req in sorted(requirements.values(), key=lambda r: -r.support):
                verb = "requires" if len(req.parents) == 1 else "requires one of"
                self.stdout.write(
                    f"{req.child:>9}  {verb:<16} {', '.join(req.parents):<24} "
                    f"{req.coverage:.0%} n={req.support}"
                )
            for comp in sorted(companions.values(), key=lambda c: -c.support):
                self.stdout.write(
                    f"{comp.parent:>9}  {'usually with':<16} {comp.child:<24} "
                    f"{comp.share:.0%} n={comp.support}"
                )
            self.stdout.write(
                f"{len(requirements)} requirements, {len(companions)} companions "
                f"(dry run, nothing written)"
            )
            return

        # USE_TZ is on, so datetime.now() would be a naive datetime.
        now = timezone.now()

        # Both kinds share one row shape: the code the user ticks, and the
        # codes that brings in. See the model for why the columns are named for
        # that direction rather than the graph's.
        write = [
            {
                "kind": Sample_type_requirements.KIND_REQUIRES,
                "trigger_code": req.child,
                "add_codes": json.dumps(req.parents),
                "coverage": round(req.coverage, 3),
                "support": req.support,
                "assay_titles": json.dumps(req.assays) if req.assays else None,
            }
            for req in requirements.values()
        ] + [
            {
                "kind": Sample_type_requirements.KIND_COMPANION,
                "trigger_code": comp.parent,
                "add_codes": json.dumps([comp.child]),
                "coverage": round(comp.share, 3),
                "support": comp.support,
                "assay_titles": json.dumps(comp.assays) if comp.assays else None,
            }
            for comp in companions.values()
        ]

        # Delete-then-insert is not a rewrite unless it is one transaction: an
        # error part way through the loop otherwise leaves the table half
        # written, and a page load landing between the delete and the last
        # insert sees a table that is empty or short. NEXTSEEK_DATABASE is the
        # alias the model is routed to.
        with transaction.atomic(using=NEXTSEEK_DATABASE):
            Sample_type_requirements.objects.all().delete()
            for row in write:
                Sample_type_requirements.objects.create(
                    source="graph", computed_at=now, **row
                )
        self.stdout.write(
            f"wrote {len(requirements)} requirements and {len(companions)} companions"
        )
