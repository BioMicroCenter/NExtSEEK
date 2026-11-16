"""Unpack a results bundle uploaded in the chat into what run-harvest's Luria stager produces.

A bundle is one ``.tar.gz``: the run's small files, the input samplesheet, and
``nessie_files.tsv``, which lists the big files the bundle leaves out (``path<TAB>bytes``)
and names where they live (``#origin<TAB><absolute path or URL>``). ``stage_bundle``
writes the allowlisted members (``harvest.staged_globs()``) into a fresh folder under the
stager's own caps, reads the newest execution report's verdict line without unpacking the
report, and returns the stager's ``(skipped, inventory)`` pair, so ``harvest_local`` reads a
bundle exactly as it reads a staged Luria folder.

Read twice, both times as a stream. The first pass refuses an unsafe or oversized bundle
before anything is written: an absolute or escaping member name, a duplicate name, more
member bytes declared than ``MAX_UNPACKED_BYTES``. The second pass writes. Links, devices and
FIFOs are skipped with a reason, as the stager skips them. Nothing is hashed: a bundle's
bytes are a copy, not the files at the origin, so no checksum is measured from them.

Pure: no Django, no network, no path outside ``staged_dir``.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tarfile
import zlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import BinaryIO

from NessieAI.ns.reingest import harvest

LISTING_NAME = "nessie_files.tsv"
MAX_BUNDLE_BYTES = int(os.environ.get("NEXTSEEK_BUNDLE_MAX_BYTES", str(200 * 1024 * 1024)))
MAX_UNPACKED_BYTES = int(os.environ.get("NEXTSEEK_BUNDLE_MAX_UNPACKED_BYTES", str(1024 * 1024 * 1024)))
MAX_MEMBERS = 20_000
MAX_LISTING_BYTES = 2 * 1024 * 1024
_VERDICT_SCAN_BYTES = 64 * 1024 * 1024
_VERDICT_RE = re.compile(rb"Workflow execution completed (?:un)?successfully[^<]*")
_REPORT_GLOB = "pipeline_info/execution_report*.html"
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://\S+$")
_CHUNK = 1024 * 1024


class BundleRefused(Exception):
    """The bundle can't be harvested. ``error_type`` is what the agent is shown."""

    def __init__(self, message: str, error_type: str):
        super().__init__(message)
        self.error_type = error_type


@dataclass
class StagedBundle:
    origin: str
    sha256: str
    skipped: list[dict] = field(default_factory=list)
    inventory: list[dict] = field(default_factory=list)


def _clean(name: str) -> str:
    """A member or listing path as a relative POSIX path, or BundleRefused if it is absolute or escapes."""
    if name.startswith("/") or "\x00" in name:
        raise BundleRefused(f"bundle member {name!r} is an absolute path", "bundle_unsafe_member")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise BundleRefused(f"bundle member {name!r} leaves the bundle", "bundle_unsafe_member")
    return "/".join(parts)


def _matches(rel: str, patterns) -> bool:
    path = PurePosixPath(rel)
    return any(path.full_match(p) for p in patterns)


def _measure(fileobj: BinaryIO) -> str:
    """sha256 of the whole bundle, refusing it past MAX_BUNDLE_BYTES; leaves fileobj at 0."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    for chunk in iter(lambda: fileobj.read(_CHUNK), b""):
        if not head:
            head = chunk[:2]
        size += len(chunk)
        if size > MAX_BUNDLE_BYTES:
            raise BundleRefused(f"the bundle is over {MAX_BUNDLE_BYTES} bytes", "bundle_too_large")
        digest.update(chunk)
    if head != b"\x1f\x8b":
        raise BundleRefused("the bundle is not a .tar.gz", "bundle_not_a_tar_gz")
    fileobj.seek(0)
    return digest.hexdigest()


def _members(fileobj: BinaryIO):
    """Stream the tar's members, turning a corrupt archive into BundleRefused."""
    fileobj.seek(0)
    try:
        with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
            for member in tar:
                yield tar, member
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile, OSError) as exc:
        raise BundleRefused(f"the bundle is not a readable .tar.gz ({exc})", "bundle_not_a_tar_gz") from exc


def _survey(fileobj: BinaryIO) -> str:
    """First pass: refuse an unsafe or oversized bundle; return the one top folder to strip, if any."""
    names: set[str] = set()
    declared = 0
    count = 0
    for _tar, member in _members(fileobj):
        count += 1
        if count > MAX_MEMBERS:
            raise BundleRefused(f"the bundle has more than {MAX_MEMBERS} members", "bundle_unpacks_too_large")
        rel = _clean(member.name)
        if member.isdir() or not rel:
            continue
        if rel in names:
            raise BundleRefused(f"the bundle holds {rel!r} twice", "bundle_unsafe_member")
        names.add(rel)
        if member.isreg():
            declared += member.size
            if declared > MAX_UNPACKED_BYTES:
                raise BundleRefused(f"the bundle unpacks to more than {MAX_UNPACKED_BYTES} bytes",
                                    "bundle_unpacks_too_large")
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and all("/" in n for n in names):
        return tops.pop() + "/"
    return ""


def _read(tar, member, limit: int) -> bytes:
    fh = tar.extractfile(member)
    return fh.read(limit) if fh is not None else b""


