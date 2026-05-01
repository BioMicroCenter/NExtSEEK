"""Coverage tests for batch_upload/insert.py — targeting uncovered lines.

Focus on:
- check_memory_limit function
- process_batches with update_existing (rows_to_update path)
- process_batches with existing_samples=None (load from DB)
- process_batches batch exception handling
- process_batches adaptive batching + GC
"""

import gc
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from nextseek_api.batch_upload.models import (
    InsertableSample, RowOutcome, BatchResult, DirectionComputation,
)
from nextseek_api.batch_upload.config import BatchUploadConfig
from nextseek_api.batch_upload.errors import ErrorCollector


# ---------------------------------------------------------------------------
# check_memory_limit
# ---------------------------------------------------------------------------

class TestCheckMemoryLimit:

    @patch("nextseek_api.batch_upload.insert.psutil.Process")
    def test_under_limit_no_gc(self, mock_process_cls):
        from nextseek_api.batch_upload.insert import check_memory_limit
        mock_process = MagicMock()
        mock_process.memory_info.return_value = MagicMock(rss=100 * 1024 * 1024)  # 100 MB
        mock_process_cls.return_value = mock_process
        # Should not trigger gc.collect for limit=200
        check_memory_limit(200)

    @patch("nextseek_api.batch_upload.insert.gc.collect")
    @patch("nextseek_api.batch_upload.insert.psutil.Process")
    def test_over_limit_triggers_gc(self, mock_process_cls, mock_gc):
        from nextseek_api.batch_upload.insert import check_memory_limit
        mock_process = MagicMock()
        mock_process.memory_info.return_value = MagicMock(rss=500 * 1024 * 1024)  # 500 MB
        mock_process_cls.return_value = mock_process
        check_memory_limit(200)
        mock_gc.assert_called_once()


# ---------------------------------------------------------------------------
# process_batches — edge cases
# ---------------------------------------------------------------------------

def _make_sample(uid, assay_ids=None):
    return InsertableSample(
        uuid=uid,
        title=uid,
        sample_type_id=1,
        json_metadata="{}",
        assay_ids=assay_ids or [],
    )


def _make_direction_computation():
    return DirectionComputation(
        direction_by_pair={},
        parents_of={},
        assays_by_uid={},
        child_uids_by_assay={},
        conflicts_by_assay={},
    )


class TestProcessBatchesAllExisting:

    @patch("nextseek_api.batch_upload.insert.determine_resume_uid", return_value=None)
    @patch("nextseek_api.batch_upload.insert.load_existing_samples", return_value={})
    def test_no_rows_to_process(self, mock_load, mock_resume):
        """All samples already exist, none to process."""
        from nextseek_api.batch_upload.insert import process_batches

        config = BatchUploadConfig()
        samples = [_make_sample("UID-1")]

        result = process_batches(
            insertable_samples=samples,
            project_id=1,
            contributor_id=1,
            config=config,
            direction_computation=_make_direction_computation(),
            error_collector=ErrorCollector(),
            existing_samples={"UID-1": 100},
        )
        assert result.outcomes["UID-1"].status == "skipped"
        assert result.outcomes["UID-1"].reason == "duplicate"


class TestProcessBatchesBatchException:

    @patch("nextseek_api.batch_upload.insert.check_memory_limit")
    @patch("nextseek_api.batch_upload.insert.clear_caches")
    @patch("nextseek_api.batch_upload.insert.write_checkpoint")
    @patch("nextseek_api.batch_upload.insert.determine_resume_uid", return_value=None)
    def test_batch_exception_marks_failed(self, mock_resume, mock_ckpt, mock_caches, mock_mem):
        """When a batch transaction raises, all rows in that batch are marked failed."""
        from nextseek_api.batch_upload.insert import process_batches

        config = BatchUploadConfig()
        config.max_rows_per_batch = 10
        config.mem_limit_mb = 0  # disable memory check
        samples = [_make_sample("UID-NEW")]

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=RuntimeError("DB exploded"))
        mock_conn.__exit__ = MagicMock(return_value=False)

        ec = ErrorCollector()
        result = process_batches(
            insertable_samples=samples,
            project_id=1,
            contributor_id=1,
            config=config,
            direction_computation=_make_direction_computation(),
            error_collector=ec,
            existing_samples={},
            conn_factory=lambda: mock_conn,
        )
        assert result.outcomes["UID-NEW"].status == "failed"
        assert len(ec.all_errors()) > 0


