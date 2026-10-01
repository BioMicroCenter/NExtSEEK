"""One chat session as the user sees it, and as one downloadable zip.

``turn_rows`` is the turn list the chat UI renders
(``GET /assistant/sessions/{sid}/?include=turns``), kept together with the
records each turn came from, so that anything exported from a session numbers
and filters its turns exactly as the screen does.

``plan_export`` and ``stream_export`` build the zip behind
``GET /assistant/sessions/{sid}/download/``: the transcript, then every turn's
files, then ``manifest.json`` naming every file shipped and every file left out.
A turn's files live under one of two roots, each with its own guard, and one
session can hold both:

* NS files (the bundle's ``files`` manifest, its ``raw_result_path`` payload and
  the legacy ``report_saved_files``) are served only from inside the outputs
  roots, through ``_safe_artifact_path`` in ``NessieAI/ns/artifacts.py``.
* Container-CC files are published to ``<CC tree>/output/artifacts/<run id>/``
  (``_publish_artifacts`` in ``NessieAI/cc/cc_engine.py``) and served through
  ``resolve_artifact_path``, the guard of the per-turn CC download. The tree is
  the project folder the session's CC turns ran in, which the turn saves as
  ``extra_state['cc_project_dirname']``, plus the owner's username; no SEEK call.

Every path is checked while planning, before the response starts, so a refusal
is a manifest line and never a half-sent zip. The zip itself is written through
a sink that hands each compressed piece to the response as it is produced, so no
file is ever read whole and no zip is ever held in memory.
"""
from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterator

from NessieAI.ns.artifacts import _safe_artifact_path
from NessieAI.ns.debug_projection import bundle_debug_entries
from nextseek_api.assistant.excel_export import (
    _INTERNAL_FILE_KINDS,
    build_artifacts,
    build_tables_from_bundle,
    generate_table_xlsx,
)
from nextseek_api.assistant.models_api import Turn
from nextseek_api.assistant.session_debug import _file_entries

logger = logging.getLogger(__name__)

#: Bytes read from a file per step. Each step's compressed output is handed on
#: before the next is read, so this also bounds what one step holds in memory.
CHUNK_BYTES = 64 * 1024

#: The engine writes ``artifacts.zip`` beside a turn's files when it published
#: more than one; shipping it as well would put every file in the zip twice.
CC_TURN_ZIP = "artifacts.zip"

#: A CC run id is a UUID the server wrote. Starting with an alphanumeric rules
#: out ".", ".." and the empty string, any of which would name the whole tree.
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]")

# Why a file named by the session is not in the zip. Recorded in manifest.json.
OUTSIDE_ROOT = "outside_artifact_root"
MISSING = "missing_on_disk"
CC_UNRESOLVED = "cc_tree_unresolved"
GENERATION_FAILED = "generation_failed"
READ_FAILED = "read_failed"


@dataclass(frozen=True)
class TurnRow:
    """One turn as the chat UI shows it, with the raw records behind it."""

    payload: dict[str, Any]          # ``Turn(...).model_dump(mode="json")``
    entry: dict[str, Any] | None     # the chat_log entry; None for a legacy bundle-only turn
    bundle: dict[str, Any] | None    # the NS bundle the turn wrote, if any


