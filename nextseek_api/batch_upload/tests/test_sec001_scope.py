"""SEC-001: a batch upload writes only into the caller's own projects.

A non-superuser may start or validate an upload only for a project they belong to, may not change
the access grant through config_overrides, and an upsert never updates a sample outside their
projects. Superusers keep full access.
"""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from sqlalchemy import create_engine, text

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
        overrides = kwargs["config_overrides"]
        assert not set(PERMISSION_OVERRIDES) & set(overrides)
        assert overrides["max_rows_per_batch"] == 50 and overrides["update_existing"] is True

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
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE samples (id INTEGER PRIMARY KEY, uuid TEXT, policy_id INTEGER, "
                          "json_metadata TEXT, title TEXT)"))
        conn.execute(text("CREATE TABLE projects_samples (project_id INTEGER, sample_id INTEGER)"))
        conn.execute(text("INSERT INTO samples VALUES (1, 'MINE-1', 11, '{}', 'mine'), "
                          "(2, 'THEIRS-1', 12, '{}', 'theirs')"))
        conn.execute(text("INSERT INTO projects_samples VALUES (7, 1), (8, 2)"))
    return engine.connect


def test_details_are_scoped_to_the_given_projects(conn_factory):
    from nextseek_api.batch_upload.update import load_existing_sample_details

    with conn_factory() as conn:
        assert set(load_existing_sample_details(["MINE-1", "THEIRS-1"], conn)) == {"MINE-1", "THEIRS-1"}
        assert set(load_existing_sample_details(["MINE-1", "THEIRS-1"], conn, project_ids=(7,))) == {"MINE-1"}
        assert load_existing_sample_details(["MINE-1", "THEIRS-1"], conn, project_ids=()) == {}


@patch("nextseek_api.batch_upload.insert.determine_resume_uid", return_value=None)
def test_update_existing_cannot_touch_another_projects_sample(_resume, conn_factory):
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
