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


# --- the rows of a finished job's summary CSV --------------------------------------------
# upload-reingest lists the UIDs a job created, so a finished job also hands back each
# row's uid, status, reason and sample type, read from its summary CSV. The path comes from
# the Celery result and is read only inside MEDIA_ROOT/batch_upload_reports.

_SUMMARY = (
    "row_index,uid,status,reason,sample_type,sample_id,assays_linked_count,access_type,"
    "uid_generated,topo_level,json_metadata,assay_ids,original_row_index\n"
    '0,A.HLA-261020BMC-1,success,,A.HLA,374194,0,,True,0,"{""Parent"":""D.SEQ-1""}",,\n'
    '1,A.HLA-261020BMC-2,failed,bad Parent,A.HLA,,0,,True,0,"{}",,\n'
    ",,,,,,,,,,,,\n"
    "TOTALS,processed=2,success=1,skipped=0,failed=1,elapsed=0.7s,throughput=3rps,,,,,,\n"
    "GRAPH,pending (1),,,,,,,,,,,\n")


def _summary(root, name="summary_job-1.csv", text=_SUMMARY):
    reports = root / "batch_upload_reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / name
    path.write_text(text, encoding="utf-8")
    return path


def _finished(path):
    return _result("SUCCESS", {"totals": {"processed": 2}, "errors": [], "summary_path": str(path)})


def test_a_finished_job_gives_its_rows(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    with patch.object(views, "AsyncResult", return_value=_finished(_summary(tmp_path))):
        out = views.job_outcome("job-1")
    assert out["rows"] == [
        {"uid": "A.HLA-261020BMC-1", "status": "success", "reason": "", "sample_type": "A.HLA"},
        {"uid": "A.HLA-261020BMC-2", "status": "failed", "reason": "bad Parent",
         "sample_type": "A.HLA"}]


def test_a_summary_outside_the_reports_folder_is_not_read(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    elsewhere = tmp_path / "elsewhere" / "summary_job-1.csv"
    elsewhere.parent.mkdir()
    elsewhere.write_text(_SUMMARY, encoding="utf-8")
    with patch.object(views, "AsyncResult", return_value=_finished(elsewhere)):
        out = views.job_outcome("job-1")
    assert "rows" not in out and out["state"] == "SUCCESS"


def test_a_symlink_out_of_the_reports_folder_is_not_followed(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    outside = tmp_path / "secret.csv"
    outside.write_text(_SUMMARY, encoding="utf-8")
    reports = tmp_path / "media" / "batch_upload_reports"
    reports.mkdir(parents=True)
    (reports / "summary_job-1.csv").symlink_to(outside)
    with patch.object(views, "AsyncResult",
                      return_value=_finished(reports / "summary_job-1.csv")):
        out = views.job_outcome("job-1")
    assert "rows" not in out


def test_a_missing_summary_gives_no_rows(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    missing = tmp_path / "batch_upload_reports" / "summary_gone.csv"
    with patch.object(views, "AsyncResult", return_value=_finished(missing)):
        out = views.job_outcome("job-1")
    assert "rows" not in out and out["state"] == "SUCCESS"


def test_rows_are_capped(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setattr(views, "_OUTCOME_ROW_CAP", 1)
    with patch.object(views, "AsyncResult", return_value=_finished(_summary(tmp_path))):
        out = views.job_outcome("job-1")
    assert [row["uid"] for row in out["rows"]] == ["A.HLA-261020BMC-1"]
