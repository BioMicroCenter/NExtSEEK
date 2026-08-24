"""Copy studies.doi / studies.pmid from MySQL onto Neo4j Study nodes.

Run after editing DOIs by any route other than `fill_study_publications --apply`,
or to repair a deferred sync.
"""

from django.core.management.base import BaseCommand

from seek.publications_graph import sync_study_publications


class Command(BaseCommand):
    help = "Sync study DOI/PMID attributes from MySQL into Neo4j."

    def handle(self, *args, **options):
        stats = sync_study_publications()
        self.stdout.write(
            f"studies={stats['studies']} "
            f"with_doi={stats['with_doi']} with_pmid={stats['with_pmid']}"
        )
