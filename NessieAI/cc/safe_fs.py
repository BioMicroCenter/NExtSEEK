"""File operations Django runs in folders a Container-CC agent or the sidecar can write.

The chat's ``cc-state`` (the agent's ``~/.claude``), the turn's ``scratch`` and the sidecar's ``_staging`` are
written by code Django does not trust, and Django runs as root. A path there is never trusted: every folder step
is opened ``O_NOFOLLOW | O_DIRECTORY`` relative to the one before it, a file is read only once ``fstat`` says it
is a regular file, a file is created ``O_EXCL | O_NOFOLLOW`` (temporary names included), a mode is set with
``fchmod`` on the open file, and a rename happens inside one opened folder. A link, a FIFO, a socket, a folder
where a file should be, or a path that leaves ``root`` raises ``UnsafePath``.

``root`` is a trusted root: a path Django built itself from its own settings, never from anything read in an
agent folder. Its components other than the last are trusted, so a root is either the backing root of a
read-write mount (the chat's ``cc-state/<session>``, the turn's ``scratch/<run_id>``, the sidecar's
``_staging``), which the agent or the sidecar can write into but cannot rename or replace because no parent of
it is mounted, or a Django-owned folder above one. Its last component is opened ``O_NOFOLLOW`` like every step
below it. Every component an agent or the sidecar controls goes in ``rel`` (or ``rel_parts``), never in
``root``. The code that resolves the mount roots registers them (``register_agent_root``: ``build_user_dirs``,
``staging_root_for``, the scrub command's session listing); a root that is not registered and lies below a
registered one raises ``UnsafePath``, so a caller cannot hand a folder inside an agent's tree over as a root.
A caller that needs an operation this module lacks (a delete, a listing of folders) opens the folder with
``open_dir`` and makes a no-follow call on its fd.

Standard library only, so the import-light modules (``cc_session``, ``cc_memory_io``) can use it. Built from
``cc_staging._deliver_file_safely``, which uses it too.
"""
from __future__ import annotations

import errno
import os
import secrets
import shutil
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

__all__ = ["UnsafePath", "agent_root_of", "copy_out", "iter_files", "open_dir", "read_file",
           "register_agent_root", "write_file_atomic"]

_DIR_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
# O_NONBLOCK: opening a FIFO for reading would otherwise wait for a writer.
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_OUT_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
# Folders deeper than this below the root are not listed: bounded work on a tree someone else built.
_MAX_DEPTH = 64
_TEMP_ATTEMPTS = 8
_COPY_CHUNK = 1024 * 1024
_REFUSED_OPEN = (errno.ELOOP, errno.ENOTDIR)

# The backing roots of the read-write mounts of agents and the sidecar, registered by the code that resolves
# them. Everything strictly below one is agent-writable, so a root there is refused. Insertion-ordered and
# bounded: the oldest entry goes first (a finished turn's scratch is not walked again), and registering a root
# again moves it to the end, so the chat folders a worker keeps resolving stay registered.
_AGENT_ROOTS: dict[str, None] = {}
_MAX_AGENT_ROOTS = 4096
_AGENT_ROOTS_LOCK = threading.Lock()


class UnsafePath(OSError):
    """A link, a non-regular file, a path that leaves the root, or a root that is not trusted."""


def _root_key(root) -> str:
    """``root`` as the absolute path Django built, checked by its text alone (no link is resolved).

    No trailing slash (it makes the kernel resolve a final link even under ``O_NOFOLLOW``), and no empty,
    ``.`` or ``..`` step (the kernel would resolve ``..`` through a link, the text check would not).
    """
    text = os.fspath(root)
    if not isinstance(text, str) or "\x00" in text or not text.startswith("/"):
        raise UnsafePath(f"a root must be an absolute path: {text!r}")
    text = text.rstrip("/") or "/"
    if text != "/" and any(part in ("", ".", "..") for part in text[1:].split("/")):
        raise UnsafePath(f"a root must not hold an empty, '.' or '..' step: {text!r}")
    return text


def _registered_above(key: str) -> str | None:
    """The registered agent root strictly above ``key``, if any. Call with the lock held."""
    parent = key
    while parent != "/":
        parent = parent.rsplit("/", 1)[0] or "/"
        if parent in _AGENT_ROOTS:
            return parent
    return None


