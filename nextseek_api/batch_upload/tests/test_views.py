"""Tests for the DRF ViewSet routing and basic behavior."""
import pytest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from unittest.mock import patch

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


class TestBatchUploadViewSetAuth:
    """Test authentication/authorization requirements."""

    @pytest.mark.django_db
    def test_unauthenticated_forbidden(self, factory):
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={"xlsx_path": "/tmp/test.xlsx", "project_id": 1},
            content_type="application/json",
        )
        response = view(request)
        assert response.status_code in (401, 403)

    @pytest.mark.django_db
    def test_non_admin_forbidden(self, factory, normal_user):
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={"xlsx_path": "/tmp/test.xlsx", "project_id": 1},
            content_type="application/json",
        )
        force_authenticate(request, user=normal_user)
        response = view(request)
        assert response.status_code == 403

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
    def test_admin_file_not_found(self, factory, admin_user):
        view = BatchUploadViewSet.as_view({"post": "start"})
        request = factory.post(
            "/api/batch-upload/start/",
            data={"xlsx_path": "/nonexistent/file.xlsx", "project_id": 1},
            content_type="application/json",
        )
        force_authenticate(request, user=admin_user)
        response = view(request)
        assert response.status_code == 400
        assert "not found" in response.data["detail"].lower()


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
    def test_status_pending_job(self, factory, admin_user):
        view = BatchUploadViewSet.as_view({"get": "job_status"})
        request = factory.get("/api/batch-upload/status/nonexistent-id/")
        force_authenticate(request, user=admin_user)
        response = view(request, job_id="nonexistent-id")
        assert response.status_code == 200
        assert response.data["state"] == "PENDING"


class TestBatchUploadFileUpload:
    """Test client-side Excel file upload."""

    @pytest.mark.django_db
    @patch("nextseek_api.batch_upload.views.run_batch_upload_task")
    @patch("nextseek_api.batch_upload.views._resolve_contributor_id", return_value=1)
    def test_upload_xlsx_returns_202(self, mock_contrib, mock_task, factory, admin_user, tmp_path):
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
        assert "upload" in response.data["detail"].lower() or "xlsx_path" in response.data["detail"]
