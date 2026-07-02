"""Create the named volumes required by the compose stack."""
from __future__ import annotations

from startup.lib.docker_ops import volume_exists, volume_create

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
