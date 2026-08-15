"""Create and verify local rollback tags before any image is rebuilt."""
from __future__ import annotations

import datetime
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class RollbackTagError(RuntimeError):
    """A required pre-rebuild rollback tag could not be proven usable."""


@dataclass(frozen=True)
class RollbackTag:
    source: str
    tag: str
    image_id: str


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


def _inspect_id(image: str) -> str:
    result = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    if result.returncode != 0 or not result.stdout.strip():
        detail = result.stderr.strip() or result.stdout.strip() or "image not found"
        raise RollbackTagError(f"cannot inspect rollback source {image}: {detail}")
    return result.stdout.strip()


def _repository(image: str) -> str:
    without_digest = image.split("@", 1)[0]
    last_slash = without_digest.rfind("/")
    last_colon = without_digest.rfind(":")
    return without_digest[:last_colon] if last_colon > last_slash else without_digest


def rollback_suffix(
    repo_root: Path,
    now: datetime.datetime | None = None,
) -> str:
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%dT%H%M%S")
    result = _run(["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"])
    sha = result.stdout.strip() if result.returncode == 0 else "unknown"
    safe_sha = re.sub(r"[^a-zA-Z0-9_.-]", "-", sha) or "unknown"
    return f"pre-{stamp}-{safe_sha}"


def create_verified(
    images: Sequence[str],
    repo_root: Path,
    now: datetime.datetime | None = None,
) -> tuple[RollbackTag, ...]:
    """Tag all sources, verify identity, and fail before any build on error."""
    suffix = rollback_suffix(repo_root, now=now)
    prepared: list[RollbackTag] = []
    for source in images:
        source_id = _inspect_id(source)
        tag = f"{_repository(source)}:{suffix}"
        result = _run(["docker", "tag", source, tag])
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "docker tag failed"
            raise RollbackTagError(f"cannot create rollback tag {tag}: {detail}")
        tagged_id = _inspect_id(tag)
        if tagged_id != source_id:
            raise RollbackTagError(
                f"rollback tag identity mismatch for {tag}: {tagged_id} != {source_id}"
            )
        prepared.append(RollbackTag(source=source, tag=tag, image_id=source_id))
    return tuple(prepared)