def turn_rows(session) -> list[TurnRow]:
    """The turns of ``session`` in the order and with the filter the chat UI uses.

    Walks ``chat_log`` when there is one and ``results_history`` otherwise.
    Entries with no reply and no bundle are hidden (PD-6: unrelated and error
    turns), while legacy preview-only turns keep rendering.
    """
    history = session.results_history or []
    chat_log = (session.extra_state or {}).get("chat_log") or []
    bundles_by_id = {b.get("id"): b for b in history if isinstance(b, dict)}
    rows: list[TurnRow] = []
    if chat_log:
        for entry in chat_log:
            if not (entry or {}).get("user_query"):
                continue
            bid = entry.get("bundle_id")
            bundle = bundles_by_id.get(bid) if bid is not None else None
            if not (entry.get("assistant_reply")
                    or entry.get("assistant_reply_preview")
                    or bundle):
                # PD-6: hide ONLY true non-answer entries (unrelated/error,
                # F §12.3). Legacy preview-only turns keep rendering.
                continue
            # Prefer the full reply stored directly on the chat_log entry
            # (wizard turns don't produce bundles, so this is the only
            # full-text source for them). Fall back to the bundle's
            # terminal_reply for legacy entries written before
            # assistant_reply existed, then to the 280-char preview.
            reply = (
                entry.get("assistant_reply")
                or (bundle.get("terminal_reply") or bundle.get("reply") if bundle else None)
                or entry.get("assistant_reply_preview", "")
            ) or ""
            artifacts = entry.get("artifacts") or (build_artifacts(bundle) if bundle else None)
            payload = Turn(
                bundle_id=bid if bid is not None else 0,
                turn_id=entry.get("turn_id") if isinstance(entry.get("turn_id"), int) else None,
                user_query=entry.get("user_query", ""),
                reply=reply,
                mode=entry.get("mode", ""),
                ts=entry.get("ts"),
                artifacts=artifacts or None,
                cc_traces=entry.get("cc_traces"),
                debug_entries=bundle_debug_entries(bundle) or None,
            ).model_dump(mode="json")
            rows.append(TurnRow(payload=payload, entry=entry, bundle=bundle))
    else:
        for b in history:
            if not (b or {}).get("user_query"):
                continue
            payload = Turn(
                bundle_id=b.get("id", 0),
                user_query=b.get("user_query", ""),
                reply=b.get("terminal_reply") or b.get("reply") or "",
                mode=b.get("mode", ""),
                ts=b.get("ts"),
                artifacts=(build_artifacts(b) or None),
                debug_entries=bundle_debug_entries(b) or None,
            ).model_dump(mode="json")
            rows.append(TurnRow(payload=payload, entry=None, bundle=b))
    return rows


# ----------------------------------------------------------------------
# The CC tree
# ----------------------------------------------------------------------

def cc_artifacts_root(session) -> Path:
    """``<CC tree>/output/artifacts`` of the session's owner, as the turns used it.

    The CC turn path saves the project folder it ran in as
    ``extra_state['cc_project_dirname']`` on the first CC turn, and refuses a later
    turn whose resolved project differs, so that folder plus the owner's username
    names every CC turn's tree. Asking SEEK instead would find the owner's project
    as it is now: after a rename, or a change of first project, files still on disk
    would be reported missing. Nothing here depends on who is asking.

    Raises ``ValueError`` when no folder was saved, or when the saved one or the
    username is not a plain path segment (``build_user_dirs`` checks both);
    ``plan_export`` turns that into a manifest line.
    """
    from NessieAI.cc.cc_config import CCPaths
    from NessieAI.cc.cc_provision import build_user_dirs

    dirname = (session.extra_state or {}).get("cc_project_dirname")
    if not dirname:
        raise ValueError("the session saved no CC project folder")
    dirs = build_user_dirs(CCPaths.from_env(), dirname, session.user.username)
    return Path(dirs.output_mnt) / "artifacts"


# ----------------------------------------------------------------------
# Planning: every path checked before a byte is sent
# ----------------------------------------------------------------------

@dataclass
class Member:
    """One file of the zip: read from ``path`` in chunks, or made by ``produce``."""

    arcname: str
    folder: str
    source: str                                   # "ns" | "cc" | "generated" | "transcript"
    key: str | None = None
    path: Path | None = None
    produce: Callable[[], bytes] | None = None


@dataclass
class ExportPlan:
    session_id: str
    filename: str
    members: list[Member] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)


