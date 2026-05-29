"""Path configuration for the Container-CC route, per the dmac_assistant SDS.

dmac_assistant's SDS (§5.3-5.5) defines the artifact flow: the agent works in a
per-user RW scratch dir, project data is mounted read-only, and after each turn a
host-side copier publishes new scratch files to a per-user output dir
(``output_root/<user_id>/<rel>``). For the dev instance this output dir is the
Dropbox ``example-project`` folder (``DMAC_USERS``: demo -> projects:["example-project"]),
so demo's artifacts land in ``example-project/demo/<run_id>/`` — exactly where
prior dmac runs wrote.

The one adaptation for NExtSEEK: the "bridge" is now the nextseek Django
container, and the CC container is its sibling (spawned via the host docker
socket). So there are TWO views of the same host dirs:

* **Host paths** (``host_*_root``) — used as Docker bind *sources* for the CC
  sibling container, and reported to the user via ``DMAC_PATH_MAPPINGS`` (D19).
* **nextseek-container mount points** (``scratch_mount`` / ``output_mount``) —
  where those same host dirs are mounted INTO the nextseek container so the
  host-side copier (``copier.copy_files``) can read scratch and write output.

All values are overridable via env (set in ``docker/nextseek.env``); defaults
target this personal dev instance.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

# Dev-instance defaults (this machine). Overridable via docker/nextseek.env.
_DEFAULT_HOST_DROPBOX_ROOT = "/Users/taishajoseph/Library/CloudStorage/Dropbox/DMAC_Data"
_DEFAULT_HOST_SCRATCH_ROOT = "/Users/taishajoseph/dmac-dev/scratch"
_DEFAULT_HOST_OUTPUT_ROOT = "/Users/taishajoseph/Library/CloudStorage/Dropbox/DMAC_Data/example-project"
_DEFAULT_SCRATCH_MOUNT = "/dmac/scratch"
_DEFAULT_OUTPUT_MOUNT = "/dmac/output"

# Single-user dev mapping (multi-user is a follow-up project). Mirrors
# dmac_assistant's DMAC_USERS demo -> ["example-project"].
_DEFAULT_USER_PROJECTS: dict[str, list[str]] = {"demo": ["example-project"]}


@dataclass(frozen=True)
class CCPaths:
    """Host paths (CC bind sources) + nextseek-container mount points (copier I/O)."""

    host_dropbox_root: str   # host: base for per-project RO mounts
    host_scratch_root: str   # host: per-user RW scratch (CC bind source)
    host_output_root: str    # host: published-artifact root (e.g. Dropbox example-project)
    scratch_mount: str       # nextseek-container path where host_scratch_root is mounted
    output_mount: str        # nextseek-container path where host_output_root is mounted

    @classmethod
    def from_env(cls) -> "CCPaths":
        return cls(
            host_dropbox_root=os.environ.get("DMAC_HOST_DROPBOX_ROOT", _DEFAULT_HOST_DROPBOX_ROOT),
            host_scratch_root=os.environ.get("DMAC_HOST_SCRATCH_ROOT", _DEFAULT_HOST_SCRATCH_ROOT),
            host_output_root=os.environ.get("DMAC_HOST_OUTPUT_ROOT", _DEFAULT_HOST_OUTPUT_ROOT),
            scratch_mount=os.environ.get("DMAC_SCRATCH_MOUNT", _DEFAULT_SCRATCH_MOUNT),
            output_mount=os.environ.get("DMAC_OUTPUT_MOUNT", _DEFAULT_OUTPUT_MOUNT),
        )


def projects_for(user_id: str) -> list[str]:
    """Return the Dropbox project folders a NExtSEEK user may read (RO mounts).

    Overridable via DMAC_CC_USER_PROJECTS (JSON: {user_id: [project, ...]}).
    Unknown users get no project mount (scoped-access default).
    """
    mapping = dict(_DEFAULT_USER_PROJECTS)
    raw = os.environ.get("DMAC_CC_USER_PROJECTS")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                mapping = parsed
        except json.JSONDecodeError:
            pass
    return list(mapping.get(user_id, []))
