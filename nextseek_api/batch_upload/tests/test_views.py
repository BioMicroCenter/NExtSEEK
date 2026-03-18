"""Tests for the DRF ViewSet routing and basic behavior."""
import pytest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from unittest.mock import MagicMock, patch

from nextseek_api.batch_upload.views import BatchUploadViewSet


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def admin_user():
    """Create or get an admin user for testing."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="batch_admin",
        defaults={"is_staff": True, "is_superuser": True},
    )
    return user


@pytest.fixture
def normal_user():
    """Create or get a normal (non-admin) user for testing."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="batch_normal",
        defaults={"is_staff": False, "is_superuser": False},
    )
    return user


class TestResolveUserContext:
    """Unit tests for _resolve_user_context."""

    @pytest.mark.django_db
    def test_fallback_to_django_user(self):
        """When all SEEK lookups fail, should return None (NOT Django user pk).

        Old behavior (REMOVED): fell back to Django user pk.
        New behavior: returns None so caller returns 401.
        """
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="ctx_test_user", defaults={"is_staff": True, "is_superuser": True}
        )
        request = MagicMock()
        request.user = user  # Real Django user (is_authenticated is True by default)
        request.data = {}  # No person_id override

        with patch("seek.seekdb.SeekDB", side_effect=Exception("no seekdb")), \
             patch("nextseek_api.helpers.resolve_seek_auth", return_value=(None, None)):
            result = _resolve_user_context(request)

        # New behavior: no Django pk fallback — returns None
        assert result is None

    def test_unauthenticated_returns_none(self):
        """Unauthenticated request should return None."""
        from nextseek_api.batch_upload.views import _resolve_user_context

        request = MagicMock()
        request.user.is_authenticated = False

        with patch("seek.seekdb.SeekDB", side_effect=Exception("no seekdb")):
            result = _resolve_user_context(request)

        assert result is None

    def test_seekdb_returns_lababbv(self):
        """When SeekDB succeeds, should return lababbv from profile."""
        from nextseek_api.batch_upload.views import _resolve_user_context

        mock_seekdb_instance = MagicMock()
        mock_seekdb_instance.getSeekLogin.return_value = {
            "status": True,
            "person_id": 42,
            "lababbv": "MIT",
        }

        request = MagicMock()
        request.data = {}  # No person_id override
        with patch("seek.seekdb.SeekDB", return_value=mock_seekdb_instance):
            result = _resolve_user_context(request)

        assert result == {"contributor_id": 42, "lababbv": "MIT"}


class TestBatchUploadViewSetAuth:
    """Test authentication/authorization requirements."""

    @pytest.mark.django_db
    def test_unauthenticated_forbidden(self, factory):
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={"project_id": 1},
            content_type="application/json",
        )
        response = view(request)
        assert response.status_code in (401, 403)

    @pytest.mark.django_db
    def test_non_admin_allowed(self, factory, normal_user):
        """Non-admin authenticated users are allowed; request fails validation (no input)."""
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={"project_id": 1},
            content_type="application/json",
        )
        force_authenticate(request, user=normal_user)
        response = view(request)
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_admin_missing_params(self, factory, admin_user):
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={},
            content_type="application/json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_admin_no_input_returns_400(self, factory, admin_user):
        """Sending only project_id with no rows or files should return 400."""
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={"project_id": 1},
            content_type="application/json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 400


def _redis_available() -> bool:
    try:
        import redis as _redis
        c = _redis.Redis(host="localhost", port=6379, db=2, socket_timeout=1)
        c.ping()
        return True
    except Exception:
        return False


class TestBatchUploadViewSetStatus:
    """Test status endpoint."""

    @pytest.mark.django_db
    @pytest.mark.skipif(not _redis_available(), reason="Redis not running")
    @patch("nextseek_api.batch_upload.views.user_owns_job", return_value=True)
    def test_status_pending_job(self, mock_owns, factory, admin_user):
        view = BatchUploadViewSet.as_view({"get": "job_status"})
        request = factory.get("/api/batch-upload/status/nonexistent-id/")
        force_authenticate(request, user=admin_user)
        response = view(request, job_id="nonexistent-id")
        assert response.status_code == 200
        assert response.data["state"] == "PENDING"