def register_agent_root(path: str | os.PathLike) -> Path:
    """Record ``path`` as the backing root of an agent or sidecar read-write mount; return it as a ``Path``.

    Called only by code that builds the path from Django's own settings (``build_user_dirs``,
    ``staging_root_for``, the scrub command's session listing). Raises ``UnsafePath`` for a path that is not
    absolute, holds an empty, ``.`` or ``..`` step, or lies strictly below a root already registered: a mount's
    root is never inside another agent-writable folder. Registering a root again is allowed.
    """
    key = _root_key(path)
    with _AGENT_ROOTS_LOCK:
        above = _registered_above(key)
        if above is not None:
            raise UnsafePath(f"{key!r} is inside the agent-writable folder {above!r}")
        _AGENT_ROOTS.pop(key, None)
        _AGENT_ROOTS[key] = None
        while len(_AGENT_ROOTS) > _MAX_AGENT_ROOTS:
            del _AGENT_ROOTS[next(iter(_AGENT_ROOTS))]
    return Path(key)


def agent_root_of(path: str | os.PathLike) -> Path | None:
    """The registered agent root at or above ``path`` (checked by its text), or None.

    Lets a reader that holds only a path Django recorded (a transcript path) start from the mount root this
    process registered. Raises ``UnsafePath`` for a path that is not absolute or holds an empty, ``.`` or
    ``..`` step.
    """
    key = _root_key(path)
    with _AGENT_ROOTS_LOCK:
        if key in _AGENT_ROOTS:
            return Path(key)
        above = _registered_above(key)
    return None if above is None else Path(above)


def _check_root(root) -> str:
    """``root``'s text when it is a trusted root, else ``UnsafePath``.

    A registered agent root, or a folder that is inside none (Django's own folders above the mounts, and a
    test's temporary folder), is trusted. A folder strictly inside a registered agent root is not: the caller
    must pass that agent root and put the rest in ``rel``.
    """
    key = _root_key(root)
    with _AGENT_ROOTS_LOCK:
        above = _registered_above(key)
    if above is not None:
        raise UnsafePath(f"{key!r} is not a trusted root: it is inside the agent-writable folder {above!r}; "
                         "pass that folder as the root and the rest as rel")
    return key


def _check_name(name) -> str:
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\x00" in name:
        raise UnsafePath(f"not a single folder or file name: {name!r}")
    return name


def _parts(rel) -> tuple[str, ...]:
    text = os.fspath(rel)
    if not isinstance(text, str) or not text or text.startswith("/"):
        raise UnsafePath(f"not a relative path: {text!r}")
    return tuple(_check_name(part) for part in text.split("/"))


