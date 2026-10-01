from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload import views


@patch("nextseek_api.batch_upload.views.register_job")
@patch("nextseek_api.batch_upload.views.run_batch_upload_task")
def test_files_are_dispatched_and_the_job_is_registered_to_the_user(task, register):
    task.delay.return_value = MagicMock(id="job-1")
    job = views.dispatch_batch_job(
        user_pk=5, user_ctx={"contributor_id": 42, "lababbv": "MIT"}, lababbv="MIT",
        project_id=14, config_overrides={"update_existing": True},
        xlsx_paths=["/m/a.xlsx"])
    assert job == "job-1"
    kwargs = task.delay.call_args.kwargs
    assert kwargs["xlsx_paths"] == ["/m/a.xlsx"] and "rows" not in kwargs
    assert kwargs["contributor_id"] == 42 and kwargs["user_id"] == 5
    assert kwargs["config_overrides"] == {"update_existing": True}
    register.assert_called_once_with(user_id=5, job_id="job-1", project_id=14)


@pytest.mark.parametrize("rows,paths", [(None, None), ([{"a": 1}], ["/m/a.xlsx"])])
def test_exactly_one_of_rows_or_files(rows, paths):
    with pytest.raises(ValueError):
        views.dispatch_batch_job(user_pk=5, user_ctx={"contributor_id": 1, "lababbv": "X"},
                                 lababbv="X", project_id=1, config_overrides={},
                                 rows=rows, xlsx_paths=paths)


def test_stage_workbook_copy_lands_in_the_upload_dir(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    src = tmp_path / "reingest_A_ALN.xlsx"
    src.write_bytes(b"xlsx")
    dest = views.stage_workbook_copy(str(src))
    assert dest.startswith(str(tmp_path / "media" / "batch_upload_uploads"))
    assert dest.endswith("_reingest_A_ALN.xlsx")
    assert open(dest, "rb").read() == b"xlsx"


def test_two_same_second_copies_of_one_workbook_do_not_overwrite(tmp_path, settings):
    """Staged copies are what the worker reads, so two uploads of the same
    workbook name must land at different paths, or the bytes uploaded stop
    being the bytes reviewed."""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    src = tmp_path / "reingest_A_ALN.xlsx"
    src.write_bytes(b"first")
    with patch("nextseek_api.batch_upload.views.time.time", return_value=1700000000.0):
        first = views.stage_workbook_copy(str(src))
        src.write_bytes(b"second")
        second = views.stage_workbook_copy(str(src))
    assert first != second
    assert open(first, "rb").read() == b"first"
    assert open(second, "rb").read() == b"second"
