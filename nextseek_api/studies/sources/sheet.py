"""The curator sheet adapter (tool spec 4.3), ported from the earlier graph-only study tool's sheet reader.

One row per (study, sample): ``study_title``, ``investigation_title``, ``sample_uuid`` (also read as ``sample_uid``),
and optionally ``study_description``, ``doi``, ``pmid``, ``seek_study_id``. Headers are trimmed and lowercased; UIDs
are literal (a ``-PUB`` suffix is never stripped from a sheet); an entirely blank row is skipped and row numbers stay
the sheet's; exact duplicate rows are dropped and counted. These stop the plan and write nothing (``SheetError``): a
blank required cell, a study claimed under two investigations, a sample claimed under two investigations, two
different values of one study's description, DOI, PMID or SEEK study id, and a study none of whose UIDs resolves.
A UID that matches nothing is not a refusal: it is reported as unmatched. The package README holds the contract.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from nextseek_api.studies.models import AssociationSet
from nextseek_api.studies.sources.matching import UID_REASONS, RawTarget, clean, match_targets

REQUIRED_COLUMNS = ("study_title", "investigation_title", "sample_uuid")
OPTIONAL_COLUMNS = ("study_description", "doi", "pmid", "seek_study_id")
HEADER_ALIASES = {"sample_uid": "sample_uuid"}
PER_STUDY_FIELDS = ("study_description", "doi", "pmid", "seek_study_id")


class SheetError(Exception):
    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__("\n".join(messages))


@dataclass(frozen=True)
class SheetRow:
    row_number: int
    study_title: str
    investigation_title: str
    sample_uuid: str
    study_description: str = ""
    doi: str = ""
    pmid: str = ""
    seek_study_id: Optional[int] = None


def normalize_header(name: Any) -> str:
    key = str(name or "").strip().lower()
    return HEADER_ALIASES.get(key, key)


def _reject_duplicate_headers(headers: list[str]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for header in headers:
        if header and header in seen:
            duplicates.add(header)
        seen.add(header)
    if duplicates:
        raise SheetError([f"Duplicate column header(s) after normalization: {', '.join(sorted(duplicates))}. "
                          "Remove the extra column(s): one would silently overwrite the other."])


def _cell(value: Any) -> str:
    return clean(value) or ""


def _rows_from_records(records: list[dict[str, Any]], start_row: int) -> list[SheetRow]:
    missing = [column for column in REQUIRED_COLUMNS if records and column not in records[0]]
    if missing:
        raise SheetError([f"Sheet is missing required column(s): {', '.join(sorted(missing))}"])
    errors: list[str] = []
    rows: list[SheetRow] = []
    for offset, record in enumerate(records):
        row_number = start_row + offset
        if all(not _cell(value) for value in record.values()):
            continue
        values = {column: _cell(record.get(column)) for column in REQUIRED_COLUMNS}
        blank = [column for column, value in values.items() if not value]
        if blank:
            errors.append(f"row {row_number}: blank required value(s): {', '.join(sorted(blank))}")
            continue
        seek_text = _cell(record.get("seek_study_id"))
        seek_study_id = None
        if seek_text:
            if not seek_text.isdigit():
                errors.append(f"row {row_number}: seek_study_id {seek_text!r} is not a whole number")
                continue
            seek_study_id = int(seek_text)
        rows.append(SheetRow(row_number=row_number, study_title=values["study_title"],
                             investigation_title=values["investigation_title"], sample_uuid=values["sample_uuid"],
                             study_description=_cell(record.get("study_description")),
                             doi=_cell(record.get("doi")), pmid=_cell(record.get("pmid")),
                             seek_study_id=seek_study_id))
    if errors:
        raise SheetError(errors)
    if not rows:
        raise SheetError(["Sheet contains no data rows."])
    return rows


def read_sheet(path, sheet_name: Optional[str] = None) -> list[SheetRow]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            reader.fieldnames = [normalize_header(name) for name in (reader.fieldnames or [])]
            _reject_duplicate_headers(reader.fieldnames)
            records = [dict(r) for r in reader]
    elif suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in raw:
            _reject_duplicate_headers([normalize_header(k) for k in item])
        records = [{normalize_header(k): v for k, v in item.items()} for item in raw]
    elif suffix == ".xlsx":
        import openpyxl

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        worksheet = workbook[sheet_name] if sheet_name else workbook.worksheets[0]
        rows_iter = worksheet.iter_rows(values_only=True)
        headers = [normalize_header(value) for value in next(rows_iter, [])]
        _reject_duplicate_headers(headers)
        records = [dict(zip(headers, values)) for values in rows_iter]
    else:
        raise SheetError([f"Unsupported sheet format {path.suffix!r}. Use .xlsx, .csv, or .json."])
    if not records:
        raise SheetError([f"{path.name} contains no data rows."])
    return _rows_from_records(records, start_row=2)


def validate_rows(rows: list[SheetRow]) -> tuple[list[SheetRow], int]:
    """Drop exact duplicates (same study and sample) and count them; refuse two values of one study's field."""
    seen: dict[tuple[str, str], SheetRow] = {}
    deduped: list[SheetRow] = []
    duplicate_count = 0
    errors: list[str] = []
    for row in rows:
        key = (row.study_title, row.sample_uuid)
        previous = seen.get(key)
        if previous is not None:
            for name in PER_STUDY_FIELDS:
                a, b = getattr(previous, name), getattr(row, name)
                if a not in ("", None) and b not in ("", None) and a != b:
                    errors.append(f"row {row.row_number}: duplicate of row {previous.row_number} but {name} "
                                  f"differs for {row.study_title!r}")
            duplicate_count += 1
            continue
        seen[key] = row
        deduped.append(row)
    for name in PER_STUDY_FIELDS:
        first: dict[str, tuple[Any, int]] = {}
        for row in deduped:
            value = getattr(row, name)
            if value in ("", None):
                continue
            existing = first.get(row.study_title)
            if existing and existing[0] != value:
                errors.append(f"row {row.row_number}: {name} for {row.study_title!r} conflicts with the value on "
                              f"row {existing[1]}")
            else:
                first.setdefault(row.study_title, (value, row.row_number))
    if errors:
        raise SheetError(errors)
    return deduped, duplicate_count