class _Planner:
    def __init__(self, session_id: str):
        self.plan = ExportPlan(session_id=session_id,
                               filename=f"nessie-chat-{session_id[:8]}.zip")
        self._arcnames: set[str] = set()
        self._seen: set[Path] = set()
        self._folders: set[str] = set()

    def skip(self, folder: str, source: str, key: Any, reason: str) -> None:
        self.plan.skipped.append({"folder": folder, "source": source,
                                  "key": None if key is None else str(key),
                                  "reason": reason})

    def _unique(self, arcname: str) -> str:
        if arcname not in self._arcnames:
            self._arcnames.add(arcname)
            return arcname
        stem, dot, ext = arcname.rpartition(".")
        if not dot or "/" in ext:
            stem, ext = arcname, ""
        n = 2
        while True:
            candidate = f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}"
            if candidate not in self._arcnames:
                self._arcnames.add(candidate)
                return candidate
            n += 1

    def add_file(self, folder: str, source: str, key: Any, path: Path, name: str) -> None:
        if path in self._seen:
            return
        self._seen.add(path)
        self._folders.add(folder)
        self.plan.members.append(Member(
            arcname=self._unique(f"{folder}/{name}" if folder else name),
            folder=folder, source=source, key=None if key is None else str(key), path=path,
        ))

    def add_bytes(self, folder: str, source: str, key: Any, name: str,
                  produce: Callable[[], bytes]) -> None:
        self._folders.add(folder)
        self.plan.members.append(Member(
            arcname=self._unique(f"{folder}/{name}" if folder else name),
            folder=folder, source=source, key=None if key is None else str(key),
            produce=produce,
        ))

    def folder_has_files(self, folder: str) -> bool:
        return folder in self._folders


def _display_name(name: Any, fallback: str) -> str:
    """The last path component of a stored display name; never a path of its own."""
    name = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return fallback if name in ("", ".", "..") else name


def _ns_candidates(bundle: dict[str, Any]) -> list[tuple[Any, Any, Any]]:
    """``(key, stored path, display name)`` for every file an NS bundle names.

    ``_file_entries`` covers the payload pointer and the ``files`` manifest; the
    kinds the chat UI hides (``_INTERNAL_FILE_KINDS``) are hidden here too.
    ``report_saved_files`` adds bundles that predate the manifest and the
    granular-op bundles, which carry nothing else, taking EVERY path of a
    multi-file key rather than the first one the per-key download serves.
    """
    out = [(row["key"], row["path"], row["filename"])
           for row in _file_entries([bundle])
           if row.get("kind") not in _INTERNAL_FILE_KINDS]
    for key, value in (bundle.get("report_saved_files") or {}).items():
        paths = [value] if isinstance(value, str) else (
            list(value) if isinstance(value, (list, tuple)) else [])
        out.extend((key, p, None) for p in paths if isinstance(p, str) and p and "://" not in p)
    return out


def _plan_ns_bundle(planner: _Planner, folder: str, bundle: dict[str, Any]) -> None:
    for key, stored, display in _ns_candidates(bundle):
        path = _safe_artifact_path(stored)
        if path is None:
            planner.skip(folder, "ns", key, OUTSIDE_ROOT)
            continue
        if not path.is_file():
            planner.skip(folder, "ns", key, MISSING)
            continue
        planner.add_file(folder, "ns", key, path, _display_name(display, path.name))

    try:
        tables = build_tables_from_bundle(bundle)
    except Exception:  # noqa: BLE001 - one odd bundle must not fail the download
        logger.exception("session export: tables of bundle %r", bundle.get("id"))
        planner.skip(folder, "generated", "all_tables", GENERATION_FAILED)
        return
    if tables:
        planner.add_bytes(folder, "generated", "all_tables",
                          f"report_{_segment(bundle.get('id'))}.xlsx",
                          partial(generate_table_xlsx, tables))


