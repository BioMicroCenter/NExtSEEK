"""Pre/post-turn snapshotter for the scratch directory.

The bridge does NOT have a per-turn run_id concept exposed by Claude or
the in-container plugin. Instead, it observes the filesystem: snapshot
every regular file under <scratch_root>/<user_id>/ before the turn,
snapshot again after, and the relative paths whose (size, mtime_ns)
pair is new or changed are the artifacts the copier should publish.

Plan A T12 — supersedes the T5 subdir-diff approach (Amendment 10).
M2 invariant: symlinks are excluded from the snapshot.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def snapshot_scratch_files(
    scratch_root: Path, user_id: str
) -> dict[str, tuple[int, int]]:
    """Return {relative_path: (size, mtime_ns)} for every regular,
    non-symlink file under <scratch_root>/<user_id>/.

    Returns empty dict if the user dir does not exist (first-turn case).
    Symlinks are excluded entirely (M2 defense-in-depth).
    """
    if not _USER_ID_RE.fullmatch(user_id):
        raise ValueError(f"invalid user_id: {user_id!r}")
    user_dir = scratch_root / user_id
    if not user_dir.is_dir():
        return {}

    out: dict[str, tuple[int, int]] = {}
    for dirpath, _dirnames, filenames in os.walk(user_dir, followlinks=False):
        for filename in filenames:
            full = Path(dirpath) / filename
            if full.is_symlink():
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            rel = str(full.relative_to(user_dir))
            out[rel] = (st.st_size, st.st_mtime_ns)
    return out


def diff_files(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> set[str]:
    """Return relative paths whose (size, mtime_ns) is new or changed."""
    return {rel for rel, version in after.items() if before.get(rel) != version}
