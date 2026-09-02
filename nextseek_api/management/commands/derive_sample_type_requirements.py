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

from nextseek_api.services.type_requirements import classify
from nextseek_api.services.sample_workbook import ASSAY_TITLE_SUFFIXES
from seek.models import NEXTSEEK_DATABASE, Sample_type_requirements

logger = logging.getLogger(__name__)

# One row per (child type, parent type, joining assay). internal_assay_title
# lives on the edge, so the assay that joins the two samples comes back with
# the pair rather than needing a second lookup.
CYPHER = """
MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample)
MATCH (c)-[:OF_TYPE]->(ct:SampleType), (p)-[:OF_TYPE]->(pt:SampleType)
WHERE ct.title <> pt.title
RETURN ct.title AS child, pt.title AS parent,
       r.internal_assay_title AS assay, count(DISTINCT c) AS n
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

        # (child, parent) may appear several times, once per assay variant.
        # The suffix must be stripped (_strip_suffix) before dedup, or every
        # SEEK title variant appended below counts as a distinct assay. Note
        # that classify() below also de-dupes the titles of the parents it
        # selects, so this file's own tests cannot detect the wrong order on
        # their own -- a bug here is silently absorbed downstream.
        merged = {}
        for row in rows:
            key = (row["child"], row["parent"])
            count, assays = merged.get(key, (0, []))
            count += int(row["n"])
            title = _strip_suffix(row["assay"])
            if title and title not in assays:
                assays.append(title)
            merged[key] = (count, assays)

        requirements = classify(
            (child, parent, count, assays)
            for (child, parent), (count, assays) in merged.items()
        )

        if options["dry_run"]:
            for req in sorted(requirements.values(), key=lambda r: -r.support):
                kind = "requires" if len(req.parents) == 1 else "requires one of"
                self.stdout.write(
                    f"{req.child:>9}  {kind} {', '.join(req.parents):<24} "
                    f"{req.coverage:.0%} n={req.support}"
                )
            self.stdout.write(f"{len(requirements)} requirements (dry run, nothing written)")
            return

        # USE_TZ is on, so datetime.now() would be a naive datetime.
        now = timezone.now()
        # Delete-then-insert is not a rewrite unless it is one transaction: an
        # error part way through the loop otherwise leaves the table half
        # written, and a page load landing between the delete and the last
        # insert sees a table that is empty or short. NEXTSEEK_DATABASE is the
        # alias the model is routed to.
        with transaction.atomic(using=NEXTSEEK_DATABASE):
            Sample_type_requirements.objects.all().delete()
            for req in requirements.values():
                Sample_type_requirements.objects.create(
                    child_code=req.child,
                    parent_codes=json.dumps(req.parents),
                    coverage=round(req.coverage, 3),
                    support=req.support,
                    assay_titles=json.dumps(req.assays) if req.assays else None,
                    source="graph",
                    computed_at=now,
                )
        self.stdout.write(f"wrote {len(requirements)} sample type requirements")
