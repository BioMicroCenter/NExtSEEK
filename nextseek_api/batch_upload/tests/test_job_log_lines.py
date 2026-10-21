"""A batch upload logs one line when it is queued and one when it starts: ids and counts, never row contents."""
import logging
from unittest.mock import MagicMock, patch

from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.batch_upload.views import BatchUploadViewSet

SECRET = "human-subject-value-123"
ROWS = [{"SampleType": "M.Mice", "json_metadata": {"Name": SECRET}}]


@patch("nextseek_api.batch_upload.views.register_job")
@patch("nextseek_api.batch_upload.views.run_batch_upload_task")
@patch("nextseek_api.batch_upload.views._resolve_user_context", return_value={"contributor_id": 1, "lababbv": "MIT"})
def test_queue_line(_ctx, task, _register, caplog):
    task.delay.return_value = MagicMock(id="job-abc")
    user = MagicMock(is_authenticated=True, is_superuser=True, pk=5, username="u")
    request = APIRequestFactory().post("/api/batch-upload/start/", data={"project_id": 7, "rows": ROWS}, format="json")
    force_authenticate(request, user=user)
    with caplog.at_level(logging.INFO, logger="nextseek_api.batch_upload.views"):
        response = BatchUploadViewSet.as_view({"post": "start"})(request)
    assert response.status_code == 202
    line = next(r.getMessage() for r in caplog.records if "batch upload queued" in r.getMessage())
    assert "job=job-abc" in line and "user=5" in line and "project=7" in line and "rows=1" in line
    assert SECRET not in caplog.text


def test_start_line(caplog):
    from nextseek_api.batch_upload import tasks

    with patch.object(tasks, "run_batch_upload_multi", return_value={"totals": {}, "errors": []}), \
         patch.object(tasks.run_batch_upload_task, "update_state"), \
         caplog.at_level(logging.INFO, logger="nextseek_api.batch_upload.tasks"):
        try:
            tasks.run_batch_upload_task.run(rows=ROWS, project_id=7, contributor_id=1, user_id=5,
                                            config_overrides={}, scope_project_ids=[7])
        except Exception:  # noqa: BLE001 (only the first log line matters)
            pass
    line = next(r.getMessage() for r in caplog.records if "batch upload started" in r.getMessage())
    assert "user=5" in line and "project=7" in line and "rows=1" in line
    assert SECRET not in caplog.text