def validate_structure(rows: list[SheetRow]) -> None:
    """A study under one investigation; a sample under one investigation (it may sit in several of its studies)."""
    errors: list[str] = []
    for study_title in sorted({r.study_title for r in rows}):
        claimed = {r.investigation_title for r in rows if r.study_title == study_title}
        if len(claimed) > 1:
            errors.append(f"Study {study_title!r} is claimed under multiple investigations {sorted(claimed)}. "
                          "Each study must belong to exactly one.")
    by_sample: dict[str, set[str]] = {}
    for r in rows:
        by_sample.setdefault(r.sample_uuid, set()).add(r.investigation_title)
    for sample_uuid, investigations in sorted(by_sample.items()):
        if len(investigations) > 1:
            numbers = sorted(r.row_number for r in rows if r.sample_uuid == sample_uuid)
            errors.append(f"Sample {sample_uuid!r} is claimed under multiple investigations {sorted(investigations)} "
                          f"(rows {', '.join(str(n) for n in numbers)}). A sample may belong to several studies, but "
                          "they must share one investigation: its bucket is that investigation's.")
    if errors:
        raise SheetError(errors)


def sheet_associations(path, sheet_name, reader, *, now: Optional[str] = None) -> AssociationSet:
    path = Path(path)
    rows, duplicates = validate_rows(read_sheet(path, sheet_name))
    validate_structure(rows)
    raws: dict[str, RawTarget] = {}
    for r in rows:
        raw = raws.get(r.study_title)
        if raw is None:
            raw = raws[r.study_title] = RawTarget(title=r.study_title, investigation_title=r.investigation_title)
        for name, attr in (("study_description", "description"), ("doi", "doi"), ("pmid", "pmid"),
                           ("seek_study_id", "seek_study_id")):
            value = getattr(r, name)
            if value not in ("", None) and getattr(raw, attr) is None:
                setattr(raw, attr, value)
        raw.uids.append((r.sample_uuid, f"row {r.row_number}"))
    targets, unmatched = match_targets(list(raws.values()), reader)
    errors = []
    for raw in raws.values():
        mine = [u for u in unmatched if u.submitted in {v for v, _p in raw.uids}]
        if len(mine) == len(raw.uids) and mine and all(u.reason in UID_REASONS for u in mine):
            errors.append(f"Study {raw.title!r} has no resolvable samples: every sample_uuid failed to match a "
                          "sample. Check the UID column and the sheet.")
    if errors:
        raise SheetError(errors)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return AssociationSet(source="sheet", source_ref=f"{path.name} sha256:{digest}",
                          created_at=now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          targets=targets, unmatched=unmatched, notes={"duplicate_rows": duplicates})
