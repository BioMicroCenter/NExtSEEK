"""A model index must declare the name its migration created.

Without an explicit ``name=``, Django regenerates one from the model and field names on every
``makemigrations`` run and proposes a rename, so ``makemigrations --check`` never exits 0. That is
why the blocking CI step carries no migration check at all (graph-sync task T8, a stated departure
from its spec's CI-2 requirement), and why a model changed without its migration is invisible to CI.

Hermetic: reads the model's ``_meta`` and the migration file, no database.
"""
import re
from pathlib import Path

from nextseek_api.assistant import models_db

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = REPO_ROOT / "nextseek_api" / "migrations" / "0010_turn_ledger.py"


class TestTheTurnLedgerIndexIsNamed:
    def test_every_index_declares_a_name(self):
        names = [getattr(index, "name", None) for index in models_db.TurnLedger._meta.indexes]
        assert names and all(names), (
            "a TurnLedger index has no explicit name, so Django proposes a rename on every "
            f"makemigrations run: {names}"
        )

    def test_the_name_is_the_one_the_migration_created(self):
        """A different name would generate a real rename migration and real DDL against a live box."""
        created = re.findall(r'name="(assistant_t_[A-Za-z0-9_]+)"', MIGRATION.read_text(encoding="utf-8"))
        assert created, f"no index name found in {MIGRATION.name}"
        declared = {getattr(index, "name", None) for index in models_db.TurnLedger._meta.indexes}
        assert set(created) <= declared, (
            f"the migration created {created} but the model declares {sorted(declared)}; "
            "these must match or makemigrations proposes a rename"
        )

    def test_the_indexed_fields_are_unchanged(self):
        fields = [tuple(index.fields) for index in models_db.TurnLedger._meta.indexes]
        assert ("task_family", "route") in fields, fields