def _plan_cc_turn(planner: _Planner, folder: str, run_id: Any, root: Path) -> None:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        planner.skip(folder, "cc", run_id, OUTSIDE_ROOT)
        return
    from nextseek_api.cc_assistant.cc_endpoint_guards import resolve_artifact_path

    try:
        run_dir = resolve_artifact_path(str(root), run_id)
    except ValueError:
        planner.skip(folder, "cc", run_id, OUTSIDE_ROOT)
        return
    if not run_dir.is_dir():
        planner.skip(folder, "cc", run_id, MISSING)
        return

    found: list[tuple[str, Path]] = []
    for candidate in sorted(run_dir.rglob("*")):
        # The engine never publishes a link, so any link here is refused outright;
        # resolve_artifact_path then catches a linked DIRECTORY that leaves the tree.
        if candidate.is_symlink() or not candidate.is_file():
            continue
        rel = candidate.relative_to(run_dir).as_posix()
        try:
            target = resolve_artifact_path(str(root), f"{run_id}/{rel}")
        except ValueError:
            planner.skip(folder, "cc", f"{run_id}/{rel}", OUTSIDE_ROOT)
            continue
        found.append((rel, target))
    if len(found) > 1:
        found = [(rel, p) for rel, p in found if rel != CC_TURN_ZIP]
    for rel, target in found:
        planner.add_file(folder, "cc", f"{run_id}/{rel}", target, rel)


def _segment(value: Any) -> str:
    return _UNSAFE_SEGMENT.sub("_", str(value)) or "_"


def plan_export(session) -> ExportPlan:
    """Decide every member of the zip and every refusal, before streaming starts.

    The owner's CC artifacts root (``cc_artifacts_root``) is located at most once,
    and only when a turn published CC files. It comes from the session alone, so
    an operator downloading someone else's chat gets the same files the owner does.
    """
    sid = str(session.session_id)
    planner = _Planner(sid)
    rows = turn_rows(session)
    history = [b for b in (session.results_history or []) if isinstance(b, dict)]

    turn_folders: list[str | None] = []
    cc_root: Path | None = None
    cc_resolved = False
    claimed: set[int] = set()

    for n, row in enumerate(rows, start=1):
        folder = f"turn-{n:02d}"
        if row.bundle is not None:
            claimed.add(id(row.bundle))
            _plan_ns_bundle(planner, folder, row.bundle)
        entry = row.entry or {}
        if entry.get("cc_run_id") and entry.get("artifacts"):
            if not cc_resolved:
                cc_resolved = True
                try:
                    cc_root = cc_artifacts_root(session)
                except ValueError:
                    logger.warning("session export: CC tree of %s unresolved", sid,
                                   exc_info=True)
            if cc_root is None:
                planner.skip(folder, "cc", entry.get("cc_run_id"), CC_UNRESOLVED)
            else:
                _plan_cc_turn(planner, folder, entry.get("cc_run_id"), cc_root)
        turn_folders.append(folder if planner.folder_has_files(folder) else None)

    # Bundles no displayed turn points at: granular-op bundles, and turns whose
    # chat_log entry was capped away. Their files are the user's too.
    other_folders: list[str] = []
    for bundle in history:
        if id(bundle) in claimed:
            continue
        folder = f"bundle-{_segment(bundle.get('id'))}"
        _plan_ns_bundle(planner, folder, bundle)
        if planner.folder_has_files(folder) and folder not in other_folders:
            other_folders.append(folder)

    transcript = {
        "session_id": sid,
        "title": session.title or "New chat",
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "turns": [{**row.payload, "folder": folder}
                  for row, folder in zip(rows, turn_folders)],
        "other_folders": other_folders,
    }
    files = planner.plan.members
    planner.plan.members = []
    planner.add_bytes("", "transcript", None, "transcript.md",
                      partial(_transcript_markdown, transcript))
    planner.add_bytes("", "transcript", None, "transcript.json",
                      partial(_json_bytes, transcript))
    planner.plan.members.extend(files)
    return planner.plan


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False).encode("utf-8")


