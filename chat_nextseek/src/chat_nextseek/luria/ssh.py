"""Thin subprocess wrappers over the openssh-client binaries for Luria submission.

Key handling: the bind-mounted key is copied to a private 600 temp file before
use (guards against SSH rejecting a key whose mounted perms/ownership are too
open). BatchMode=yes forces a fast non-interactive failure rather than a hang.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "UserKnownHostsFile=/app/.ssh/known_hosts",
    "-o", "BatchMode=yes",
]


def _target(luria_env: dict) -> str:
    return f'{luria_env["user"]}@{luria_env["host"]}'


def prepare_key(key_path: str) -> str:
    """Copy the private key to a fresh 600 temp file; caller removes it when done."""
    fd, tmp = tempfile.mkstemp(prefix="luria_key_")
    os.close(fd)
    shutil.copyfile(key_path, tmp)
    os.chmod(tmp, 0o600)
    return tmp


def ssh_run(luria_env: dict, remote_cmd: str, *, key_path: str) -> str:
    """Run one remote command over SSH; return stdout, raise RuntimeError on nonzero exit."""
    cmd = ["ssh", "-i", key_path, *_SSH_OPTS, _target(luria_env), remote_cmd]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ssh failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def scp_file(luria_env: dict, local_path, remote_path: str, *, key_path: str) -> None:
    """Copy one local file to an explicit remote path (renaming as needed)."""
    dest = f'{_target(luria_env)}:{remote_path}'
    cmd = ["scp", "-i", key_path, *_SSH_OPTS, str(local_path), dest]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"scp failed ({proc.returncode}): {proc.stderr.strip()}")
