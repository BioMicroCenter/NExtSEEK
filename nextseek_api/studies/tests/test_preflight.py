"""What apply, the graph step and rollback check first (tool spec 4.6, 7 row 1, T20, T24)."""
from unittest.mock import MagicMock

from nextseek_api.studies import preflight
from nextseek_api.studies.tests.conftest import FakeReader, StudyRow


class LockConn:
    def __init__(self, answer):
        self.answer, self.sent, self.closed, self.invalidated = answer, [], False, False

    def execute(self, statement, params=None):
        self.sent.append(str(statement))
        result = MagicMock()
        result.scalar.return_value = self.answer if "GET_LOCK" in str(statement) else 1
        return result

    def close(self):
        self.closed = True

    def invalidate(self):
        self.invalidated = True


def _mysql(monkeypatch, conn):
    monkeypatch.setitem(preflight.settings.DATABASES[preflight.settings.SEEK_DATABASE], "ENGINE",
                        "django.db.backends.mysql")
    engine = MagicMock()
    engine.connect.return_value = conn
    monkeypatch.setattr(preflight, "get_engine", lambda: engine)
    return engine


def test_the_run_lock_is_taken_and_released_on_its_own_connection(monkeypatch):
    conn = LockConn(1)
    _mysql(monkeypatch, conn)
    with preflight.run_lock() as held:
        assert held and any("GET_LOCK" in s for s in conn.sent)
    assert any("RELEASE_LOCK" in s for s in conn.sent) and conn.closed


def test_a_second_run_does_not_get_the_lock(monkeypatch):
    conn = LockConn(0)
    _mysql(monkeypatch, conn)
    with preflight.run_lock() as held:
        assert not held
    assert not any("RELEASE_LOCK" in s for s in conn.sent) and conn.closed


def test_sqlite_needs_no_lock():
    with preflight.run_lock() as held:
        assert held


def test_the_studies_release_must_be_done(monkeypatch):
    monkeypatch.setattr(preflight, "_switch_follows", lambda: False)
    monkeypatch.setattr(preflight, "_acting_merge_ids", lambda d, db: [])
    assert "NEXTSEEK_GRAPH_SYNC_STUDY_LINKS" in preflight.studies_release_refusal(None, "neo4j")
    monkeypatch.setattr(preflight, "_switch_follows", lambda: True)
    monkeypatch.setattr(preflight, "_acting_merge_ids", lambda d, db: [3, 4])
    assert "[3, 4]" in preflight.studies_release_refusal(None, "neo4j")
    monkeypatch.setattr(preflight, "_acting_merge_ids", lambda d, db: [])
    assert preflight.studies_release_refusal(None, "neo4j") is None


def test_apply_refuses_a_moved_bucket_and_a_low_next_study_id(apply_env, monkeypatch):
    run_dir, plan = apply_env.make()
    reader = FakeReader(apply_env.world)
    assert preflight.apply_refusal(plan, plan.targets, None, "neo4j", reader) is None
    apply_env.world.next_study_id = 50
    assert "not above" in preflight.apply_refusal(plan, plan.targets, None, "neo4j", reader)
    apply_env.world.next_study_id = 100
    apply_env.world.studies[0] = StudyRow(20, 7, "Alpha Holding", None)
    apply_env.world.studies.append(StudyRow(25, 7, "Alpha Unpublished", None))
    assert "bucket" in preflight.apply_refusal(plan, plan.targets, None, "neo4j", reader)


def test_the_acting_ids_are_the_merge_selections_acting_kinds(monkeypatch):
    from nextseek_api.graph_sync import study_merge

    seen = []

    def selection(driver, db, ids=None, *, detail=True):
        seen.append(detail)
        return {"kinds": {3: study_merge.MERGE, 4: study_merge.ID_COLLISION, 5: study_merge.REKEY_IN_PLACE,
                          6: study_merge.LEGACY_ONLY, 7: study_merge.MERGE_OTHER_INVESTIGATION}}

    monkeypatch.setattr(study_merge, "plan", selection)
    assert preflight._acting_merge_ids(None, "neo4j") == [3, 5, 7]
    assert seen == [False]
