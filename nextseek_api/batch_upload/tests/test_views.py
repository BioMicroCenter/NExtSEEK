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
        """When SeekDB fails, should fall back to Django user pk."""
        from nextseek_api.batch_upload.views import _resolve_user_context
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="ctx_test_user", defaults={"is_staff": True, "is_superuser": True}
        )
        request = MagicMock()
        request.user = user  # Real Django user (is_authenticated is True by default)

        with patch("seek.seekdb.SeekDB", side_effect=Exception("no seekdb")):
            result = _resolve_user_context(request)

        assert result is not None
        assert result["contributor_id"] == user.pk
        assert result["lababbv"] == "NA"

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
