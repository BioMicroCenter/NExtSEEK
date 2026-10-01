"""``manage.py scrub_stored_sample_properties``: remove two derived sample properties from stored graph results.

A Sample node in the graph carries two properties the graph sync derives for its own use, ``parent_titles`` and
``parent_title_hashes`` (``chat_nextseek.graph_scope.HIDDEN_SAMPLE_PROPERTIES``). Graph results for non-admin users
leave them out, and results stored before every path did so can still hold them. This command removes the two keys
from what NExtSEEK stored, in every place a user can read a stored result back:

| Store | What is read |
|---|---|
| ``sessions`` | ``assistant_chat_session``: ``results_history``, ``last_debug``, ``extra_state`` |
| ``tasks`` | ``assistant_query_task``: ``result``, ``progress`` |
| ``cc_transcripts`` | ``assistant_cc_transcript.blob`` (zstd JSON lines; ``uncompressed_size`` follows it) |
| ``ns_files`` | the files a chat session names under the outputs roots: everything under its ``extra_state["log_dir"]``, and every path its bundles name; the download endpoints' guard (``NessieAI.ns.artifacts._safe_artifact_path``) decides what is inside |
| ``cc_previous_turns`` | ``<CC user root>/<project>/<user>/_memory/<session>/previous_turns/`` |
| ``cc_artifacts`` | ``<CC user root>/<project>/<user>/output/artifacts/`` |
| ``cc_transcript_files`` | ``<CC user root>/<project>/<user>/cc-state/<session>/`` and ``.../_memory/<session>/transcripts/`` |

Files ending in ``.json``, ``.jsonl``, ``.ndjson``, ``.ipynb``, ``.txt``, ``.csv``, ``.tsv`` or ``.zip`` (a zip's
members by the same rule) are cleaned. Every other file in those trees, an ``.xlsx`` included, is only checked: one
that names a property is counted as "left in text" and never rewritten.

What counts as one of the properties: a mapping key, an entry of a table's ``columns`` (its cells in every positional
sibling, ``rows`` or ``data``, go with it), or a CSV header (its column goes with it), equal to one of the two names
ignoring case, or ending in ``.`` plus one of them (the column name Neo4j gives ``RETURN s.parent_titles``). A string
that holds JSON is parsed, cleaned and written back in its own layout. In text that is not JSON as a whole (a
cut-off tool output), every complete ``"name": value`` pair is cut. A Claude ``thinking`` block is never edited,
because a resumed turn sends it back under its signature. Whatever still names a property after all that (a cut-off
value, a thinking block, the text of a query) is counted as "left in text" and left as it is.

Dry run by default: per store, records scanned and affected, keys found, and the first affected ids (never values),
for non-admin and admin owners both. ``--apply`` needs ``--backup-dir`` and works in two passes. The first writes
every affected record's original value to a JSON lines file there (mode 0600) and changes nothing. The second reads
that file back and changes each record: a targeted update of its one field under a row lock, or an atomic rewrite of
its one file that keeps its mode, owner and times. A record that changed between the passes, or cannot be written,
is left as it is and reported; run again. ``--restore <file>`` puts the originals back (dry run unless ``--apply``),
for records that still hold exactly what the scrub wrote.

A CC transcript file written in the last ``--min-transcript-age-minutes`` (15) is left for a later run, as a turn may
be appending to it. When a transcript under ``cc-state/<session>/projects`` is rewritten, the two records the CC side
keeps about its exact bytes move with it (``_carry_transcript_marks``): the clean watermark ``cc_sweep`` checks, and
the summary fingerprint in the session's ``extra_state``. Restore moves them back.

Run every store in one pass, sessions first (the default order): the next CC turn re-stages ``previous_turns`` from
the session row, so a trial on the files alone is undone by it. A chat turn that is running while the command writes
can put the keys back in its own session when it saves; the dry run afterwards shows it, and a second apply removes
them.

Only records owned by non-superusers are changed unless ``--include-admins`` is given; both are always counted.
A CC file's owner is the user its directory is named after. ``--store`` (repeatable) and ``--limit`` (per store,
counting records that would change) narrow a trial.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from chat_nextseek.graph_scope import HIDDEN_SAMPLE_PROPERTIES
from nextseek_api.assistant.models_db import CCSessionTranscript, ChatSession, QueryTask

NAMES = tuple(sorted(name.lower() for name in HIDDEN_SAMPLE_PROPERTIES))
BACKUP_PREFIX = "scrub_stored_sample_properties"
SAMPLE_IDS = 5
FILE_KINDS = {".json": "text", ".jsonl": "text", ".ndjson": "text", ".ipynb": "text", ".txt": "text",
              ".csv": "csv", ".tsv": "tsv", ".zip": "zip", ".xlsx": "xlsx"}
_REWRITTEN_KINDS = frozenset({"text", "csv", "tsv", "zip"})
#: Claude blocks the Messages API checks by signature when a resumed turn sends them back: never edited, only counted.
_SIGNED_BLOCKS = frozenset({"thinking", "redacted_thinking"})
#: A zip holding a larger member is counted and left whole, so a member is never inflated past this in memory.
ZIP_MEMBER_MAX_BYTES = 256 * 1024 * 1024


# =============================================================================================== the cleaners
#
# Every cleaner returns the very object it was given when it removed nothing, so "changed" is an identity test and
# a record that holds neither property is never re-serialized.


class Count:
    """What one clean found: properties removed (keys, headers, cells) and strings that still name one."""

    __slots__ = ("removed", "left")

    def __init__(self) -> None:
        self.removed = 0
        self.left = 0


def is_property_key(key: Any) -> bool:
    return isinstance(key, str) and key.strip().lower().rsplit(".", 1)[-1] in NAMES


def names_a_property(text: str | bytes) -> bool:
    low = text.lower()
    if isinstance(low, bytes):
        return any(name.encode() in low for name in NAMES)
    return any(name in low for name in NAMES)


def scrub_value(value: Any, count: Count) -> Any:
    """A decoded JSON value without the two properties, at any depth."""
    if isinstance(value, dict):
        return _scrub_mapping(value, count)
    if isinstance(value, list):
        items = [scrub_value(item, count) for item in value]
        return items if any(new is not old for new, old in zip(items, value)) else value
    if isinstance(value, str):
        return scrub_text(value, count)
    return value


def _table_positions(mapping: dict) -> tuple[frozenset[int], frozenset[str]]:
    """For a ``{"columns": [...], "rows": [[...], ...]}`` table: the property columns' positions, and every sibling
    key holding positional rows (``rows``, or ``data`` in a table written by someone else)."""
    columns = mapping.get("columns")
    if not isinstance(columns, list):
        return frozenset(), frozenset()
    drop = frozenset(i for i, column in enumerate(columns) if is_property_key(column))
    if not drop:
        return drop, frozenset()
    positional = frozenset(key for key, value in mapping.items() if key != "columns" and isinstance(value, list)
                           and any(isinstance(row, list) for row in value))
    if any(isinstance(row, list) and len(row) != len(columns) for key in positional for row in mapping[key]):
        # The cells do not line up with the header, so removing a header would shift them. The table is left, and
        # its header string is counted as left in text when the walk reaches it.
        return frozenset(), frozenset()
    return drop, positional


def _scrub_mapping(mapping: dict, count: Count) -> dict:
    if mapping.get("type") in _SIGNED_BLOCKS:
        if names_a_property(json.dumps(mapping, default=str)):
            count.left += 1
        return mapping
    drop, positional = _table_positions(mapping)
    out: dict = {}
    changed = False
    for key, item in mapping.items():
        if is_property_key(key):
            count.removed += 1
            changed = True
            continue
        if isinstance(key, str) and names_a_property(key):
            count.left += 1
        if drop and key == "columns":
            item = [column for i, column in enumerate(item) if i not in drop]
            count.removed += len(drop)
            changed = True
        elif key in positional:
            rows = []
            for row in item:
                if isinstance(row, list):
                    rows.append([cell for i, cell in enumerate(row) if i not in drop])
                    count.removed += len(drop)
                else:
                    rows.append(row)
            item = rows
            changed = True
        new = scrub_value(item, count)
        changed = changed or new is not item
        out[key] = new
    return out if changed else mapping


_NOT_JSON = object()
_DECODER = json.JSONDecoder()
_SPACE = " \t\r\n"
_PAIR = re.compile(r'"(?:[^"\\\n]*\.)?(?:' + "|".join(re.escape(n) for n in NAMES) + r')"\s*:', re.IGNORECASE)
_INDENT = re.compile(r"[\[{]\r?\n([ \t]*)\S")
_KEY_SEPARATOR = re.compile(r'(?<!\\)":(.)')


def _json_or_not(text: str) -> Any:
    if text.lstrip()[:1] not in ("{", "[", '"'):
        return _NOT_JSON
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return _NOT_JSON


def dump_like(original: str, value: Any) -> str | None:
    """``value`` as JSON in ``original``'s layout: its indent or compactness, its escaping, its line ends, its outer
    whitespace. None when it cannot be written as strict JSON (a number beyond a double, read back as infinity)."""
    body = original.strip()
    lead = original[: len(original) - len(original.lstrip())]
    trail = original[len(original.rstrip()):]
    indented = _INDENT.match(body)
    if indented:
        spaces = indented.group(1)
        indent: int | str | None = spaces if spaces.startswith("\t") else len(spaces)
        separators = (",", ": ")
    else:
        indent = None
        first = _KEY_SEPARATOR.search(body)
        spaced = first.group(1) == " " if first else ", " in body
        separators = (", ", ": ") if spaced else (",", ":")
    ascii_only = body.isascii()
    try:
        text = json.dumps(value, indent=indent, separators=separators, ensure_ascii=ascii_only, allow_nan=False)
        if not ascii_only:
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:  # a lone surrogate from an escape: keep it escaped
                text = json.dumps(value, indent=indent, separators=separators, ensure_ascii=True, allow_nan=False)
    except ValueError:
        return None
    if indent is not None and "\r\n" in body:
        text = text.replace("\n", "\r\n")  # every newline json.dumps writes is structural; strings escape theirs
    return lead + text + trail


def _cut_pairs(text: str, count: Count) -> str:
    """Cut every complete ``"name": value`` pair out of text that is not JSON as a whole, with one comma."""
    pieces: list[str] = []
    pos = 0
    for match in _PAIR.finditer(text):
        if match.start() < pos:
            continue
        start = match.end()
        while start < len(text) and text[start] in _SPACE:
            start += 1
        try:
            _, end = _DECODER.raw_decode(text, start)
        except ValueError:
            continue  # a value cut off mid-way: left, and counted by the caller
        cut_from, cut_to = match.start(), end
        back = cut_from - 1
        while back >= pos and text[back] in _SPACE:
            back -= 1
        if back >= pos and text[back] == ",":
            cut_from = back
        else:
            ahead = cut_to
            while ahead < len(text) and text[ahead] in _SPACE:
                ahead += 1
            if ahead < len(text) and text[ahead] == ",":
                cut_to = ahead + 1
                while cut_to < len(text) and text[cut_to] in _SPACE:
                    cut_to += 1
        pieces.append(text[pos:cut_from])
        pos = cut_to
        count.removed += 1
    if not pieces:
        return text
    pieces.append(text[pos:])
    return "".join(pieces)


def _is_json_lines(text: str) -> bool:
    """Two or more lines, each a JSON value, the last excepted (a file cut off mid-line)."""
    lines = [line for line in text.split("\n") if line.strip()]
    return len(lines) > 1 and all(_json_or_not(line) is not _NOT_JSON for line in lines[:-1])


def _scrub_lines(text: str, count: Count) -> str:
    out: list[str] = []
    changed = False
    for part in text.split("\n"):  # never splitlines(): U+2028 inside a JSON string is not a line break
        body, cr = (part[:-1], "\r") if part.endswith("\r") else (part, "")
        new = scrub_text(body, count) if body else body
        changed = changed or new is not body
        out.append(new + cr)
    return "\n".join(out) if changed else text


def scrub_text(text: str, count: Count) -> str:
    """A string that may hold JSON, JSON lines, or text with JSON pairs in it."""
    if not names_a_property(text):
        return text
    parsed = _json_or_not(text)
    if parsed is not _NOT_JSON:
        before = count.removed
        cleaned = scrub_value(parsed, count)
        if count.removed == before:
            return text
        redone = dump_like(text, cleaned)
        if redone is not None:
            return redone
        count.removed = before
        count.left += 1
        return text
    if "\n" in text and _is_json_lines(text):
        return _scrub_lines(text, count)
    cleaned = _cut_pairs(text, count)
    if names_a_property(cleaned):
        count.left += 1
    return cleaned


def _lift_csv_field_limit() -> None:
    """A previous turn's rows.csv holds a whole node as JSON in one cell, past the csv module's 128 KiB default."""
    try:
        csv.field_size_limit(sys.maxsize)
    except OverflowError:  # pragma: no cover - a platform whose C long is 32 bits
        csv.field_size_limit(2 ** 31 - 1)


def scrub_csv(text: str, count: Count, delimiter: str = ",") -> str:
    """CSV without the property columns, every other cell cleaned as text; untouched text comes back as is."""
    if not names_a_property(text):
        return text
    _lift_csv_field_limit()
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        return text
    drop = {i for i, header in enumerate(rows[0]) if is_property_key(header)}
    before = count.removed
    out = []
    for row in rows:
        kept = []
        for i, cell in enumerate(row):
            if i in drop:
                count.removed += 1
                continue
            kept.append(scrub_text(cell, count))
        out.append(kept)
    if count.removed == before:
        return text
    terminator = "\r\n" if "\r\n" in text else "\n"
    buf = io.StringIO(newline="")
    csv.writer(buf, delimiter=delimiter, lineterminator=terminator).writerows(out)
    result = buf.getvalue()
    if not text.endswith(("\n", "\r")) and result.endswith(terminator):
        result = result[: -len(terminator)]
    return result


def _scrub_zip(data: bytes, count: Count, *, rewrite: bool) -> bytes:
    """A zip with its readable members cleaned (``rewrite``), or only counted (an xlsx)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return data
    members: list[tuple[zipfile.ZipInfo, bytes, bytes]] = []
    with archive:
        if any(info.file_size > ZIP_MEMBER_MAX_BYTES for info in archive.infolist()):
            count.left += 1
            return data
        comment = archive.comment
        for info in archive.infolist():
            raw = archive.read(info)
            kind = kind_of(info.filename)
            if kind in ("zip", "xlsx"):
                new = _scrub_zip(raw, count, rewrite=rewrite and kind == "zip")
            elif rewrite and kind in _REWRITTEN_KINDS:
                new = scrub_bytes(raw, kind, count)
            else:
                new = raw
                if names_a_property(raw):
                    count.left += 1
            members.append((info, raw, new))
    if all(new is raw for _, raw, new in members):
        return data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.comment = comment
        for info, _, new in members:
            # A fresh header: the source's extra field can hold size records that no longer hold.
            fresh = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            fresh.compress_type = info.compress_type
            fresh.comment = info.comment
            fresh.create_system = info.create_system
            fresh.external_attr = info.external_attr
            out.writestr(fresh, new)
    return buf.getvalue()


