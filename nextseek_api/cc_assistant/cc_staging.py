"""User-scoped NS sidecar staging sweep (G7-11, Task 14).

The NS sidecar (``docker/ns-sidecar/app/staging.py``) stages downloaded
``report`` / ``generate-submission`` artifacts into its ``SIDECAR_STAGING_DIR``
under the layout::

    {SIDECAR_STAGING_DIR}/{sha256(api_user)}/{request_id}/<files>
    {SIDECAR_STAGING_DIR}/{sha256(api_user)}/{request_id}.complete   # atomic marker

In this integration ``SIDECAR_STAGING_DIR`` is the RESERVED top-level
``_staging/`` subpath of the ``dmac-cc-users`` volume, mounted read-write into
the sidecar (compose ``volume.subpath: _staging``) and visible to the trusted
Django process as ``{DMAC_USER_ROOT_MOUNT}/_staging/...``. Upstream had a
host-run bridge (``src/dmac_assistant/staging_sweep.py`` + ``ws.py``) copy
``.complete``-marked request dirs into the agent's scratch the same turn; this
integration has NO bridge, so that same-turn sweep is re-homed here as a single
trusted-code entrypoint.

SWEEP CONTRACT (this module is the ONLY writer of ``{project}/{user}/`` paths
in the staging flow — never the agent, never the sidecar):

* The sidecar's mount is locked to ``_staging/`` (it CANNOT write
  ``{project}/{user}/`` by construction); this module reads ``_staging/`` and
  writes ONLY the requesting user's own ``{project}/{user}/scratch/`` subtree.
* The destination is caller-supplied and derived exclusively from the validated
  ``(project_dirname, user_id)`` of the CURRENT request — NEVER from any staged
  file/dir name. Staged content therefore cannot redirect delivery to a foreign
  subtree, and the hashed staging dir it reads is keyed by the current
  ``api_user`` only. Cross-user delivery is impossible by construction.
* Path safety (defense in depth, independent of the sidecar's own key
  sanitization): symlinks are skipped, relative components are rejected if
  absolute or containing ``..``, and every destination is asserted to stay
  within the user's scratch subtree.

Same-turn vs. recovery (``since_ts``):

* ``since_ts`` set (in-turn call from ``run_cc_turn``, pre-``_publish_artifacts``):
  sweep ONLY ``.complete`` markers with ``mtime >= since_ts`` — i.e. artifacts
  staged during THIS turn. They land in the user's scratch and are surfaced by
  the turn's existing publish diff (``_publish_artifacts``), so a turn-N staged
  artifact appears in turn N's published set. OLDER strays (crashed/timed-out
  earlier turns) are deliberately LEFT as ``.complete`` breadcrumbs so they are
  NOT attributed to the current turn.
* ``since_ts is None`` (recovery / management-command / Task 15 gate call):
  sweep ALL ``.complete`` markers, delivering older strays too. This is the
  PERMITTED recovery path (upstream keeps ``.complete`` breadcrumbs for exactly
  this).

PATH-PAYLOAD DECISION (op result ``saved_files``/``staged_files`` strings):
the sidecar op result carries sidecar-container paths under
``SIDECAR_STAGING_DIR`` (e.g. ``/home/sidecar/staging/<hash>/<req>/<name>``).
Those are meaningless in the agent's container and are SUPERSEDED by the swept +
published artifacts. After a sweep, the agent reads the file at
``/data/scratch/nextseek-artifacts/<name>`` (its own scratch mount) and the
turn reports it in the ``artifacts`` channel via ``_publish_artifacts`` — the
same contract as upstream, where "the agent never sees SIDECAR_STAGING_DIR
paths as such; it only ever sees the swept copies at
``<scratch>/nextseek-artifacts/<relpath>``". The engine does not rewrite the
op-result strings (doing so would couple it to the sidecar's internal
``sha256(api_user)/request_id`` layout for no gain).
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Reserved top-level name inside the dmac-cc-users volume. ``project_dirname()``
# always emits ``{pid}-{slug}`` (always a hyphen after a numeric id), so
# ``_staging`` (no hyphen) can never collide with a per-project CC tree.
STAGING_SUBDIR = "_staging"
# Destination child under the user's scratch subtree (upstream parity:
# staging_sweep.py:52 uses ``nextseek-artifacts``).
ARTIFACTS_SUBDIR = "nextseek-artifacts"


@dataclass(frozen=True)
class SweepResult:
    """Outcome of one sweep run.

    ``delivered`` — scratch-relative paths (under ``nextseek-artifacts/``)
    actually copied into the user's scratch this run.
    ``deferred_markers`` — request ids of OLDER strays intentionally left as
    ``.complete`` breadcrumbs (in-turn mode only; empty in recovery mode). These
    are the artifacts that "sweep in a later run" — NOT attributed to this turn.
    """

    delivered: list[str] = field(default_factory=list)
    deferred_markers: list[str] = field(default_factory=list)


def _user_hash(api_user: str) -> str:
    """SHA-256 of ``api_user``. MUST match the sidecar's
    ``sidecar.app.staging._user_hash`` (docker/ns-sidecar/app/staging.py:24-25),
    or staged artifacts are never found (silent drift)."""
    return hashlib.sha256(api_user.encode("utf-8")).hexdigest()


# Reuse the engine's segment validators so the sweep's identity guards are
# identical to the mount path's (a divergent guard is a cross-user vector).
def _validate_identity(api_user: str, user_id: str, project_dirname: str) -> None:
    from .cc_engine import _validate_user_id, _validate_project

    _validate_user_id(user_id)
    _validate_project(project_dirname)
    if not isinstance(api_user, str) or not api_user or "/" in api_user or "\x00" in api_user \
            or api_user in (".", ".."):
        raise ValueError(f"invalid api_user: {api_user!r}")


def _safe_rel(rel: Path) -> bool:
    """A staged file's request-relative path must never be absolute or contain a
    ``..`` component — otherwise the destination could escape the user subtree.
    (The sidecar sanitizes keys already; this is an independent floor.)"""
    return bool(rel.parts) and not rel.is_absolute() and ".." not in rel.parts


def _is_within(base: Path, candidate: Path) -> bool:
    """True iff ``candidate`` is lexically inside ``base`` (no symlink resolution
    — dest is the user's own scratch; we only guard against ``..`` escapes)."""
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _disambiguate(dst: Path) -> Path:
    """First free ``<stem>__N<suffix>`` sibling (never clobber a prior artifact;
    upstream staging_sweep.py:19-26 pattern)."""
    n = 1
    candidate = dst.parent / f"{dst.stem}__{n}{dst.suffix}"
    while candidate.exists():
        n += 1
        candidate = dst.parent / f"{dst.stem}__{n}{dst.suffix}"
    return candidate


def staging_root_for(user_root_mount: str) -> Path:
    """The trusted-process view of the sidecar's staging root: the reserved
    ``_staging`` subpath at the top of the ``dmac-cc-users`` volume mount."""
    return Path(str(user_root_mount).rstrip("/")) / STAGING_SUBDIR


def sweep_user_staging(
    *,
    user_root_mount: str,
    scratch_dir: str,
    api_user: str,
    user_id: str,
    project_dirname: str,
    since_ts: float | None = None,
) -> SweepResult:
    """Move this user's completed staged artifacts into their own scratch subtree.

    Reads ``{user_root_mount}/_staging/{sha256(api_user)}/*.complete`` and, for
    each completed request dir, copies its regular files into
    ``{scratch_dir}/nextseek-artifacts/<relpath>`` (disambiguating collisions),
    then removes the swept request dir + marker. On cleanup failure the marker is
    kept as a breadcrumb for a later retry (upstream parity).

    ``since_ts`` gates same-turn vs. recovery behavior (see module docstring):
    when set, only ``.complete`` markers with ``mtime >= since_ts`` are swept and
    older strays are left behind (deferred); when ``None``, all are swept.

    Cross-user safety: the destination is derived ONLY from the validated
    ``(project_dirname, user_id)`` and the source ONLY from ``sha256(api_user)``
    — never from staged content. See module docstring for the path-payload
    supersession contract.
    """
    _validate_identity(api_user, user_id, project_dirname)

    src_base = staging_root_for(user_root_mount) / _user_hash(api_user)
    result = SweepResult()
    if not src_base.is_dir():
        return result

    dst_base = Path(scratch_dir) / ARTIFACTS_SUBDIR

    for marker in sorted(src_base.glob("*.complete")):
        req_id = marker.stem
        req_dir = src_base / req_id

        # In-turn mode: skip OLDER strays (not this turn) so they are not
        # attributed to the current turn's publish set. They remain as
        # breadcrumbs for the recovery (since_ts=None) sweep.
        if since_ts is not None:
            try:
                if marker.stat().st_mtime < since_ts:
                    result.deferred_markers.append(req_id)
                    continue
            except OSError:
                continue

        # Defense in depth: a completed marker whose backing dir is a symlink or
        # escapes the hashed base is refused (never followed).
        if req_dir.is_symlink() or not _is_within(src_base, req_dir):
            logger.warning("cc staging sweep: refusing non-canonical request dir %r", req_id)
            continue
        if not req_dir.is_dir():
            marker.unlink(missing_ok=True)  # stray marker with no dir
            continue

        swept_ok = True
        for src in sorted(req_dir.rglob("*")):
            if src.is_symlink() or not src.is_file():
                continue
            rel = src.relative_to(req_dir)
            if not _safe_rel(rel):
                logger.warning("cc staging sweep: refusing unsafe staged relpath %r", str(rel))
                continue
            out_rel = Path(ARTIFACTS_SUBDIR) / rel
            dst = dst_base / rel
            if not _is_within(dst_base, dst):
                logger.warning("cc staging sweep: refusing out-of-subtree dest for %r", str(rel))
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    dst = _disambiguate(dst)
                    out_rel = out_rel.parent / dst.name
                shutil.copy2(src, dst)
            except OSError as exc:
                swept_ok = False
                logger.warning(
                    "cc staging sweep: copy failed for %r (%s)", str(rel), type(exc).__name__
                )
                continue
            result.delivered.append(str(out_rel))

        # Cleanup after a successful sweep; on failure keep the marker so a later
        # sweep retries (upstream staging_sweep.py:67-78).
        if not swept_ok:
            continue
        try:
            shutil.rmtree(req_dir)
        except OSError as exc:
            logger.warning(
                "cc staging sweep: cleanup of %r failed (%s); keeping marker for retry",
                req_id, type(exc).__name__,
            )
            continue
        marker.unlink(missing_ok=True)

    return result
