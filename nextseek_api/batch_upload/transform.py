"""Stage 4: TRANSFORM — Convert InputRowModel to InsertableSample."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Dict, List, Optional, Tuple, Union

from sqlalchemy.engine import Connection

from .errors import JsonNormalizationError
from .models import InputRowModel, InsertableSample, SampleMetadata
from .prefetch import resolve_sample_type_id, validate_assay_ids

try:
    import orjson

    def _json_loads(s):
        return orjson.loads(s)

    def _json_dumps_min(obj):
        return orjson.dumps(obj).decode("utf-8")

except ImportError:
    _json_loads = json.loads

    def _json_dumps_min(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


log = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def build_insertable(
    row: InputRowModel,
    project_id: int,
    conn: Connection,
) -> Tuple[InsertableSample, Dict[str, List[str]]]:
    """Transform an InputRowModel into an InsertableSample.

    Returns (InsertableSample, warnings_dict) where warnings_dict maps
    warning categories to lists of messages.
    """
    warnings: Dict[str, List[str]] = {}

    # 1. Effective project_id (per-row override > global default)
    effective_pid = row.project_id if row.project_id is not None else project_id

    # 2. Parse and minify JSON metadata
    try:
        minified = parse_and_minify_json(row.json_metadata)
    except JsonNormalizationError:
        raise

    # 3. Title from metadata
    try:
        meta = SampleMetadata.model_validate(_json_loads(minified))
    except Exception:
        meta = SampleMetadata()
    title = title_from_metadata(meta, row.UID)

    # 4. Resolve sample type
    sample_type_id = resolve_sample_type_id(row.SampleType, effective_pid, conn)

    # 5. Validate assay IDs
    valid_assays, missing_assays = validate_assay_ids(row.assay_ids, conn)
    if missing_assays:
        warnings.setdefault("missing_assays", []).append(
            f"Assay IDs not found: {sorted(missing_assays)}"
        )

    # 6. Construct InsertableSample
    sample = InsertableSample(
        uuid=row.UID,
        title=title,
        sample_type_id=sample_type_id,
        json_metadata=minified,
        assay_ids=sorted(valid_assays),
    )

    return sample, warnings


def parse_and_minify_json(raw: str) -> str:
    """Strip BOM, parse, and re-serialize JSON with minimal whitespace.

    Raises JsonNormalizationError on malformed input.
    """
    try:
        s = raw.lstrip("\ufeff").strip() if isinstance(raw, str) else str(raw)
        if not s:
            return "{}"
        parsed = _json_loads(s)
        minified = _json_dumps_min(parsed)
        # NFC normalize the output
        return unicodedata.normalize("NFC", minified)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise JsonNormalizationError(f"Cannot parse JSON metadata: {exc}") from exc


def title_from_metadata(metadata: SampleMetadata, uid: str) -> str:
    """Determine sample title with priority chain.

    Priority: metadata.Name > metadata.File_PrimartyData > uid
    (File_PrimartyData typo is intentional — matches SEEK schema)
    """
    if metadata.Name is not None:
        name = str(metadata.Name).strip()
        if name:
            return _normalize_text(name)

    if metadata.File_PrimartyData is not None:
        fpd = str(metadata.File_PrimartyData).strip()
        if fpd:
            return _normalize_text(fpd)

    return _normalize_text(uid)


def _normalize_text(s: str) -> str:
    """NFC-normalize, strip, and collapse whitespace."""
    s = unicodedata.normalize("NFC", s)
    s = s.strip()
    s = _WHITESPACE_RE.sub(" ", s)
    return s