def kind_of(name: str | Path) -> str | None:
    return FILE_KINDS.get(Path(str(name)).suffix.lower())


def scrub_bytes(data: bytes, kind: str | None, count: Count) -> bytes:
    """A stored file's bytes without the two properties; the same object when there was nothing to remove."""
    if kind == "zip":
        return _scrub_zip(data, count, rewrite=True)
    if kind == "xlsx":
        return _scrub_zip(data, count, rewrite=False)
    if kind not in _REWRITTEN_KINDS:  # a file this command does not parse: counted when it names a property
        if names_a_property(data):
            count.left += 1
        return data
    if not names_a_property(data):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        count.left += 1
        return data
    if kind in ("csv", "tsv"):
        cleaned = scrub_csv(text, count, "\t" if kind == "tsv" else ",")
    else:
        cleaned = scrub_text(text, count)
    return data if cleaned is text else cleaned.encode("utf-8")


# =============================================================================================== bookkeeping


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _pk_value(pk: Any) -> Any:
    return pk if isinstance(pk, int) and not isinstance(pk, bool) else str(pk)


@dataclass
class OwnerCounts:
    scanned: int = 0
    affected: int = 0
    keys: int = 0
    left: int = 0
    backup_bytes: int = 0
    ids: list[str] = field(default_factory=list)


