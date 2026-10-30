"""A record of each workbook build-upload-xlsx rendered, keyed by its sha256.

upload-reingest trusts nothing else: it loads the record by (user, build id),
re-hashes the workbook and refuses any mismatch, so the bytes uploaded are the
bytes the curator reviewed. Records live under a per-user directory; a build
with no signed-in user is filed under "anonymous", which load() never reads,
so an unattributed build can never be uploaded.

``shown_in`` is the chat turn the build ran in, as the server saw it (chat
session id, turn number, task id), or None outside a chat turn. upload-reingest
uploads a build only from a later turn of that chat, once that turn's reply
reached the user.

Same root convention as store.py's manifests. Writes only under the outputs
root, never to NExtSEEK.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import tempfile

_ROOT = os.environ.get("NEXTSEEK_BUILD_RECORDS_DIR") or os.path.join(
    os.environ.get("NEXTSEEK_OUTPUTS_DIR") or "outputs", "reingest_builds")

_BUILD_ID = re.compile(r"[0-9a-f]{64}")

#: What a chat turn is named by: the chat, the turn's number in it, its QueryTask.
_TURN_KEYS = ("session_id", "turn_number", "task_id")


class BuildRecordError(ValueError):
    """No usable build record for this user and id."""


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _owner_dir(user_id) -> str:
    return os.path.join(_ROOT, str(user_id) if user_id else "anonymous")


def write(*, path, artifact_key, sample_type, mode, manifest_id, disposition,
          open_warnings, row_count, project_id, project_note, answers_digest,
          user_id, test_only, chat_turn=None, answers=None, assays_to_create=(),
          parent_assays=(), rebuilt_from=None) -> dict:
    record = {
        "build_id": sha256_of(path), "artifact_key": artifact_key,
        "sample_type": sample_type, "mode": mode, "manifest_id": manifest_id,
        "disposition": disposition, "open_warnings": list(open_warnings),
        "row_count": row_count, "project_id": project_id, "project_note": project_note,
        "answers_digest": answers_digest, "built_by_user_id": user_id,
        "test_only": bool(test_only),
        "shown_in": ({key: chat_turn.get(key) for key in _TURN_KEYS}
                     if isinstance(chat_turn, dict) else None),
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "path": os.path.abspath(path),
        # The upload rebuilds from these (spec 3.5): the curator's answers, the
        # study assays it must create first, and the parents it then registers.
        "answers": answers,
        "assays_to_create": list(assays_to_create),
        "parent_assays": list(parent_assays),
        "rebuilt_from": rebuilt_from,
    }
    owner = _owner_dir(user_id)
    os.makedirs(owner, exist_ok=True)
    final = os.path.join(owner, f"{record['build_id']}.json")
    release(record["build_id"], user_id)    # a fresh build starts with no upload claim
    fh = tempfile.NamedTemporaryFile("w", dir=owner, delete=False, suffix=".tmp",
                                     encoding="utf-8")
    tmp = fh.name
    try:
        # The dump, the close (which flushes) and the replace all fail the same
        # way: no record, and no .tmp left behind.
        with fh:
            json.dump(record, fh, indent=2)
        os.replace(tmp, final)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {k: v for k, v in record.items() if k != "path"}


def load(build_id: str, user_id) -> dict:
    if not isinstance(build_id, str) or not _BUILD_ID.fullmatch(build_id):
        raise BuildRecordError(f"not a build id: {build_id!r}")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise BuildRecordError("no signed-in user")
    path = os.path.join(_owner_dir(user_id), f"{build_id}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise BuildRecordError(f"no build {build_id[:12]} for this user") from None
    except (OSError, ValueError) as exc:
        raise BuildRecordError(f"build {build_id[:12]} record is unreadable") from exc


def _update(build_id: str, user_id, changes: dict) -> None:
    """Rewrite one record atomically with ``changes`` (a None value removes the key)."""
    record = load(build_id, user_id)
    for key, value in changes.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    path = os.path.join(_owner_dir(user_id), f"{build_id}.json")
    with tempfile.NamedTemporaryFile("w", dir=_owner_dir(user_id), delete=False, suffix=".tmp",
                                     encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    os.replace(fh.name, path)


def mark_uploaded(build_id: str, user_id) -> None:
    """Stamp the record once its upload job is done, so the same build is never uploaded twice.
    Clears any job it was waiting on. Best-effort: the upload already started."""
    try:
        _update(build_id, user_id, {
            "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "waiting_job": None})
    except Exception:  # noqa: BLE001 -- bookkeeping must not fail an upload that started
        pass


def mark_waiting(build_id: str, user_id, *, job_id: str, manifest_id, uploaded_build_id: str) -> None:
    """Remember the job a call stopped waiting for while it still ran (the build keeps its claim), so a
    later reingest op can settle it once it ends (``upload.settle_waiting``). ``uploaded_build_id`` is
    the workbook the job uploaded: this build, or the rebuild the call made from it. Best-effort."""
    try:
        _update(build_id, user_id, {"waiting_job": {
            "job_id": str(job_id), "manifest_id": manifest_id,
            "uploaded_build_id": uploaded_build_id}})
    except Exception:  # noqa: BLE001 -- bookkeeping must not fail an upload that started
        pass


def clear_waiting(build_id: str, user_id) -> None:
    """Forget the job a build waited on (it failed: the build is given back). Best-effort."""
    try:
        _update(build_id, user_id, {"waiting_job": None})
    except Exception:  # noqa: BLE001
        pass


def waiting(user_id) -> list[dict]:
    """``user_id``'s records whose upload job outlived its call and has not been settled yet."""
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return []
    out: list[dict] = []
    try:
        names = sorted(os.listdir(_owner_dir(user_id)))
    except OSError:
        return []
    for name in names:
        build_id, ext = os.path.splitext(name)
        if ext != ".json" or not _BUILD_ID.fullmatch(build_id):
            continue
        try:
            record = load(build_id, user_id)
        except BuildRecordError:
            continue
        if isinstance(record.get("waiting_job"), dict) and not record.get("uploaded_at"):
            out.append(record)
    return out


def _claim_path(build_id: str, user_id) -> str:
    return os.path.join(_owner_dir(user_id), f"{build_id}.claim")


def claim(build_id: str, user_id) -> bool:
    """Take the one upload claim on a build; False when another call already holds it. Atomic
    (exclusive create), so two overlapping calls cannot both start a job."""
    os.makedirs(_owner_dir(user_id), exist_ok=True)
    try:
        os.close(os.open(_claim_path(build_id, user_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except FileExistsError:
        return False


def release(build_id: str, user_id) -> None:
    """Give the claim back (the job did not start or did not succeed)."""
    try:
        os.unlink(_claim_path(build_id, user_id))
    except OSError:
        pass
