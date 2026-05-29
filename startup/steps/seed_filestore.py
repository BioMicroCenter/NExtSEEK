"""Seed the SEEK filestore volume from a tar.gz snapshot.

The seek_production MySQL dump references content blobs (uploaded data files,
SOPs, avatars, ...) whose actual bytes live on the `seek-filestore` volume at
/seek/filestore. Without those bytes, SEEK shows the metadata but every
download/preview 404s. This step streams a committed snapshot of that volume
into the running seek container.

Mirrors seed.py's pattern: stream bytes over stdin into a `docker compose exec`
(no `docker cp`, no temp file, no cleanup). The seek container has tar+gzip but
NOT unzip, so the archive is a gzipped tar that `tar -xzf -` reads from stdin.
SEEK runs as www-data (uid/gid 33); the host archive carries the builder's uid,
so we chown back to www-data after extracting.
"""
from __future__ import annotations

from pathlib import Path

from startup.lib.docker_ops import compose_exec, DockerOpsError

# NOT committed (gitignored): at ~215MB it exceeds GitHub's 100MB/file push
# limit, so unlike the small *.sql.gz / *.cypher.gz DB seeds it's distributed
# out-of-band and dropped in here by hand. Callers warn-and-skip if absent.
FILESTORE_ARCHIVE = "startup/seed/filestore.tar.gz"
FILESTORE_PATH = "/seek/filestore"
SEEK_OWNER = "www-data:www-data"


def archive_present(repo_root: Path) -> bool:
    """True if the filestore snapshot is available to load."""
    return (repo_root / FILESTORE_ARCHIVE).exists()


def filestore_is_populated(repo_root: Path, env: dict[str, str]) -> bool:
    """True if the filestore already holds asset blobs.

    SEEK's entrypoint creates the empty skeleton dirs (assets/, avatars/, ...)
    on first boot, so dir existence isn't a useful signal. A real file under
    assets/ means a prior seed (or live usage) already populated the volume.
    """
    try:
        out = compose_exec(
            service="seek",
            command=["sh", "-c", f"find {FILESTORE_PATH}/assets -type f 2>/dev/null | head -1"],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    return bool(out.strip())


def load_filestore(repo_root: Path, env: dict[str, str]) -> None:
    """Stream startup/seed/filestore.tar.gz into the seek filestore volume.

    Extracts (overwrite-merge) into /seek/filestore, then restores www-data
    ownership so SEEK can read/write the blobs. Requires the seek container to
    be running with its filestore dirs initialized (see build.start_seek_side).
    """
    archive = repo_root / FILESTORE_ARCHIVE
    compose_exec(
        service="seek",
        command=[
            "sh",
            "-c",
            f"tar -C {FILESTORE_PATH} -xzf - && chown -R {SEEK_OWNER} {FILESTORE_PATH}",
        ],
        project_dir=repo_root,
        env=env,
        stdin=archive.read_bytes(),
    )