def _open_step(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
    except OSError as exc:
        if exc.errno in _REFUSED_OPEN:
            raise UnsafePath(f"{name!r} is a link or not a folder") from exc
        raise
    try:
        os.mkdir(name, 0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass  # made in between; the open below still refuses a link
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in _REFUSED_OPEN:
            raise UnsafePath(f"{name!r} is a link or not a folder") from exc
        raise


def open_dir(root: Path, rel_parts: tuple[str, ...] = (), *, create: bool = False) -> int:
    """An fd of ``root/rel_parts``, every step opened ``O_NOFOLLOW | O_DIRECTORY``; the caller closes it.

    ``create`` makes missing steps below ``root`` (mode 0755), never ``root`` itself. ``root`` must be a trusted
    root (``_check_root``): a folder inside a registered agent root raises ``UnsafePath`` before anything opens.
    """
    if isinstance(rel_parts, (str, bytes)):
        raise TypeError("rel_parts is a tuple of names, not one string")
    names = tuple(_check_name(part) for part in rel_parts)
    key = _check_root(root)
    try:
        fd = os.open(key, _DIR_FLAGS)
    except OSError as exc:
        if exc.errno in _REFUSED_OPEN:
            raise UnsafePath(f"{os.fspath(root)!r} is a link or not a folder") from exc
        raise
    try:
        for name in names:
            nxt = _open_step(fd, name, create=create)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_leaf(dir_fd: int, name: str) -> tuple[int, os.stat_result]:
    """A read fd, and its ``fstat``, for the regular file ``name`` in ``dir_fd``."""
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENXIO):
            raise UnsafePath(f"{name!r} is a link or not a regular file") from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafePath(f"{name!r} is not a regular file")
        return fd, st
    except BaseException:
        os.close(fd)
        raise


def _open_for_read(root, rel) -> tuple[int, os.stat_result]:
    parts = _parts(rel)
    dir_fd = open_dir(root, parts[:-1])
    try:
        return _open_leaf(dir_fd, parts[-1])
    finally:
        os.close(dir_fd)


def read_file(root: Path, rel: str | Path, *, max_bytes: int | None = None) -> bytes:
    """The bytes of the regular file ``root/rel``, read without following a link.

    Raises ``UnsafePath`` for a link or a non-regular file on the way, ``FileNotFoundError`` when it is
    missing, and ``OSError(EFBIG)`` (not ``UnsafePath``) when it holds more than ``max_bytes``.
    """
    fd, st = _open_for_read(root, rel)
    with os.fdopen(fd, "rb") as fh:
        if max_bytes is None:
            return fh.read()
        if st.st_size > max_bytes:
            raise OSError(errno.EFBIG, f"larger than {max_bytes} bytes", os.fspath(rel))
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise OSError(errno.EFBIG, f"larger than {max_bytes} bytes", os.fspath(rel))
    return data


def _create_temp(dir_fd: int) -> tuple[str, int]:
    for _ in range(_TEMP_ATTEMPTS):
        name = f".safe_fs.{secrets.token_hex(8)}.tmp"
        try:
            return name, os.open(name, _CREATE_FLAGS, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            continue
    raise UnsafePath("no free temporary name")


def write_file_atomic(root: Path, rel: str | Path, data: bytes, *, mode: int = 0o644) -> None:
    """Replace ``root/rel`` with ``data`` in one rename, never writing through a link.

    The temporary file is created ``O_EXCL | O_NOFOLLOW`` under a random name in the same opened folder, its
    mode is set with ``fchmod`` and it is synced before the rename. A link at ``rel`` is replaced, not
    followed; a folder there raises ``UnsafePath``. Missing folders on the way raise ``FileNotFoundError``.
    """
    parts = _parts(rel)
    dir_fd = open_dir(root, parts[:-1])
    try:
        tmp, fd = _create_temp(dir_fd)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fchmod(fh.fileno(), mode)
                os.fsync(fh.fileno())
            try:
                os.replace(tmp, parts[-1], src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            except (IsADirectoryError, NotADirectoryError) as exc:
                raise UnsafePath(f"{parts[-1]!r} is a folder") from exc
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except OSError:
                pass
            raise
    finally:
        os.close(dir_fd)


def iter_files(root: Path, rel_parts: tuple[str, ...] = (), *,
               suffix: str | None = None) -> Iterator[tuple[str, os.stat_result]]:
    """``(path relative to root, lstat)`` of every regular file under ``root/rel_parts``: a folder's files in
    name order, then its folders in name order.

    ``rel_parts`` are walked like ``open_dir``'s (each ``O_NOFOLLOW``), and each yielded path starts with them,
    so it can be handed straight back to ``read_file(root, rel)``. Folders are entered only when they are real
    folders, each opened ``O_NOFOLLOW`` from its parent; links, FIFOs, sockets and devices are skipped, and so
    is a folder that cannot be opened on the way. A generator: a start folder that is missing
    (``FileNotFoundError``), or a link or not a folder or under an untrusted root (``UnsafePath``), raises at
    the first ``next()``.
    """
    start_fd = open_dir(root, rel_parts)
    prefix = "".join(f"{part}/" for part in rel_parts)
    try:
        yield from _walk(start_fd, prefix, 0, suffix)
    finally:
        os.close(start_fd)


def _walk(dir_fd: int, prefix: str, depth: int, suffix: str | None) -> Iterator[tuple[str, os.stat_result]]:
    with os.scandir(dir_fd) as it:
        entries = sorted(it, key=lambda entry: entry.name)
    subdirs: list[str] = []
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                subdirs.append(entry.name)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if suffix is None or entry.name.endswith(suffix):
            yield prefix + entry.name, st
    if depth >= _MAX_DEPTH:
        return
    for name in subdirs:
        try:
            child = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
        except OSError:
            continue  # became a link, or vanished, after the listing
        try:
            yield from _walk(child, f"{prefix}{name}/", depth + 1, suffix)
        finally:
            os.close(child)


def copy_out(root: Path, rel: str | Path, dest: Path) -> None:
    """Copy the regular file ``root/rel``, read without following a link, to ``dest``, a path Django owns.

    Streams the bytes, makes ``dest``'s folders, and keeps the source's access and modification times (as
    ``shutil.copy2`` did). The agent's mode bits are not copied: the copy is created 0644 (umask applied).
    """
    src_fd, st = _open_for_read(root, rel)
    with os.fdopen(src_fd, "rb") as src:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out_fd = os.open(dest, _OUT_FLAGS, 0o644)
        with os.fdopen(out_fd, "wb") as out:
            shutil.copyfileobj(src, out, _COPY_CHUNK)
            out.flush()
            os.utime(out.fileno(), ns=(st.st_atime_ns, st.st_mtime_ns))
