"""Regression tests for Plan 018 verifier source-evidence helpers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plan018_verifier_support import (
    derive_migration_graph,
    migration_lineage_status,
    summarize_junit,
)


def _migration(path: Path, dependencies: list[str]) -> None:
    dependency_rows = ", ".join(f'("nextseek_api", "{dependency}")' for dependency in dependencies)
    path.write_text(
        "from django.db import migrations\n\n"
        "class Migration(migrations.Migration):\n"
        f"    dependencies = [{dependency_rows}]\n"
        "    operations = []\n"
    )


class MigrationGraphTests(unittest.TestCase):
    def test_derives_leaf_and_ancestry_from_migration_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            migrations = Path(tempdir)
            _migration(migrations / "0001_initial.py", [])
            _migration(migrations / "0002_registry.py", ["0001_initial"])
            _migration(migrations / "0003_leaf.py", ["0002_registry"])

            graph = derive_migration_graph(migrations)

        self.assertEqual(graph.leaves, ("0003_leaf",))
        self.assertEqual(
            graph.ancestors_of("0003_leaf"),
            frozenset({"0001_initial", "0002_registry", "0003_leaf"}),
        )

    def test_reports_multiple_on_disk_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            migrations = Path(tempdir)
            _migration(migrations / "0001_initial.py", [])
            _migration(migrations / "0002_left.py", ["0001_initial"])
            _migration(migrations / "0002_right.py", ["0001_initial"])

            graph = derive_migration_graph(migrations)

        self.assertEqual(graph.leaves, ("0002_left", "0002_right"))

    def test_accepts_required_migration_below_a_newer_unique_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            migrations = Path(tempdir)
            _migration(migrations / "0001_initial.py", [])
            _migration(migrations / "0002_required.py", ["0001_initial"])
            _migration(migrations / "0003_later.py", ["0002_required"])

            status = migration_lineage_status(
                derive_migration_graph(migrations), "0002_required"
            )

        self.assertEqual(status.leaf, "0003_later")
        self.assertTrue(status.required_is_ancestor)
        self.assertEqual(status.error, None)

    def test_refuses_ambiguous_leaf_even_when_required_is_on_one_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            migrations = Path(tempdir)
            _migration(migrations / "0001_initial.py", [])
            _migration(migrations / "0002_required.py", ["0001_initial"])
            _migration(migrations / "0002_other.py", ["0001_initial"])

            status = migration_lineage_status(
                derive_migration_graph(migrations), "0002_required"
            )

        self.assertEqual(status.leaf, None)
        self.assertFalse(status.required_is_ancestor)
        self.assertEqual(status.error, "expected one migration leaf; found 2")

    def test_rejects_unique_leaf_outside_required_lineage(self) -> None:
        graph = derive_migration_graph(
            Path(__file__).resolve().parents[1] / "nextseek_api/migrations"
        )

        status = migration_lineage_status(graph, "9999_missing")

        self.assertEqual(status.leaf, graph.leaves[0])
        self.assertFalse(status.required_is_ancestor)
        self.assertEqual(status.error, None)


class JUnitSummaryTests(unittest.TestCase):
    def test_aggregates_all_leaf_suites(self) -> None:
        xml = """<testsuites>
  <testsuite name="one" tests="2" failures="0" errors="0" skipped="0" />
  <testsuite name="two" tests="3" failures="0" errors="0" skipped="0" />
</testsuites>"""
        with tempfile.TemporaryDirectory() as tempdir:
            junit = Path(tempdir) / "result.xml"
            junit.write_text(xml)
            summary = summarize_junit(junit)

        self.assertEqual((summary.tests, summary.failures, summary.errors, summary.skipped), (5, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
