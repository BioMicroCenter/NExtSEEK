import os
import uuid
import json
from pathlib import Path

from django.db import connections


def test_disposable_database_identity_is_random_and_not_shared(settings, disposable_attribute_db):
    alias = disposable_attribute_db.django_alias
    parsed = uuid.UUID(disposable_attribute_db.database_uuid)
    name = settings.DATABASES[alias]["NAME"]
    assert str(parsed).replace("-", "")[:12] in name.replace("-", "")
    assert name not in {"dmac", "seek_production", "test_dmac"}


def test_sentinel_transaction_and_teardown_contract(settings, disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    alias = disposable_attribute_db.django_alias
    assert settings.SEEK_DATABASE == alias
    marker = f"attribute_sentinel_{uuid.uuid4().hex}"
    with connections[alias].cursor() as cursor:
        cursor.execute(f"CREATE TABLE `{marker}` (id INTEGER PRIMARY KEY)")
        cursor.execute(f"INSERT INTO `{marker}` (id) VALUES (1)")
        cursor.execute(f"SELECT COUNT(*) FROM `{marker}`")
        assert cursor.fetchone()[0] == 1
        cursor.execute(f"DROP TABLE `{marker}`")
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", [marker])
        assert cursor.fetchone()[0] == 0


def test_shared_database_denylist_is_exact():
    assert os.environ["ATTRIBUTE_TEST_SHARED_DB_DENYLIST"].split(",") == ["dmac", "seek_production", "test_dmac"]


def test_disposable_uuid_is_required():
    assert os.environ["ATTRIBUTE_TEST_REQUIRE_DISPOSABLE_DB_UUID"] == "1"


def test_freeze_measured_baseline_artifacts(settings, disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    from scripts.freeze_attribute_baseline import freeze_baseline

    alias = disposable_attribute_db.django_alias
    repo_root = Path(__file__).resolve().parents[2]
    freeze_baseline(alias, repo_root)
    state_root = Path("/home/taishajo/work/state/attribute-viewset")
    for name in ("DB-FACTS.json", "DEPENDENT-SURFACES.json", "BASELINE.md", "TEST-LANES.md"):
        path = state_root / name
        assert path.is_file() and path.stat().st_size > 0
    db_facts = json.loads((state_root / "DB-FACTS.json").read_text())
    assert set(db_facts) >= {"schema_version", "observed_at", "commands", "observed_facts", "user_decisions", "unresolved_policy"}
    surfaces = json.loads((state_root / "DEPENDENT-SURFACES.json").read_text())
    observed = surfaces["observed_facts"]
    assert set(observed) >= {"sql_identifier_allowlist", "surfaces", "rules_sha256"}
