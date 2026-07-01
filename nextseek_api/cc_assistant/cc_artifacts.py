"""Step 3 — hybrid output partition + artifact bundling (SPEC-3 §5).

Deliverables (everything NOT under scratch/raw/) become downloadable artifacts;
scratch/raw/ is debug output kept on disk + (the jsonl) in the DB, never bundled.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

RAW_PREFIX = "raw/"


def partition_changed(changed: set[str]) -> tuple[set[str], set[str]]:
    """Split changed scratch relpaths into (artifacts, raw). Rel under ``raw/`` is
    raw; everything else is a deliverable artifact."""
    raw = {r for r in changed if r == "raw" or r.startswith(RAW_PREFIX)}
    artifacts = set(changed) - raw
    return artifacts, raw


def build_artifact_zip(srcs: list[Path], dest_zip: Path, *, arc_prefix: Path | None = None) -> Path:
    """Zip ``srcs`` preserving relpaths under ``arc_prefix`` (default: common parent)."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    base = arc_prefix or (srcs[0].parent if srcs else Path("."))
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sorted(srcs, key=lambda p: str(p)):
            arcname = str(src.relative_to(base))
            zf.writestr(arcname, src.read_bytes())
    return dest_zip
