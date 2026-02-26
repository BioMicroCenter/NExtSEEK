"""Tests for per-user job index tracking."""
from __future__ import annotations

import json
import threading
import time

import pytest
from django.test import override_settings

from nextseek_api.batch_upload.job_index import list_jobs, register_job, user_owns_job


class TestRegisterAndListJobs:
    def test_register_and_list_jobs(self, tmp_path):
        """Register 3 jobs, list returns all 3 newest-first."""
        d = str(tmp_path)
        register_job(1, "aaa", project_id=10, jobs_dir=d)
        time.sleep(0.01)
        register_job(1, "bbb", project_id=20, jobs_dir=d)
        time.sleep(0.01)
        register_job(1, "ccc", project_id=30, jobs_dir=d)

        result = list_jobs(1, jobs_dir=d)
        assert result["total"] == 3
        ids = [j["job_id"] for j in result["jobs"]]
        assert ids == ["ccc", "bbb", "aaa"]


class TestUserOwnsJob:
    def test_user_owns_job_true(self, tmp_path):
        """Registered job returns True."""
        d = str(tmp_path)
        register_job(1, "job-1", project_id=10, jobs_dir=d)
        assert user_owns_job(1, "job-1", jobs_dir=d) is True

    def test_user_owns_job_false(self, tmp_path):
        """Unregistered job returns False."""
        d = str(tmp_path)
        register_job(1, "job-1", project_id=10, jobs_dir=d)
        assert user_owns_job(1, "job-999", jobs_dir=d) is False

    def test_user_owns_job_different_user(self, tmp_path):
        """User A's job not visible to user B."""
        d = str(tmp_path)
        register_job(1, "job-1", project_id=10, jobs_dir=d)
        assert user_owns_job(2, "job-1", jobs_dir=d) is False


class TestTTLPurge:
    @override_settings(BATCH_UPLOAD_JOB_TTL_HOURS=1)
    def test_ttl_purge_on_list(self, tmp_path):
        """Register with old timestamp, list purges it."""
        d = str(tmp_path)
        # Manually write a job with an old created_at (2 hours ago)
        path = tmp_path / "1.json"
        old_entry = {"job_id": "old-job", "project_id": 10, "created_at": time.time() - 7200}
        new_entry = {"job_id": "new-job", "project_id": 20, "created_at": time.time()}
        path.write_text(json.dumps([old_entry, new_entry]))

        result = list_jobs(1, jobs_dir=d)
        assert result["total"] == 1
        assert result["jobs"][0]["job_id"] == "new-job"

        # Verify the file was rewritten without the old entry
        with open(path) as f:
            persisted = json.load(f)
        assert len(persisted) == 1
        assert persisted[0]["job_id"] == "new-job"


class TestPagination:
    def test_pagination(self, tmp_path):
        """Register 25 jobs, page 1 returns 20, page 2 returns 5."""
        d = str(tmp_path)
        for i in range(25):
            register_job(1, f"job-{i:03d}", project_id=i, jobs_dir=d)

        page1 = list_jobs(1, page=1, page_size=20, jobs_dir=d)
        assert len(page1["jobs"]) == 20
        assert page1["total"] == 25
        assert page1["page"] == 1

        page2 = list_jobs(1, page=2, page_size=20, jobs_dir=d)
        assert len(page2["jobs"]) == 5
        assert page2["total"] == 25
        assert page2["page"] == 2


class TestEmptyUserFile:
    def test_empty_user_file(self, tmp_path):
        """List for user with no jobs returns empty."""
        d = str(tmp_path)
        result = list_jobs(999, jobs_dir=d)
        assert result == {"jobs": [], "total": 0, "page": 1, "page_size": 20}


class TestConcurrentWrites:
    def test_concurrent_writes(self, tmp_path):
        """Use threading to register from multiple threads, verify all entries present."""
        d = str(tmp_path)
        num_threads = 10
        errors = []

        def worker(idx):
            try:
                register_job(1, f"thread-job-{idx}", project_id=idx, jobs_dir=d)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent writes: {errors}"

        result = list_jobs(1, jobs_dir=d)
        assert result["total"] == num_threads
        ids = {j["job_id"] for j in result["jobs"]}
        expected = {f"thread-job-{i}" for i in range(num_threads)}
        assert ids == expected
