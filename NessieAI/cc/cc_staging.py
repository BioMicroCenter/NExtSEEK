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
  sanitization): ``_staging/<hash>`` and the user's scratch are read, listed and written only through
  ``NessieAI/cc/safe_fs.py``, each from its mount root (``_staging``; the scratch being delivered into: the turn's in-turn, the user's
  ``scratch_mnt`` on a recovery sweep) with the folders
  below it in ``rel``, so no link on either side is followed; relative components are rejected if absolute or
  containing ``..``, and every destination is asserted to stay within the user's scratch subtree. A staged
  file over ``_MAX_STAGED_BYTES`` is left in place, by the in-turn sweep and ``cc_sweep_staging`` alike.

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

import errno
import hashlib
import itertools
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import safe_fs

logger = logging.getLogger(__name__)

# Reserved top-level name inside the dmac-cc-users volume. ``project_dirname()``
# always emits ``{pid}-{slug}`` (always a hyphen after a numeric id), so
# ``_staging`` (no hyphen) can never collide with a per-project CC tree.
STAGING_SUBDIR = "_staging"
# Destination child under the user's scratch subtree (upstream parity:
# staging_sweep.py:52 uses ``nextseek-artifacts``).
ARTIFACTS_SUBDIR = "nextseek-artifacts"

# Fix round 1 (reviewer finding): request dirs are named by the sidecar's
# request_id, which the WS contract canonicalizes via ``str(UUID(v))``
# (docker/ns-sidecar/app/contract.py:57-64) — always the 36-char lowercase
# hex+hyphen form. Pin the sweep to exactly that shape so a marker whose stem
# is ``..`` (or any other non-canonical name) can never become a request-dir
# path segment — a guard INDEPENDENT of the sidecar's own canonicalization,
# as the module docstring promises.
_REQUEST_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


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
    """True iff ``candidate`` is lexically inside ``base`` AND its base-relative
    tail carries no ``..`` component. ``Path.relative_to`` alone is purely
    lexical — ``(base / "..").relative_to(base)`` yields ``PurePath("..")``
    WITHOUT raising — so the tail must additionally be ``..``-free or a
    ``..``-named segment would escape ``base`` (fix round 1, reviewer finding).
    No symlink resolution (symlinks are refused separately by the callers)."""
    try:
        rel = candidate.relative_to(base)
    except ValueError:
        return False
    return ".." not in rel.parts


def _disambiguate_names(base_name: str):
    """Yield ``base_name`` then ``<stem>__1<suffix>``, ``<stem>__2<suffix>``, …
    (never clobber a prior artifact; upstream staging_sweep.py:19-26 pattern).
    Name-only generator — the authoritative no-clobber guard is O_EXCL at open,
    so this must not stat/follow anything (fix round 2)."""
    yield base_name
    stem, dot, suffix = base_name.partition(".")
    suffix = f".{suffix}" if dot else ""
    n = 1
    while True:
        yield f"{stem}__{n}{suffix}"
        n += 1


class _DestUnsafe(RuntimeError):
    """A destination path component is (or raced into) a symlink / non-dir /
    escapes the user's real scratch subtree. Fail closed: refuse the request dir,
    preserve the marker, never deliver cross-user (fix round 2, reviewer C1)."""


_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW

# Staged files are read whole (the sidecar's stage_bytes held each in memory; make_stage streams, so a file can
# be larger than this); a larger one is never delivered by
# any path (the sweep and cc_sweep_staging share this cap) and is left in place.
_MAX_STAGED_BYTES = 256 * 1024 * 1024
_MAX_NAME_ATTEMPTS = 1000


