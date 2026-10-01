"""A record of each workbook build-upload-xlsx rendered, keyed by its sha256.

upload-reingest trusts nothing else: it loads the record by (user, build id),
re-hashes the workbook and refuses any mismatch, so the bytes uploaded are the
bytes the curator reviewed. Records live under a per-user directory; a build
with no signed-in user is filed under "anonymous", which load() never reads,
so an unattributed build can never be uploaded.

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
          user_id) -> dict:
    record = {
        "build_id": sha256_of(path), "artifact_key": artifact_key,
        "sample_type": sample_type, "mode": mode, "manifest_id": manifest_id,
        "disposition": disposition, "open_warnings": list(open_warnings),
        "row_count": row_count, "project_id": project_id, "project_note": project_note,
        "answers_digest": answers_digest, "built_by_user_id": user_id,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "path": os.path.abspath(path),
    }
    owner = _owner_dir(user_id)
    os.makedirs(owner, exist_ok=True)
    final = os.path.join(owner, f"{record['build_id']}.json")
    with tempfile.NamedTemporaryFile("w", dir=owner, delete=False, suffix=".tmp",
                                     encoding="utf-8") as fh:
        tmp = fh.name
        try:
            json.dump(record, fh, indent=2)
        except BaseException:
            fh.close()
            os.unlink(tmp)
            raise
    os.replace(tmp, final)
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