@dataclass
class Tally:
    owners: dict[bool, OwnerCounts] = field(default_factory=lambda: {False: OwnerCounts(), True: OwnerCounts()})
    skipped: dict[str, int] = field(default_factory=dict)
    outcomes: dict[str, int] = field(default_factory=dict)
    in_scope: int = 0
    stopped: bool = False

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def outcome(self, status: str) -> None:
        self.outcomes[status] = self.outcomes.get(status, 0) + 1


class Backup:
    """The JSON lines file of original values, created 0600 and never overwritten."""

    def __init__(self, directory: Path, header: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for n in range(1, 1000):
            path = directory / f"{BACKUP_PREFIX}-{stamp}{'' if n == 1 else f'-{n}'}.jsonl"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                continue
        else:  # pragma: no cover - a thousand runs in one second
            raise CommandError(f"could not create a new backup file in {directory}")
        self.path = path
        self._fh = os.fdopen(fd, "w", encoding="utf-8")
        self.write({"kind": "header", **header})
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    def write(self, entry: dict) -> int:
        line = json.dumps(entry, ensure_ascii=True) + "\n"
        self._fh.write(line)
        return len(line)

    def close(self) -> None:
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()


@dataclass
class Run:
    include_admins: bool
    limit: int | None
    batch_size: int
    max_file_bytes: int
    min_transcript_age_s: int
    admin_ids: frozenset
    admin_usernames: frozenset
    backup: Backup | None = None
    progress: Callable[[str], None] | None = None

    def in_scope(self, admin: bool) -> bool:
        return self.include_admins or not admin

    def note(self, tally: Tally, *, admin: bool, rid: str, count: Count, entries: Callable[[], list[dict]]) -> bool:
        """Count one scanned record and back it up when it is affected and in scope; False once --limit is met."""
        owner = tally.owners[admin]
        owner.scanned += 1
        owner.left += count.left
        if self.progress and owner.scanned % 1000 == 0:
            self.progress(f"{owner.scanned} {'admin' if admin else 'non-admin'} records scanned")
        if not count.removed:
            return True
        owner.affected += 1
        owner.keys += count.removed
        if len(owner.ids) < SAMPLE_IDS:
            owner.ids.append(rid)
        if not self.in_scope(admin):
            return True
        for entry in entries():
            owner.backup_bytes += self.backup.write(entry) if self.backup else len(json.dumps(entry)) + 1
        tally.in_scope += 1
        if self.limit and tally.in_scope >= self.limit:
            tally.stopped = True
            return False
        return True


# =============================================================================================== database stores


class JsonFieldStore:
    """JSON columns of one table; a record is a row, and each affected column is backed up and written on its own."""

    kind = "db_json"

    def __init__(self, name: str, model, fields: tuple[str, ...], owner: str) -> None:
        self.name, self.model, self.fields, self.owner = name, model, fields, owner

    @property
    def label(self) -> str:
        return f"{self.model._meta.db_table}: {', '.join(self.fields)}"

    def scan(self, run: Run, tally: Tally) -> None:
        rows = list(self.model.objects.order_by("pk").values_list("pk", self.owner))
        for start in range(0, len(rows), run.batch_size):
            owners = dict(rows[start:start + run.batch_size])
            # .order_by() with no arguments: the model's default ordering would put these JSON columns into a sort.
            fetched = {pk: values for pk, *values in
                       self.model.objects.filter(pk__in=list(owners)).order_by().values_list("pk", *self.fields)}
            for pk, owner_id in owners.items():
                if pk not in fetched:
                    continue
                count, hits = Count(), []
                for name, value in zip(self.fields, fetched[pk]):
                    if not names_a_property(json.dumps(value, default=str)):
                        continue  # the common case, decided by the C encoder instead of a walk in Python
                    before = count.removed
                    scrub_value(value, count)
                    if count.removed > before:
                        hits.append((name, value))
                entries = lambda: [{"kind": self.kind, "store": self.name, "table": self.model._meta.db_table,
                                    "pk": _pk_value(pk), "field": name, "value": value} for name, value in hits]
                rid = f"{pk} [{','.join(name for name, _ in hits)}]" if hits else str(pk)
                if not run.note(tally, admin=owner_id in run.admin_ids, rid=rid, count=count, entries=entries):
                    return

    def _locked(self, entry: dict) -> list:
        return list(self.model.objects.select_for_update().filter(pk=entry["pk"]).order_by()
                    .values_list(entry["field"], flat=True))

    def apply(self, entry: dict) -> str:
        with transaction.atomic():
            current = self._locked(entry)
            if not current:
                return "gone"
            if _canonical(current[0]) != _canonical(entry["value"]):
                return "changed"
            count = Count()
            cleaned = scrub_value(current[0], count)
            if not count.removed:
                return "changed"
            self.model.objects.filter(pk=entry["pk"]).update(**{entry["field"]: cleaned})
        return "written"

    def restore(self, entry: dict, write: bool) -> str:
        with transaction.atomic():
            current = self._locked(entry)
            if not current:
                return "gone"
            if _canonical(current[0]) == _canonical(entry["value"]):
                return "already original"
            if _canonical(current[0]) != _canonical(scrub_value(entry["value"], Count())):
                return "changed"
            if write:
                self.model.objects.filter(pk=entry["pk"]).update(**{entry["field"]: entry["value"]})
        return "restored"


class TranscriptBlobStore:
    """``assistant_cc_transcript.blob``: zstd-compressed JSON lines, cleaned as text and compressed again."""

    kind = "db_blob"
    name = "cc_transcripts"
    label = "assistant_cc_transcript: blob"

    @staticmethod
    def _max_bytes() -> int:
        return getattr(settings, "CC_TRANSCRIPT_MAX_BYTES", 256 * 1024 * 1024)

    def scan(self, run: Run, tally: Tally) -> None:
        from NessieAI.cc.cc_transcript_store import decompress

        rows = list(CCSessionTranscript.objects.order_by("pk").values_list("pk", "chat_session__user_id"))
        for start in range(0, len(rows), run.batch_size):
            owners = dict(rows[start:start + run.batch_size])
            fetched = dict(CCSessionTranscript.objects.filter(pk__in=list(owners)).order_by()
                           .values_list("pk", "blob"))
            for pk, owner_id in owners.items():
                if pk not in fetched:
                    continue
                blob = bytes(fetched[pk])
                try:
                    jsonl = decompress(blob, max_bytes=self._max_bytes())
                except Exception:  # noqa: BLE001 - one unreadable row must not stop the scan
                    tally.skip("unreadable")
                    continue
                count = Count()
                scrub_bytes(jsonl, "text", count)
                entries = lambda: [{"kind": self.kind, "store": self.name, "table": "assistant_cc_transcript",
                                    "pk": _pk_value(pk), "field": "blob", "value_b64": b64encode(blob),
                                    "sha256": sha256(blob), "uncompressed_size": len(jsonl)}]
                if not run.note(tally, admin=owner_id in run.admin_ids, rid=str(pk), count=count, entries=entries):
                    return

    @staticmethod
    def _locked(entry: dict) -> list:
        return list(CCSessionTranscript.objects.select_for_update().filter(pk=entry["pk"]).order_by()
                    .values_list("blob", flat=True))

    def apply(self, entry: dict) -> str:
        from NessieAI.cc.cc_transcript_store import compress, decompress

        with transaction.atomic():
            current = self._locked(entry)
            if not current:
                return "gone"
            blob = bytes(current[0])
            if sha256(blob) != entry["sha256"]:
                return "changed"
            count = Count()
            cleaned = scrub_bytes(decompress(blob, max_bytes=self._max_bytes()), "text", count)
            if not count.removed:
                return "changed"
            CCSessionTranscript.objects.filter(pk=entry["pk"]).update(
                blob=compress(cleaned), uncompressed_size=len(cleaned))
        return "written"

    def restore(self, entry: dict, write: bool) -> str:
        from NessieAI.cc.cc_transcript_store import decompress

        original = b64decode(entry["value_b64"])
        with transaction.atomic():
            current = self._locked(entry)
            if not current:
                return "gone"
            blob = bytes(current[0])
            if blob == original:
                return "already original"
            expected = scrub_bytes(decompress(original, max_bytes=self._max_bytes()), "text", Count())
            if decompress(blob, max_bytes=self._max_bytes()) != expected:
                return "changed"
            if write:
                CCSessionTranscript.objects.filter(pk=entry["pk"]).update(
                    blob=original, uncompressed_size=entry["uncompressed_size"])
        return "restored"


# =============================================================================================== file stores


def _replace_file(path: Path, data: bytes, *, mode: int, uid: int, gid: int, atime_ns: int, mtime_ns: int) -> None:
    """Write ``data`` to ``path`` atomically, with the given mode, owner and times.

    The times matter as much as the bytes: the CC turn picks a session's newest transcript by modification time.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".scrub")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, stat.S_IMODE(mode))
        try:
            os.chown(tmp, uid, gid)
        except OSError:
            pass  # not root: the file is already ours
        os.utime(tmp, ns=(atime_ns, mtime_ns))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_regular(path: Path, max_bytes: int | None = None) -> tuple[os.stat_result, bytes] | str:
    """(stat, bytes) of a regular file, or why it was not read."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "gone"
    except OSError:
        return "unreadable"
    if not stat.S_ISREG(st.st_mode):
        return "not_a_regular_file"
    if max_bytes is not None and st.st_size > max_bytes:
        return "too_large"
    try:
        return st, path.read_bytes()
    except OSError:
        return "unreadable"


class FileStore:
    """Files on disk; a record is a file, backed up whole and replaced whole.

    ``live`` marks a store whose files a running turn appends to: a file written in the last
    ``--min-transcript-age-minutes`` is left for a later run. ``after_write(path, old, new)`` runs after every
    rewrite and every restore.
    """

    kind = "file"

    def __init__(self, name: str, label: str, files: Callable[[Run, Tally], Iterator[tuple[Path, bool, str]]], *,
                 live: bool = False, after_write: Callable[[Path, bytes, bytes], None] | None = None):
        self.name, self.label, self._files = name, label, files
        self.live, self.after_write = live, after_write

    def scan(self, run: Run, tally: Tally) -> None:
        for path, admin, rid in self._files(run, tally):
            read = _read_regular(path, run.max_file_bytes)
            if isinstance(read, str):
                tally.skip(read)
                continue
            st, data = read
            if self.live and st.st_mtime > time.time() - run.min_transcript_age_s:
                tally.skip("recently_modified")
                continue
            count = Count()
            try:
                scrub_bytes(data, kind_of(path), count)
            except Exception:  # noqa: BLE001 - one unreadable file must not stop the scan
                tally.skip("unreadable")
                continue
            entries = lambda: [{"kind": self.kind, "store": self.name, "path": str(path),
                                "value_b64": b64encode(data), "sha256": sha256(data), "mode": st.st_mode,
                                "uid": st.st_uid, "gid": st.st_gid, "atime_ns": st.st_atime_ns,
                                "mtime_ns": st.st_mtime_ns}]
            if not run.note(tally, admin=admin, rid=rid, count=count, entries=entries):
                return

    def apply(self, entry: dict) -> str:
        path = Path(entry["path"])
        read = _read_regular(path)
        if isinstance(read, str):
            return "gone" if read == "gone" else "changed"
        st, data = read
        if sha256(data) != entry["sha256"]:
            return "changed"
        count = Count()
        cleaned = scrub_bytes(data, kind_of(path), count)
        if not count.removed:
            return "changed"
        _replace_file(path, cleaned, mode=st.st_mode, uid=st.st_uid, gid=st.st_gid,
                      atime_ns=st.st_atime_ns, mtime_ns=st.st_mtime_ns)
        if self.after_write:
            self.after_write(path, data, cleaned)
        return "written"

    def restore(self, entry: dict, write: bool) -> str:
        path = Path(entry["path"])
        read = _read_regular(path)
        if isinstance(read, str):
            return "gone" if read == "gone" else "changed"
        _, data = read
        original = b64decode(entry["value_b64"])
        if data == original:
            return "already original"
        if data != scrub_bytes(original, kind_of(path), Count()):
            return "changed"
        if write:
            _replace_file(path, original, mode=entry["mode"], uid=entry["uid"], gid=entry["gid"],
                          atime_ns=entry["atime_ns"], mtime_ns=entry["mtime_ns"])
            if self.after_write:
                self.after_write(path, data, original)
        return "restored"


def _walk(top: Path) -> Iterator[Path]:
    """Every non-link file under ``top``, in a stable order. Kinds this command does not parse are only checked."""
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if not path.is_symlink():
                yield path


def _path_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        if value and "://" not in value and "/" in value:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _path_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _path_strings(item)


def _bundle_paths(history: Any) -> Iterator[str]:
    """Every file path a session's bundles name: the download manifest, the payload and debug files, report files."""
    for bundle in history if isinstance(history, list) else []:
        if not isinstance(bundle, dict):
            continue
        for entry in bundle.get("files") or []:
            if isinstance(entry, dict):
                yield from _path_strings(entry.get("path"))
        for key in ("raw_result_path", "graph_debug_path", "paths", "report_saved_files"):
            yield from _path_strings(bundle.get(key))


def _ns_files(run: Run, tally: Tally) -> Iterator[tuple[Path, bool, str]]:
    from NessieAI.ns.artifacts import _safe_artifact_path

    done: dict[Path, bool] = {}  # path -> whether it was read as an admin's; a non-admin's read is final
    rows = list(ChatSession.objects.order_by("pk").values_list("pk", "user_id"))
    for start in range(0, len(rows), run.batch_size):
        owners = dict(rows[start:start + run.batch_size])
        fetched = {pk: (extra, history) for pk, extra, history in
                   ChatSession.objects.filter(pk__in=list(owners)).order_by()
                   .values_list("pk", "extra_state", "results_history")}
        for pk, owner_id in owners.items():
            if pk not in fetched:
                continue
            extra_state, history = fetched[pk]
            admin = owner_id in run.admin_ids
            candidates: list[Path] = []
            log_dir = extra_state.get("log_dir") if isinstance(extra_state, dict) else None
            if isinstance(log_dir, str) and log_dir:
                root = _safe_artifact_path(log_dir)
                if root is None:
                    tally.skip("outside_the_artifact_roots")
                elif root.is_dir():
                    candidates.extend(p for p in _walk(root) if _safe_artifact_path(str(p)) is not None)
            for stored in _bundle_paths(history):
                path = _safe_artifact_path(stored)
                if path is None:
                    tally.skip("outside_the_artifact_roots")
                elif path.is_file():
                    candidates.append(path)
            for path in candidates:
                # Read again only for a non-admin owner after an admin one whose files were out of scope.
                if path in done and (done[path] is False or admin or run.include_admins):
                    continue
                done[path] = admin
                yield path, admin, str(path)


def _cc_user_dirs(run: Run, tally: Tally) -> Iterator[tuple[Path, Path, bool]]:
    """(root, <project>/<user> directory, whether that user is a superuser) for every CC user tree."""
    from NessieAI.cc.cc_config import CCPaths

    root = Path(CCPaths.from_env().user_root_mount)
    if not root.is_dir():
        tally.skip("no_cc_user_root")
        return

    def subdirs(path: Path) -> list[Path]:
        return sorted(p for p in path.iterdir() if p.is_dir() and not p.is_symlink())

    for project in subdirs(root):
        if project.name.startswith(("_", ".")):  # _staging belongs to the sidecar, not to a user
            continue
        for user_dir in subdirs(project):
            if user_dir.name != "shared":
                yield root, user_dir, user_dir.name in run.admin_usernames


def _cc_files(*tops: Callable[[Path], list[Path]]) -> Callable[[Run, Tally], Iterator[tuple[Path, bool, str]]]:
    def files(run: Run, tally: Tally) -> Iterator[tuple[Path, bool, str]]:
        for root, user_dir, admin in _cc_user_dirs(run, tally):
            for pick in tops:
                for top in pick(user_dir):
                    if top.is_dir() and not top.is_symlink():
                        for path in _walk(top):
                            yield path, admin, str(path.relative_to(root))
    return files


def _carry_transcript_marks(path: Path, old: bytes, new: bytes) -> None:
    """Move the two records the CC side keeps about a transcript's exact bytes from ``old`` to ``new``.

    ``cc_engine.scrub_transcript_store`` records the sha256 of every transcript it cleaned of credentials in
    ``cc-state/.<session>.scrub.json``; ``cc_sweep`` summarizes only transcripts whose bytes match. The CC turn keeps
    the fingerprint of the session's newest transcript in ``extra_state["summary_fingerprint"]`` and summarizes the
    session again when it differs. Each record moves only when it described ``old`` exactly: removing two keys from
    clean bytes leaves them clean, and anything else is left for the CC side to redo.
    """
    from NessieAI.cc.cc_engine import _read_scrub_manifest, _write_scrub_manifest
    from NessieAI.cc.cc_summary import fingerprint

    store_roots = [p for p in path.parents if p.name == "projects"]  # the outermost one, as the CC side derives it
    if not store_roots or store_roots[-1].parent.parent.name != "cc-state":
        return
    cc_state_dir = store_roots[-1].parent
    rel = str(path.relative_to(cc_state_dir))
    files = _read_scrub_manifest(cc_state_dir)
    if files.get(rel) == sha256(old):
        manifest = cc_state_dir.parent / f".{cc_state_dir.name}.scrub.json"
        times = os.stat(manifest)
        _write_scrub_manifest(cc_state_dir, {**files, rel: sha256(new)})
        os.utime(manifest, ns=(times.st_atime_ns, times.st_mtime_ns))
    try:
        with transaction.atomic():
            rows = list(ChatSession.objects.select_for_update().filter(pk=cc_state_dir.name).order_by()
                        .values_list("extra_state", flat=True))
            if not rows or not isinstance(rows[0], dict):
                return
            recorded = rows[0].get("summary_fingerprint")
            if isinstance(recorded, dict) and recorded == fingerprint(old):
                ChatSession.objects.filter(pk=cc_state_dir.name).update(
                    extra_state={**rows[0], "summary_fingerprint": fingerprint(new)})
    except (ValueError, ValidationError):  # a directory that is not a session id
        return


def _session_dirs(parent: Path, *tail: str) -> list[Path]:
    if not parent.is_dir():
        return []
    return [Path(d, *tail) for d in sorted(parent.iterdir()) if d.is_dir() and not d.is_symlink()]


STORES = {
    "sessions": JsonFieldStore("sessions", ChatSession, ("results_history", "last_debug", "extra_state"), "user_id"),
    "tasks": JsonFieldStore("tasks", QueryTask, ("result", "progress"), "user_id"),
    "cc_transcripts": TranscriptBlobStore(),
    "ns_files": FileStore("ns_files", "files a chat session names under the outputs roots", _ns_files),
    "cc_previous_turns": FileStore(
        "cc_previous_turns", "<CC user root>/<project>/<user>/_memory/<session>/previous_turns",
        _cc_files(lambda u: _session_dirs(u / "_memory", "previous_turns"))),
    "cc_artifacts": FileStore(
        "cc_artifacts", "<CC user root>/<project>/<user>/output/artifacts",
        _cc_files(lambda u: [u / "output" / "artifacts"])),
    "cc_transcript_files": FileStore(
        "cc_transcript_files", "<CC user root>/<project>/<user>/cc-state/<session> and _memory/<session>/transcripts",
        _cc_files(lambda u: _session_dirs(u / "cc-state"),
                  lambda u: _session_dirs(u / "_memory", "transcripts")),
        live=True, after_write=_carry_transcript_marks),
}
STORE_ORDER = tuple(STORES)


def apply_entry(store, entry: dict) -> str:
    """Change one backed-up record (the second pass); a module function so a test can stop the pass."""
    return store.apply(entry)


def _backup_entries(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                entry = json.loads(line)
                if entry.get("kind") != "header":
                    yield entry


# =============================================================================================== the command


class Command(BaseCommand):
    help = "Remove two derived sample properties from stored graph results (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write the changes (default: dry run). Needs --backup-dir.")
        parser.add_argument("--backup-dir", help="Directory for the JSON lines backup of every original value.")
        parser.add_argument("--include-admins", action="store_true",
                            help="Also change records owned by superusers (always counted).")
        parser.add_argument("--store", action="append", choices=STORE_ORDER,
                            help="Only this store; repeat for several (default: all).")
        parser.add_argument("--limit", type=int, help="Per store, stop after this many records that would change.")
        parser.add_argument("--batch-size", type=int, default=10, help="Database rows read per query (default 10).")
        parser.add_argument("--max-file-mb", type=int, default=256,
                            help="Skip, and count, files larger than this (default 256).")
        parser.add_argument("--min-transcript-age-minutes", type=int, default=15,
                            help="Leave CC transcript files written more recently than this, as a turn may be "
                                 "appending to them (default 15).")
        parser.add_argument("--restore", metavar="BACKUP_FILE",
                            help="Put back the originals from a backup file (dry run unless --apply).")

    def handle(self, *args, **options):
        if options["restore"]:
            return self._restore(Path(options["restore"]), write=options["apply"])
        apply = options["apply"]
        if apply and not options["backup_dir"]:
            raise CommandError("--apply writes a backup of every original value first: name its directory "
                               "with --backup-dir.")
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError("--limit must be at least 1.")
        backup_dir = Path(options["backup_dir"]).resolve() if options["backup_dir"] else None
        if backup_dir is not None:
            self._refuse_inside_a_scanned_root(backup_dir)
        stores = [STORES[name] for name in dict.fromkeys(options["store"] or STORE_ORDER)]
        users = get_user_model().objects.filter(is_superuser=True)
        run = Run(include_admins=options["include_admins"], limit=options["limit"],
                  batch_size=max(1, options["batch_size"]), max_file_bytes=options["max_file_mb"] * 1024 * 1024,
                  min_transcript_age_s=max(0, options["min_transcript_age_minutes"]) * 60,
                  admin_ids=frozenset(users.values_list("pk", flat=True)),
                  admin_usernames=frozenset(users.values_list("username", flat=True)))
        if options["verbosity"] >= 2:
            run.progress = lambda message: self.stderr.write(message)

        write = self.stdout.write
        write(f"scrub_stored_sample_properties: "
              f"{'APPLY' if apply else 'DRY RUN, nothing is written (pass --apply with --backup-dir to write)'}")
        write(f"Removes these keys from stored graph results: {', '.join(NAMES)}")
        write("In scope: " + ("records of every owner (--include-admins)" if run.include_admins else
                              "records of non-admin owners; superusers' records are counted and left as they are"))

        tallies: dict[str, Tally] = {}
        if apply:
            try:
                run.backup = Backup(backup_dir, {
                    "command": BACKUP_PREFIX, "created": datetime.now(timezone.utc).isoformat(),
                    "names": list(NAMES), "include_admins": run.include_admins,
                    "stores": [s.name for s in stores], "limit": run.limit})
            except OSError as exc:
                raise CommandError(f"could not create the backup file in {backup_dir}: {exc}") from exc
        try:
            for store in stores:
                if run.progress:
                    run.progress(f"scanning {store.name}")
                tallies[store.name] = tally = Tally()
                store.scan(run, tally)
        except OSError as exc:
            raise CommandError(f"the scan or the backup failed before any record was changed: {exc}") from exc
        finally:
            if run.backup:
                run.backup.close()

        for store in stores:
            self._report(store, tallies[store.name], run)
        in_scope = sum(t.in_scope for t in tallies.values())
        size = sum(o.backup_bytes for t in tallies.values() for o in t.owners.values())
        write("")
        write(f"Total in scope: {in_scope} records, backup {'written' if apply else 'would be'} about {_mb(size)}")
        if not apply:
            return

        write(f"Backup: {run.backup.path}")
        by_name = {store.name: store for store in stores}
        for entry in _backup_entries(run.backup.path):
            try:
                status = apply_entry(by_name[entry["store"]], entry)
            except Exception as exc:  # noqa: BLE001 - one record that cannot be written must not stop the rest
                status = "failed"
                self.stderr.write(f"{entry['store']} {_entry_id(entry)}: not written: {type(exc).__name__}: {exc}")
            tallies[entry["store"]].outcome(status)
        write("")
        write("Second pass, one line per store (a value is one column of one row, or one file):")
        for store in stores:
            outcomes = tallies[store.name].outcomes
            write(f"{store.name}: {outcomes.get('written', 0)} written, "
                  f"{outcomes.get('changed', 0)} changed since the scan (left as they are; run again), "
                  f"{outcomes.get('gone', 0)} gone, {outcomes.get('failed', 0)} failed")
        write("Run the dry run again: it should find 0 records in scope.")

    def _report(self, store, tally: Tally, run: Run) -> None:
        write = self.stdout.write
        write("")
        write(f"{store.name} ({store.label})")
        write(f"  {'':10}{'scanned':>9}{'affected':>10}{'keys':>9}{'left in text':>14}")
        for admin, who in ((False, "non-admin"), (True, "admin")):
            o = tally.owners[admin]
            write(f"  {who:10}{o.scanned:>9}{o.affected:>10}{o.keys:>9}{o.left:>14}")
        scoped = [tally.owners[a] for a in (False, True) if run.in_scope(a)]
        write(f"  in scope: {sum(o.affected for o in scoped)} records, {sum(o.keys for o in scoped)} keys, "
              f"backup about {_mb(sum(o.backup_bytes for o in scoped))}")
        for admin, who in ((False, "non-admin"), (True, "admin")):
            ids = tally.owners[admin].ids
            if ids:
                write(f"  first affected ids, {who}: {', '.join(ids)}")
        if tally.skipped:
            write("  skipped: " + ", ".join(f"{reason} {n}" for reason, n in sorted(tally.skipped.items())))
        if tally.stopped:
            write(f"  stopped at --limit {run.limit}: the counts above are partial")

    @staticmethod
    def _refuse_inside_a_scanned_root(backup_dir: Path) -> None:
        from NessieAI.cc.cc_config import CCPaths
        from NessieAI.ns.artifacts import _artifact_roots

        for root in [*_artifact_roots(), Path(CCPaths.from_env().user_root_mount).resolve()]:
            if backup_dir == root or root in backup_dir.parents:
                raise CommandError(f"--backup-dir {backup_dir} is inside {root}, which this command scans and "
                                   "users can read from; choose a directory outside it (e.g. under /app/logs).")

    def _restore(self, path: Path, write: bool) -> None:
        if not path.is_file():
            raise CommandError(f"no backup file at {path}")
        self.stdout.write(f"scrub_stored_sample_properties --restore {path}: "
                          f"{'APPLY' if write else 'DRY RUN, nothing is written (pass --apply to write)'}")
        outcomes: dict[str, dict[str, int]] = {}
        for entry in _backup_entries(path):
            try:
                status = STORES[entry["store"]].restore(entry, write)
            except Exception as exc:  # noqa: BLE001 - one record that cannot be restored must not stop the rest
                status = "failed"
                self.stderr.write(f"{entry['store']} {_entry_id(entry)}: not restored: {type(exc).__name__}: {exc}")
            counts = outcomes.setdefault(entry["store"], {})
            counts[status] = counts.get(status, 0) + 1
        for name in STORE_ORDER:
            if name in outcomes:
                c = outcomes[name]
                self.stdout.write(f"{name}: {c.get('restored', 0)} {'restored' if write else 'would be restored'}, "
                                  f"{c.get('already original', 0)} already original, "
                                  f"{c.get('changed', 0)} changed since the scrub (left as they are), "
                                  f"{c.get('gone', 0)} gone, {c.get('failed', 0)} failed")


def _entry_id(entry: dict) -> str:
    return entry.get("path") or f"{entry.get('pk')} [{entry.get('field')}]"


def _mb(n: int) -> str:
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024)) if n else 0} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"