def _deliver_file_safely(data: bytes, scratch_dir: str, rel_dir_parts: tuple[str, ...],
                         leaf_name: str, *, times_ns: tuple[int, int] | None = None) -> str:
    """Write ``data`` to ``{scratch_dir}/nextseek-artifacts/<rel_dir>/<name>`` without ever following a link,
    and return the basename actually written (``__N``-disambiguated on collision).

    The destination subtree is the agent's own scratch, so every folder step is opened with
    ``safe_fs.open_dir`` (``O_NOFOLLOW | O_DIRECTORY``, relative to the step before) and the leaf is created
    ``O_CREAT | O_EXCL | O_NOFOLLOW`` in that opened folder: a link at any step, or at the name, never
    redirects the write. Any unsafe step raises ``_DestUnsafe``. ``times_ns`` (atime, mtime) is set on the new
    file's fd. The staged source is read by the caller through ``safe_fs`` too.
    """
    try:
        dir_fd = safe_fs.open_dir(Path(scratch_dir), (ARTIFACTS_SUBDIR, *rel_dir_parts), create=True)
    except OSError as exc:
        raise _DestUnsafe(f"unsafe destination folder: {type(exc).__name__}") from exc
    try:
        for candidate in itertools.islice(_disambiguate_names(leaf_name), _MAX_NAME_ATTEMPTS):
            try:
                leaf_fd = os.open(candidate, _FILE_FLAGS, 0o644, dir_fd=dir_fd)
            except FileExistsError:
                continue  # name (or a link at that name) taken -> next __N
            except OSError as exc:
                raise _DestUnsafe(f"unsafe leaf {candidate!r}: {type(exc).__name__}") from exc
            try:
                with os.fdopen(leaf_fd, "wb") as out:
                    out.write(data)
                    out.flush()
                    if times_ns is not None:
                        os.utime(out.fileno(), ns=times_ns)
            except OSError as exc:
                raise _DestUnsafe(f"write failed for {candidate!r}: {type(exc).__name__}") from exc
            return candidate
        raise _DestUnsafe("no free disambiguated name")
    finally:
        os.close(dir_fd)


def _request_dir_state(staging_root: Path, user_hash: str, req_id: str) -> str:
    """``"dir"``, ``"missing"`` or ``"unsafe"`` for ``staging_root/user_hash/req_id``, never following a link."""
    try:
        fd = safe_fs.open_dir(staging_root, (user_hash, req_id))
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    os.close(fd)
    return "dir"


def _remove_request(staging_root: Path, user_hash: str, req_id: str, *, dir_too: bool) -> None:
    """Delete the request folder (with ``dir_too``) and its ``.complete`` marker inside the user's opened
    staging folder, walked from the sidecar's mount root: ``rmtree`` and ``unlink`` relative to its fd, so no
    link on the way is followed."""
    base_fd = safe_fs.open_dir(staging_root, (user_hash,))
    try:
        if dir_too:
            shutil.rmtree(req_id, dir_fd=base_fd)
        try:
            os.unlink(f"{req_id}.complete", dir_fd=base_fd)
        except FileNotFoundError:
            pass
    finally:
        os.close(base_fd)


