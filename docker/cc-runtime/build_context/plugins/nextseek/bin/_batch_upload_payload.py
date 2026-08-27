"""Flat NExtSEEK batch-upload payload builder.

T2 owns only artifact construction and unit-level row safety checks. It does
not fetch sample state, resolve assay titles, or call NExtSEEK; T3 supplies the
resolved current assay map, id-to-title map, and structured sample schema.
"""
from __future__ import annotations

import pathlib
from collections.abc import Iterable
from typing import Any

import orjson
import polars as pl

REVIEW_PREFIX = "REVIEW_ONLY__"


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        uid = _clean(row.get("UID"))
        attrs = dict(row.get("attributes") or {})
        meta_uid = _clean(attrs.get("UID"))
        if uid and meta_uid and uid != meta_uid:
            raise ValueError("uid_mismatch")
        if not uid and meta_uid:
            raise ValueError("uid_mismatch")
        metadata = {str(key): value for key, value in attrs.items() if key != "UID"}
        if uid:
            metadata["UID"] = uid
        out.append(
            {
                "UID": uid,
                "SampleType": str(row["SampleType"]),
                "json_metadata": metadata,
                "assay_ids": _int_list(row.get("assay_ids") or []),
            }
        )
    return out


def check_known_attributes(
    rows: list[dict[str, Any]],
    sample_type_attributes: Iterable[dict[str, Any]],
) -> list[tuple[str, str]]:
    allowed_by_type = _schema_by_type(sample_type_attributes)
    violations: list[tuple[str, str]] = []
    for row in rows:
        sample_type = row["SampleType"]
        allowed = set(allowed_by_type.get(sample_type, {})) | {"UID"}
        for key in row["json_metadata"]:
            if key not in allowed:
                violations.append((sample_type, key))
    return violations


def build(
    rows: list[dict[str, Any]],
    out_dir: str | pathlib.Path,
    *,
    resolved_current: dict[str, set[int]] | None = None,
    id_to_title: dict[int, str] | None = None,
    sample_type_attributes: Iterable[dict[str, Any]] | None = None,
    output_name: str = "payload_flat.xlsx",
) -> list[str]:
    normalized = normalize_rows(rows)
    sample_type_attributes = list(sample_type_attributes or [])
    schema = _schema_by_type(sample_type_attributes)
    if not schema:
        raise ValueError("schema_missing")

    violations = check_known_attributes(normalized, sample_type_attributes)
    if violations:
        raise ValueError(f"invented_attribute:{violations[0][0]}:{violations[0][1]}")

    current = resolved_current or {}
    title_map = {int(key): value for key, value in (id_to_title or {}).items()}
    records: list[dict[str, Any]] = []
    review_attr_titles: list[str] = []
    for row in normalized:
        _validate_row(row, schema)
        delivered_ids = _delivered_assay_ids(row, current)
        delivered_titles = _assay_titles(delivered_ids, title_map)
        if len(delivered_ids) != len(delivered_titles):
            raise ValueError("assay_ids_titles_length_mismatch")
        if not _has_real_attribute(row["json_metadata"]) and not delivered_ids:
            raise ValueError("non_empty")
        for key in row["json_metadata"]:
            if key != "UID" and key not in review_attr_titles:
                review_attr_titles.append(key)
        records.append(_record(row, delivered_ids, delivered_titles))

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / output_name
    _write_flat_xlsx(records, path, review_attr_titles=review_attr_titles)
    return [str(path)]


def _schema_by_type(
    sample_type_attributes: Iterable[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    by_type: dict[str, dict[str, dict[str, Any]]] = {}
    for item in sample_type_attributes:
        sample_type = str(item.get("sample_type") or item.get("SampleType") or "")
        attrs = item.get("attributes") or item.get("sample_attributes") or []
        if not sample_type or not isinstance(attrs, list):
            continue
        by_type[sample_type] = {
            str(attr.get("title")): attr
            for attr in attrs
            if isinstance(attr, dict) and attr.get("title")
        }
    return by_type


def _validate_row(
    row: dict[str, Any],
    schema: dict[str, dict[str, dict[str, Any]]],
) -> None:
    sample_type = row["SampleType"]
    attrs = schema.get(sample_type)
    if not attrs:
        raise ValueError(f"schema_missing:{sample_type}")
    metadata = row["json_metadata"]
    uid = _clean(row.get("UID"))
    if uid:
        for key, value in metadata.items():
            if key != "UID" and _is_blank(value):
                raise ValueError(f"present_blank:{key}")
        return

    for title, attr in attrs.items():
        if title == "UID":
            continue
        if bool(attr.get("required")) or bool(attr.get("is_title")):
            if _is_blank(metadata.get(title)):
                raise ValueError(f"required_missing:{title}")


def _delivered_assay_ids(
    row: dict[str, Any],
    resolved_current: dict[str, set[int]],
) -> list[int]:
    ids = set(row["assay_ids"])
    uid = _clean(row.get("UID"))
    if uid:
        ids |= {int(item) for item in resolved_current.get(uid, set())}
    return sorted(ids)


def _assay_titles(ids: list[int], id_to_title: dict[int, str]) -> list[str]:
    titles: list[str] = []
    for assay_id in ids:
        title = id_to_title.get(assay_id)
        if not title:
            raise ValueError(f"assay_title_missing:{assay_id}")
        titles.append(title)
    return titles


def _record(
    row: dict[str, Any],
    assay_ids: list[int],
    assay_titles: list[str],
) -> dict[str, Any]:
    metadata = dict(row["json_metadata"])
    return {
        "UID": row["UID"],
        "SampleType": row["SampleType"],
        "json_metadata": orjson.dumps(metadata).decode(),
        "assay_ids": orjson.dumps(assay_ids).decode(),
        "assay_titles": orjson.dumps(assay_titles).decode(),
        f"{REVIEW_PREFIX}do_not_edit": "Tell the skill what changed; do not hand-edit this sheet.",
        f"{REVIEW_PREFIX}attributes": _review_attributes(metadata),
        f"{REVIEW_PREFIX}assays": ", ".join(assay_titles),
    }


def _write_flat_xlsx(
    records: list[dict[str, Any]],
    path: pathlib.Path,
    *,
    review_attr_titles: list[str],
) -> None:
    columns = [
        "UID",
        "SampleType",
        "json_metadata",
        "assay_ids",
        "assay_titles",
        f"{REVIEW_PREFIX}do_not_edit",
        f"{REVIEW_PREFIX}attributes",
        f"{REVIEW_PREFIX}assays",
    ]
    for title in review_attr_titles:
        columns.append(f"{REVIEW_PREFIX}{title}")
        for record in records:
            metadata = orjson.loads(record["json_metadata"])
            record[f"{REVIEW_PREFIX}{title}"] = metadata.get(title, "")
    df = pl.DataFrame(
        {column: [str(record.get(column) or "") for record in records] for column in columns},
        schema={column: pl.Utf8 for column in columns},
    )
    df.write_excel(
        workbook=str(path),
        worksheet="Samples",
        include_header=True,
        autofilter=False,
        table_style=None,
    )


def _review_attributes(metadata: dict[str, Any]) -> str:
    return "; ".join(
        f"{key}={value}"
        for key, value in metadata.items()
        if key != "UID" and not _is_blank(value)
    )


def _has_real_attribute(metadata: dict[str, Any]) -> bool:
    return any(key != "UID" and not _is_blank(value) for key, value in metadata.items())


def _int_list(values: Iterable[Any]) -> list[int]:
    return sorted({int(value) for value in values})


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
