"""Tests for tasks.py — Celery task wrappers.

Strategy: We mock os.makedirs and run_batch_upload_multi, then call the
Celery task via task.run(...) which passes the Task instance as `self`.
The Task instance's request.id and update_state are Celery internals
that we patch on the Task class.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.celery_app import app as celery_app
from nextseek_api.batch_upload.config import BatchUploadConfig


@pytest.fixture(autouse=True)
def _celery_eager():
    """Run all Celery tasks eagerly (synchronously) in tests."""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


# ── run_batch_upload_task ────────────────────────────────────────────────


class TestRunBatchUploadTask:
    """Test the run_batch_upload_task code paths.

    With bind=True tasks, calling task.run(...) auto-injects the Task
    instance as `self`. We patch `self.request.id` and `self.update_state`
    via the Task class.
    """

    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_successful_run_with_rows(self, mock_makedirs, mock_run):
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.return_value = {
            "summary_path": "/tmp/summary.csv",
            "totals": {"processed": 5, "success": 5},
        }

        # Calling .run() on a bound task passes the Task object as self.
        # The Task object's request.id will be None in test context.
        result = run_batch_upload_task.run(
            rows=[{"UID": "UID-001", "SampleType": "NHP"}],
            project_id=1,
            contributor_id=1,
            user_id=42,
        )

        assert result["summary_path"] == "/tmp/summary.csv"
        mock_run.assert_called_once()

    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_deprecated_xlsx_path_converted(self, mock_makedirs, mock_run):
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.return_value = {"summary_path": "", "totals": {}}

        run_batch_upload_task.run(
            xlsx_path="/tmp/legacy.xlsx",
            project_id=1,
            contributor_id=1,
        )

        # run_batch_upload_multi should receive xlsx_paths list
        call_kwargs = mock_run.call_args
        # Extract xlsx_paths from either positional or keyword args
        if call_kwargs[1]:
            assert call_kwargs[1]["xlsx_paths"] == ["/tmp/legacy.xlsx"]
        else:
            assert call_kwargs[0][0] == ["/tmp/legacy.xlsx"]

    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_xlsx_paths_takes_precedence(self, mock_makedirs, mock_run):
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.return_value = {"summary_path": "", "totals": {}}

        run_batch_upload_task.run(
            xlsx_paths=["/tmp/a.xlsx"],
            xlsx_path="/tmp/legacy.xlsx",
            project_id=1,
            contributor_id=1,
        )

        call_kwargs = mock_run.call_args
        if call_kwargs[1]:
            assert call_kwargs[1]["xlsx_paths"] == ["/tmp/a.xlsx"]

    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_config_overrides_applied(self, mock_makedirs, mock_run):
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.return_value = {"summary_path": "", "totals": {}}

        run_batch_upload_task.run(
            rows=[],
            project_id=1,
            contributor_id=1,
            config_overrides={"max_rows_per_batch": 500},
        )

        call_kwargs = mock_run.call_args
        config = call_kwargs[1].get("config") if call_kwargs[1] else None
        assert config is not None
        assert config.max_rows_per_batch == 500

    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_soft_time_limit_returns_error(self, mock_makedirs, mock_run):
        from celery.exceptions import SoftTimeLimitExceeded
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.side_effect = SoftTimeLimitExceeded()

        result = run_batch_upload_task.run(rows=[], project_id=1, contributor_id=1)

        assert result["totals"]["error"] == "Task timed out (soft limit)"

    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_generic_exception_reraises(self, mock_makedirs, mock_run):
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.side_effect = RuntimeError("Something broke")

        with pytest.raises(RuntimeError, match="Something broke"):
            run_batch_upload_task.run(rows=[], project_id=1, contributor_id=1)

    @patch("nextseek_api.batch_upload.tasks.resolve_orphans_task")
    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_orphan_dispatch_on_identity_map(self, mock_makedirs, mock_run, mock_orphan_task):
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.return_value = {
            "summary_path": "",
            "totals": {},
            "_identity_map": {"UID-001": 100},
            "_parent_info": {"UID-001": ["Parent-001"]},
        }

        run_batch_upload_task.run(rows=[], project_id=1, contributor_id=1)
        mock_orphan_task.apply_async.assert_called_once()

    @patch("nextseek_api.batch_upload.tasks.resolve_orphans_task")
    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_orphan_dispatch_failure_non_fatal(self, mock_makedirs, mock_run, mock_orphan_task):
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.return_value = {
            "summary_path": "",
            "totals": {},
            "_identity_map": {"UID-001": 100},
            "_parent_info": {},
        }
        mock_orphan_task.apply_async.side_effect = Exception("Redis down")

        # Should not raise
        result = run_batch_upload_task.run(rows=[], project_id=1, contributor_id=1)
        assert "summary_path" in result

    @patch("nextseek_api.batch_upload.tasks.run_batch_upload_multi")
    @patch("nextseek_api.batch_upload.tasks.os.makedirs")
    def test_default_empty_paths(self, mock_makedirs, mock_run):
        from nextseek_api.batch_upload.tasks import run_batch_upload_task

        mock_run.return_value = {"summary_path": "", "totals": {}}

        run_batch_upload_task.run(project_id=1, contributor_id=1)

        call_kwargs = mock_run.call_args
        if call_kwargs[1]:
            assert call_kwargs[1]["xlsx_paths"] == []


# ── resolve_orphans_task ─────────────────────────────────────────────────


class TestResolveOrphansTask:
    def test_empty_identity_map_returns_early(self):
        from nextseek_api.batch_upload.tasks import resolve_orphans_task

        result = resolve_orphans_task.run(identity_map={}, parent_info={})
        assert result == {"resolved": 0}

    @patch("nextseek_api.batch_upload.config.Neo4jConfig.from_django_settings")
    def test_neo4j_disabled(self, mock_from_settings):
        from nextseek_api.batch_upload.tasks import resolve_orphans_task

        mock_cfg = MagicMock()
        mock_cfg.NEO4J_UPLOAD_ENABLED = False
        mock_from_settings.return_value = mock_cfg

        result = resolve_orphans_task.run(
            identity_map={"UID-001": 100}, parent_info={}
        )
        assert result == {"resolved": 0}

    def test_exception_returns_error_dict(self):
        from nextseek_api.batch_upload.tasks import resolve_orphans_task

        # Mock the neo4j import to succeed but config to fail
        with patch.dict("sys.modules", {"neo4j": MagicMock()}):
            with patch(
                "nextseek_api.batch_upload.config.Neo4jConfig.from_django_settings",
                side_effect=Exception("Config error"),
            ):
                result = resolve_orphans_task.run(
                    identity_map={"UID-001": 100}, parent_info={}
                )

        assert result["resolved"] == 0
        assert "error" in result
