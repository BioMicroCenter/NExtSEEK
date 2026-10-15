"""SEC-001: a batch upload writes only into the caller's own projects.

A non-superuser may start or validate an upload only for a project they belong to, may not change
the access grant through config_overrides, and an upsert never updates a sample outside their
projects. Superusers keep full access.
"""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from sqlalchemy import create_engine, event, text

from nextseek_api.batch_upload.config import BatchUploadConfig
from nextseek_api.batch_upload.errors import ErrorCollector, ErrorType
from nextseek_api.batch_upload.models import DirectionComputation, InsertableSample, RowOutcome
from nextseek_api.batch_upload.views import BatchUploadViewSet
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable

ROWS = [{"SampleType": "M.Mice", "json_metadata": {"Name": "mouse1"}}]
PERMISSION_OVERRIDES = {
    "enable_auto_permissions": False,
    "permissions_default_access_type": 2,
    "permissions_default_contributor_type": "Person",
}


def _member_of(*project_ids):
    return Scope(is_admin=False, person_id=9, project_ids=tuple(project_ids))


@pytest.fixture
def user():
    """The views read the user only through the patched resolve_scope (and is_superuser for lababbv)."""
    u = MagicMock(is_authenticated=True, is_superuser=False, pk=5, username="member")
    return u


def _start(user, project_id, **extra):
    request = APIRequestFactory().post(
        "/api/batch-upload/start/",
        data={"project_id": project_id, "rows": ROWS, **extra},
        format="json",
    )
    force_authenticate(request, user=user)
    return BatchUploadViewSet.as_view({"post": "start"})(request)


@patch("nextseek_api.batch_upload.views.register_job")
@patch("nextseek_api.batch_upload.views.run_batch_upload_task")
@patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
class TestStart:

    def test_non_member_gets_403_and_no_job(self, _ctx, task, register, user):
        with patch("nextseek_api.batch_upload.views.resolve_scope", create=True, return_value=_member_of(3)):
            response = _start(user, 7, update_existing=True)
        assert response.status_code == 403
        task.delay.assert_not_called()
        register.assert_not_called()

    def test_member_starts_with_scope_and_no_permission_overrides(self, _ctx, task, register, user):
        task.delay.return_value.id = "job-1"
        with patch("nextseek_api.batch_upload.views.resolve_scope", create=True, return_value=_member_of(3, 7)):
            response = _start(user, 7, update_existing=True,
                              config_overrides={**PERMISSION_OVERRIDES, "max_rows_per_batch": 50})
        assert response.status_code == 202
        kwargs = task.delay.call_args.kwargs
        assert kwargs["scope_project_ids"] == [3, 7]
        # An allowlist: a member keeps update_existing and nothing else, so a tunable added later stays closed.
        assert kwargs["config_overrides"] == {"update_existing": True}

    def test_superuser_any_project_unscoped_and_overrides_kept(self, _ctx, task, register, user):
        user.is_superuser = True
        task.delay.return_value.id = "job-2"
        with patch("nextseek_api.batch_upload.views.resolve_scope",
                   return_value=Scope(is_admin=True, person_id=None, project_ids=())):
            response = _start(user, 7, config_overrides=dict(PERMISSION_OVERRIDES))
        assert response.status_code == 202
        kwargs = task.delay.call_args.kwargs
        assert kwargs["scope_project_ids"] is None
        assert kwargs["config_overrides"]["enable_auto_permissions"] is False

    @pytest.mark.parametrize("failure", [ScopeUnavailable("no person"), RuntimeError("db down")])
    def test_unreadable_membership_refuses(self, _ctx, task, register, user, failure):
        with patch("nextseek_api.batch_upload.views.resolve_scope", create=True, side_effect=failure):
            response = _start(user, 7)
        assert response.status_code == 403
        task.delay.assert_not_called()


class TestValidate:

    def _validate(self, user, project_id):
        request = APIRequestFactory().post(
            "/api/batch-upload/validate/", data={"project_id": project_id, "rows": ROWS}, format="json",
        )
        force_authenticate(request, user=user)
        return BatchUploadViewSet.as_view({"post": "validate"})(request)

    def test_member_validates(self, user):
        result = MagicMock()
        result.model_dump.return_value = {"valid": True}
        with patch("nextseek_api.batch_upload.views.resolve_scope", create=True, return_value=_member_of(7)), \
             patch("nextseek_api.batch_upload.views.run_validation_multi", return_value=result) as run:
            response = self._validate(user, 7)
        assert response.status_code == 200
        run.assert_called_once()

    def test_non_member_gets_403(self, user):
        with patch("nextseek_api.batch_upload.views.resolve_scope", create=True, return_value=_member_of(3)), \
             patch("nextseek_api.batch_upload.views.run_validation_multi") as run:
            response = self._validate(user, 7)
        assert response.status_code == 403
        run.assert_not_called()


