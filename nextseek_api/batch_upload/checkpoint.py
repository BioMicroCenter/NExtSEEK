"""Append-only checkpoint/resume for batch upload."""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def write_checkpoint(checkpoint_dir: str, checkpoint_name: str, last_uid: str) -> None:
    """Append the last processed UID to the checkpoint file."""
    if not checkpoint_dir:
        return
    os.makedirs(checkpoint_dir, exist_ok=True)
    path = os.path.join(checkpoint_dir, checkpoint_name)
    with open(path, "a", encoding="utf-8") as f:
        f.write(last_uid + "\n")


def get_last_checkpoint_uid(
    checkpoint_dir: str, checkpoint_name: str
) -> Optional[str]:
    """Read the last UID from the checkpoint file."""
    if not checkpoint_dir:
        return None
    path = os.path.join(checkpoint_dir, checkpoint_name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        return lines[-1].strip() if lines else None
    except OSError:
        return None


def determine_resume_uid(
    cli_flag: Optional[str],
    checkpoint_dir: str,
    checkpoint_name: str,
) -> Optional[str]:
    """Determine the resume UID, never moving backward.

    Takes the lexicographic max of cli_flag and last checkpoint.
    """
    last = get_last_checkpoint_uid(checkpoint_dir, checkpoint_name)
    candidates = [x for x in (cli_flag, last) if x]
    if not candidates:
        return None
    return max(candidates)