def _verdict(tar, member) -> str | None:
    fh = tar.extractfile(member)
    if fh is None:
        return None
    scanned = 0
    for line in fh:
        match = _VERDICT_RE.search(line)
        if match:
            return " ".join(match.group(0).decode("utf-8", "replace").split())
        scanned += len(line)
        if scanned > _VERDICT_SCAN_BYTES:
            return None
    return None


def _parse_listing(data: bytes) -> tuple[str, list[tuple[str, int]]]:
    origin = ""
    entries: list[tuple[str, int]] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleRefused(f"{LISTING_NAME} is not UTF-8 text", "bundle_listing_malformed") from exc
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            key, _, value = line[1:].partition("\t")
            if key == "origin":
                origin = value.strip()
                if not origin.isprintable() or not (origin.startswith("/") or _URL_RE.match(origin)) \
                        or len(origin) > 1000:
                    raise BundleRefused(f"{LISTING_NAME}'s origin must be an absolute path or a URL",
                                        "bundle_listing_malformed")
            continue
        path, sep, size = line.partition("\t")
        if not sep or not size.strip().isdigit():
            raise BundleRefused(f"{LISTING_NAME} line {line!r} is not 'path<TAB>bytes'",
                                "bundle_listing_malformed")
        try:
            rel = _clean(path)
        except BundleRefused as exc:
            raise BundleRefused(str(exc), "bundle_listing_malformed") from exc
        entries.append((rel, int(size)))
    return origin, entries


def stage_bundle(fileobj: BinaryIO, staged_dir: str) -> StagedBundle:
    """Unpack ``fileobj`` (a seekable .tar.gz) into the empty folder ``staged_dir``.

    Raises BundleRefused for a bundle that can't be read safely. Returns the bundle's sha256,
    the origin its listing names, and the stager's ``skipped`` and ``inventory`` lists.
    """
    sha256 = _measure(fileobj)
    prefix = _survey(fileobj)
    stage_patterns = harvest.staged_globs()
    inventory_patterns = harvest.inventory_globs()
    out = StagedBundle(origin="", sha256=sha256)
    listed: dict[str, int] = {}
    reports: dict[str, str | None] = {}
    listing = None
    total = files = 0

    for tar, member in _members(fileobj):
        rel = _clean(member.name)
        if prefix and rel.startswith(prefix):
            rel = rel[len(prefix):]
        if member.isdir() or not rel:
            continue
        if not member.isreg():
            out.skipped.append({"path": rel, "reason": "not a regular file (a link or a device); a bundle "
                                                      "carries regular files only"})
            continue
        if rel == LISTING_NAME:
            if member.size > MAX_LISTING_BYTES:
                raise BundleRefused(f"{LISTING_NAME} is over {MAX_LISTING_BYTES} bytes", "bundle_listing_malformed")
            listing = _read(tar, member, MAX_LISTING_BYTES)
            continue
        if rel == harvest.RUN_VERDICT_NAME:
            out.skipped.append({"path": rel, "reason": "a name reingest reserves for the run's verdict"})
            continue
        if PurePosixPath(rel).full_match(_REPORT_GLOB):
            reports[rel] = _verdict(tar, member)  # never unpacked: it can be larger than any cap
            continue
        if _matches(rel, inventory_patterns):
            listed[rel] = member.size
        if not _matches(rel, stage_patterns):
            continue
        size = member.size
        if size > harvest.MAX_FILE_BYTES:
            out.skipped.append({"path": rel, "reason": f"exceeds max file bytes ({size} > {harvest.MAX_FILE_BYTES})"})
            continue
        if total + size > harvest.MAX_TOTAL_BYTES:
            out.skipped.append({"path": rel, "reason": f"exceeds total byte cap ({total} + {size} > {harvest.MAX_TOTAL_BYTES})"})
            continue
        if files >= harvest.MAX_FILES:
            out.skipped.append({"path": rel, "reason": f"exceeds max file count ({harvest.MAX_FILES})"})
            continue
        dest = os.path.join(staged_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "xb") as fh:
            fh.write(_read(tar, member, size))
        total += size
        files += 1

    if listing is None:
        raise BundleRefused(f"the bundle has no {LISTING_NAME} naming where its files live",
                            "bundle_has_no_origin")
    origin, entries = _parse_listing(listing)
    if not origin:
        raise BundleRefused(f"{LISTING_NAME} has no '#origin' line naming where the files live",
                            "bundle_has_no_origin")
    out.origin = origin
    for rel, size in entries:
        if rel not in listed and _matches(rel, inventory_patterns):
            listed[rel] = size
    for rel in sorted(listed):
        if len(out.inventory) >= harvest.MAX_INVENTORY_FILES:
            out.skipped.append({"path": rel, "reason": f"exceeds max inventory file count ({harvest.MAX_INVENTORY_FILES})"})
            continue
        out.inventory.append({"path": rel, "bytes": listed[rel]})

    if reports:
        newest = max(reports)
        with open(os.path.join(staged_dir, harvest.RUN_VERDICT_NAME), "x") as fh:
            json.dump({"path": newest, "verdict": reports[newest]}, fh)
    return out