# ── the pipeline: an upsert never updates another project's sample ────────


@pytest.fixture
def conn_factory():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _mysql_now(dbapi_conn, _record):  # the orphan rewrite writes updated_at = NOW()
        dbapi_conn.create_function("NOW", 0, lambda: "2026-10-14 00:00:00")

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE samples (id INTEGER PRIMARY KEY, uuid TEXT, policy_id INTEGER, "
                          "json_metadata TEXT, title TEXT, updated_at TEXT, contributor_id INTEGER)"))
        conn.execute(text("CREATE TABLE sample_attributes (sample_type_id INTEGER, title TEXT)"))
        conn.execute(text("CREATE TABLE policies (id INTEGER PRIMARY KEY, access_type INTEGER)"))
        conn.execute(text("CREATE TABLE permissions (id INTEGER PRIMARY KEY, policy_id INTEGER, "
                          "contributor_type TEXT, contributor_id INTEGER, access_type INTEGER)"))
        conn.execute(text("CREATE TABLE projects_samples (project_id INTEGER, sample_id INTEGER)"))
        conn.execute(text("CREATE TABLE assay_assets (asset_id INTEGER, asset_type TEXT, assay_id INTEGER)"))
        conn.execute(text("""INSERT INTO samples VALUES (1, 'MINE-1', 11, '{"Parent":"P1"}', 'mine', NULL, NULL), """
                          """(2, 'THEIRS-1', 12, '{"Parent":"P1"}', 'theirs', NULL, NULL)"""))
        conn.execute(text("ALTER TABLE samples ADD COLUMN sample_type_id INTEGER"))
        # The member's project holds the edit right on both samples' policies.
        conn.execute(text("INSERT INTO policies VALUES (11, 0), (12, 0)"))
        conn.execute(text("INSERT INTO permissions (policy_id, contributor_type, contributor_id, access_type) "
                          "VALUES (11, 'Project', 7, 4), (12, 'Project', 7, 4)"))
        conn.execute(text("INSERT INTO projects_samples VALUES (7, 1), (8, 2)"))
        # MINE-1 is already linked to assay 80, which belongs to project 8.
        conn.execute(text("INSERT INTO assay_assets VALUES (1, 'Sample', 80)"))
    return engine.connect


def test_details_are_scoped_to_the_given_projects(conn_factory):
    from nextseek_api.batch_upload.update import load_existing_sample_details

    with conn_factory() as conn:
        assert set(load_existing_sample_details(["MINE-1", "THEIRS-1"], conn)) == {"MINE-1", "THEIRS-1"}
        assert set(load_existing_sample_details(["MINE-1", "THEIRS-1"], conn, project_ids=(7,))) == {"MINE-1"}
        assert load_existing_sample_details(["MINE-1", "THEIRS-1"], conn, project_ids=()) == {}


def test_update_existing_cannot_touch_another_projects_sample(conn_factory):
    from nextseek_api.batch_upload.insert import process_batches
    import nextseek_api.batch_upload.update as update_mod

    def fake_bulk(rows, details, **kwargs):
        return {s.uuid: RowOutcome(status="success", sample_id=details[s.uuid]["sample_id"]) for s in rows}

    config = BatchUploadConfig()
    config.scope_project_ids = (7,)
    samples = [InsertableSample(uuid=u, title="new title", sample_type_id=1, json_metadata="{}")
               for u in ("MINE-1", "THEIRS-1")]
    errors = ErrorCollector()
    with patch.object(update_mod, "bulk_update_samples", side_effect=fake_bulk) as bulk:
        result = process_batches(
            insertable_samples=samples,
            project_id=7,
            contributor_id=1,
            config=config,
            direction_computation=DirectionComputation({}, {}, {}, {}, {}),
            error_collector=errors,
            existing_samples={"MINE-1": 1, "THEIRS-1": 2},
            update_existing=True,
            conn_factory=conn_factory,
        )
    assert [s.uuid for s in bulk.call_args.kwargs["rows"]] == ["MINE-1"]
    assert result.outcomes["MINE-1"].status == "success"
    assert result.outcomes["THEIRS-1"].status == "failed"
    assert "not in one of your projects" in result.outcomes["THEIRS-1"].reason
    assert [e.error_type for e in errors.all_errors()] == [ErrorType.PERMISSION_DENIED]


def test_task_without_scope_changes_no_existing_sample():
    """A queued message that carries no scope (an older sender) is treated as no project."""
    from nextseek_api.batch_upload import tasks

    seen = {}

    def fake_run(**kwargs):
        seen["scope"] = kwargs["config"].scope_project_ids
        return {"totals": {}, "errors": []}

    with patch.object(tasks, "run_batch_upload_multi", side_effect=fake_run), \
         patch.object(tasks.run_batch_upload_task, "update_state"):
        try:
            tasks.run_batch_upload_task.run(rows=[], project_id=7, contributor_id=1, config_overrides={})
        except Exception:  # noqa: BLE001 (only the config handed to the pipeline matters here)
            pass
    assert seen["scope"] == ()