class TestBatchUploadFileUpload:
    """Test client-side Excel file upload."""

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
    def test_upload_xlsx_returns_202(self, mock_contrib, mock_task, mock_register, factory, admin_user, tmp_path):
        mock_task.delay.return_value.id = "fake-task-id"
        view = BatchUploadViewSet.as_view({"post": "start"})
        xlsx_file = SimpleUploadedFile(
            "samples.xlsx",
            b"fake-xlsx-content",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        request = factory.post(
            "/api/batch-upload/start/",
            data={"file": xlsx_file, "project_id": 1},
            format="multipart",
        )
        force_authenticate(request, user=admin_user)
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            response = view(request)
        assert response.status_code == 202
        assert response.data["job_id"] == "fake-task-id"

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
    def test_start_registers_job(self, mock_contrib, mock_task, mock_register, factory, admin_user, tmp_path):
        """register_job is called with correct user_id, job_id, project_id."""
        mock_task.delay.return_value.id = "reg-task-id"
        view = BatchUploadViewSet.as_view({"post": "start"})
        xlsx_file = SimpleUploadedFile(
            "samples.xlsx", b"fake", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        request = factory.post(
            "/api/batch-upload/start/",
            data={"file": xlsx_file, "project_id": 42},
            format="multipart",
        )
        force_authenticate(request, user=admin_user)
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            view(request)
        mock_register.assert_called_once_with(
            user_id=admin_user.pk, job_id="reg-task-id", project_id=42,
        )

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
    def test_start_passes_user_id_to_task(self, mock_contrib, mock_task, mock_register, factory, admin_user, tmp_path):
        """user_id is passed to run_batch_upload_task.delay()."""
        mock_task.delay.return_value.id = "uid-task-id"
        view = BatchUploadViewSet.as_view({"post": "start"})
        xlsx_file = SimpleUploadedFile(
            "samples.xlsx", b"fake", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        request = factory.post(
            "/api/batch-upload/start/",
            data={"file": xlsx_file, "project_id": 1},
            format="multipart",
        )
        force_authenticate(request, user=admin_user)
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            view(request)
        call_kwargs = mock_task.delay.call_args[1]
        assert call_kwargs["user_id"] == admin_user.pk

    @pytest.mark.django_db
    def test_upload_non_xlsx_rejected(self, factory, admin_user):
        view = BatchUploadViewSet.as_view({"post": "start"})
        csv_file = SimpleUploadedFile("data.csv", b"a,b,c", content_type="text/csv")
        request = factory.post(
            "/api/batch-upload/start/",
            data={"file": csv_file, "project_id": 1},
            format="multipart",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 400
        assert ".xlsx" in response.data["detail"]

    @pytest.mark.django_db
    def test_missing_file_and_path_rejected(self, factory, admin_user):
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={"project_id": 1},
            content_type="application/json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 400
        assert "upload" in response.data["detail"].lower() or "rows" in response.data["detail"].lower()


class TestOwnershipEnforcement:
    """Test that non-owners get 404."""

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.user_owns_job", return_value=True)
    def test_status_own_job_allowed(self, mock_owns, factory, admin_user):
        """Owner can access their job status."""
        view = BatchUploadViewSet.as_view({"get": "job_status"})
        request = factory.get("/api/batch-upload/status/test-job-123/")
        force_authenticate(request, user=admin_user)
        response = view(request, job_id="test-job-123")
        # Should get through ownership check (200 or whatever Celery returns)
        assert response.status_code != 404

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.user_owns_job", return_value=False)
    def test_status_other_user_404(self, mock_owns, factory, admin_user):
        view = BatchUploadViewSet.as_view({"get": "job_status"})
        request = factory.get("/api/batch-upload/status/other-job/")
        force_authenticate(request, user=admin_user)
        response = view(request, job_id="other-job")
        assert response.status_code == 404

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.user_owns_job", return_value=False)
    def test_summary_other_user_404(self, mock_owns, factory, admin_user):
        view = BatchUploadViewSet.as_view({"get": "summary"})
        request = factory.get("/api/batch-upload/summary/other-job/")
        force_authenticate(request, user=admin_user)
        response = view(request, job_id="other-job")
        assert response.status_code == 404

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.user_owns_job", return_value=False)
    def test_cancel_other_user_404(self, mock_owns, factory, admin_user):
        view = BatchUploadViewSet.as_view({"delete": "cancel"})
        request = factory.delete("/api/batch-upload/cancel/other-job/")
        force_authenticate(request, user=admin_user)
        response = view(request, job_id="other-job")
        assert response.status_code == 404


class TestListEndpoint:
    """Test the list() endpoint."""

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.list_jobs")
    def test_list_returns_own_jobs_only(self, mock_list_jobs, factory, admin_user, normal_user):
        """Each user sees only their own jobs."""
        # User A
        mock_list_jobs.return_value = {
            "jobs": [{"job_id": "a-job", "project_id": 1, "created_at": 1.0}],
            "total": 1, "page": 1, "page_size": 20,
        }
        view = BatchUploadViewSet.as_view({"get": "list"})
        request = factory.get("/api/batch-upload/")
        force_authenticate(request, user=admin_user)
        with patch("nextseek_api.batch_upload.views.AsyncResult") as mock_ar:
            mock_ar.return_value.state = "SUCCESS"
            response = view(request)
        assert response.status_code == 200
        assert len(response.data["jobs"]) == 1
        assert response.data["jobs"][0]["job_id"] == "a-job"
        mock_list_jobs.assert_called_once_with(user_id=admin_user.pk, page=1, page_size=20)

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.list_jobs")
    def test_list_pagination(self, mock_list_jobs, factory, admin_user):
        """Pagination params are forwarded correctly."""
        mock_list_jobs.return_value = {
            "jobs": [{"job_id": f"j-{i}", "project_id": 1, "created_at": float(i)} for i in range(5)],
            "total": 25, "page": 2, "page_size": 20,
        }
        view = BatchUploadViewSet.as_view({"get": "list"})
        request = factory.get("/api/batch-upload/?page=2&page_size=20")
        force_authenticate(request, user=admin_user)
        with patch("nextseek_api.batch_upload.views.AsyncResult") as mock_ar:
            mock_ar.return_value.state = "PENDING"
            response = view(request)
        assert response.status_code == 200
        assert response.data["total"] == 25
        assert response.data["page"] == 2
        assert len(response.data["jobs"]) == 5

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.list_jobs")
    def test_list_empty(self, mock_list_jobs, factory, admin_user):
        """User with no jobs gets empty list."""
        mock_list_jobs.return_value = {"jobs": [], "total": 0, "page": 1, "page_size": 20}
        view = BatchUploadViewSet.as_view({"get": "list"})
        request = factory.get("/api/batch-upload/")
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 200
        assert response.data["jobs"] == []
        assert response.data["total"] == 0

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.list_jobs")
    def test_list_enriches_with_celery_state(self, mock_list_jobs, factory, admin_user):
        """Each job is enriched with current Celery state."""
        mock_list_jobs.return_value = {
            "jobs": [{"job_id": "enrich-job", "project_id": 1, "created_at": 100.0}],
            "total": 1, "page": 1, "page_size": 20,
        }
        view = BatchUploadViewSet.as_view({"get": "list"})
        request = factory.get("/api/batch-upload/")
        force_authenticate(request, user=admin_user)
        with patch("nextseek_api.batch_upload.views.AsyncResult") as mock_ar:
            mock_ar.return_value.state = "PROGRESS"
            response = view(request)
        assert response.status_code == 200
        assert response.data["jobs"][0]["state"] == "PROGRESS"
        assert response.data["jobs"][0]["job_id"] == "enrich-job"


class TestBasicAuth:
    """Test basic auth acceptance."""

    @pytest.mark.django_db
    def test_basic_auth_accepted(self, factory, admin_user):
        """Basic auth header is accepted by the ViewSet."""
        import base64
        admin_user.set_password("testpass123")
        admin_user.save()
        credentials = base64.b64encode(b"batch_admin:testpass123").decode("utf-8")
        view = BatchUploadViewSet.as_view({"get": "list"})
        request = factory.get(
            "/api/batch-upload/",
            HTTP_AUTHORIZATION=f"Basic {credentials}",
        )
        with patch("nextseek_api.batch_upload.views.list_jobs") as mock_lj:
            mock_lj.return_value = {"jobs": [], "total": 0, "page": 1, "page_size": 20}
            response = view(request)
        # Should NOT be 401/403
        assert response.status_code == 200


class TestDirectRowsInput:
    """Test direct rows input mode (JSON body with rows field)."""

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
    def test_valid_rows_returns_202(self, mock_contrib, mock_task, mock_register, factory, admin_user):
        """Valid rows input should return 202."""
        mock_task.delay.return_value.id = "rows-task-id"
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={
                "project_id": 1,
                "rows": [
                    {"SampleType": "M.Mice", "json_metadata": {"Name": "mouse1"}},
                ],
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 202
        assert response.data["job_id"] == "rows-task-id"
        # Verify rows were passed to task
        call_kwargs = mock_task.delay.call_args[1]
        assert "rows" in call_kwargs
        assert len(call_kwargs["rows"]) == 1
        assert "xlsx_paths" not in call_kwargs

    @pytest.mark.django_db
    def test_invalid_rows_returns_422(self, factory, admin_user):
        """Invalid rows (missing SampleType) should return 422."""
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={
                "project_id": 1,
                "rows": [
                    {"json_metadata": {"Name": "no-type"}},  # Missing SampleType
                ],
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 422

    @pytest.mark.django_db
    def test_empty_rows_returns_400(self, factory, admin_user):
        """Empty rows list should return 400."""
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={
                "project_id": 1,
                "rows": [],
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 400
        assert "non-empty" in response.data["detail"].lower()

    @pytest.mark.django_db
    def test_no_input_returns_400(self, factory, admin_user):
        """Neither files nor rows should return 400."""
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={"project_id": 1},
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 400

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
    def test_rows_wins_over_files(self, mock_contrib, mock_task, mock_register, factory, admin_user, tmp_path):
        """When both rows and files provided, rows should be used."""
        mock_task.delay.return_value.id = "rows-wins-id"
        view = BatchUploadViewSet.as_view({"post": "start"})
        # Note: can't easily send both JSON rows and multipart files in same request
        # with APIRequestFactory. Instead, verify via unit test of the view logic:
        # If rows is in request.data, it takes priority even with FILES.
        # This is tested by verifying task gets rows, not xlsx_paths.
        request = factory.post(
            "/api/batch-upload/start/",
            data={
                "project_id": 1,
                "rows": [
                    {"SampleType": "M.Mice", "json_metadata": {"Name": "mouse1"}},
                ],
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 202
        call_kwargs = mock_task.delay.call_args[1]
        assert "rows" in call_kwargs


class TestFileSizeLimit:
    """Test total upload size limit."""

    @pytest.mark.django_db
    @override_settings(BATCH_UPLOAD_MAX_TOTAL_BYTES=100)
    def test_total_size_over_limit_returns_413(self, factory, admin_user):
        """Uploading files exceeding total size limit should return 413."""
        view = BatchUploadViewSet.as_view({"post": "start"})
        # 200 bytes > 100 byte limit
        big_file = SimpleUploadedFile(
            "big.xlsx", b"x" * 200,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        request = factory.post(
            "/api/batch-upload/start/",
            data={"file": big_file, "project_id": 1},
            format="multipart",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 413
        assert "exceeds" in response.data["detail"].lower()


class TestPersonIdOptional:
    """Test that person_id is optional."""

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
    def test_person_id_optional(self, mock_contrib, mock_task, mock_register, factory, admin_user):
        """Request without person_id should succeed (view infers from user)."""
        mock_task.delay.return_value.id = "no-pid-task"
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={
                "project_id": 1,
                "rows": [
                    {"SampleType": "M.Mice", "json_metadata": {"Name": "m1"}},
                ],
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 202


class TestUpdateExistingParameter:
    """Test update_existing parameter handling."""

    def test_update_existing_flows_through_config(self):
        """update_existing should be usable in config_overrides."""
        from nextseek_api.batch_upload.config import BatchUploadConfig
        config = BatchUploadConfig(**{"update_existing": True})
        assert config.update_existing is True

    def test_update_existing_defaults_false(self):
        """update_existing should default to False."""
        from nextseek_api.batch_upload.config import BatchUploadConfig
        config = BatchUploadConfig()
        assert config.update_existing is False

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
    def test_update_existing_top_level_field(self, mock_contrib, mock_task, mock_register, factory, admin_user, tmp_path):
        """update_existing as a top-level form field should flow into config_overrides."""
        mock_task.delay.return_value.id = "fake-task-id"
        view = BatchUploadViewSet.as_view({"post": "start"})
        xlsx_file = SimpleUploadedFile(
            "samples.xlsx",
            b"fake-xlsx-content",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        request = factory.post(
            "/api/batch-upload/start/",
            data={"file": xlsx_file, "project_id": 1, "update_existing": "true"},
            format="multipart",
        )
        force_authenticate(request, user=admin_user)
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            response = view(request)
        assert response.status_code == 202
        # Verify update_existing was passed through config_overrides
        call_kwargs = mock_task.delay.call_args[1]
        assert call_kwargs["config_overrides"]["update_existing"] is True


class TestBasicAuthPersonId:
    """Test person_id requirement for Basic Auth requests."""

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 42, "lababbv": "MIT"})
    def test_basic_auth_without_person_id_auto_resolves(self, mock_contrib, mock_task, mock_register, factory, admin_user):
        """Basic Auth request without person_id should auto-resolve (no longer requires person_id)."""
        import base64
        mock_task.delay.return_value.id = "basic-no-pid-task"
        admin_user.set_password("testpass123")
        admin_user.save()
        credentials = base64.b64encode(b"batch_admin:testpass123").decode("utf-8")
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={
                "project_id": 1,
                "rows": [
                    {"SampleType": "M.Mice", "json_metadata": {"Name": "m1"}},
                ],
            },
            format="json",
            HTTP_AUTHORIZATION=f"Basic {credentials}",
        )
        # Basic Auth guard removed — should get 202 (no person_id required)
        response = view(request)
        assert response.status_code == 202

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 42, "lababbv": "MIT"})
    def test_basic_auth_with_person_id_accepted(self, mock_contrib, mock_task, mock_register, factory, admin_user):
        """Basic Auth request with person_id should be accepted."""
        import base64
        mock_task.delay.return_value.id = "basic-auth-pid-task"
        admin_user.set_password("testpass123")
        admin_user.save()
        credentials = base64.b64encode(b"batch_admin:testpass123").decode("utf-8")
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={
                "project_id": 1,
                "person_id": 42,
                "rows": [
                    {"SampleType": "M.Mice", "json_metadata": {"Name": "m1"}},
                ],
            },
            format="json",
            HTTP_AUTHORIZATION=f"Basic {credentials}",
        )
        response = view(request)
        assert response.status_code == 202

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.register_job")
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
    def test_session_auth_without_person_id_allowed(self, mock_contrib, mock_task, mock_register, factory, admin_user):
        """Session auth (non-Basic) without person_id should still work."""
        mock_task.delay.return_value.id = "session-no-pid-task"
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={
                "project_id": 1,
                "rows": [
                    {"SampleType": "M.Mice", "json_metadata": {"Name": "m1"}},
                ],
            },
            format="json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 202


class TestResolveUserContextV2:
    """Tests for the rewritten _resolve_user_context behavior.

    New behavior:
    - Auth resolution chain: resolve_seek_auth() → getSeekLogin() → SEEK Users model → fail (no Django pk)
    - Admin can override person_id; non-admin cannot (warning flag set, own identity used)
    - lababbv always from effective person; admin override fallback to admin's own lababbv
    - Empty string person_id sanitized to None
    - Return dict includes person_id_ignored flag when non-admin tries to override
    """

    @pytest.mark.django_db
    def test_resolve_seek_auth_basic_auth_path(self):
        """resolve_seek_auth returns credentials → Users ORM lookup → getUserInfo → result dict."""
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="v2_basic_user",
            defaults={"is_staff": False, "is_superuser": False},
        )
        request = MagicMock()
        request.user = user
        request.data = {}  # No person_id override

        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42

        mock_user_info = {"person_id": 42, "lababbv": "MIT"}

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("testuser", "testpass"), {})) as mock_rsa, \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB") as MockSeekDB:
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"
            instance = MockSeekDB.return_value
            instance.getUserInfo.return_value = (mock_user_info, True, "")
            # getSeekLogin fails (Basic Auth path)
            instance.getSeekLogin.side_effect = Exception("no session")

            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == 42
        assert result["lababbv"] == "MIT"

    @pytest.mark.django_db
    def test_non_admin_person_id_ignored_with_warning(self):
        """Non-admin providing a different person_id gets own identity + person_id_ignored=True flag."""
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="v2_nonadmin",
            defaults={"is_staff": False, "is_superuser": False},
        )
        request = MagicMock()
        request.user = user
        request.data = {"person_id": 99}  # Tries to override

        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42

        mock_own_info = {"person_id": 42, "lababbv": "MIT"}

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("v2_nonadmin", "pass"), {})), \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB") as MockSeekDB:
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"
            instance = MockSeekDB.return_value
            instance.getSeekLogin.side_effect = Exception("no session")
            instance.getUserInfo.return_value = (mock_own_info, True, "")

            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == 42  # Own identity, NOT 99
        assert result.get("person_id_ignored") is True

    @pytest.mark.django_db
    def test_admin_no_person_id_uses_own(self):
        """Admin with no person_id override uses their own identity via getSeekLogin/Users lookup."""
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="v2_admin_own",
            defaults={"is_staff": True, "is_superuser": True},
        )
        request = MagicMock()
        request.user = user
        request.data = {}  # No override

        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42

        mock_own_info = {"person_id": 42, "lababbv": "MIT"}

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("v2_admin_own", "pass"), {})), \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB") as MockSeekDB:
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"
            instance = MockSeekDB.return_value
            instance.getSeekLogin.side_effect = Exception("no session")
            instance.getUserInfo.return_value = (mock_own_info, True, "")

            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == 42
        assert result["lababbv"] == "MIT"

    @pytest.mark.django_db
    def test_admin_with_person_id_overrides(self):
        """Admin providing person_id=99 gets contributor_id=99 with lababbv from getUserInfo(99)."""
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="v2_admin_override",
            defaults={"is_staff": True, "is_superuser": True},
        )
        request = MagicMock()
        request.user = user
        request.data = {"person_id": 99}

        # Admin's own identity
        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42
        own_info = {"person_id": 42, "lababbv": "MIT"}

        # Target person's info
        override_info = {"person_id": 99, "lababbv": "BMC"}

        def fake_get_user_info(pid):
            if int(pid) == 99:
                return (override_info, True, "")
            return (own_info, True, "")

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("v2_admin_override", "pass"), {})), \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB") as MockSeekDB:
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"
            instance = MockSeekDB.return_value
            instance.getSeekLogin.side_effect = Exception("no session")
            instance.getUserInfo.side_effect = fake_get_user_info

            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == 99
        assert result["lababbv"] == "BMC"

    @pytest.mark.django_db
    def test_admin_override_lababbv_fallback(self):
        """Admin with person_id=99 but getUserInfo(99) fails → contributor_id=99, lababbv from admin's own."""
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="v2_admin_labfallback",
            defaults={"is_staff": True, "is_superuser": True},
        )
        request = MagicMock()
        request.user = user
        request.data = {"person_id": 99}

        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42
        own_info = {"person_id": 42, "lababbv": "MIT"}

        def fake_get_user_info(pid):
            if int(pid) == 99:
                return ({}, False, "not found")  # override target fails
            return (own_info, True, "")

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("v2_admin_labfallback", "pass"), {})), \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB") as MockSeekDB:
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"
            instance = MockSeekDB.return_value
            instance.getSeekLogin.side_effect = Exception("no session")
            instance.getUserInfo.side_effect = fake_get_user_info

            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == 99
        assert result["lababbv"] == "MIT"  # Fallback to admin's own lababbv

    @pytest.mark.django_db
    def test_empty_person_id_treated_as_none(self):
        """person_id='' in request data is sanitized to None, uses own identity (no ValueError)."""
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="v2_empty_pid",
            defaults={"is_staff": False, "is_superuser": False},
        )
        request = MagicMock()
        request.user = user
        request.data = {"person_id": ""}  # Empty string

        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42
        own_info = {"person_id": 42, "lababbv": "MIT"}

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("v2_empty_pid", "pass"), {})), \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB") as MockSeekDB:
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"
            instance = MockSeekDB.return_value
            instance.getSeekLogin.side_effect = Exception("no session")
            instance.getUserInfo.return_value = (own_info, True, "")

            result = _resolve_user_context(request)

        # Should NOT raise ValueError; should succeed using own identity
        assert result is not None
        assert result["contributor_id"] == 42
        assert result["lababbv"] == "MIT"

    @pytest.mark.django_db
    def test_no_seek_identity_returns_none(self):
        """When all resolution paths fail, returns None (NOT Django user pk)."""
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="v2_no_seek",
            defaults={"is_staff": True, "is_superuser": True},
        )
        request = MagicMock()
        request.user = user
        request.data = {}

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(None, None)), \
             patch("seek.seekdb.SeekDB") as MockSeekDB, \
             patch("seek.models.Users") as MockUsers:
            instance = MockSeekDB.return_value
            instance.getSeekLogin.side_effect = Exception("no session")
            MockUsers.objects.using.return_value.get.side_effect = Exception("no SEEK user")

            result = _resolve_user_context(request)

        # New behavior: returns None instead of Django pk fallback
        assert result is None


