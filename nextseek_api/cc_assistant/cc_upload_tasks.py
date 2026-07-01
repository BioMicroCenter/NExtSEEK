"""Step 3 — CC upload Celery task (SPEC-3 §4).

Saves uploaded files into the user's persistent input/ dir (E2). Runs host-side;
no credential reaches the agent (OI-3). Mirrors batch_upload's async + update_state.
Filename validation lives in the celery-free `cc_upload_validate` module so the
hermetic validator test never imports the celery block below.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from nextseek_api.cc_assistant.cc_upload_validate import validate_upload_filename


try:
    from nextseek_api.batch_upload.celery_app import app
except Exception:  # pragma: no cover
    from celery import shared_task as _shared

    def app_task(*a, **k):
        return _shared(*a, **k)
else:
    app_task = app.task


@app_task(bind=True, queue="batch_upload", name="cc_assistant.upload")
def run_cc_upload_task(self, *, input_mnt: str, files: list[dict]):
    """``files`` = [{"name": str, "tmp_path": str}] staged by the view under MEDIA_ROOT.
    Validate each name and move it into ``input_mnt``."""
    dest_root = Path(input_mnt)
    dest_root.mkdir(parents=True, exist_ok=True)
    saved = []
    total = len(files) or 1
    try:
        for i, f in enumerate(files):
            safe = validate_upload_filename(f["name"])
            dst = dest_root / safe
            # MUST be shutil.move, not os.replace/os.rename: the staging dir is
            # MEDIA_ROOT="/media" (container overlayfs, no volume mount) while
            # input_mnt is under the host bind mount /srv/dmac/users:/dmac/users —
            # a different device. os.replace is rename(2) and raises
            # OSError(EXDEV, "Invalid cross-device link") across devices; shutil.move
            # falls back to copy2 + unlink when rename fails cross-device.
            shutil.move(f["tmp_path"], dst)
            saved.append(safe)
            self.update_state(state="PROGRESS",
                              meta={"progress_pct": int((i + 1) / total * 100), "saved": saved})
        return {"saved": saved, "count": len(saved)}
    finally:
        for f in files:
            try:
                os.unlink(f["tmp_path"])
            except FileNotFoundError:
                pass