def _process(conn_factory, samples, existing, *, scope, update_existing):
    from nextseek_api.batch_upload.insert import process_batches

    config = BatchUploadConfig()
    config.scope_project_ids = scope
    errors = ErrorCollector()
    result = process_batches(
        insertable_samples=samples,
        project_id=7,
        contributor_id=1,
        config=config,
        direction_computation=DirectionComputation({}, {}, {}, {}, {}),
        error_collector=errors,
        existing_samples=existing,
        update_existing=update_existing,
        conn_factory=conn_factory,
    )
    return result, errors


def _fake_bulk(rows, details, **kwargs):
    return {s.uuid: RowOutcome(status="success", sample_id=details[s.uuid]["sample_id"]) for s in rows}


def test_a_superuser_may_update_any_projects_sample(conn_factory):
    import nextseek_api.batch_upload.update as update_mod

    samples = [InsertableSample(uuid=u, title="t", sample_type_id=1, json_metadata="{}") for u in ("MINE-1", "THEIRS-1")]
    with patch.object(update_mod, "bulk_update_samples", side_effect=_fake_bulk) as bulk:
        _process(conn_factory, samples, {"MINE-1": 1, "THEIRS-1": 2}, scope=None, update_existing=True)
    assert [s.uuid for s in bulk.call_args.kwargs["rows"]] == ["MINE-1", "THEIRS-1"]


@patch("nextseek_api.assay_registration.resolver.projects_for_assays", return_value={80: {8}, 81: {8}, 70: {7}})
def test_a_member_cannot_link_samples_into_another_projects_assay(_assays, conn_factory):
    import nextseek_api.batch_upload.update as update_mod

    samples = [
        InsertableSample(uuid="NEW-1", title="t", sample_type_id=1, json_metadata="{}", assay_ids=[81]),
        InsertableSample(uuid="MINE-1", title="t", sample_type_id=1, json_metadata="{}", assay_ids=[70, 80]),
        InsertableSample(uuid="MINE-2", title="t", sample_type_id=1, json_metadata="{}", assay_ids=[81]),
    ]
    with patch.object(update_mod, "bulk_update_samples", side_effect=_fake_bulk) as bulk, \
         patch.object(update_mod, "load_existing_sample_details",
                      return_value={"MINE-1": {"sample_id": 1}, "MINE-2": {"sample_id": 1}}):
        result, errors = _process(conn_factory, samples, {"MINE-1": 1, "MINE-2": 1}, scope=(7,), update_existing=True)
    # A new sample naming another project's assay is refused before any insert.
    assert result.outcomes["NEW-1"].status == "failed" and result.inserted_count == 0
    # An existing sample keeps a link it already has (80) and may add its own project's (70) ...
    assert [s.uuid for s in bulk.call_args.kwargs["rows"]] == ["MINE-1"]
    # ... but may not add a new link into another project's assay (81).
    assert result.outcomes["MINE-2"].status == "failed"
    assert {e.error_type for e in errors.all_errors()} == {ErrorType.PERMISSION_DENIED}


def test_orphan_rewrite_stays_inside_the_scope(conn_factory):
    from nextseek_api.batch_upload.orphan_resolution import resolve_orphans

    orphans = [{"id": 1, "uuid": "MINE-1", "matched_tokens": {"P1": "NEW-UID"}},
               {"id": 2, "uuid": "THEIRS-1", "matched_tokens": {"P1": "NEW-UID"}}]
    with conn_factory() as conn:
        stats = resolve_orphans(orphans=orphans, sql_conn=conn, project_ids=(7,), person_id=9)
        meta = dict(conn.execute(text("SELECT uuid, json_metadata FROM samples")).fetchall())
    assert stats["sample_ids"] == [1]
    assert "NEW-UID" in meta["MINE-1"] and "P1" in meta["THEIRS-1"] and "NEW-UID" not in meta["THEIRS-1"]


def test_the_orphan_task_inherits_the_upload_scope():
    from nextseek_api.batch_upload import tasks

    def fake_run(**kwargs):
        return {"totals": {}, "errors": [], "_identity_map": {"P1": "NEW-UID"}, "_parent_info": {}}

    with patch.object(tasks, "run_batch_upload_multi", side_effect=fake_run), \
         patch.object(tasks.run_batch_upload_task, "update_state"), \
         patch.object(tasks.resolve_orphans_task, "apply_async") as dispatch:
        tasks.run_batch_upload_task.run(rows=[], project_id=7, contributor_id=1, config_overrides={},
                                        scope_project_ids=[7])
    assert dispatch.call_args.kwargs["kwargs"]["scope_project_ids"] == [7]
