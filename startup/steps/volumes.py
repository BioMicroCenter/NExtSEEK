"""Create the named volumes required by the compose stack."""
from __future__ import annotations

from startup.lib.docker_ops import volume_exists, volume_create, bootstrap_staging_dir

REQUIRED_VOLUMES: list[str] = [
    "seek-filestore",
    "seek-mysql-db",
    "seek-solr-data",
    "seek-cache",
    "nextseek-static-files",
    "neo4j-data",
    # Container-CC per-project/per-user trees (G7-10) — persists CC input,
    # shared, scratch, cc-state, memory, and output across container recreate.
    "dmac-cc-users",
]


def volume_names_for_prefix(prefix: str) -> list[str]:
    """Return the full volume names including the instance prefix."""
    return [f"{prefix}{name}" for name in REQUIRED_VOLUMES]


def ensure_volumes(prefix: str) -> list[str]:
    """Create any missing volumes. Returns the names actually created (idempotent: empty if all already exist)."""
    created: list[str] = []
    for full_name in volume_names_for_prefix(prefix):
        if not volume_exists(full_name):
            volume_create(full_name)
            created.append(full_name)
    return created


def ensure_cc_staging_dir(prefix: str) -> None:
    """Step 2c (G7-11 Task 13, iter-1 M-1): ensure the `_staging` subdir
    exists inside the (possibly instance-prefixed) `dmac-cc-users` volume,
    owned by uid 1001 (the NS sidecar's non-root user), before Task 14's
    `_staging` subpath mount is ever attempted.

    Docker's Engine refuses to start a container whose
    `VolumeOptions.Subpath` backing directory does not already exist inside
    the volume, and compose `restart:` does NOT retry container-create
    failures -- so this must run at install time (`./startup.sh install`,
    same as `ensure_volumes` above), not lazily at sidecar startup. `_staging`
    is a reserved top-level name distinct from every project dir (those are
    always `{pid}-{slug}`, always containing a hyphen after a numeric id),
    so it can never collide with per-project CC trees in the same volume.
    Idempotent (mkdir -p / chown).
    """
    bootstrap_staging_dir(f"{prefix}dmac-cc-users")