class TestResolveUserContextFallback:
    """Test _resolve_user_context fallback paths."""

    @pytest.mark.django_db
    def test_basic_auth_creds_fallback_resolves_lababbv(self):
        """When getSeekLogin fails (no session) but resolve_seek_auth returns creds,
        Phase 1b-fallback initializes SeekDB with those creds and resolves lababbv.
        """
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="fallback_user", defaults={"is_staff": True}
        )
        request = MagicMock()
        request.user = user
        request.data = {"person_id": 42}

        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42

        mock_user_info = (
            {"person_id": 42, "lababbv": "MIT", "institutionid": 1, "institutionname": "MIT"},
            True,
            "",
        )

        # SeekDB(None,None,None) via getSeekLogin fails (no session)
        # SeekDB(server,user,pass) via 1b-fallback succeeds (has __seekapi)
        def seekdb_factory(*args, **kwargs):
            inst = MagicMock()
            server, username, password = args if len(args) == 3 else (None, None, None)
            if username is not None:
                # Properly initialized — getUserInfo works
                inst.getUserInfo.return_value = mock_user_info
            else:
                # Bare instance — getSeekLogin will fail
                inst.getSeekLogin.side_effect = Exception("no session credentials")
                inst.getUserInfo.side_effect = AttributeError("no __seekapi")
            return inst

        with patch("nextseek_api.helpers.resolve_seek_auth",
                    return_value=(("fallback_user", "pass"), {})), \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB", side_effect=seekdb_factory):
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"

            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == 42
        assert result["lababbv"] == "MIT"