class TestProcessBatchesUpdatePath:

    @patch("nextseek_api.batch_upload.insert.determine_resume_uid", return_value=None)
    def test_update_existing_path(self, mock_resume):
        """update_existing=True routes samples to the update path."""
        from nextseek_api.batch_upload.insert import process_batches

        import nextseek_api.batch_upload.update as update_mod
        mock_bulk = MagicMock(return_value={
            "UID-1": RowOutcome(status="success", sample_id=100, reason="updated"),
        })
        mock_details = MagicMock(return_value={"UID-1": {"sample_id": 100}})

        with patch.object(update_mod, "bulk_update_samples", mock_bulk), \
             patch.object(update_mod, "load_existing_sample_details", mock_details):

            config = BatchUploadConfig()
            samples = [_make_sample("UID-1")]

            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)

            result = process_batches(
                insertable_samples=samples,
                project_id=1,
                contributor_id=1,
                config=config,
                direction_computation=_make_direction_computation(),
                error_collector=ErrorCollector(),
                existing_samples={"UID-1": 100},
                update_existing=True,
                conn_factory=lambda: mock_conn,
            )
            assert result.outcomes["UID-1"].status == "success"
            assert mock_bulk.call_args.kwargs["enable_auto_permissions"] is True


class TestProcessBatchesPermissions:

    @patch("nextseek_api.batch_upload.insert.determine_resume_uid", return_value=None)
    def test_new_insert_path_creates_project_permissions_by_default(self, mock_resume):
        """New inserts should create project permission rows by default."""
        from nextseek_api.batch_upload.insert import process_batches

        config = BatchUploadConfig()
        samples = [_make_sample("UID-NEW")]

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("nextseek_api.batch_upload.insert.insert_policies_for_uids", return_value=[("UID-NEW", 501)]), \
             patch("nextseek_api.batch_upload.insert.insert_samples", return_value=[(100, "UID-NEW")]), \
             patch("nextseek_api.batch_upload.insert.batch_insert_projects_samples", return_value=1), \
             patch("nextseek_api.batch_upload.insert.batch_insert_assay_assets", return_value=0), \
             patch("nextseek_api.batch_upload.insert.write_checkpoint"), \
             patch("nextseek_api.batch_upload.insert.PermissionsInserter.insert_for_policy_ids", return_value=1) as mock_insert_permissions:

            result = process_batches(
                insertable_samples=samples,
                project_id=1,
                contributor_id=1,
                config=config,
                direction_computation=_make_direction_computation(),
                error_collector=ErrorCollector(),
                existing_samples={},
                conn_factory=lambda: mock_conn,
            )

        assert result.outcomes["UID-NEW"].status == "success"
        assert result.permissions_inserted_count == 1
        assert mock_insert_permissions.call_args.args[0] == [501]

    @patch("nextseek_api.batch_upload.insert.determine_resume_uid", return_value=None)
    def test_update_exception_path(self, mock_resume):
        """Exception during bulk update marks samples as failed."""
        from nextseek_api.batch_upload.insert import process_batches

        import nextseek_api.batch_upload.update as update_mod
        with patch.object(update_mod, "load_existing_sample_details", side_effect=RuntimeError("Update DB error")), \
             patch.object(update_mod, "bulk_update_samples", MagicMock()):

            config = BatchUploadConfig()
            samples = [_make_sample("UID-1")]

            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)

            ec = ErrorCollector()
            result = process_batches(
                insertable_samples=samples,
                project_id=1,
                contributor_id=1,
                config=config,
                direction_computation=_make_direction_computation(),
                error_collector=ec,
                existing_samples={"UID-1": 100},
                update_existing=True,
                conn_factory=lambda: mock_conn,
            )
            assert result.outcomes["UID-1"].status == "failed"


class TestProcessBatchesExistingSamplesNone:

    @patch("nextseek_api.batch_upload.insert.determine_resume_uid", return_value=None)
    def test_loads_existing_from_db_when_none(self, mock_resume):
        """When existing_samples=None, load_existing_samples is called."""
        from nextseek_api.batch_upload.insert import process_batches

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))

        with patch("nextseek_api.batch_upload.insert.load_existing_samples", return_value={"UID-1": 100}) as mock_load:
            config = BatchUploadConfig()
            samples = [_make_sample("UID-1")]

            result = process_batches(
                insertable_samples=samples,
                project_id=1,
                contributor_id=1,
                config=config,
                direction_computation=_make_direction_computation(),
                error_collector=ErrorCollector(),
                existing_samples=None,
                conn_factory=lambda: mock_conn,
            )
            mock_load.assert_called_once()
            assert result.outcomes["UID-1"].status == "skipped"
