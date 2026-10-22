"""MariaDB 10.5 round trip for INSERT ... RETURNING. Skipped unless SPIKE_DB_HOST/USER/PASSWORD are set
(the MariaDB step in ci-pytest.yml sets them)."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("pymysql")
sa = pytest.importorskip("sqlalchemy")

# Same throwaway database, `samples` table and migration SQL as the name_identity tests.
from nextseek_api.batch_upload.tests.test_migration_name_identity import (  # noqa: E402,F401
    _load_migration_sql,
    throwaway_db,
)

_HOST = os.environ.get("SPIKE_DB_HOST")
_USER = os.environ.get("SPIKE_DB_USER")
_PASS = os.environ.get("SPIKE_DB_PASSWORD")
_PORT = os.environ.get("SPIKE_DB_PORT") or "3306"

pytestmark = pytest.mark.skipif(
    not (_HOST and _USER and _PASS), reason="needs SPIKE_DB_HOST/USER/PASSWORD (MariaDB)"
)


def test_returning_capability_and_three_row_round_trip(monkeypatch, throwaway_db):
    from nextseek_api.batch_upload import db_engine
    from nextseek_api.batch_upload.insert_strategies import insert_samples

    raw, db_name = throwaway_db
    forward, _ = _load_migration_sql()
    engine = sa.create_engine(f"mysql+pymysql://{_USER}:{_PASS}@{_HOST}:{_PORT}/{db_name}")
    try:
        with raw.cursor() as c:
            c.execute(forward)  # adds name_identity, the MariaDB-specific virtual column

        monkeypatch.setitem(db_engine.CAPABILITIES, "insert_returning", False)
        db_engine._detect_capabilities(engine)
        assert db_engine.CAPABILITIES["insert_returning"] is True

        rows = [
            {
                "title": f"t{i}", "sample_type_id": 1, "json_metadata": "{}", "uuid": str(uuid.uuid4()),
                "contributor_id": 1, "first_letter": "t", "policy_id": 1,
            }
            for i in range(3)
        ]
        with engine.begin() as conn:
            out = insert_samples(rows, conn)
        assert [u for _, u in out] == [r["uuid"] for r in rows]
        assert len({i for i, _ in out}) == 3
    finally:
        engine.dispose()