def _transcript_markdown(transcript: dict[str, Any]) -> bytes:
    lines = [
        f"# {transcript['title']}",
        "",
        f"Nessie chat `{transcript['session_id']}`, started {transcript['created_at']}.",
        "",
        "Each turn's files are in the folder named under it. `manifest.json` lists "
        "every file in this zip, and every file that could not be included with the "
        "reason.",
    ]
    for n, turn in enumerate(transcript["turns"], start=1):
        lines += ["", "---", "", f"## Turn {n}", ""]
        meta = [part for part in (turn.get("mode"), turn.get("ts")) if part]
        if meta:
            lines += [f"*{', '.join(str(p) for p in meta)}*", ""]
        lines += ["**Question**", "", turn.get("user_query") or "", "",
                  "**Answer**", "", turn.get("reply") or ""]
        if turn.get("folder"):
            lines += ["", f"Files: `{turn['folder']}/`"]
    if transcript["other_folders"]:
        lines += ["", "---", "", "## Files from outside a turn", "",
                  "Operations that ran without a chat turn of their own:", ""]
        lines += [f"- `{folder}/`" for folder in transcript["other_folders"]]
    return ("\n".join(lines) + "\n").encode("utf-8")


# ----------------------------------------------------------------------
# Streaming
# ----------------------------------------------------------------------

class _Sink:
    """The write-only file ``zipfile`` writes into; ``take`` empties it.

    It has no ``tell`` and no ``seek``, so ``ZipFile`` writes every member with a
    data descriptor and never goes back to patch a header. That is what lets the
    bytes leave as soon as they are written.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def write(self, data) -> int:
        self._buf += data
        return len(data)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out


def _now_zip() -> tuple[int, int, int, int, int, int]:
    return time.localtime()[:6]


def stream_export(plan: ExportPlan) -> Iterator[bytes]:
    """The zip of ``plan``, one compressed piece at a time; manifest.json last."""
    sink = _Sink()
    written: list[dict[str, Any]] = []
    skipped = list(plan.skipped)
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for member in plan.members:
            if member.path is not None:
                # A file removed since planning is a manifest line, not a broken zip.
                try:
                    info = zipfile.ZipInfo.from_file(member.path, member.arcname,
                                                     strict_timestamps=False)
                    fh = member.path.open("rb")
                except OSError:
                    skipped.append({"folder": member.folder, "source": member.source,
                                    "key": member.key, "reason": READ_FAILED})
                    continue
                info.compress_type = zipfile.ZIP_DEFLATED
                size = 0
                with fh:
                    with zf.open(info, "w") as dest:
                        while chunk := fh.read(CHUNK_BYTES):
                            dest.write(chunk)
                            size += len(chunk)
                            piece = sink.take()
                            if piece:
                                yield piece
            else:
                try:
                    data = member.produce()
                except Exception:  # noqa: BLE001 - one bad workbook must not end the zip
                    logger.exception("session export: %s", member.arcname)
                    skipped.append({"folder": member.folder, "source": member.source,
                                    "key": member.key, "reason": GENERATION_FAILED})
                    continue
                info = zipfile.ZipInfo(member.arcname, date_time=_now_zip())
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, data)
                size = len(data)
            written.append({"name": member.arcname, "folder": member.folder,
                            "source": member.source, "key": member.key, "bytes": size})
            piece = sink.take()
            if piece:
                yield piece

        manifest = {"session_id": plan.session_id, "files": written, "skipped": skipped}
        info = zipfile.ZipInfo("manifest.json", date_time=_now_zip())
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        zf.writestr(info, _json_bytes(manifest))
    tail = sink.take()
    if tail:
        yield tail


async def astream_export(plan: ExportPlan):
    """``stream_export`` for an ASGI server, each piece made on a worker thread.

    Handed a synchronous iterator, Django's ASGI response reads the whole of it
    into a list before sending a byte, which would hold the entire zip in memory.
    """
    from asgiref.sync import sync_to_async

    pieces = stream_export(plan)
    step = sync_to_async(next, thread_sensitive=False)
    done = object()
    try:
        while True:
            piece = await step(pieces, done)
            if piece is done:
                break
            yield piece
    finally:
        await sync_to_async(pieces.close, thread_sensitive=False)()