def staging_root_for(user_root_mount: str) -> Path:
    """The trusted-process view of the sidecar's staging root: the reserved
    ``_staging`` subpath at the top of the ``dmac-cc-users`` volume mount.

    The sidecar mounts this folder whole and read-write, so it is registered with ``safe_fs`` as an agent root:
    the user's ``<hash>`` folder below it is sidecar-controlled and is always walked in ``rel``, never used as
    a root."""
    return safe_fs.register_agent_root(Path(str(user_root_mount).rstrip("/")) / STAGING_SUBDIR)


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

    # The sidecar's mount root is the trusted root (registered by staging_root_for); the sidecar controls
    # everything below it, the user's <hash> folder included, so that folder is a step in rel and is never
    # trusted as part of a root.
    staging_root = staging_root_for(user_root_mount)
    user_hash = _user_hash(api_user)
    result = SweepResult()
    try:
        # One listing of this user's staging folder, never through a link: the sidecar writes it.
        listing = list(safe_fs.iter_files(staging_root, (user_hash,)))
    except FileNotFoundError:
        return result
    except OSError as exc:
        logger.warning("cc staging sweep: refusing the staging folder (%s)", type(exc).__name__)
        return result
    markers: dict[str, os.stat_result] = {}
    staged: dict[str, list[tuple[str, os.stat_result]]] = {}
    for rel_from_root, st in listing:
        head, sep, tail = rel_from_root.removeprefix(f"{user_hash}/").partition("/")
        if not sep:
            if head.endswith(".complete"):
                markers[head[: -len(".complete")]] = st
            continue
        staged.setdefault(head, []).append((tail, st))

    dst_base = Path(scratch_dir) / ARTIFACTS_SUBDIR

    for req_id in sorted(markers):
        # Fix round 1: req_id becomes a path segment; pin it to the canonical UUID form the sidecar contract
        # guarantees BEFORE any use. A non-canonical marker stem is refused, never interpolated.
        if not _REQUEST_ID_RE.fullmatch(req_id):
            logger.warning("cc staging sweep: refusing non-canonical request id %r", req_id)
            continue
        # In-turn mode: skip OLDER strays (not this turn); they stay as breadcrumbs for the recovery sweep.
        if since_ts is not None and markers[req_id].st_mtime < since_ts:
            result.deferred_markers.append(req_id)
            continue
        state = _request_dir_state(staging_root, user_hash, req_id)
        if state == "unsafe":
            logger.warning("cc staging sweep: refusing non-canonical request dir %r", req_id)
            continue
        if state == "missing":
            try:
                _remove_request(staging_root, user_hash, req_id, dir_too=False)  # stray marker with no dir
            except OSError:
                pass
            continue

        if any(st.st_size > _MAX_STAGED_BYTES for _, st in staged.get(req_id, [])):
            # One oversized file: the whole request stays in place, nothing from it is delivered, on this
            # sweep or any later one (its marker is kept).
            logger.warning("cc staging sweep: leaving request %s in place, a file is over the size cap", req_id)
            continue

        swept_ok = True
        for tail, st in sorted(staged.get(req_id, []), key=lambda item: item[0]):
            rel = Path(tail)
            if not _safe_rel(rel):
                logger.warning("cc staging sweep: refusing unsafe staged relpath %r", tail)
                continue
            if not _is_within(dst_base, dst_base / rel):
                logger.warning("cc staging sweep: refusing out-of-subtree dest for %r", tail)
                continue
            try:
                data = safe_fs.read_file(staging_root, f"{user_hash}/{req_id}/{tail}",
                                         max_bytes=_MAX_STAGED_BYTES)
            except OSError as exc:
                swept_ok = False
                logger.warning("cc staging sweep: copy failed for %r (%s)", tail, type(exc).__name__)
                if exc.errno == errno.EFBIG:
                    break  # grew past the cap after the listing: deliver nothing further from this request
                continue
            try:
                final_name = _deliver_file_safely(data, scratch_dir, rel.parent.parts, rel.name,
                                                  times_ns=(st.st_atime_ns, st.st_mtime_ns))
            except _DestUnsafe as exc:
                # Unsafe destination: refuse the WHOLE request dir (fail closed), keep the marker for a
                # later retry, never deliver elsewhere.
                swept_ok = False
                logger.warning("cc staging sweep: refusing request %s, unsafe destination for %r (%s)",
                               req_id, tail, exc)
                break
            del data  # one file in memory at a time
            result.delivered.append(str(Path(ARTIFACTS_SUBDIR) / rel.parent / final_name))

        # Cleanup after a successful sweep; on failure keep the marker so a later sweep retries.
        if not swept_ok:
            continue
        try:
            _remove_request(staging_root, user_hash, req_id, dir_too=True)
        except OSError as exc:
            logger.warning("cc staging sweep: cleanup of %r failed (%s); keeping marker for retry",
                           req_id, type(exc).__name__)

    return result
