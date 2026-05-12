import pytest
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import User
from rest_framework.test import APIClient, APIRequestFactory


@pytest.fixture
def api_user(db):
    """Create a standard test user."""
    return User.objects.create_user(username="testuser", password="testpass123")


@pytest.fixture
def admin_user(db):
    """Create an admin/staff test user."""
    return User.objects.create_user(
        username="admin", password="adminpass123", is_staff=True, is_superuser=True
    )


@pytest.fixture
def api_client():
    """DRF APIClient instance."""
    return APIClient()


@pytest.fixture
def auth_client(api_user, api_client):
    """APIClient authenticated as api_user."""
    api_client.force_authenticate(user=api_user)
    return api_client


@pytest.fixture
def admin_client(admin_user, api_client):
    """APIClient authenticated as admin_user."""
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def factory():
    """DRF APIRequestFactory."""
    return APIRequestFactory()


@pytest.fixture
def mock_seek_client():
    """Mocked SeekAPIClient that returns successful responses."""
    with patch("nextseek_api.helpers.SeekAPIClient") as MockClient:
        instance = MockClient.return_value
        default_response = (b'{"data": {}}', 200, {"Content-Type": "application/json"}, MagicMock())
        for method_name in [
            "list_sops", "get_sop", "create_sop", "update_sop",
            "list_data_files", "get_data_file", "create_data_file", "update_data_file",
            "list_projects", "get_project", "create_project", "update_project",
            "list_people", "get_person", "get_current_person", "create_person", "update_person",
            "list_investigations", "get_investigation", "create_investigation", "update_investigation",
            "list_assays", "get_assay", "create_assay", "update_assay",
            "list_sample_types", "get_sample_type", "create_sample_type", "update_sample_type",
            "get_sample", "create_sample", "update_sample", "delete_sample",
            "stream_content_blob", "upload_content_blob",
        ]:
            getattr(instance, method_name).return_value = default_response
        yield instance


@pytest.fixture
def mock_seek_auth():
    """Mock resolve_seek_auth to return valid auth tuple."""
    with patch("nextseek_api.helpers.resolve_seek_auth") as mock:
        mock.return_value = (("testuser", "testpass"), {})
        yield mock


@pytest.fixture
def mock_assistant_permission():
    """Mock UserInParticipatingProject to always allow."""
    with patch(
        "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
        return_value=True,
    ):
        yield
