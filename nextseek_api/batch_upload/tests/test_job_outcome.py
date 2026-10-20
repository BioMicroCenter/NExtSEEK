"""job_outcome: one batch-upload job's state for the engine's upload-reingest wait.

It reads the same Celery result the status endpoint reads, and hands back only the
state, the totals and the errors; the engine turns that into its reply.
"""
from types import SimpleNamespace
from unittest.mock import patch

from nextseek_api.batch_upload import views


def _result(state, result=None):
    return SimpleNamespace(state=state, result=result)


def test_a_finished_job_gives_its_totals_and_errors():
    payload = {"totals": {"processed": 2, "uids_generated": 2}, "errors": [{"row": 1}]}
    with patch.object(views, "AsyncResult", return_value=_result("SUCCESS", payload)):
        out = views.job_outcome("job-1")
    assert out == {"state": "SUCCESS", "totals": payload["totals"], "errors": [{"row": 1}]}


def test_a_failed_job_gives_its_error():
    with patch.object(views, "AsyncResult", return_value=_result("FAILURE", ValueError("boom"))):
        out = views.job_outcome("job-1")
    assert out == {"state": "FAILURE", "error": "boom"}


def test_a_running_job_gives_only_its_state():
    with patch.object(views, "AsyncResult", return_value=_result("PROGRESS", {"done": 1})):
        assert views.job_outcome("job-1") == {"state": "PROGRESS"}
