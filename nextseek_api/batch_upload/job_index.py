"""Per-user job tracking with file-based persistence."""
from __future__ import annotations

import fcntl
import json
import os
import time
from typing import List, Optional

from django.conf import settings

_JOBS_DIR = os.path.join(
    getattr(settings, "MEDIA_ROOT", "/tmp"), "celery_jobs"
)
DEFAULT_TTL_HOURS = 168  # 7 days default

def _get_ttl_seconds() -> int:
    return getattr(settings, "BATCH_UPLOAD_JOB_TTL_HOURS", DEFAULT_TTL_HOURS) * 3600

def _user_file(user_id: int, jobs_dir: str = None) -> str:
    d = jobs_dir or _JOBS_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{user_id}.json")

def register_job(user_id: int, job_id: str, project_id: int, jobs_dir: str = None) -> None:
    """Append a job entry to the user's index file. Thread/process-safe via fcntl."""
    path = _user_file(user_id, jobs_dir)
    entry = {"job_id": job_id, "project_id": project_id, "created_at": time.time()}

    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r+") as f:
            try:
                jobs = json.load(f)
            except (json.JSONDecodeError, ValueError):
                jobs = []
            jobs.append(entry)
            f.seek(0)
            f.truncate()
            json.dump(jobs, f)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

def list_jobs(user_id: int, page: int = 1, page_size: int = 20, jobs_dir: str = None) -> dict:
    """List jobs for user with pagination. Purges expired entries (lazy TTL)."""
    path = _user_file(user_id, jobs_dir)
    if not os.path.exists(path):
        return {"jobs": [], "total": 0, "page": page, "page_size": page_size}

    ttl = _get_ttl_seconds()
    now = time.time()

    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r+") as f:
            try:
                jobs = json.load(f)
            except (json.JSONDecodeError, ValueError):
                jobs = []
            # Purge expired
            active = [j for j in jobs if (now - j.get("created_at", 0)) < ttl]
            if len(active) != len(jobs):
                f.seek(0)
                f.truncate()
                json.dump(active, f)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # Sort newest first
    active.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    total = len(active)
    start = (page - 1) * page_size
    end = start + page_size
    return {"jobs": active[start:end], "total": total, "page": page, "page_size": page_size}

def user_owns_job(user_id: int, job_id: str, jobs_dir: str = None) -> bool:
    """Check if user_id owns the given job_id."""
    path = _user_file(user_id, jobs_dir)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                jobs = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return any(j.get("job_id") == job_id for j in jobs)
    except (json.JSONDecodeError, ValueError, OSError):
        return False