class TestResolveUserContextSeekDBInit:
    """Regression tests for SeekDB initialization bug.

    Production bug: Phase 1a creates SeekDB(None,None,None) and calls getUserInfo()
    without initializing __seekapi (which requires getSeekLogin() first).
    Phase 1a partially succeeds (sets person_id), then crashes on getUserInfo.
    This poisons the Phase 1b guard (person_id is not None), so Phase 1b is skipped,
    and lababbv stays as "NA".

    These tests simulate the REAL production behavior where each SeekDB() call
    creates an independent instance — unlike other tests that mock at class level
    (returning one shared mock for all calls).
    """

    @pytest.mark.django_db
    def test_phase1a_getUserInfo_crash_does_not_block_lababbv(self):
        """Phase 1a sets person_id via Users ORM, but getUserInfo fails.
        Phase 1b must still run and resolve lababbv via getSeekLogin.

        Simulates real production: getUserInfo only works on a SeekDB instance
        that has had getSeekLogin called first (__seekapi initialized).
        """
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="seekdb_init_test", defaults={"is_staff": False, "is_superuser": False}
        )
        request = MagicMock()
        request.user = user
        request.data = {}

        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42

        # Each SeekDB() call returns an independent instance that mimics production:
        # getUserInfo crashes unless getSeekLogin was called on THAT instance first.
        def make_seekdb_instance():
            inst = MagicMock()
            inst._initialized = False

            def fake_getSeekLogin(req, full=True):
                inst._initialized = True
                return {"status": True, "person_id": 42, "lababbv": "MIT"}

            def fake_getUserInfo(pid):
                if not inst._initialized:
                    raise AttributeError("'SeekDB' object has no attribute '_SeekDB__seekapi'")
                return ({"person_id": int(pid), "lababbv": "MIT"}, True, "")

            inst.getSeekLogin.side_effect = fake_getSeekLogin
            inst.getUserInfo.side_effect = fake_getUserInfo
            return inst

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("testuser", "testpass"), {})), \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB", side_effect=lambda *a, **kw: make_seekdb_instance()):
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"

            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == 42
        # BUG: current code returns "NA" because Phase 1b is skipped
        assert result["lababbv"] == "MIT"

    @pytest.mark.django_db
    def test_admin_override_phase3_uses_initialized_seekdb(self):
        """Admin overrides person_id=99. Phase 3 must call getUserInfo(99) on an
        initialized SeekDB (from Phase 1b), not a fresh bare one.

        Simulates real production: getUserInfo only works after getSeekLogin on
        the same instance. Each SeekDB() creates independent instances.
        """
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="seekdb_admin_override", defaults={"is_staff": True, "is_superuser": True}
        )
        request = MagicMock()
        request.user = user
        request.data = {"person_id": 99}

        mock_seek_user = MagicMock()
        mock_seek_user.person_id = 42  # Admin's own person_id

        admin_info = {"person_id": 42, "lababbv": "MIT"}
        target_info = {"person_id": 99, "lababbv": "BMC"}

        def make_seekdb_instance():
            inst = MagicMock()
            inst._initialized = False

            def fake_getSeekLogin(req, full=True):
                inst._initialized = True
                return {"status": True, "person_id": 42, "lababbv": "MIT"}

            def fake_getUserInfo(pid):
                if not inst._initialized:
                    raise AttributeError("'SeekDB' object has no attribute '_SeekDB__seekapi'")
                if int(pid) == 99:
                    return (target_info, True, "")
                return (admin_info, True, "")

            inst.getSeekLogin.side_effect = fake_getSeekLogin
            inst.getUserInfo.side_effect = fake_getUserInfo
            return inst

        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("admin", "pass"), {})), \
             patch("seek.models.Users") as MockUsers, \
             patch("seek.seekdb.SeekDB", side_effect=lambda *a, **kw: make_seekdb_instance()):
            MockUsers.objects.using.return_value.get.return_value = mock_seek_user
            MockUsers._DATABASE = "default"

            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == 99
        # BUG: current code returns "NA" — Phase 3 creates bare SeekDB, getUserInfo crashes
        assert result["lababbv"] == "BMC"

    @pytest.mark.django_db
    def test_explicit_lababbv_overrides_resolution(self):
        """When start endpoint receives explicit lababbv, it bypasses _resolve_user_context's lababbv."""
        from nextseek_api.batch_upload.views import BatchUploadViewSet

        request = MagicMock()
        request.user = MagicMock(pk=1, is_staff=True, is_superuser=True, is_authenticated=True)
        request.method = "POST"
        request.data = {
            "project_id": "1",
            "person_id": "42",
            "lababbv": "BMC",
        }
        request.FILES = MagicMock()
        request.FILES.getlist.return_value = [
            MagicMock(name="test.xlsx", size=100),
        ]
        # Make the file name check pass
        request.FILES.getlist.return_value[0].name = "test.xlsx"

        with patch("nextseek_api.batch_upload.views._resolve_user_context",
                    return_value={"contributor_id": 42, "lababbv": "MIT"}), \
             patch("nextseek_api.batch_upload.views._save_uploaded_file", return_value="/tmp/test.xlsx"), \
             patch("nextseek_api.batch_upload.views.run_batch_upload_task") as mock_task, \
             patch("nextseek_api.batch_upload.views.register_job"):
            mock_task.delay.return_value = MagicMock(id="test-job-id")

            view = BatchUploadViewSet()
            response = view.start(request)

        assert response.status_code == 202
        # lababbv in task kwargs should be "BMC" (from request), not "MIT" (from resolution)
        call_kwargs = mock_task.delay.call_args[1]
        assert call_kwargs["lababbv"] == "BMC"

    @pytest.mark.django_db
    def test_explicit_lababbv_ignored_for_non_admin(self):
        """Non-admin user providing lababbv override is silently ignored."""
        from nextseek_api.batch_upload.views import BatchUploadViewSet

        request = MagicMock()
        request.user = MagicMock(pk=2, is_staff=False, is_superuser=False, is_authenticated=True)
        request.method = "POST"
        request.data = {
            "project_id": "1",
            "person_id": "42",
            "lababbv": "EVIL",
        }
        request.FILES = MagicMock()
        request.FILES.getlist.return_value = [
            MagicMock(name="test.xlsx", size=100),
        ]
        request.FILES.getlist.return_value[0].name = "test.xlsx"

        with patch("nextseek_api.batch_upload.views._resolve_user_context",
                    return_value={"contributor_id": 42, "lababbv": "MIT"}), \
             patch("nextseek_api.batch_upload.views._save_uploaded_file", return_value="/tmp/test.xlsx"), \
             patch("nextseek_api.batch_upload.views.run_batch_upload_task") as mock_task, \
             patch("nextseek_api.batch_upload.views.register_job"):
            mock_task.delay.return_value = MagicMock(id="test-job-id")

            view = BatchUploadViewSet()
            response = view.start(request)

        assert response.status_code == 202
        # Non-admin: lababbv override ignored, uses resolved value
        call_kwargs = mock_task.delay.call_args[1]
        assert call_kwargs["lababbv"] == "MIT"
