"""Project one SEEK sample row onto its graph schema v1.1 node (docs/neo4j-schema.md, v1.1).

Pure: no database, no Django. The rules it implements are the schema doc's v1.1 "Rules" 1 to 4 and 6: metadata
property names are attribute titles verbatim (``UID`` excepted), empty values are absent, values are cast by the
attribute's ``value_type`` and keep their raw form when the cast fails, every sample gets one ``T_`` label, and
``search_text`` holds every non-empty raw value, one per line, never a key name.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

SYSTEM_KEYS = frozenset({"id", "uuid", "type", "title", "project_ids", "search_text", "synced_at",
                         "parent_titles", "parent_title_hashes"})
SKIPPED_METADATA_KEYS = frozenset({"UID"})

_LABEL_RE = re.compile(r"[^A-Za-z0-9_]")
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Neo4j integers are signed 64-bit; a larger Python int fails the whole write transaction.
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1
_VALUE_TYPES = {"Float": "float", "Integer": "integer", "Date": "date", "DateTime": "date"}


def label_for(title: str) -> str:
    """The sample's type label: ``T_`` plus the title with every character outside [A-Za-z0-9_] made ``_``."""
    return "T_" + _LABEL_RE.sub("_", title)


def value_type_for(base_type: str | None) -> str:
    """``float``, ``integer``, ``date`` or ``string`` from SEEK's ``sample_attribute_types.base_type``."""
    return _VALUE_TYPES.get(base_type or "", "string")


def is_empty(value) -> bool:
    """Empty is absent: ``None``, ``""``, an empty list or an empty map. ``0``, ``False`` and ``" "`` are values."""
    if value is None:
        return True
    if isinstance(value, (str, list, dict)):
        return len(value) == 0
    return False


def _in_int64(value: int) -> bool:
    return _INT64_MIN <= value <= _INT64_MAX


def _parse_date(s: str) -> date:
    """An ISO date, an ISO datetime (to its date) or M/D/YYYY. Anything else, a bare year included, raises."""
    s = s.strip()
    m = _MDY_RE.match(s)
    if m:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    if _ISO_DATE_RE.match(s):
        if len(s) == 10:
            return date.fromisoformat(s)
        # The whole string must be an ISO datetime; "2024-01-31 (approx)" is not cut down to a date.
        return datetime.fromisoformat(s).date()
    raise ValueError(s)


def _number(value, value_type: str):
    """A finite float, or an int within Neo4j's range for ``integer``. Raises ValueError otherwise."""
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, str):
        text = value.strip()
        if not _NUM_RE.match(text):
            raise ValueError(value)
        if value_type == "float":
            number = float(text)
        else:
            number = Decimal(text)
            # Refuse before int() so a huge exponent never builds a huge int.
            if number.adjusted() > 18 or number != number.to_integral_value():
                raise ValueError(value)
            number = int(number)
    elif isinstance(value, int):
        number = float(value) if value_type == "float" else value
    elif isinstance(value, float):
        if value_type == "integer":
            if not (math.isfinite(value) and value.is_integer()):
                raise ValueError(value)
            number = int(value)
        else:
            number = value
    else:
        raise ValueError(value)
    if isinstance(number, float) and not math.isfinite(number):
        raise ValueError(value)
    if isinstance(number, int) and not _in_int64(number):
        raise ValueError(value)
    return number


def cast_value(value, value_type: str) -> tuple[object, bool]:
    """Cast one non-empty value by its attribute's ``value_type``.

    Returns the value to store and whether the cast succeeded. A failed cast keeps the value as it came, so a range
    comparison sees only typed values. ``string`` keeps any JSON primitive as it is, except an int outside Neo4j's
    64-bit range, which becomes its string (and counts as a failure) so it cannot fail the write.
    """
    if value_type in ("float", "integer"):
        try:
            return _number(value, value_type), True
        except (ValueError, OverflowError, InvalidOperation):
            return value, False
    if value_type == "date":
        if not isinstance(value, str):
            return value, False
        try:
            return _parse_date(value), True
        except ValueError:
            return value, False
    if isinstance(value, int) and not isinstance(value, bool) and not _in_int64(value):
        return str(value), False
    return value, True


@dataclass
class SampleProjection:
    id: int
    sample_type_id: int
    label: str
    props: dict
    cast_failures: list[str] = field(default_factory=list)


def project_sample(row: dict, sample_type_title: str, value_types: dict[str, str],
                   project_ids: list[int]) -> SampleProjection:
    """Project a ``samples`` row onto its node.

    ``row`` has ``id``, ``uuid``, ``title``, ``sample_type_id`` and ``json_metadata`` (a JSON object as text);
    ``value_types`` maps attribute title to value_type for this sample's type (missing titles are ``string``);
    ``project_ids`` are the sample's ``projects_samples`` projects, duplicates allowed.

    Raises ValueError when ``json_metadata`` is not a JSON object, or when a metadata key is exactly a system
    property name, which would overwrite the node's own key (case variants such as ``Type`` and ``ID`` are fine).
    """
    sample_id = int(row["id"])
    meta = json.loads(row["json_metadata"] or "{}")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError(f"sample {sample_id}: json_metadata is a {type(meta).__name__}, not a JSON object")
    props = {"id": sample_id, "uuid": row["uuid"], "type": sample_type_title,
             "project_ids": sorted({int(x) for x in project_ids})}
    if not is_empty(row.get("title")):
        props["title"] = row["title"]
    values, failures = [], []
    for key, raw in meta.items():
        if is_empty(raw):
            continue
        if key in SKIPPED_METADATA_KEYS:
            # Not a property (``uuid`` already holds it), but still a value: advanced_search's LIKE over
            # json_metadata matches a term found only in the UID ("TIS-" finds every TIS sample), so keyword
            # search must see it too.
            values.append(str(raw))
            continue
        if key in SYSTEM_KEYS:
            raise ValueError(f"sample {sample_id}: metadata key {key!r} is a system property name")
        if isinstance(raw, (list, dict)):
            raw = json.dumps(raw)
        typed, ok = cast_value(raw, value_types.get(key, "string"))
        if not ok:
            failures.append(key)
        props[key] = typed
        values.append(str(raw))
    props["search_text"] = "\n".join(values)
    return SampleProjection(sample_id, int(row["sample_type_id"]), label_for(sample_type_title), props, failures)
